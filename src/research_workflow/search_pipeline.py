from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .evidence import extract_evidence_from_sources, source_from_url
from .external_adapters import normalize_external_payload
from .factcheck import extract_atomic_claims, verify_claims
from .models import Claim, Evidence, QueryPlan, RevisionSuggestion, Source, now_iso
from .pipeline import dedupe_rows
from .planner import _looks_inventory_topic as planner_looks_inventory_topic
from .planner import plan_query, uses_project_discovery
from .search import SearchError, multi_source_search_with_status
from .revisions import suggest_revisions
from .routing import RoutingPlan, build_routing_plan, coverage_gate
from .sqlite_store import sync_jsonl_to_sqlite_quietly
from .store import append_jsonl, ensure_data_dir, read_jsonl, stable_id, write_text
from .synthesis import synthesize_claim_text


@dataclass(frozen=True, slots=True)
class SearchQuerySpec:
    query: str
    bucket: str = "general"
    purpose: str = ""


CURATED_INVENTORY_SEEDS: tuple[SearchQuerySpec, ...] = (
    SearchQuerySpec("AnySearch Skill GitHub", "skill best practices", "external-result seed: search skill repository"),
    SearchQuerySpec("OpenFactVerification Loki GitHub", "fact-checking methods", "external-result seed: fact-checking system"),
    SearchQuerySpec("Holmes fact checking framework arxiv 2505.03135", "fact-checking methods", "external-result seed: fact-checking paper"),
    SearchQuerySpec("SAFE LongFact google deepmind long-form-factuality", "papers and benchmarks", "external-result seed: long-form factuality"),
    SearchQuerySpec("FacTool GAIR-NLP GitHub", "fact-checking methods", "external-result seed: factuality tool"),
    SearchQuerySpec("FlashCheck arxiv 2502.05803 fact checking", "papers and benchmarks", "external-result seed: fast evidence retrieval"),
    SearchQuerySpec("DeepTRACE deep research audit fact support matrix arxiv", "papers and benchmarks", "external-result seed: report audit"),
    SearchQuerySpec("BrowseComp-Plus deep search benchmark arxiv", "papers and benchmarks", "external-result seed: deep search benchmark"),
    SearchQuerySpec("OpenResearcher deep research agent arxiv", "open-source projects", "external-result seed: deep research agent"),
    SearchQuerySpec("OpenDeepResearcher GPT-Researcher DeerFlow Open Deep Search OWL GitHub", "open-source projects", "external-result seed: deep research ecosystem"),
)

UNVERIFIED_EXTERNAL_AI_SEEDS: tuple[SearchQuerySpec, ...] = (
    SearchQuerySpec("Sift Source Inspection Fact-checking Tool ashg2099 GitHub", "unverified external candidates", "external-result seed: verify existence before citing"),
    SearchQuerySpec("TRUST Agents GautamaShastry news_agent fact checking GitHub", "unverified external candidates", "external-result seed: verify existence before citing"),
    SearchQuerySpec("OpenJiuwen DeepSearch BrowseComp-Plus GitHub", "unverified external candidates", "external-result seed: verify benchmark claim before citing"),
    SearchQuerySpec("academic-research-skills graykode GitHub", "unverified external candidates", "external-result seed: verify repository and star claims"),
)


CURATED_INVENTORY_URLS: tuple[tuple[str, str], ...] = (
    ("https://github.com/anysearch-ai/anysearch-skill", "Skill best-practice seed: AnySearch Skill repository."),
    ("https://github.com/Libr-AI/OpenFactVerification", "Fact-checking seed: OpenFactVerification/Loki repository."),
    ("https://arxiv.org/abs/2505.03135", "Fact-checking seed: Holmes paper."),
    ("https://deepmind.google/research/publications/85420/", "Benchmark seed: SAFE/LongFact publication page."),
    ("https://github.com/GAIR-NLP/factool", "Fact-checking seed: FacTool repository."),
    ("https://arxiv.org/abs/2502.05803", "Benchmark seed: FlashCheck paper."),
    ("https://arxiv.org/abs/2509.04499", "Benchmark seed: DeepTRACE paper."),
    ("https://arxiv.org/abs/2508.06600", "Deep-search benchmark seed: BrowseComp-Plus paper."),
    ("https://arxiv.org/abs/2603.20278", "Deep-research agent seed: OpenResearcher paper."),
    ("https://github.com/assafelovic/gpt-researcher", "Deep-research project seed: GPT Researcher repository."),
    ("https://github.com/langchain-ai/open_deep_research", "Deep-research project seed: Open Deep Research repository."),
    ("https://github.com/stanford-oval/storm", "Deep-research project seed: STORM repository."),
)

LIVE_QUERY_BUDGET = int(os.environ.get("RESEARCH_LIVE_QUERY_BUDGET", "4"))
FOLLOWUP_QUERY_BUDGET = int(os.environ.get("RESEARCH_FOLLOWUP_QUERY_BUDGET", "2"))


@dataclass(slots=True)
class SearchPipelineResult:
    run_id: str
    plan: QueryPlan
    sources: list[Source]
    evidence: list[Evidence]
    claims: list[Claim]
    revision_suggestions: list[RevisionSuggestion]
    report_markdown: str
    report_json: str
    search_queries: list[dict]
    provider_health: list[dict]
    canonical_groups: list[dict]
    tool_matrix: list[dict]
    source_quality: dict
    routing_plan: dict = field(default_factory=dict)
    routing_trace: list[dict] = field(default_factory=list)
    coverage_gate: dict = field(default_factory=dict)
    provider_budget: dict = field(default_factory=dict)
    external_acquisition: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    duration_seconds: float = 0.0
    warning: str = ""
    report_path: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "kind": "search",
            "plan": self.plan.to_dict(),
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "claims": [claim.to_dict() for claim in self.claims],
            "revision_suggestions": [item.to_dict() for item in self.revision_suggestions],
            "search_queries": self.search_queries,
            "provider_health": self.provider_health,
            "canonical_groups": self.canonical_groups,
            "tool_matrix": self.tool_matrix,
            "source_quality": self.source_quality,
            "routing_plan": self.routing_plan or {},
            "routing_trace": self.routing_trace or [],
            "coverage_gate": self.coverage_gate or {},
            "provider_budget": self.provider_budget or {},
            "external_acquisition": self.external_acquisition or [],
            "stop_reason": self.stop_reason,
            "interface_contract": self.interface_contract(),
            "duration_seconds": self.duration_seconds,
            "research_buckets": {bucket: [source.to_dict() for source in bucket_sources] for bucket, bucket_sources in _bucket_sources(self.sources).items()},
            "coverage_gaps": _coverage_gaps(self.sources, self.plan),
            "report_markdown": self.report_markdown,
            "report_json": self.report_json,
            "warning": self.warning,
            "report_path": self.report_path,
        }

    def interface_contract(self) -> dict:
        return _search_interface_contract(self.sources, self.evidence, self.claims)


def _search_interface_contract(sources: list[Source], evidence: list[Evidence], claims: list[Claim]) -> dict:
    if evidence:
        evidence_status = "collected"
    elif sources:
        evidence_status = "sources_only"
    else:
        evidence_status = "unavailable"
    if not sources:
        source_trail_status = "unavailable"
    elif all(source.url and source.search_buckets and source.search_stages for source in sources):
        source_trail_status = "complete"
    else:
        source_trail_status = "incomplete"
    return {
        "schema": "research.search-result.v1",
        "task_status": "succeeded",
        "evidence_status": evidence_status,
        "source_trail_status": source_trail_status,
        "source_count": len(sources),
        "evidence_count": len(evidence),
        "claim_count": len(claims),
    }


def run_search_pipeline(
    *,
    topic: str,
    data_dir: Path,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    fetch_urls: bool = False,
    searxng_url: str = "",
    search_providers: list[str] | None = None,
    profile: str = "default",
    routing_strategy: str = "legacy_fanout",
    providers_explicit: bool = False,
    external_payloads: list[dict] | None = None,
    skip_providers: list[str] | None = None,
    max_adaptive_stages: int | None = None,
    deep: bool = False,
    auto_verify: bool = False,
) -> SearchPipelineResult:
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")

    started_at = time.perf_counter()
    data_dir = ensure_data_dir(data_dir)
    plan = plan_query(topic, mode)
    warnings: list[str] = []
    sources: list[Source] = []
    search_queries: list[dict] = []
    route_plan = build_routing_plan(
        topic,
        mode=plan.mode,
        profile=profile,
        routing_strategy=routing_strategy,
        provider_pool=search_providers,
        explicit_providers=search_providers if providers_explicit else None,
        deep=deep,
    )
    routing_trace: list[dict] = []
    gate: dict = {}
    stop_reason = ""

    if route_plan.routing_strategy == "adaptive_staged":
        gate, stop_reason = _run_adaptive_route(
            route_plan,
            plan=plan,
            provider_stats_path=str(data_dir / "provider_stats.json"),
            max_results=max_results,
            no_network=no_network,
            searxng_url=searxng_url,
            sources=sources,
            search_queries=search_queries,
            warnings=warnings,
            routing_trace=routing_trace,
            skip_providers=skip_providers,
            max_stages=max_adaptive_stages,
        )
    else:
        _run_specs(
            _live_search_specs(plan, budget=_live_query_budget(deep)),
            stage="stage_1_recall",
            plan=plan,
            provider_stats_path=str(data_dir / "provider_stats.json"),
            max_results=max_results,
            no_network=no_network,
            searxng_url=searxng_url,
            search_providers=search_providers,
            sources=sources,
            search_queries=search_queries,
            warnings=warnings,
        )
        followup_specs = _gap_followup_specs(plan, sources, search_queries, budget=_followup_query_budget(deep))
        if followup_specs:
            _run_specs(
                followup_specs,
                stage="stage_2_gap_followup",
                plan=plan,
                provider_stats_path=str(data_dir / "provider_stats.json"),
                max_results=max_results,
                no_network=no_network,
                searxng_url=searxng_url,
                search_providers=search_providers,
                sources=sources,
                search_queries=search_queries,
                warnings=warnings,
            )
        statuses = [status for item in search_queries for status in item.get("provider_statuses", [])]
        gate = coverage_gate(route_plan.route_kind, _dedupe_sources(_canonicalize_sources(sources)), statuses)
        stop_reason = "legacy_fanout_complete" if route_plan.routing_strategy == "legacy_fanout" else "manual_provider_run_complete"

    provider_returned_no_sources = not sources and any(item["status"] == "empty" for item in search_queries)

    external_evidence: list[Evidence] = []
    for payload in external_payloads or []:
        records = normalize_external_payload(payload, topic=plan.topic)
        sources.extend(records.sources)
        external_evidence.extend(records.evidence)
        warnings.extend(records.warnings)

    effective_urls = list(urls or [])
    if route_plan.route_kind == "known_url" and topic not in effective_urls:
        effective_urls.append(topic)
        fetch_urls = fetch_urls or not no_network
    if effective_urls:
        sources.extend(
            source_from_url(
                url.strip(),
                search_queries=[topic],
                search_buckets=["manual URLs"],
                search_purposes=["user supplied source"],
                search_stages=["manual_ingest"],
            )
            for url in effective_urls
            if url.strip()
        )

    if _looks_inventory_topic(plan.topic) and not sources:
        if provider_returned_no_sources:
            warnings.append("live providers returned no sources; using curated seed candidates")
        sources.extend(_curated_seed_sources())

    sources = _select_sources(_filter_sources_for_plan(plan, _dedupe_sources(_canonicalize_sources(sources))), max_results=max_results)
    annotate_source_quality(sources)
    evidence = extract_evidence_from_sources(sources, fetch=fetch_urls) if sources else []
    evidence = _dedupe_evidence([*evidence, *external_evidence])
    claims = _auto_verify_claims(topic, evidence) if auto_verify else []
    revision_suggestions = suggest_revisions(claims, evidence) if claims else []
    warning = "; ".join(dict.fromkeys(warnings))

    if sources:
        append_jsonl(data_dir / "sources.jsonl", sources)
    if evidence:
        append_jsonl(data_dir / "evidence.jsonl", evidence)
    if claims:
        append_jsonl(data_dir / "claims.jsonl", claims)

    provider_health = provider_health_from_queries(search_queries)
    canonical_groups = canonical_groups_from_sources(sources)
    tool_matrix = build_tool_matrix(sources)
    source_quality = source_quality_summary(sources, evidence)
    duration_seconds = round(time.perf_counter() - started_at, 3)
    routing_metadata = {
        "routing_plan": route_plan.to_dict(),
        "routing_trace": routing_trace,
        "coverage_gate": gate,
        "provider_budget": route_plan.provider_budget,
        "external_acquisition": route_plan.external_acquisition,
        "stop_reason": stop_reason,
    }
    report_markdown = _append_routing_markdown(search_report_to_markdown(plan, sources, evidence, claims=claims, revision_suggestions=revision_suggestions, search_queries=search_queries, provider_health=provider_health, canonical_groups=canonical_groups, tool_matrix=tool_matrix, source_quality=source_quality, duration_seconds=duration_seconds, warning=warning), routing_metadata)
    report_json = _inject_routing_json(search_report_to_json(plan, sources, evidence, claims=claims, revision_suggestions=revision_suggestions, search_queries=search_queries, provider_health=provider_health, canonical_groups=canonical_groups, tool_matrix=tool_matrix, source_quality=source_quality, duration_seconds=duration_seconds, warning=warning), routing_metadata)
    run_record = persist_search_run(
        data_dir=data_dir,
        plan=plan,
        sources=sources,
        evidence=evidence,
        claims=claims,
        revision_suggestions=revision_suggestions,
        search_queries=search_queries,
        provider_health=provider_health,
        canonical_groups=canonical_groups,
        tool_matrix=tool_matrix,
        source_quality=source_quality,
        duration_seconds=duration_seconds,
        report_markdown=report_markdown,
        report_json=report_json,
        warning=warning,
        routing_metadata=routing_metadata,
    )
    sync_jsonl_to_sqlite_quietly(data_dir)
    return SearchPipelineResult(
        run_id=run_record["id"],
        plan=plan,
        sources=sources,
        evidence=evidence,
        claims=claims,
        revision_suggestions=revision_suggestions,
        report_markdown=report_markdown,
        report_json=report_json,
        search_queries=search_queries,
        provider_health=provider_health,
        canonical_groups=canonical_groups,
        tool_matrix=tool_matrix,
        source_quality=source_quality,
        routing_plan=route_plan.to_dict(),
        routing_trace=routing_trace,
        coverage_gate=gate,
        provider_budget=route_plan.provider_budget,
        external_acquisition=route_plan.external_acquisition,
        stop_reason=stop_reason,
        duration_seconds=duration_seconds,
        warning=warning,
        report_path=str(run_record["report_path"]),
    )


def persist_search_run(
    *,
    data_dir: Path,
    plan: QueryPlan,
    sources: list[Source],
    evidence: list[Evidence],
    claims: list[Claim],
    revision_suggestions: list[RevisionSuggestion],
    search_queries: list[dict],
    provider_health: list[dict],
    canonical_groups: list[dict],
    tool_matrix: list[dict],
    source_quality: dict,
    report_markdown: str,
    report_json: str,
    warning: str = "",
    duration_seconds: float = 0.0,
    routing_metadata: dict | None = None,
) -> dict:
    created_at = now_iso()
    run_id = stable_id(created_at, plan.topic, report_json, prefix="search_")
    report_file = data_dir / "reports" / f"{_safe_filename(created_at)}-{_safe_filename(plan.topic)}.search.md"
    write_text(report_file, report_markdown)
    routing_metadata = routing_metadata or {}
    record = {
        "id": run_id,
        "kind": "search",
        "topic": plan.topic,
        "mode": plan.mode,
        "plan": plan.to_dict(),
        "created_at": created_at,
        "claim_count": len(claims),
        "source_count": len(sources),
        "evidence_count": len(evidence),
        "supported_count": sum(1 for claim in claims if str(claim.verdict) == "supported"),
        "partial_count": sum(1 for claim in claims if str(claim.verdict) == "partial"),
        "contradicted_count": sum(1 for claim in claims if str(claim.verdict) == "contradicted"),
        "not_found_count": sum(1 for claim in claims if str(claim.verdict) == "not_found"),
        "report_path": str(report_file),
        "warning": warning,
        "report_markdown": report_markdown,
        "report_json": report_json,
        "search_queries": search_queries,
        "provider_health": provider_health,
        "canonical_groups": canonical_groups,
        "tool_matrix": tool_matrix,
        "source_quality": source_quality,
        "routing_plan": routing_metadata.get("routing_plan", {}),
        "routing_trace": routing_metadata.get("routing_trace", []),
        "coverage_gate": routing_metadata.get("coverage_gate", {}),
        "provider_budget": routing_metadata.get("provider_budget", {}),
        "external_acquisition": routing_metadata.get("external_acquisition", []),
        "stop_reason": routing_metadata.get("stop_reason", ""),
        "interface_contract": _search_interface_contract(sources, evidence, claims),
        "duration_seconds": duration_seconds,
        "sources": [source.to_dict() for source in sources],
        "evidence": [item.to_dict() for item in evidence],
        "claims": [claim.to_dict() for claim in claims],
        "revision_suggestions": [item.to_dict() for item in revision_suggestions],
        "audit": {"overall": "not_run", "reason": "search-only run"},
    }
    append_jsonl(data_dir / "runs.jsonl", [record])
    append_jsonl(data_dir / "search_runs.jsonl", [record])
    return record


def list_search_runs(data_dir: Path, limit: int = 20) -> list[dict]:
    rows = dedupe_rows(read_jsonl(data_dir / "search_runs.jsonl"))
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows[:limit]


def _run_specs(
    specs: list[SearchQuerySpec],
    *,
    stage: str,
    plan: QueryPlan,
    provider_stats_path: str,
    max_results: int,
    no_network: bool,
    searxng_url: str,
    search_providers: list[str] | None,
    sources: list[Source],
    search_queries: list[dict],
    warnings: list[str],
) -> None:
    for spec in specs:
        if no_network:
            warning = "live provider search disabled"
            warnings.append(warning)
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage,
                    "status": "skipped",
                    "source_count": 0,
                    "warning": warning,
                }
            )
            continue
        try:
            provider_result = multi_source_search_with_status(
                spec.query,
                mode=plan.mode,
                max_results=_per_query_limit(max_results),
                no_network=no_network,
                searxng_url=searxng_url,
                providers=search_providers,
                provider_stats_path=provider_stats_path,
            )
            query_sources = provider_result.sources
            _annotate_retrieval_provenance(query_sources, spec, stage)
            warnings.extend(provider_result.warnings)
            sources.extend(query_sources)
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage,
                    "status": _query_status(query_sources, provider_result.provider_statuses),
                    "source_count": len(query_sources),
                    "provider_statuses": provider_result.provider_statuses,
                    "warning": "; ".join(provider_result.warnings),
                }
            )
        except SearchError as exc:
            warning = str(exc)
            warnings.append(warning)
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage,
                    "status": "error",
                    "source_count": 0,
                    "warning": warning,
                }
            )


def _run_adaptive_route(
    route_plan: RoutingPlan,
    *,
    plan: QueryPlan,
    provider_stats_path: str,
    max_results: int,
    no_network: bool,
    searxng_url: str,
    sources: list[Source],
    search_queries: list[dict],
    warnings: list[str],
    routing_trace: list[dict],
    skip_providers: list[str] | None = None,
    max_stages: int | None = None,
) -> tuple[dict, str]:
    if not route_plan.stages:
        reason = "external_acquisition_required" if route_plan.external_acquisition else "no_native_route"
        return coverage_gate(route_plan.route_kind, sources, []), reason

    all_statuses: list[dict] = []
    skipped = {str(item).strip().lower() for item in (skip_providers or []) if str(item).strip()}
    stages_called = 0
    for index, route_stage in enumerate(route_plan.stages):
        stage_name = f"adaptive_{route_stage.id}"
        stage_providers = [provider for provider in route_stage.providers if provider not in skipped]
        if not stage_providers:
            routing_trace.append(
                {
                    "stage": stage_name,
                    "providers": route_stage.providers,
                    "status": "skipped",
                    "reason": "providers already succeeded in previous run",
                }
            )
            continue
        if max_stages is not None and stages_called >= max(0, int(max_stages)):
            routing_trace.append(
                {
                    "stage": stage_name,
                    "providers": stage_providers,
                    "status": "deferred",
                    "reason": "next-gap stage limit reached",
                }
            )
            continue
        stages_called += 1
        spec = _adaptive_query_spec(plan, route_plan.route_kind, index)
        if no_network:
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage_name,
                    "status": "skipped",
                    "source_count": 0,
                    "provider_statuses": [],
                    "warning": "live provider search disabled",
                }
            )
            routing_trace.append({"stage": stage_name, "providers": stage_providers, "status": "skipped", "reason": "no_network"})
            continue
        try:
            provider_result = multi_source_search_with_status(
                spec.query,
                mode="daily",
                max_results=min(max_results, route_stage.max_results_per_provider * max(1, len(stage_providers))),
                searxng_url=searxng_url,
                providers=stage_providers,
                provider_stats_path=provider_stats_path,
                route_kind=route_plan.route_kind,
            )
            query_sources = provider_result.sources
            _annotate_retrieval_provenance(query_sources, spec, stage_name)
            sources.extend(query_sources)
            warnings.extend(provider_result.warnings)
            all_statuses.extend(provider_result.provider_statuses)
            current_sources = _dedupe_sources(_canonicalize_sources(sources))
            gate = coverage_gate(route_plan.route_kind, current_sources, all_statuses)
            status = _query_status(query_sources, provider_result.provider_statuses)
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage_name,
                    "status": status,
                    "source_count": len(query_sources),
                    "provider_statuses": provider_result.provider_statuses,
                    "warning": "; ".join(provider_result.warnings),
                }
            )
            routing_trace.append(
                {
                    "stage": stage_name,
                    "providers": stage_providers,
                    "status": status,
                    "source_count": len(query_sources),
                    "coverage_passed": gate["passed"],
                    "reason": route_stage.reason,
                }
            )
            if gate["passed"]:
                return gate, "coverage_gate_passed"
        except SearchError as exc:
            warning = str(exc)
            warnings.append(warning)
            search_queries.append(
                {
                    "query": spec.query,
                    "bucket": spec.bucket,
                    "purpose": spec.purpose,
                    "stage": stage_name,
                    "status": "error",
                    "source_count": 0,
                    "provider_statuses": [],
                    "warning": warning,
                }
            )
            routing_trace.append({"stage": stage_name, "providers": stage_providers, "status": "error", "reason": warning})
    gate = coverage_gate(route_plan.route_kind, _dedupe_sources(_canonicalize_sources(sources)), all_statuses)
    if no_network:
        return gate, "network_disabled"
    if skipped and stages_called == 0:
        return gate, "no_remaining_deepen_stage"
    if max_stages is not None and stages_called >= max(0, int(max_stages)):
        return gate, "next_gap_stage_complete"
    return gate, "provider_budget_exhausted" if not gate["passed"] else "coverage_gate_passed"


def _adaptive_query_spec(plan: QueryPlan, route_kind: str, stage_index: int) -> SearchQuerySpec:
    suffixes = {
        "daily_web": ("", " official source"),
        "chinese_web": ("", " 中文 官方", " 深度 证据"),
        "deep_research": ("", " primary sources evidence", " limitations independent review"),
        "developer": (" API documentation GitHub", " issue pull request error", " official docs changelog"),
        "academic": (" systematic review paper", " dataset benchmark DOI", " official research institution"),
        "high_risk_factcheck": (" official guideline", " systematic review independent evidence", " regulator consensus"),
    }
    variants = suffixes.get(route_kind, ("",))
    suffix = variants[min(stage_index, len(variants) - 1)]
    bucket = "official docs" if stage_index > 0 else "general"
    if route_kind == "academic":
        bucket = "papers and benchmarks"
    if route_kind == "developer":
        bucket = "open-source projects"
    return SearchQuerySpec(f"{plan.topic}{suffix}".strip(), bucket, f"adaptive route {route_kind} stage {stage_index + 1}")


def _append_routing_markdown(markdown: str, metadata: dict) -> str:
    plan = metadata.get("routing_plan") or {}
    gate = metadata.get("coverage_gate") or {}
    lines = [
        markdown.rstrip(),
        "",
        "## Search Routing",
        "",
        f"- Route: `{plan.get('route_kind', 'unknown')}`",
        f"- Strategy: `{plan.get('routing_strategy', 'unknown')}`",
        f"- Profile: `{plan.get('profile', 'default')}`",
        f"- Stop reason: `{metadata.get('stop_reason') or 'unknown'}`",
        f"- Coverage gate: `{'passed' if gate.get('passed') else 'not_passed'}`",
    ]
    for trace in metadata.get("routing_trace") or []:
        lines.append(f"- `{trace.get('stage')}`: {', '.join(trace.get('providers') or [])} - {trace.get('status')}")
    if metadata.get("external_acquisition"):
        lines.extend(["", "### External Acquisition"])
        for item in metadata["external_acquisition"]:
            lines.append(f"- `{item.get('adapter')}`: {item.get('action')}")
    return "\n".join(lines) + "\n"


def _inject_routing_json(payload: str, metadata: dict) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    data.update(metadata)
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def _query_status(sources: list[Source], provider_statuses: list[dict]) -> str:
    provider_states = {str(item.get("status", "")) for item in provider_statuses}
    if sources and provider_states & {"limited", "auth_error", "network_error", "error"}:
        return "partial"
    if sources:
        return "ok"
    if "limited" in provider_states:
        return "limited"
    if "not_configured" in provider_states and provider_states <= {"not_configured"}:
        return "not_configured"
    if provider_states & {"auth_error", "network_error", "error"}:
        return "error"
    return "empty"


def _annotate_retrieval_provenance(sources: list[Source], spec: SearchQuerySpec, stage: str) -> None:
    for source in sources:
        source.search_queries = _dedupe_texts([*source.search_queries, spec.query])
        source.search_buckets = _dedupe_texts([*source.search_buckets, spec.bucket])
        source.search_purposes = _dedupe_texts([*source.search_purposes, spec.purpose])
        source.search_stages = _dedupe_texts([*source.search_stages, stage])


def _provider_status_summary(provider_statuses: list[dict]) -> str:
    if not provider_statuses:
        return ""
    counts: dict[str, int] = {}
    for item in provider_statuses:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return ", ".join(f"{status}:{count}" for status, count in sorted(counts.items()))


def _queries_by_stage(search_queries: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in search_queries:
        grouped.setdefault(str(item.get("stage") or "stage_1_recall"), []).append(item)
    return grouped


def _stage_label(stage: str) -> str:
    labels = {
        "stage_1_recall": "Stage 1 Recall",
        "stage_2_gap_followup": "Stage 2 Gap Follow-up",
    }
    return labels.get(stage, stage)


def planned_search_query_specs(plan: QueryPlan, max_queries: int = 32) -> list[SearchQuerySpec]:
    candidates = [
        SearchQuerySpec(plan.topic, "general", "original user wording"),
        *(SearchQuerySpec(query, "general", "Chinese expansion") for query in plan.zh_keywords[1:3]),
        *(SearchQuerySpec(query, "general", "English expansion") for query in plan.en_keywords[1:3]),
    ]
    if plan.mode == "tool_selection":
        candidates.extend(_tool_selection_query_specs(plan.topic))
    elif _looks_inventory_topic(plan.topic):
        candidates.extend(_inventory_query_specs(plan.topic))
        candidates.extend(CURATED_INVENTORY_SEEDS)
        candidates.extend(UNVERIFIED_EXTERNAL_AI_SEEDS)
    if plan.mode == "academic":
        candidates.extend(
            [
                SearchQuerySpec(f"{plan.topic} paper benchmark dataset", "papers and benchmarks", "academic coverage"),
                SearchQuerySpec(f"{plan.topic} arxiv OR semantic scholar", "papers and benchmarks", "academic indexes"),
            ]
        )
    candidates.append(SearchQuerySpec(f"{plan.topic} official guidance", "official docs", "primary documentation"))
    if uses_project_discovery(plan.topic, plan.mode):
        candidates.append(SearchQuerySpec(f"{plan.topic} GitHub", "open-source projects", "project repositories"))
    deduped: dict[str, SearchQuerySpec] = {}
    for spec in candidates:
        if spec.query.strip() and spec.query not in deduped:
            deduped[spec.query] = spec
    return list(deduped.values())[:max_queries]


def _live_search_specs(plan: QueryPlan, *, budget: int | None = None) -> list[SearchQuerySpec]:
    specs = planned_search_query_specs(plan)
    budget = LIVE_QUERY_BUDGET if budget is None else budget
    if budget <= 0:
        return specs
    if len(specs) <= budget:
        return specs
    must_keep = [
        spec
        for spec in specs
        if spec.purpose == "original user wording"
        or spec.bucket
        in {
            "open-source projects",
            "fact-checking methods",
            "search and extraction infrastructure",
            "papers and benchmarks",
            "official docs",
            "candidate tools",
            "api and automation docs",
        }
    ]
    selected: list[SearchQuerySpec] = []
    seen: set[str] = set()
    for spec in [*must_keep, *specs]:
        if spec.query in seen:
            continue
        selected.append(spec)
        seen.add(spec.query)
        if len(selected) >= budget:
            break
    return selected


def _gap_followup_specs(plan: QueryPlan, sources: list[Source], search_queries: list[dict], *, budget: int | None = None) -> list[SearchQuerySpec]:
    budget = FOLLOWUP_QUERY_BUDGET if budget is None else budget
    if budget <= 0:
        return []
    existing_queries = {str(item.get("query")) for item in search_queries}
    bucketed = _bucket_sources(_dedupe_sources(sources))
    specs: list[SearchQuerySpec] = []
    if plan.mode == "tool_selection":
        if "Candidate tools" not in bucketed:
            specs.extend(
                [
                    SearchQuerySpec("Vikunja OpenProject Plane NocoDB Baserow Taiga API webhook custom fields", "candidate tools", "gap follow-up: task/inbox candidates"),
                    SearchQuerySpec(f"{plan.topic} open source self-hosted task management API webhook", "candidate tools", "gap follow-up: open-source inbox candidates"),
                ]
            )
        if "API and automation docs" not in bucketed:
            specs.extend(
                [
                    SearchQuerySpec("Vikunja API webhooks task relations priority kanban", "api and automation docs", "gap follow-up: Vikunja docs"),
                    SearchQuerySpec("OpenProject API webhooks custom fields work package relations", "api and automation docs", "gap follow-up: OpenProject docs"),
                    SearchQuerySpec("NocoDB API webhooks custom fields kanban table", "api and automation docs", "gap follow-up: NocoDB docs"),
                ]
            )
        selected: list[SearchQuerySpec] = []
        seen: set[str] = set()
        for spec in specs:
            if spec.query in existing_queries or spec.query in seen:
                continue
            selected.append(spec)
            seen.add(spec.query)
            if len(selected) >= budget:
                break
        return selected
    if not _looks_inventory_topic(plan.topic):
        return []
    if "Fact-checking methods" not in bucketed:
        specs.extend(
            [
                SearchQuerySpec("FActScore RARR FacTool SAFE OpenFactCheck GitHub paper", "fact-checking methods", "gap follow-up: fact-checking methods"),
                SearchQuerySpec("OpenFactVerification Loki fact verification GitHub ACL", "fact-checking methods", "gap follow-up: fact verification tools"),
            ]
        )
    if "Open-source projects" not in bucketed:
        specs.append(SearchQuerySpec("GPT Researcher STORM Open Deep Research DeerFlow GitHub", "open-source projects", "gap follow-up: canonical project repositories"))
    if "Papers and benchmarks" not in bucketed:
        specs.extend(
            [
                SearchQuerySpec("LongFact SAFE FlashCheck BrowseComp-Plus DeepTRACE arxiv", "papers and benchmarks", "gap follow-up: factuality and deep-search benchmarks"),
                SearchQuerySpec("Holmes fact checking framework arxiv 2505.03135", "papers and benchmarks", "gap follow-up: recent fact-check framework"),
            ]
        )
    if "Search and extraction infrastructure" not in bucketed:
        specs.append(SearchQuerySpec("SearXNG Firecrawl Jina Reader Tavily Exa search API agent", "search and extraction infrastructure", "gap follow-up: retrieval infrastructure"))
    if "Skill best practices" not in bucketed and _looks_inventory_topic(plan.topic):
        specs.append(SearchQuerySpec("AnySearch Skill AI agent search skill GitHub", "skill best practices", "gap follow-up: agent search skill references"))

    selected: list[SearchQuerySpec] = []
    seen: set[str] = set()
    for spec in specs:
        if spec.query in existing_queries or spec.query in seen:
            continue
        selected.append(spec)
        seen.add(spec.query)
        if len(selected) >= budget:
            break
    return selected


def _live_query_budget(deep: bool) -> int:
    return max(LIVE_QUERY_BUDGET, 8) if deep else LIVE_QUERY_BUDGET


def _followup_query_budget(deep: bool) -> int:
    return max(FOLLOWUP_QUERY_BUDGET, 5) if deep else FOLLOWUP_QUERY_BUDGET


def planned_search_queries(plan: QueryPlan, max_queries: int = 32) -> list[str]:
    return [spec.query for spec in planned_search_query_specs(plan, max_queries=max_queries)]


def search_report_to_markdown(
    plan: QueryPlan,
    sources: list[Source],
    evidence: list[Evidence],
    *,
    claims: list[Claim] | None = None,
    revision_suggestions: list[RevisionSuggestion] | None = None,
    search_queries: list[dict] | None = None,
    provider_health: list[dict] | None = None,
    canonical_groups: list[dict] | None = None,
    tool_matrix: list[dict] | None = None,
    source_quality: dict | None = None,
    duration_seconds: float = 0.0,
    warning: str = "",
) -> str:
    evidence_by_source: dict[str, list[Evidence]] = {}
    for item in evidence:
        evidence_by_source.setdefault(item.source_id, []).append(item)
    search_queries = search_queries or []
    claims = claims or []
    revision_suggestions = revision_suggestions or []
    provider_health = provider_health or []
    canonical_groups = canonical_groups or []
    tool_matrix = tool_matrix or []
    source_quality = source_quality or {}

    lines = [
        f"# {plan.topic}",
        "",
        "## Search Summary",
        "",
        f"- Mode: {plan.mode}",
        f"- Sources collected: {len(sources)}",
        f"- Evidence snippets collected: {len(evidence)}",
        f"- Auto-verified claims: {len(claims)}",
        f"- Duration: {duration_seconds:.3f}s",
        f"- Warning: {warning or 'none'}",
        f"- Search queries attempted: {len(search_queries)}",
        "",
        "## Query Plan",
        "",
        f"- Core question: {plan.core_question}",
        "",
        "## Subquestions",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.subquestions)
    lines.extend(["", "## Keywords", "", f"- Chinese: {', '.join(plan.zh_keywords)}", f"- English: {', '.join(plan.en_keywords)}"])
    lines.extend(["", "## Queries Run", ""])
    if search_queries:
        for stage, items in _queries_by_stage(search_queries).items():
            lines.append(f"### {_stage_label(stage)}")
            for item in items:
                suffix = f"; warning: {item.get('warning')}" if item.get("warning") else ""
                provider_summary = _provider_status_summary(item.get("provider_statuses") or [])
                provider_suffix = f"; providers: {provider_summary}" if provider_summary else ""
                bucket = item.get("bucket") or "general"
                purpose = item.get("purpose") or "coverage"
                lines.append(
                    f"- `{item.get('status', 'unknown')}` {item.get('source_count', 0)} results "
                    f"[{bucket}; {purpose}]: {item.get('query', '')}{provider_suffix}{suffix}"
                )
    else:
        lines.append("- No live provider queries were attempted.")

    lines.extend(["", "## Evidence Quality", ""])
    if source_quality:
        lines.append(f"- Overall: {source_quality.get('overall', 'unknown')} ({source_quality.get('average_score', 0)} avg score)")
        lines.append(f"- Primary/reference sources: {source_quality.get('primary_source_count', 0)} / {source_quality.get('source_count', 0)}")
        lines.append(f"- Sources with evidence: {source_quality.get('sources_with_evidence', 0)} / {source_quality.get('source_count', 0)}")
        if source_quality.get("warnings"):
            lines.extend(f"- Warning: {warning_item}" for warning_item in source_quality.get("warnings", []))
    else:
        lines.append("- Source quality summary not available.")

    lines.extend(["", "## Provider Health", ""])
    if provider_health:
        for item in provider_health:
            limits = item.get("limit_note") or "check provider console for exact quota"
            warnings = item.get("warnings") or []
            warning_note = f"; warnings: {' | '.join(str(warning) for warning in warnings[:2])}" if warnings else ""
            health_parts = [
                f"{item.get('ok_count', 0)} ok",
                f"{item.get('limited_count', 0)} limited",
                f"{item.get('not_configured_count', 0)} not configured",
                f"{item.get('auth_error_count', 0)} auth",
                f"{item.get('network_error_count', 0)} network",
                f"{item.get('error_count', 0)} other errors",
                f"{item.get('avg_seconds', 0)}s avg",
                f"{item.get('max_seconds', 0)}s max",
                f"recent {item.get('recent_status', 'unknown')}",
                f"rank {item.get('rank_score', 0)}",
            ]
            lines.append(
                f"- `{item.get('provider')}`: {item.get('overall')} "
                f"({', '.join(health_parts)}); cost: {item.get('cost_tier')}; {limits}; rank reason: {item.get('rank_reason', 'n/a')}{warning_note}"
            )
    else:
        lines.append("- No provider calls recorded.")

    lines.extend(["", "## Canonical Groups", ""])
    if canonical_groups:
        for group in canonical_groups:
            lines.append(f"- [{group.get('title') or group.get('canonical_url')}]({group.get('canonical_url')}) - {group.get('duplicate_count')} merged URL(s)")
    else:
        lines.append("- No duplicate groups detected.")

    lines.extend(["", "## Tool Matrix", ""])
    if tool_matrix:
        lines.append("| Tool / Project | Type | Open Source | Maintenance | Recommendation | Evidence |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for item in tool_matrix:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _escape_md_table(str(item.get("name") or "")),
                        _escape_md_table(str(item.get("type") or "")),
                        _escape_md_table(str(item.get("open_source") or "")),
                        _escape_md_table(str(item.get("maintenance_status") or "")),
                        _escape_md_table(str(item.get("recommendation") or "")),
                        f"[source]({item.get('evidence_url')})" if item.get("evidence_url") else "",
                    ]
                )
                + " |"
            )
    else:
        lines.append("- No tool/project rows inferred yet.")

    lines.extend(["", "## Research Buckets", ""])
    bucketed = _bucket_sources(sources)
    if bucketed:
        for bucket, bucket_sources in bucketed.items():
            lines.append(f"### {bucket}")
            for source in bucket_sources:
                lines.append(f"- [{source.title or source.url}]({source.url}) - {source.source_type}; {source.publisher or 'unknown'}")
            lines.append("")
    else:
        lines.append("- No categorized sources yet.")

    lines.extend(["", "## Coverage Gaps", ""])
    gaps = _coverage_gaps(sources, plan)
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps)
    else:
        lines.append("- Primary buckets have at least one candidate source. Review source quality before treating this as complete.")

    if _looks_inventory_topic(plan.topic) and plan.mode != "tool_selection":
        lines.extend(["", "## External AI Seeds To Verify", ""])
        lines.append("- These seeds are search prompts, not facts. Keep dates, benchmark numbers, and star counts untrusted until primary sources are fetched.")
        for spec in CURATED_INVENTORY_SEEDS:
            lines.append(f"- `{spec.bucket}`: {spec.query}")
        lines.extend(["", "### Higher-risk external candidates", ""])
        for spec in UNVERIFIED_EXTERNAL_AI_SEEDS:
            lines.append(f"- `{spec.bucket}`: {spec.query}")

    lines.extend(["", "## Sources", ""])
    if sources:
        for index, source in enumerate(sources, start=1):
            snippet = _clip(source.snippet or "No snippet captured yet.", 220)
            queries = "; ".join(source.search_queries[:2]) if source.search_queries else "unknown"
            buckets = ", ".join(source.search_buckets[:3]) if source.search_buckets else "unknown"
            lines.append(f"{index}. [{source.title or source.url}]({source.url})")
            lines.append(f"   - Type: {source.source_type}; publisher: {source.publisher or 'unknown'}; engine: {source.search_engine}")
            lines.append(f"   - Retrieved by: {buckets}; query: {queries}")
            lines.append(f"   - Snippet: {snippet}")
    else:
        lines.append("- No live sources collected. Enable SearXNG/commercial providers or ingest manual URLs.")

    lines.extend(["", "## Evidence Snippets", ""])
    if evidence:
        for item in evidence:
            quote_type = item.source_quote_type or "unknown"
            method = item.extraction_method or "unknown"
            lines.append(f"- `{item.id}` from `{item.source_id}` (`{quote_type}`, `{method}`): {_clip(item.quote_or_paraphrase, 360)}")
    else:
        lines.append("- No evidence snippets collected yet.")

    lines.extend(["", "## Auto Fact Check", ""])
    if claims:
        for claim in claims:
            lines.append(f"- `{claim.verdict}` ({claim.confidence:.2f}): {claim.text}")
    else:
        lines.append("- Auto verification was not run for this search. Use Verify Text or enable auto fact-check.")

    if revision_suggestions:
        lines.extend(["", "## Revision Suggestions", ""])
        for item in revision_suggestions:
            evidence_refs = ", ".join(item.evidence_ids) or "none"
            lines.append(f"- `{item.action}` `{item.verdict}`: {item.claim_text}")
            lines.append(f"  - Reason: {item.failure_reason}; evidence: {evidence_refs}")
            if item.suggested_replacement:
                lines.append(f"  - Suggestion: {_clip(item.suggested_replacement, 360)}")

    lines.extend(["", "## Source Coverage", ""])
    if sources:
        for source in sources:
            count = len(evidence_by_source.get(source.id, []))
            lines.append(f"- `{source.id}`: {count} evidence snippets")
    else:
        lines.append("- No source coverage to report.")

    lines.extend(["", "## Next Search Steps", ""])
    lines.extend(f"- Search: {target}" for target in plan.source_targets)
    lines.extend(
        [
            "- Add high-value URLs manually when search recall is weak.",
            "- Run `research run` or Web full chain later when you want atomic fact verification.",
        ]
    )
    return "\n".join(lines) + "\n"


def search_report_to_json(
    plan: QueryPlan,
    sources: list[Source],
    evidence: list[Evidence],
    *,
    claims: list[Claim] | None = None,
    revision_suggestions: list[RevisionSuggestion] | None = None,
    search_queries: list[dict] | None = None,
    provider_health: list[dict] | None = None,
    canonical_groups: list[dict] | None = None,
    tool_matrix: list[dict] | None = None,
    source_quality: dict | None = None,
    duration_seconds: float = 0.0,
    warning: str = "",
) -> str:
    claims = claims or []
    revision_suggestions = revision_suggestions or []
    payload = {
        "kind": "search",
        "topic": plan.topic,
        "mode": plan.mode,
        "plan": plan.to_dict(),
        "warning": warning,
        "search_queries": search_queries or [],
        "provider_health": provider_health or [],
        "canonical_groups": canonical_groups or [],
        "tool_matrix": tool_matrix or [],
        "source_quality": source_quality or {},
        "duration_seconds": duration_seconds,
        "research_buckets": {bucket: [source.to_dict() for source in bucket_sources] for bucket, bucket_sources in _bucket_sources(sources).items()},
        "coverage_gaps": _coverage_gaps(sources, plan),
        "seed_queries": [asdict(spec) for spec in CURATED_INVENTORY_SEEDS] if _looks_inventory_topic(plan.topic) and plan.mode != "tool_selection" else [],
        "unverified_seed_queries": [asdict(spec) for spec in UNVERIFIED_EXTERNAL_AI_SEEDS] if _looks_inventory_topic(plan.topic) and plan.mode != "tool_selection" else [],
        "sources": [source.to_dict() for source in sources],
        "evidence": [item.to_dict() for item in evidence],
        "claim_table": [claim.to_dict() for claim in claims],
        "revision_suggestions": [item.to_dict() for item in revision_suggestions],
        "gaps": plan.source_targets,
        "generated_at": now_iso(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    deduped: dict[str, Source] = {}
    for source in sources:
        key = source.artifact_id or source.url.rstrip("/") or source.id
        deduped[key] = source
    return list(deduped.values())


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    deduped: dict[str, Evidence] = {}
    for item in items:
        deduped[item.id] = item
    return list(deduped.values())


def _canonicalize_sources(sources: list[Source]) -> list[Source]:
    groups: dict[str, list[Source]] = {}
    for source in sources:
        groups.setdefault(_canonical_source_key(source), []).append(source)
    merged: list[Source] = []
    for key, group in groups.items():
        ranked = sorted(enumerate(group), key=lambda item: (-_source_quality_score(item[1]), item[0]))
        primary = ranked[0][1]
        urls = _dedupe_texts([item.url for item in group if item.url])
        snippets = _dedupe_texts([item.snippet for item in group if item.snippet])
        engines = _dedupe_texts([item.search_engine for item in group if item.search_engine])
        queries = _dedupe_texts([query for item in group for query in item.search_queries])
        buckets = _dedupe_texts([bucket for item in group for bucket in item.search_buckets])
        purposes = _dedupe_texts([purpose for item in group for purpose in item.search_purposes])
        stages = _dedupe_texts([stage for item in group for stage in item.search_stages])
        primary.canonical_url = _canonical_url(primary.url) or key
        primary.duplicate_count = len(group)
        primary.merged_urls = urls
        primary.search_queries = queries
        primary.search_buckets = buckets
        primary.search_purposes = purposes
        primary.search_stages = stages
        if snippets and not primary.snippet:
            primary.snippet = snippets[0]
        elif len(snippets) > 1:
            primary.snippet = _clip(" ".join(snippets), 520)
        if len(engines) > 1:
            primary.search_engine = "+".join(engines[:4])
        merged.append(primary)
    return merged


def canonical_groups_from_sources(sources: list[Source]) -> list[dict]:
    groups = []
    for source in sources:
        urls = source.merged_urls or ([source.url] if source.url else [])
        if len(urls) <= 1 and source.duplicate_count <= 1:
            continue
        groups.append(
            {
                "canonical_url": source.canonical_url or _canonical_url(source.url) or source.url,
                "title": source.title,
                "duplicate_count": source.duplicate_count,
                "urls": urls,
                "publisher": source.publisher,
            }
        )
    return groups


def build_tool_matrix(sources: list[Source]) -> list[dict]:
    rows: list[dict] = []
    github_status = github_repo_status_for_sources(sources)
    for source in sources:
        if not _looks_like_tool_source(source):
            continue
        repo_key = _github_repo_key(source.url)
        status = github_status.get(repo_key, {}) if repo_key else {}
        row = {
            "name": _tool_name(source),
            "type": _source_bucket(source),
            "open_source": "yes" if repo_key else "unknown",
            "scenario": _tool_scenario(source),
            "evidence_url": source.url,
            "publisher": source.publisher,
            "maintenance_status": status.get("maintenance_status") or ("unknown (GitHub not checked)" if repo_key else "not applicable"),
            "pushed_at": status.get("pushed_at", ""),
            "stars": status.get("stars"),
            "license": status.get("license", ""),
            "recommendation": _tool_recommendation(source, status),
            "source_count": source.duplicate_count,
        }
        rows.append(row)
    return rows[:12]


def github_repo_status_for_sources(sources: list[Source]) -> dict[str, dict]:
    repos = _dedupe_texts([repo for source in sources if (repo := _github_repo_key(source.url))])[:6]
    if not repos:
        return {}
    if os.environ.get("RESEARCH_GITHUB_STATUS", "1").strip().lower() in {"0", "false", "off", "no"}:
        return {repo: {"maintenance_status": "unknown (disabled)"} for repo in repos}
    statuses: dict[str, dict] = {}
    for repo in repos:
        statuses[repo] = _fetch_github_repo_status(repo)
    return statuses


def _fetch_github_repo_status(repo: str) -> dict:
    req = Request(
        f"https://api.github.com/repos/{repo}",
        headers={"User-Agent": "ai-research-workflow/0.1", "Accept": "application/vnd.github+json"},
    )
    try:
        with urlopen(req, timeout=3) as response:
            if response.status != HTTPStatus.OK:
                return {"maintenance_status": f"unknown (GitHub HTTP {response.status})"}
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        return {"maintenance_status": f"unknown ({type(exc).__name__})"}
    pushed_at = str(payload.get("pushed_at") or "")
    return {
        "maintenance_status": _maintenance_status(pushed_at, bool(payload.get("archived"))),
        "pushed_at": pushed_at,
        "stars": payload.get("stargazers_count"),
        "license": (payload.get("license") or {}).get("spdx_id") or "",
        "archived": bool(payload.get("archived")),
    }


def _maintenance_status(pushed_at: str, archived: bool) -> str:
    if archived:
        return "archived"
    if not pushed_at:
        return "unknown"
    try:
        pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    age_days = (datetime.now(timezone.utc) - pushed).days
    if age_days <= 180:
        return "active"
    if age_days <= 730:
        return "quiet"
    return "stale"


def _looks_like_tool_source(source: Source) -> bool:
    text = f"{source.title} {source.url} {source.snippet}".lower()
    if "github.com/" in text:
        return _source_bucket(source) not in {"Secondary commentary", "Official or institutional references"}
    return any(
        token in text
        for token in [
            "searxng",
            "firecrawl",
            "jina",
            "tavily",
            "exa",
            "factscore",
            "factool",
            "rarr",
            "storm",
            "gpt-researcher",
            "deerflow",
            "vikunja",
            "openproject",
            "open project",
            "plane.so",
            "nocodb",
            "baserow",
            "taiga",
            "linear",
            "github projects",
        ]
    )


def _tool_name(source: Source) -> str:
    repo = _github_repo_key(source.url)
    if repo:
        return repo
    title = str(source.title or "").strip()
    if title and title != source.publisher:
        return title[:80]
    return source.publisher or source.url


def _tool_scenario(source: Source) -> str:
    bucket = _source_bucket(source)
    scenarios = {
        "Open-source projects": "deep research / report generation",
        "Fact-checking methods": "atomic claim extraction and verification",
        "Search and extraction infrastructure": "retrieval, crawling, and page extraction",
        "Skill best practices": "agent search workflow design",
        "Papers and benchmarks": "method reference / evaluation",
        "Candidate tools": "inbox/task system candidate",
        "API and automation docs": "agent integration and automation evidence",
    }
    return scenarios.get(bucket, "supporting reference")


def _tool_recommendation(source: Source, status: dict) -> str:
    bucket = _source_bucket(source)
    maintenance = str(status.get("maintenance_status") or "")
    if maintenance == "archived":
        return "reference only"
    if bucket in {"Open-source projects", "Search and extraction infrastructure", "Candidate tools", "API and automation docs"} and (maintenance in {"active", "quiet", ""} or maintenance.startswith("unknown")):
        return "candidate to integrate"
    if bucket == "Fact-checking methods":
        return "use as method/reference"
    if bucket == "Papers and benchmarks":
        return "use for evaluation criteria"
    return "review manually"


def _github_repo_key(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.netloc.lower().removeprefix("www.") != "github.com":
        return ""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def provider_health_from_queries(search_queries: list[dict]) -> list[dict]:
    by_provider: dict[str, dict] = {}
    for query in search_queries:
        for status in query.get("provider_statuses") or []:
            provider = str(status.get("provider") or "unknown")
            record = by_provider.setdefault(
                provider,
                {
                    "provider": provider,
                    "overall": "empty",
                    "ok_count": 0,
                    "empty_count": 0,
                    "limited_count": 0,
                    "auth_error_count": 0,
                    "network_error_count": 0,
                    "not_configured_count": 0,
                    "cooldown_count": 0,
                    "error_count": 0,
                    "source_count": 0,
                    "total_seconds": 0.0,
                    "max_seconds": 0.0,
                    "warnings": [],
                    "recent_status": "unknown",
                    "rank_score": 0.0,
                    "rank_reason": "",
                },
            )
            state = str(status.get("status") or "unknown")
            count_key = f"{state}_count"
            record[count_key] = int(record.get(count_key, 0)) + 1
            record["source_count"] = int(record.get("source_count", 0)) + int(status.get("source_count") or 0)
            elapsed = float(status.get("elapsed_seconds") or 0.0)
            record["total_seconds"] = round(float(record.get("total_seconds", 0.0)) + elapsed, 3)
            record["max_seconds"] = max(float(record.get("max_seconds", 0.0)), elapsed)
            if status.get("warning"):
                record["warnings"].append(str(status.get("warning")))
            if status.get("recent_status"):
                record["recent_status"] = str(status.get("recent_status"))
            if status.get("rank_reason"):
                record["rank_reason"] = str(status.get("rank_reason"))
            if status.get("rank_score") is not None:
                record["rank_score"] = float(status.get("rank_score") or 0.0)
    for record in by_provider.values():
        record.update(_provider_cost_notes(str(record["provider"])))
        call_count = sum(
            int(record.get(key, 0))
            for key in [
                "ok_count",
                "empty_count",
                "limited_count",
                "auth_error_count",
                "network_error_count",
                "not_configured_count",
                "cooldown_count",
                "error_count",
            ]
        )
        record["avg_seconds"] = round(float(record.get("total_seconds", 0.0)) / call_count, 3) if call_count else 0.0
        record["max_seconds"] = round(float(record.get("max_seconds", 0.0)), 3)
        record["rank_score"] = round(float(record.get("rank_score", 0.0)), 3)
        if record["ok_count"]:
            record["overall"] = "ok" if not (record["limited_count"] or record["auth_error_count"] or record["network_error_count"] or record["error_count"]) else "partial"
        elif record["cooldown_count"]:
            record["overall"] = "cooldown"
        elif record["limited_count"]:
            record["overall"] = "limited"
        elif record["auth_error_count"]:
            record["overall"] = "auth_error"
        elif record["network_error_count"]:
            record["overall"] = "network_error"
        elif record["not_configured_count"]:
            record["overall"] = "not_configured"
        elif record["error_count"]:
            record["overall"] = "error"
        record["warnings"] = _dedupe_texts(record["warnings"])[:3]
    order = ["searxng", "metaso", "tavily", "serpapi", "brave", "you", "exa", "jina", "perplexity", "kagi"]
    return sorted(by_provider.values(), key=lambda item: order.index(item["provider"]) if item["provider"] in order else 99)


def _provider_cost_notes(provider: str) -> dict:
    notes = {
        "searxng": ("local/free", "native local SearXNG; no API quota, upstream engines may throttle"),
        "metaso": ("free-tier/API", "watch metaso dashboard for quota and rate limits"),
        "tavily": ("free-tier/API", "free credits may return quota/rate errors after monthly use"),
        "serpapi": ("free-tier/API", "free quota is small; expect 429/quota errors near limit"),
        "brave": ("free-credits/API", "billing profile can be capped to free credits only"),
        "you": ("free-tier/API", "watch You.com platform usage for monthly limits"),
        "exa": ("optional/API", "reserved provider; enable when API key and quota are available"),
        "jina": ("optional/API", "reserved provider; Reader/Search may have separate quota"),
        "perplexity": ("paid/API", "paid optional provider; disabled unless key is set"),
        "kagi": ("paid/API", "paid optional provider; disabled unless key is set"),
    }
    cost_tier, limit_note = notes.get(provider, ("unknown", "check provider console for quota"))
    return {"cost_tier": cost_tier, "limit_note": limit_note}


def _auto_verify_claims(topic: str, evidence: list[Evidence]) -> list[Claim]:
    text = synthesize_claim_text(topic, evidence)
    if not text:
        return []
    return verify_claims(extract_atomic_claims(text), evidence)


def _canonical_source_key(source: Source) -> str:
    if source.artifact_id:
        return source.artifact_id.lower()
    url = _canonical_url(source.url)
    if "github.com/" in url:
        parts = url.split("github.com/", 1)[1].split("/")
        if len(parts) >= 2:
            return f"github.com/{parts[0].lower()}/{parts[1].lower()}"
    if "arxiv.org/abs/" in url:
        return url.split("arxiv.org/abs/", 1)[1].split("?")[0].split("#")[0].lower()
    if "arxiv.org/pdf/" in url:
        return url.split("arxiv.org/pdf/", 1)[1].removesuffix(".pdf").split("?")[0].lower()
    return url or source.id


def _canonical_url(url: str) -> str:
    cleaned = str(url or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.split("#", 1)[0].rstrip("/")
    cleaned = re.sub(r"[?&](utm_[^=&]+|fbclid|gclid)=[^&]+", "", cleaned)
    cleaned = cleaned.replace("?&", "?").rstrip("?&")
    return cleaned


def _dedupe_texts(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = " ".join(str(item or "").split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _select_sources(sources: list[Source], *, max_results: int) -> list[Source]:
    ranked = sorted(enumerate(sources), key=lambda item: (-_source_quality_score(item[1]), item[0]))
    selected: list[Source] = []
    selected_urls: set[str] = set()
    publisher_counts: dict[str, int] = {}
    for _, source in ranked:
        publisher = source.publisher or ""
        if publisher and publisher_counts.get(publisher, 0) >= 2:
            continue
        selected.append(source)
        selected_urls.add(source.url.rstrip("/"))
        publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1
        if len(selected) >= max_results:
            annotate_source_quality(selected)
            return selected
    for _, source in ranked:
        key = source.url.rstrip("/")
        if key in selected_urls:
            continue
        selected.append(source)
        selected_urls.add(key)
        if len(selected) >= max_results:
            annotate_source_quality(selected)
            return selected
    selected = selected[:max_results]
    annotate_source_quality(selected)
    return selected


def _filter_sources_for_plan(plan: QueryPlan, sources: list[Source]) -> list[Source]:
    if plan.mode != "tool_selection" or not sources:
        return sources
    relevant = [source for source in sources if _tool_selection_relevance(source) > 0]
    return relevant or sources


def _tool_selection_relevance(source: Source) -> int:
    text = f"{source.title} {source.url} {source.snippet}".lower()
    score = 0
    has_named_tool = any(token in text for token in _task_tool_tokens())
    has_generic_tool = any(token in text for token in _generic_task_tool_tokens())
    if not (has_named_tool or has_generic_tool):
        return 0
    if has_named_tool:
        score += 2
    if has_generic_tool:
        score += 1
    if any(token in text for token in _automation_doc_tokens()):
        score += 1
    if (has_named_tool or has_generic_tool) and str(source.source_type) in {"official", "project", "institutional"}:
        score += 1
    if source.publisher.lower() == "github.com" and not any(token in text for token in _task_tool_tokens()):
        score -= 2
    return score


def annotate_source_quality(sources: list[Source]) -> None:
    for source in sources:
        source.quality_score = _source_quality_score(source)
        source.quality_reasons = _source_quality_reasons(source)


def source_quality_summary(sources: list[Source], evidence: list[Evidence]) -> dict:
    source_ids_with_evidence = {item.source_id for item in evidence if item.source_id}
    scores = [source.quality_score or _source_quality_score(source) for source in sources]
    primary_types = {"official", "academic", "project", "institutional"}
    primary_count = sum(1 for source in sources if str(source.source_type) in primary_types)
    low_quality = [source for source in sources if (source.quality_score or _source_quality_score(source)) < 25]
    missing_evidence = [source for source in sources if source.id not in source_ids_with_evidence]
    warnings: list[str] = []
    if sources and primary_count < max(1, len(sources) // 2):
        warnings.append("Primary/reference sources are under half of selected sources.")
    if missing_evidence:
        warnings.append(f"{len(missing_evidence)} selected source(s) have no evidence snippet.")
    if low_quality:
        warnings.append(f"{len(low_quality)} selected source(s) have low quality score.")
    average = round(sum(scores) / len(scores), 1) if scores else 0
    if not sources:
        overall = "empty"
    elif warnings:
        overall = "needs_review"
    elif average >= 55:
        overall = "strong"
    else:
        overall = "usable"
    return {
        "overall": overall,
        "source_count": len(sources),
        "average_score": average,
        "primary_source_count": primary_count,
        "sources_with_evidence": len(source_ids_with_evidence),
        "low_quality_count": len(low_quality),
        "warnings": warnings,
        "source_scores": [
            {
                "id": source.id,
                "title": source.title,
                "url": source.url,
                "score": source.quality_score or _source_quality_score(source),
                "reasons": source.quality_reasons or _source_quality_reasons(source),
            }
            for source in sources
        ],
    }


def _source_quality_score(source: Source) -> int:
    score_by_type = {
        "official": 50,
        "academic": 48,
        "project": 45,
        "institutional": 35,
        "community": 18,
        "unknown": 10,
    }
    score = score_by_type.get(str(source.source_type), 10)
    text = f"{source.title} {source.url} {source.snippet}".lower()
    if any(token in text for token in ["github.com", "arxiv", "paper", "benchmark", "documentation", "docs"]):
        score += 8
    if any(token in text for token in ["factscore", "rarr", "factool", "safe", "storm", "gpt-researcher", "deerflow", "searxng", "firecrawl"]):
        score += 6
    if any(token in text for token in _task_tool_tokens()):
        score += 6
    if any(token in text for token in _canonical_source_tokens()):
        score += 18
    if any(token in text for token in ["quickstart", "mirror", "awesome-", "tutorial", "入门", "实测对比"]):
        score -= 6
    if any(token in source.publisher.lower() for token in ["csdn.net", "zhihu.com", "juejin.cn", "cnblogs.com"]):
        score -= 8
    return score


def _source_quality_reasons(source: Source) -> list[str]:
    reasons: list[str] = []
    text = f"{source.title} {source.url} {source.snippet}".lower()
    source_type = str(source.source_type)
    if source_type in {"official", "academic", "project", "institutional"}:
        reasons.append(f"primary-ish source type: {source_type}")
    else:
        reasons.append(f"secondary/unknown source type: {source_type}")
    if any(token in text for token in _canonical_source_tokens()):
        reasons.append("canonical source match")
    if "github.com" in text:
        reasons.append("project repository")
    if "arxiv" in text or "aclanthology.org" in text:
        reasons.append("paper or academic index")
    if source.duplicate_count > 1:
        reasons.append(f"merged from {source.duplicate_count} provider hits")
    if source.snippet:
        reasons.append("has snippet/evidence seed")
    if any(token in text for token in ["quickstart", "mirror", "awesome-", "tutorial", "入门", "实测对比"]):
        reasons.append("penalty: derivative/tutorial-like page")
    if any(token in source.publisher.lower() for token in ["csdn.net", "zhihu.com", "juejin.cn", "cnblogs.com"]):
        reasons.append("penalty: commentary/community domain")
    return reasons


def _canonical_source_tokens() -> tuple[str, ...]:
    return (
        "github.com/bytedance/deer-flow",
        "github.com/langchain-ai/open_deep_research",
        "github.com/assafelovic/gpt-researcher",
        "github.com/stanford-oval/storm",
        "github.com/shmsw25/factscore",
        "github.com/gair-nlp/factool",
        "github.com/libr-ai/openfactverification",
        "aclanthology.org",
        "arxiv.org/abs/",
        "deepmind.google/research/publications",
        "docs.searxng.org",
        "jina.ai",
        "docs.firecrawl.dev",
        "docs.tavily.com",
        "docs.exa.ai",
        "vikunja.io",
        "try.vikunja.io/api",
        "openproject.org/docs",
        "developers.plane.so",
        "nocodb.com/docs",
        "baserow.io",
        "docs.baserow.io",
        "docs.taiga.io",
        "developers.linear.app",
        "docs.github.com",
    )


def _looks_inventory_topic(topic: str) -> bool:
    return planner_looks_inventory_topic(topic)


def _inventory_query_specs(topic: str) -> list[SearchQuerySpec]:
    return [
        SearchQuerySpec("deep research open source github", "open-source projects", "clean English project discovery"),
        SearchQuerySpec("Open Deep Research GPT Researcher STORM DeerFlow GitHub", "open-source projects", "known deep research seeds"),
        SearchQuerySpec("LLM fact checking open source FActScore RARR FacTool SAFE", "fact-checking methods", "known fact-check seeds"),
        SearchQuerySpec("FActScore OpenFActScore RARR FacTool SAFE paper", "papers and benchmarks", "method and paper coverage"),
        SearchQuerySpec("AI agent skills best practices Index Load Runtime", "skill best practices", "skill design practices"),
        SearchQuerySpec("SearXNG Firecrawl Jina Reader search extraction open source", "search and extraction infrastructure", "retrieval infrastructure"),
        SearchQuerySpec("深度研究 智能体 开源 项目 对比", "open-source projects", "Chinese project overview"),
        SearchQuerySpec("事实核查 原子事实 LLM 开源 项目", "fact-checking methods", "Chinese fact-check coverage"),
        SearchQuerySpec(f"{topic} site:github.com", "open-source projects", "GitHub targeted search"),
        SearchQuerySpec(f"{topic} 论文 方法 benchmark", "papers and benchmarks", "Chinese academic coverage"),
    ]


def _tool_selection_query_specs(topic: str) -> list[SearchQuerySpec]:
    return [
        SearchQuerySpec(f"{topic} REST API webhook custom fields dependencies status priority", "api and automation docs", "feature requirements"),
        SearchQuerySpec("Vikunja API webhooks task relations priority kanban", "api and automation docs", "Vikunja integration evidence"),
        SearchQuerySpec("OpenProject API webhooks custom fields work package relations dependencies", "api and automation docs", "OpenProject integration evidence"),
        SearchQuerySpec("NocoDB API webhooks custom fields table kanban", "api and automation docs", "NocoDB integration evidence"),
        SearchQuerySpec("Plane API webhooks issue properties states priority", "api and automation docs", "Plane integration evidence"),
        SearchQuerySpec("Baserow API webhooks kanban custom fields", "api and automation docs", "Baserow integration evidence"),
        SearchQuerySpec("Taiga API webhooks custom fields kanban", "api and automation docs", "Taiga integration evidence"),
        SearchQuerySpec("Linear API webhooks issue relations priority state", "api and automation docs", "Linear SaaS integration evidence"),
        SearchQuerySpec("open source task management API webhook custom fields dependencies kanban table", "candidate tools", "open-source candidate discovery"),
    ]


def _curated_seed_sources() -> list[Source]:
    sources: list[Source] = []
    for url, snippet in CURATED_INVENTORY_URLS:
        source = source_from_url(url)
        source.search_engine = "curated_seed"
        source.snippet = snippet
        sources.append(source)
    return sources


def _bucket_sources(sources: list[Source]) -> dict[str, list[Source]]:
    bucketed: dict[str, list[Source]] = {}
    for source in sources:
        bucket = _source_bucket(source)
        bucketed.setdefault(bucket, []).append(source)
    order = [
        "Candidate tools",
        "API and automation docs",
        "Open-source projects",
        "Fact-checking methods",
        "Search and extraction infrastructure",
        "Skill best practices",
        "Papers and benchmarks",
        "Official or institutional references",
        "Secondary commentary",
    ]
    return {bucket: bucketed[bucket] for bucket in order if bucket in bucketed}


def _source_bucket(source: Source) -> str:
    text = f"{source.title} {source.url} {source.snippet}".lower()
    if any(token in text for token in _task_tool_tokens()) and any(token in text for token in _automation_doc_tokens()):
        return "API and automation docs"
    if any(token in text for token in _task_tool_tokens()):
        return "Candidate tools"
    fact_tokens = ["factscore", "openfactscore", "openfactverification", "loki", "rarr", "factool", "holmes", "flashcheck", "fact check", "fact-check"]
    if any(token in text for token in fact_tokens) or re.search(r"(^|[\s/_-])safe($|[\s/_-])", text):
        return "Fact-checking methods"
    if any(token in text for token in ["gpt-researcher", "openresearcher", "opendeepresearcher", "open_deep_research", "open deep research", "storm", "deerflow", "deep research", "open deep search", "owl"]) and "github.com" in text:
        return "Open-source projects"
    if any(token in text for token in ["searxng", "firecrawl", "jina reader", "tavily", "exa.ai", "browser-use", "anysearch"]):
        return "Search and extraction infrastructure"
    if any(token in text for token in ["skill", "skills/", "index/load/runtime", "perplexity"]):
        return "Skill best practices"
    if str(source.source_type) == "academic" or any(token in text for token in ["arxiv", "paper", "benchmark", "dataset", "longfact", "deeptrace", "browsecomp-plus"]):
        return "Papers and benchmarks"
    if str(source.source_type) in {"official", "institutional", "project"}:
        return "Official or institutional references"
    return "Secondary commentary"


def _task_tool_tokens() -> tuple[str, ...]:
    return ("vikunja", "openproject", "open project", "plane.so", "nocodb", "baserow", "taiga", "linear", "github projects")


def _generic_task_tool_tokens() -> tuple[str, ...]:
    return ("task management", "project management", "issue tracking", "work package", "kanban board", "agent inbox")


def _automation_doc_tokens() -> tuple[str, ...]:
    return ("api", "webhook", "webhooks", "custom fields", "custom properties", "task relations", "work package", "dependencies", "kanban", "table")


def _coverage_gaps(sources: list[Source], plan: QueryPlan) -> list[str]:
    if not sources:
        targets = plan.source_targets or ["relevant primary sources"]
        return [f"Add sources for: {target}" for target in targets]
    if plan.mode == "tool_selection":
        buckets = set(_bucket_sources(sources))
        expected = {
            "Candidate tools": "Add direct candidate tool pages or repositories such as Vikunja, OpenProject, Plane, NocoDB, Baserow, Taiga, Linear, or GitHub Projects.",
            "API and automation docs": "Add API/Webhook/custom-field/dependency documentation for each shortlisted tool.",
            "Official or institutional references": "Prefer official product/docs pages over blogs and listicles.",
        }
        return [message for bucket, message in expected.items() if bucket not in buckets]
    if not _looks_inventory_topic(plan.topic):
        return []
    buckets = set(_bucket_sources(sources))
    expected = {
        "Open-source projects": "Add direct GitHub repositories for deep research/search agents.",
        "Fact-checking methods": "Add FActScore/RARR/FacTool/SAFE/OpenFactCheck primary sources.",
        "Search and extraction infrastructure": "Add retrieval/extraction tools such as SearXNG, Firecrawl, Jina Reader, Tavily, or Exa.",
        "Skill best practices": "Add primary Skill design references, not only commentary.",
        "Papers and benchmarks": "Add papers, benchmarks, or official method pages such as LongFact, DeepTRACE, BrowseComp-Plus, or FlashCheck.",
    }
    return [message for bucket, message in expected.items() if bucket not in buckets]


def _per_query_limit(max_results: int) -> int:
    return max(3, min(max_results, max(1, (max_results + 1) // 2)))


def _clip(text: str, max_length: int) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _escape_md_table(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _safe_filename(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "search"
