from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse


PROFILES = (
    "default",
    "anti_seo",
    "technical_primary",
    "medical_high_risk",
    "policy_primary",
    "product_feedback",
)
ROUTING_STRATEGIES = ("adaptive_staged", "legacy_fanout", "manual")
ROUTE_KINDS = (
    "daily_web",
    "deep_research",
    "chinese_web",
    "developer",
    "academic",
    "high_risk_factcheck",
    "social_platform",
    "known_url",
)


PROVIDER_SPECS: dict[str, dict] = {
    "searxng": {"cost_tier": "local_free", "credential_mode": "none", "intents": ["daily", "deep", "zh", "academic", "verify", "developer"]},
    "you": {"cost_tier": "free_quota", "credential_mode": "api_key", "intents": ["daily", "deep", "zh"]},
    "brave": {"cost_tier": "free_credits", "credential_mode": "api_key", "intents": ["daily", "zh", "verify", "developer"]},
    "tavily": {"cost_tier": "free_quota", "credential_mode": "api_key", "intents": ["daily", "deep", "academic", "verify"]},
    "exa": {"cost_tier": "free_quota", "credential_mode": "api_key", "intents": ["deep", "developer", "academic", "verify"]},
    "jina": {"cost_tier": "free_quota", "credential_mode": "api_key", "intents": ["deep", "academic"]},
    "metaso": {"cost_tier": "small_free_quota", "credential_mode": "api_key", "intents": ["zh"]},
    "serpapi": {"cost_tier": "small_free_quota", "credential_mode": "api_key", "intents": ["fallback"]},
    "firecrawl_developer": {"cost_tier": "keyless_or_credits", "credential_mode": "keyless_or_api_key", "intents": ["developer"]},
    "github_cli": {"cost_tier": "local_free", "credential_mode": "optional_login", "intents": ["developer"]},
    "perplexity": {"cost_tier": "paid", "credential_mode": "api_key", "intents": ["manual"]},
    "kagi": {"cost_tier": "paid", "credential_mode": "api_key", "intents": ["manual"]},
}


ROUTE_MATRIX: dict[str, list[list[str]]] = {
    "daily_web": [["searxng", "you"], ["brave"]],
    "chinese_web": [["searxng", "you"], ["metaso"], ["brave", "tavily"]],
    "deep_research": [["searxng", "tavily"], ["exa", "you"], ["brave", "serpapi"]],
    "developer": [["firecrawl_developer", "github_cli"], ["exa"], ["searxng", "brave"]],
    "academic": [["searxng", "tavily"], ["exa", "jina"]],
    "high_risk_factcheck": [["searxng", "brave"], ["tavily", "exa"]],
    "social_platform": [],
    "known_url": [],
}


ROUTE_BUDGETS: dict[str, dict[str, int]] = {
    "daily_web": {"max_stages": 2, "max_provider_calls": 3, "max_results_per_provider": 5},
    "chinese_web": {"max_stages": 3, "max_provider_calls": 5, "max_results_per_provider": 5},
    "deep_research": {"max_stages": 3, "max_provider_calls": 5, "max_results_per_provider": 5},
    "developer": {"max_stages": 3, "max_provider_calls": 5, "max_results_per_provider": 5},
    "academic": {"max_stages": 3, "max_provider_calls": 5, "max_results_per_provider": 5},
    "high_risk_factcheck": {"max_stages": 3, "max_provider_calls": 6, "max_results_per_provider": 5},
    "social_platform": {"max_stages": 0, "max_provider_calls": 0, "max_results_per_provider": 0},
    "known_url": {"max_stages": 0, "max_provider_calls": 0, "max_results_per_provider": 0},
}


@dataclass(frozen=True, slots=True)
class RouteStage:
    id: str
    providers: list[str]
    reason: str
    max_results_per_provider: int = 5


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    query: str
    mode: str
    profile: str
    route_kind: str
    routing_strategy: str
    stages: list[RouteStage] = field(default_factory=list)
    provider_pool: list[str] = field(default_factory=list)
    provider_budget: dict[str, int] = field(default_factory=dict)
    stop_conditions: dict[str, int | bool] = field(default_factory=dict)
    external_acquisition: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_routing_plan(
    query: str,
    *,
    mode: str = "auto",
    profile: str = "default",
    routing_strategy: str = "adaptive_staged",
    provider_pool: list[str] | None = None,
    explicit_providers: list[str] | None = None,
    deep: bool = False,
) -> RoutingPlan:
    cleaned = " ".join(str(query).split())
    selected_profile = profile if profile in PROFILES else "default"
    selected_strategy = routing_strategy if routing_strategy in ROUTING_STRATEGIES else "adaptive_staged"
    route_kind = classify_route(cleaned, mode=mode, profile=selected_profile, deep=deep)
    pool = _dedupe(provider_pool or list(PROVIDER_SPECS))
    warnings: list[str] = []
    if profile not in PROFILES:
        warnings.append(f"unknown profile {profile!r}; using default")
    if routing_strategy not in ROUTING_STRATEGIES:
        warnings.append(f"unknown routing strategy {routing_strategy!r}; using adaptive_staged")

    if explicit_providers:
        selected_strategy = "manual"
        providers = _dedupe(explicit_providers)
        stages = [RouteStage("manual", providers, "explicit provider override", 5)]
        budget = {"max_stages": 1, "max_provider_calls": len(providers), "max_results_per_provider": 5}
    elif selected_strategy == "legacy_fanout":
        providers = _dedupe(provider_pool or ["searxng"])
        stages = [RouteStage("legacy_fanout", providers, "legacy configured-provider fanout", 5)]
        budget = {"max_stages": 1, "max_provider_calls": len(providers), "max_results_per_provider": 5}
    else:
        budget = dict(ROUTE_BUDGETS[route_kind])
        stages = _adaptive_stages(route_kind, pool, budget)

    external = _external_acquisition(route_kind, cleaned)
    return RoutingPlan(
        query=cleaned,
        mode=mode,
        profile=selected_profile,
        route_kind=route_kind,
        routing_strategy=selected_strategy,
        stages=stages,
        provider_pool=pool,
        provider_budget=budget,
        stop_conditions=_stop_conditions(route_kind),
        external_acquisition=external,
        warnings=warnings,
    )


def classify_route(query: str, *, mode: str = "auto", profile: str = "default", deep: bool = False) -> str:
    text = query.lower().strip()
    if _is_known_url(text):
        return "known_url"
    if _contains(text, ("twitter", "x.com", "reddit", "小红书", "xiaohongshu", "bilibili", "b站", "facebook", "instagram", "v2ex", "linkedin", "领英", "youtube", "播客")):
        return "social_platform"
    if profile in {"medical_high_risk", "policy_primary"} or _contains(text, ("medical", "clinical", "disease", "drug", "health", "医疗", "疾病", "用药", "诊断", "治疗", "法律", "投资建议", "保险条款")):
        return "high_risk_factcheck"
    if mode == "academic" or _contains(text, ("paper", "systematic review", "meta-analysis", "dataset", "benchmark", "arxiv", "pubmed", "论文", "系统综述", "荟萃分析", "数据集")):
        return "academic"
    if mode == "tool_selection" or profile == "technical_primary":
        return "developer"
    if deep or profile == "anti_seo" or _contains(text, ("deep research", "deep search", "fact check", "深度研究", "深度搜索", "事实核查", "全面调研", "证据包")):
        return "deep_research"
    if _contains(text, ("api", "sdk", "webhook", "github", "repository", "issue", "pull request", "error", "exception", "代码", "报错", "开源项目", "接口", "开发者")):
        return "developer"
    if _looks_chinese(text):
        return "chinese_web"
    return "daily_web"


def coverage_gate(route_kind: str, sources: list, provider_statuses: list[dict] | None = None) -> dict:
    urls = [str(getattr(item, "url", "") or "") for item in sources]
    domains = {urlparse(url).netloc.lower().removeprefix("www.") for url in urls if urlparse(url).netloc}
    providers = {
        str(status.get("provider") or "")
        for status in provider_statuses or []
        if status.get("status") == "ok" and int(status.get("source_count") or 0) > 0
    }
    source_types = {str(getattr(item, "source_type", "unknown")) for item in sources}
    primary_count = sum(1 for item in sources if str(getattr(item, "source_type", "")) in {"official", "academic", "project"})
    conditions = _stop_conditions(route_kind)
    checks = {
        "result_count": len(sources) >= int(conditions.get("min_results", 0)),
        "domain_count": len(domains) >= int(conditions.get("min_domains", 0)),
        "provider_count": len(providers) >= int(conditions.get("min_providers", 0)),
        "source_type_count": len(source_types) >= int(conditions.get("min_source_types", 0)),
        "primary_count": primary_count >= int(conditions.get("min_primary", 0)),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "observed": {
            "result_count": len(sources),
            "domain_count": len(domains),
            "provider_count": len(providers),
            "source_type_count": len(source_types),
            "primary_count": primary_count,
        },
        "required": conditions,
    }


def provider_catalog() -> dict[str, dict]:
    return {name: dict(spec) for name, spec in PROVIDER_SPECS.items()}


def routing_plan_to_markdown(plan: RoutingPlan) -> str:
    lines = [
        f"# Route Plan: {plan.query}",
        "",
        f"- Route: `{plan.route_kind}`",
        f"- Mode: `{plan.mode}`",
        f"- Profile: `{plan.profile}`",
        f"- Strategy: `{plan.routing_strategy}`",
        f"- Budget: {plan.provider_budget.get('max_provider_calls', 0)} provider calls across {plan.provider_budget.get('max_stages', 0)} stage(s)",
        "",
        "## Stages",
    ]
    if plan.stages:
        for stage in plan.stages:
            lines.append(f"- `{stage.id}`: {', '.join(stage.providers) or 'none'} - {stage.reason}")
    else:
        lines.append("- No native search stage; follow external acquisition instructions.")
    if plan.external_acquisition:
        lines.extend(["", "## External Acquisition"])
        for item in plan.external_acquisition:
            lines.append(f"- `{item['adapter']}`: {item['action']}")
    return "\n".join(lines) + "\n"


def _adaptive_stages(route_kind: str, provider_pool: list[str], budget: dict[str, int]) -> list[RouteStage]:
    stages: list[RouteStage] = []
    calls = 0
    reasons = ("free/local primary recall", "coverage and diversity gap follow-up", "last-resort fallback")
    for index, configured in enumerate(ROUTE_MATRIX[route_kind]):
        providers = [item for item in configured if item in provider_pool]
        remaining = max(0, budget["max_provider_calls"] - calls)
        providers = providers[:remaining]
        if not providers:
            continue
        stages.append(RouteStage(f"stage_{len(stages) + 1}", providers, reasons[min(index, len(reasons) - 1)], budget["max_results_per_provider"]))
        calls += len(providers)
        if len(stages) >= budget["max_stages"] or calls >= budget["max_provider_calls"]:
            break
    return stages


def _stop_conditions(route_kind: str) -> dict[str, int | bool]:
    if route_kind in {"deep_research", "developer", "academic"}:
        return {"min_results": 8, "min_domains": 4, "min_providers": 2, "min_source_types": 2, "min_primary": 2}
    if route_kind == "high_risk_factcheck":
        return {"min_results": 8, "min_domains": 4, "min_providers": 2, "min_source_types": 2, "min_primary": 2, "requires_two_independent_sources_per_high_risk_claim": True}
    if route_kind in {"social_platform", "known_url"}:
        return {"min_results": 0, "min_domains": 0, "min_providers": 0, "min_source_types": 0, "min_primary": 0}
    return {"min_results": 5, "min_domains": 3, "min_providers": 2, "min_source_types": 1, "min_primary": 0}


def _external_acquisition(route_kind: str, query: str) -> list[dict]:
    if route_kind == "social_platform":
        return [{"adapter": "agent_reach", "action": "use the matching platform backend, then return normalized external_payloads", "query": query}]
    if route_kind == "known_url":
        return [{"adapter": "web_content_fetcher", "action": "fetch the known URL and ingest extracted evidence", "url": query}]
    return []


def _is_known_url(text: str) -> bool:
    return bool(re.fullmatch(r"https?://\S+", text.strip()))


def _looks_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _contains(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(str(item).strip().lower() for item in items if str(item).strip()))
