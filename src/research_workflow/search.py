from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .config import load_config
from .models import Source, SourceType, now_iso
from .store import stable_id


class SearchError(RuntimeError):
    pass


@dataclass(slots=True)
class ProviderSearchResult:
    sources: list[Source]
    provider_statuses: list[dict]
    warnings: list[str]


DEFAULT_SEARXNG_URLS = [
    "http://127.0.0.1:8928",
    "http://localhost:8928",
]
PUBLIC_SEARXNG_URLS_ENV = "RESEARCH_PUBLIC_SEARXNG_URLS"
PUBLIC_SEARXNG_DISCOVERY_ENV = "RESEARCH_SEARXNG_PUBLIC_DISCOVERY"
PUBLIC_SEARXNG_INSTANCES_URL_ENV = "RESEARCH_SEARXNG_PUBLIC_INSTANCES_URL"
PUBLIC_SEARXNG_LIMIT_ENV = "RESEARCH_SEARXNG_PUBLIC_LIMIT"
PUBLIC_SEARXNG_CACHE_SECONDS_ENV = "RESEARCH_SEARXNG_PUBLIC_CACHE_SECONDS"
DEFAULT_PUBLIC_SEARXNG_INSTANCES_URL = "https://searx.space/data/instances.json"
PROVIDER_TIMEOUT_SECONDS = int(os.environ.get("RESEARCH_PROVIDER_TIMEOUT", "5"))
SEARXNG_TIMEOUT_SECONDS = float(os.environ.get("RESEARCH_SEARXNG_TIMEOUT", "10"))
PROVIDER_STATS_PATH_ENV = "RESEARCH_PROVIDER_STATS_PATH"
PROVIDER_ADAPTIVE_ENV = "RESEARCH_PROVIDER_ADAPTIVE_RANKING"
PROVIDER_COOLDOWN_ENV = "RESEARCH_PROVIDER_COOLDOWN"
PROVIDER_LIMITED_COOLDOWN_SECONDS = int(os.environ.get("RESEARCH_PROVIDER_LIMITED_COOLDOWN_SECONDS", str(6 * 60 * 60)))
PROVIDER_NETWORK_COOLDOWN_SECONDS = int(os.environ.get("RESEARCH_PROVIDER_NETWORK_COOLDOWN_SECONDS", str(15 * 60)))
PROVIDER_COOLDOWN_THRESHOLD = int(os.environ.get("RESEARCH_PROVIDER_COOLDOWN_THRESHOLD", "3"))


def multi_source_search(
    query: str,
    mode: str = "daily",
    max_results: int = 10,
    *,
    no_network: bool = False,
    searxng_url: str = "",
    providers: list[str] | None = None,
    provider_stats_path: str = "",
    route_kind: str = "",
) -> list[Source]:
    return multi_source_search_with_status(
        query,
        mode=mode,
        max_results=max_results,
        no_network=no_network,
        searxng_url=searxng_url,
        providers=providers,
        provider_stats_path=provider_stats_path,
        route_kind=route_kind,
    ).sources


def multi_source_search_with_status(
    query: str,
    mode: str = "daily",
    max_results: int = 10,
    *,
    no_network: bool = False,
    searxng_url: str = "",
    providers: list[str] | None = None,
    provider_stats_path: str = "",
    route_kind: str = "",
) -> ProviderSearchResult:
    if no_network:
        return ProviderSearchResult([], [], [])

    config = load_config()
    enabled = normalize_providers(providers if providers is not None else config.search_providers)
    if not enabled:
        enabled = ["searxng"]
    resolved_candidates = resolve_searxng_urls(searxng_url) if "searxng" in enabled else []
    resolved_searxng = resolved_candidates[0] if resolved_candidates else ""
    available = available_providers(resolved_searxng)
    stats_path = _provider_stats_path(provider_stats_path)
    provider_stats = load_provider_stats(stats_path)
    active = [provider for provider in enabled if provider not in available or bool(available.get(provider, {}).get("configured"))]
    inactive = [provider for provider in enabled if provider in available and not bool(available.get(provider, {}).get("configured"))]
    route_stats = {provider: _route_stats_record(provider_stats.get(provider, {}), route_kind) for provider in active}
    rank_notes = _provider_rank_notes(active, route_stats)
    cooldown_statuses: list[dict] = []
    active = _filter_cooldown_providers(active, provider_stats, rank_notes, cooldown_statuses)
    for status in cooldown_statuses:
        status["route_kind"] = route_kind
    active = _rank_providers(active, route_stats)
    per_provider_limit = max(1, min(max_results, (max_results + max(len(active), 1) - 1) // max(len(active), 1)))

    sources: list[Source] = []
    errors: list[str] = []
    provider_statuses: list[dict] = []
    for provider in inactive:
        detail = str(available.get(provider, {}).get("detail") or f"{provider} is not configured")
        provider_statuses.append({"provider": provider, "route_kind": route_kind, "status": "not_configured", "source_count": 0, "elapsed_seconds": 0.0, "warning": detail, **_rank_note(provider, rank_notes)})
    provider_statuses.extend(cooldown_statuses)
    _collect_provider_results(
        query=query,
        providers=active,
        max_results=per_provider_limit,
        searxng_url=searxng_url,
        sources=sources,
        provider_statuses=provider_statuses,
        errors=errors,
        provider_rank_notes=rank_notes,
        route_kind=route_kind,
    )
    if mode == "academic":
        academic_query = f"{query} paper OR benchmark OR dataset"
        academic_limit = max(1, per_provider_limit)
        for provider in inactive:
            detail = str(available.get(provider, {}).get("detail") or f"{provider} is not configured")
            provider_statuses.append({"provider": provider, "route_kind": route_kind, "status": "not_configured", "source_count": 0, "elapsed_seconds": 0.0, "query_variant": "academic", "warning": detail, **_rank_note(provider, rank_notes)})
        provider_statuses.extend([{**status, "query_variant": "academic"} for status in cooldown_statuses])
        _collect_provider_results(
            query=academic_query,
            providers=active,
            max_results=academic_limit,
            searxng_url=searxng_url,
            sources=sources,
            provider_statuses=provider_statuses,
            errors=errors,
            query_variant="academic",
            provider_rank_notes=rank_notes,
            route_kind=route_kind,
        )
    deduped = _dedupe_sources(sources)[:max_results]
    update_provider_stats(stats_path, provider_statuses)
    if not deduped and errors:
        raise SearchError("; ".join(errors))
    return ProviderSearchResult(deduped, provider_statuses, errors)


def _collect_provider_results(
    *,
    query: str,
    providers: list[str],
    max_results: int,
    searxng_url: str,
    sources: list[Source],
    provider_statuses: list[dict],
    errors: list[str],
    query_variant: str = "",
    provider_rank_notes: dict[str, dict] | None = None,
    route_kind: str = "",
) -> None:
    if not providers:
        return
    max_workers = max(1, min(len(providers), int(os.environ.get("RESEARCH_PROVIDER_WORKERS", "4"))))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            provider: executor.submit(_timed_provider_search, provider, query, max_results=max_results, searxng_url=searxng_url)
            for provider in providers
        }
        for provider in providers:
            status = {"provider": provider, "route_kind": route_kind, "source_count": 0}
            status.update(_rank_note(provider, provider_rank_notes or {}))
            if query_variant:
                status["query_variant"] = query_variant
            result = futures[provider].result()
            status["elapsed_seconds"] = result["elapsed_seconds"]
            if result["error"]:
                warning = str(result["error"])
                errors.append(warning)
                status.update({"status": _provider_error_status(warning), "warning": warning})
            else:
                provider_sources = result["sources"]
                sources.extend(provider_sources)
                status.update({"status": "ok" if provider_sources else "empty", "source_count": len(provider_sources)})
            provider_statuses.append(status)


def _timed_provider_search(provider: str, query: str, *, max_results: int, searxng_url: str) -> dict:
    started_at = time.perf_counter()
    try:
        sources = search_provider(provider, query, max_results=max_results, searxng_url=searxng_url)
    except SearchError as exc:
        return {"sources": [], "error": exc, "elapsed_seconds": round(time.perf_counter() - started_at, 3)}
    except Exception as exc:  # noqa: BLE001
        error = SearchError(f"{provider} provider crashed: {exc}")
        return {"sources": [], "error": error, "elapsed_seconds": round(time.perf_counter() - started_at, 3)}
    return {"sources": sources, "error": None, "elapsed_seconds": round(time.perf_counter() - started_at, 3)}


def _provider_stats_path(path: str = "") -> Path | None:
    configured = path or os.environ.get(PROVIDER_STATS_PATH_ENV, "")
    return Path(configured) if configured else None


def load_provider_stats(path: Path | str | None) -> dict[str, dict]:
    if path is None:
        return {}
    stats_path = Path(path)
    if not stats_path.exists():
        return {}
    try:
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(value, dict)}


def update_provider_stats(path: Path | str | None, provider_statuses: list[dict]) -> None:
    if path is None:
        return
    stats_path = Path(path)
    stats = load_provider_stats(stats_path)
    timestamp = now_iso()
    for status in provider_statuses:
        provider = str(status.get("provider") or "unknown")
        record = stats.setdefault(
            provider,
            {
                "provider": provider,
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
                "consecutive_limited_count": 0,
                "consecutive_network_error_count": 0,
            },
        )
        state = str(status.get("status") or "unknown")
        count_key = f"{state}_count"
        record[count_key] = int(record.get(count_key, 0)) + 1
        record["source_count"] = int(record.get("source_count", 0)) + int(status.get("source_count") or 0)
        elapsed = float(status.get("elapsed_seconds") or 0.0)
        record["total_seconds"] = round(float(record.get("total_seconds", 0.0)) + elapsed, 3)
        record["max_seconds"] = round(max(float(record.get("max_seconds", 0.0)), elapsed), 3)
        record["last_status"] = state
        record["last_seen_at"] = timestamp
        if state == "limited":
            record["consecutive_limited_count"] = int(record.get("consecutive_limited_count", 0)) + 1
        elif state not in {"not_configured", "cooldown"}:
            record["consecutive_limited_count"] = 0
        if state == "network_error":
            record["consecutive_network_error_count"] = int(record.get("consecutive_network_error_count", 0)) + 1
        elif state not in {"not_configured", "cooldown"}:
            record["consecutive_network_error_count"] = 0
        if state in {"ok", "empty"}:
            record["last_success_at"] = timestamp
        if state in {"limited", "auth_error", "network_error", "error"}:
            record["last_error_at"] = timestamp
        if status.get("warning"):
            record["last_warning"] = str(status.get("warning"))
        route_kind = str(status.get("route_kind") or "").strip()
        if route_kind:
            route_records = record.setdefault("routes", {})
            route_record = route_records.setdefault(
                route_kind,
                {
                    "provider": provider,
                    "route_kind": route_kind,
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
                },
            )
            route_record[count_key] = int(route_record.get(count_key, 0)) + 1
            route_record["source_count"] = int(route_record.get("source_count", 0)) + int(status.get("source_count") or 0)
            route_record["total_seconds"] = round(float(route_record.get("total_seconds", 0.0)) + elapsed, 3)
            route_record["max_seconds"] = round(max(float(route_record.get("max_seconds", 0.0)), elapsed), 3)
            route_record["last_status"] = state
            route_record["last_seen_at"] = timestamp
            if status.get("warning"):
                route_record["last_warning"] = str(status.get("warning"))
    try:
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        return


def _rank_providers(providers: list[str], stats: dict[str, dict]) -> list[str]:
    if os.environ.get(PROVIDER_ADAPTIVE_ENV, "1").strip().lower() in {"0", "false", "off", "no"}:
        return providers
    indexed = list(enumerate(providers))
    indexed.sort(key=lambda item: (-_provider_rank_score(stats.get(item[1], {})), item[0]))
    return [provider for _, provider in indexed]


def _route_stats_record(record: dict, route_kind: str) -> dict:
    if not route_kind:
        return record
    route_record = record.get("routes", {}).get(route_kind, {}) if isinstance(record.get("routes"), dict) else {}
    return route_record if isinstance(route_record, dict) and route_record else record


def _provider_rank_notes(providers: list[str], stats: dict[str, dict]) -> dict[str, dict]:
    return {provider: _rank_note_from_stats(provider, stats.get(provider, {})) for provider in providers}


def _rank_note(provider: str, notes: dict[str, dict]) -> dict:
    return dict(notes.get(provider, {}))


def _rank_note_from_stats(provider: str, record: dict) -> dict:
    score = _provider_rank_score(record)
    recent_status = str(record.get("last_status") or "unknown")
    cooldown = _provider_cooldown_note(record)
    if cooldown:
        reason = cooldown["rank_reason"]
    elif not record:
        reason = "no local history; using configured provider order"
    elif int(record.get("consecutive_limited_count", 0)) > 1:
        reason = f"deprioritized after {record.get('consecutive_limited_count')} consecutive limited responses"
    elif int(record.get("consecutive_network_error_count", 0)) > 1:
        reason = f"deprioritized after {record.get('consecutive_network_error_count')} consecutive network errors"
    elif recent_status in {"limited", "auth_error"}:
        reason = f"deprioritized after recent {recent_status}"
    elif recent_status in {"network_error", "error"}:
        reason = f"deprioritized after recent {recent_status}"
    elif int(record.get("ok_count", 0)) > 0:
        reason = "preferred from local successful search history"
    else:
        reason = "ranked from local provider history"
    return {"recent_status": recent_status, "rank_score": round(score, 3), "rank_reason": reason}


def _provider_rank_score(record: dict) -> float:
    if not record:
        return 0.0
    ok = int(record.get("ok_count", 0))
    empty = int(record.get("empty_count", 0))
    limited = int(record.get("limited_count", 0))
    auth = int(record.get("auth_error_count", 0))
    network = int(record.get("network_error_count", 0))
    error = int(record.get("error_count", 0))
    cooldown = int(record.get("cooldown_count", 0))
    source_count = int(record.get("source_count", 0))
    consecutive_limited = int(record.get("consecutive_limited_count", 0))
    consecutive_network = int(record.get("consecutive_network_error_count", 0))
    calls = max(1, ok + empty + limited + auth + network + error + cooldown + int(record.get("not_configured_count", 0)))
    avg_seconds = float(record.get("total_seconds", 0.0)) / calls
    score = ok * 3.0 + source_count * 0.25 - empty * 0.4 - limited * 2.0 - auth * 3.0 - network * 1.5 - error * 2.0 - consecutive_limited * 3.0 - consecutive_network * 2.0 - avg_seconds * 0.2
    last_status = str(record.get("last_status") or "")
    if last_status in {"limited", "auth_error"}:
        score -= 3.0
    elif last_status == "cooldown":
        score -= 5.0
    elif last_status in {"network_error", "error"}:
        score -= 1.5
    elif last_status == "ok":
        score += 1.0
    return score


def _filter_cooldown_providers(providers: list[str], stats: dict[str, dict], rank_notes: dict[str, dict], cooldown_statuses: list[dict]) -> list[str]:
    if os.environ.get(PROVIDER_COOLDOWN_ENV, "1").strip().lower() in {"0", "false", "off", "no"}:
        return providers
    active: list[str] = []
    for provider in providers:
        note = _provider_cooldown_note(stats.get(provider, {}))
        if not note:
            active.append(provider)
            continue
        rank_notes[provider] = {**rank_notes.get(provider, {}), **note}
        cooldown_statuses.append(
            {
                "provider": provider,
                "status": "cooldown",
                "source_count": 0,
                "elapsed_seconds": 0.0,
                "warning": note["warning"],
                **_rank_note(provider, rank_notes),
            }
        )
    return active


def _provider_cooldown_note(record: dict) -> dict:
    if not record:
        return {}
    threshold = max(1, PROVIDER_COOLDOWN_THRESHOLD)
    limited_count = int(record.get("consecutive_limited_count", 0))
    network_count = int(record.get("consecutive_network_error_count", 0))
    last_status = str(record.get("last_status") or "")
    if last_status in {"limited", "auth_error"} and limited_count >= threshold:
        return _cooldown_note(record, PROVIDER_LIMITED_COOLDOWN_SECONDS, f"{limited_count} consecutive quota/auth-limited responses")
    if last_status == "network_error" and network_count >= threshold:
        return _cooldown_note(record, PROVIDER_NETWORK_COOLDOWN_SECONDS, f"{network_count} consecutive network errors")
    return {}


def _cooldown_note(record: dict, cooldown_seconds: int, reason: str) -> dict:
    last_error = _parse_iso_datetime(str(record.get("last_error_at") or record.get("last_seen_at") or ""))
    if not last_error:
        return {}
    until = last_error + timedelta(seconds=max(1, cooldown_seconds))
    now = datetime.now(timezone.utc)
    if now >= until:
        return {}
    seconds_left = int((until - now).total_seconds())
    until_text = until.replace(microsecond=0).isoformat()
    warning = f"provider cooldown active until {until_text} after {reason}"
    return {
        "recent_status": "cooldown",
        "rank_score": -99.0,
        "rank_reason": warning,
        "cooldown_until": until_text,
        "cooldown_seconds_left": seconds_left,
        "warning": warning,
    }


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_providers(providers: list[str] | str | None) -> list[str]:
    if providers is None:
        return []
    if isinstance(providers, str):
        raw = providers.split(",")
    else:
        raw = providers
    normalized: list[str] = []
    for provider in raw:
        name = str(provider).strip().lower()
        if not name or name in normalized:
            continue
        normalized.append(name)
    return normalized


def available_providers(resolved_searxng: str | None = None) -> dict[str, dict[str, str | bool]]:
    resolved_searxng = resolve_searxng_url() if resolved_searxng is None else resolved_searxng
    return {
        "searxng": {
            "enabled_by_default": True,
            "configured": bool(resolved_searxng),
            "detail": resolved_searxng or "set RESEARCH_SEARXNG_URL or run a local SearXNG JSON endpoint",
        },
        "brave": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("BRAVE_SEARCH_API_KEY")),
            "detail": "set BRAVE_SEARCH_API_KEY and include brave in RESEARCH_SEARCH_PROVIDERS",
        },
        "exa": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("EXA_API_KEY")),
            "detail": "set EXA_API_KEY and include exa in RESEARCH_SEARCH_PROVIDERS",
        },
        "jina": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("JINA_API_KEY")),
            "detail": "set JINA_API_KEY and include jina in RESEARCH_SEARCH_PROVIDERS",
        },
        "kagi": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("KAGI_API_KEY")),
            "detail": "set KAGI_API_KEY and include kagi in RESEARCH_SEARCH_PROVIDERS",
        },
        "metaso": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("METASO_API_KEY")),
            "detail": "set METASO_API_KEY and include metaso in RESEARCH_SEARCH_PROVIDERS",
        },
        "perplexity": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("PERPLEXITY_API_KEY")),
            "detail": "set PERPLEXITY_API_KEY and include perplexity in RESEARCH_SEARCH_PROVIDERS",
        },
        "tavily": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("TAVILY_API_KEY")),
            "detail": "set TAVILY_API_KEY and include tavily in RESEARCH_SEARCH_PROVIDERS",
        },
        "serpapi": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("SERPAPI_API_KEY")),
            "detail": "set SERPAPI_API_KEY and include serpapi in RESEARCH_SEARCH_PROVIDERS",
        },
        "you": {
            "enabled_by_default": False,
            "configured": bool(os.environ.get("YOU_API_KEY")),
            "detail": "set YOU_API_KEY and include you in RESEARCH_SEARCH_PROVIDERS",
        },
        "firecrawl_developer": {
            "enabled_by_default": False,
            "configured": True,
            "credential_mode": "api_key" if bool(os.environ.get("FIRECRAWL_API_KEY")) else "keyless",
            "detail": "keyless Developer Index is available; set FIRECRAWL_API_KEY for higher limits",
        },
        "github_cli": {
            "enabled_by_default": False,
            "configured": bool(shutil.which("gh")),
            "detail": "install GitHub CLI; authentication is optional for public search but may raise limits",
        },
    }


def search_provider(provider: str, query: str, max_results: int = 10, *, searxng_url: str = "") -> list[Source]:
    name = provider.strip().lower()
    if name == "searxng":
        candidate_urls = resolve_searxng_urls(searxng_url)
        if not candidate_urls:
            return []
        errors: list[str] = []
        for candidate_url in candidate_urls:
            try:
                return search_searxng(candidate_url, query, max_results=max_results)
            except SearchError as exc:
                errors.append(f"{candidate_url}: {exc}")
        raise SearchError("SearXNG query failed for all configured endpoints: " + " | ".join(errors))
    if name == "brave":
        return search_brave(query, max_results=max_results)
    if name == "exa":
        return search_exa(query, max_results=max_results)
    if name == "jina":
        return search_jina(query, max_results=max_results)
    if name == "kagi":
        return search_kagi(query, max_results=max_results)
    if name == "metaso":
        return search_metaso(query, max_results=max_results)
    if name == "perplexity":
        return search_perplexity(query, max_results=max_results)
    if name == "tavily":
        return search_tavily(query, max_results=max_results)
    if name == "serpapi":
        return search_serpapi(query, max_results=max_results)
    if name == "you":
        return search_you(query, max_results=max_results)
    if name == "firecrawl_developer":
        return search_firecrawl_developer(query, max_results=max_results)
    if name == "github_cli":
        return search_github_cli(query, max_results=max_results)
    raise SearchError(f"Unknown search provider: {provider}")


def _provider_error_status(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ["quota", "limit", "rate", "429", "credit", "billing", "insufficient", "余额不足", "额度不足", "欠费"]):
        return "limited"
    if any(token in lowered for token in ["unauthorized", "forbidden", "invalid api", "invalid key", "api密钥无效", "密钥无效", "无效", "401", "403"]):
        return "auth_error"
    if any(token in lowered for token in ["timeout", "timed out", "ssl", "handshake", "connection"]):
        return "network_error"
    return "error"


def resolve_searxng_url(configured_url: str = "") -> str:
    urls = resolve_searxng_urls(configured_url, check=True)
    return urls[0] if urls else ""


def resolve_searxng_urls(configured_url: str = "", *, check: bool = False) -> list[str]:
    config = load_config()
    candidates: list[str] = []
    if configured_url.strip():
        candidates.extend(_split_urls(configured_url))
    configured = os.environ.get("RESEARCH_SEARXNG_URL", "").strip().rstrip("/")
    if configured:
        candidates.extend(_split_urls(configured))
    configured_many = os.environ.get("RESEARCH_SEARXNG_URLS", "").strip()
    if configured_many:
        candidates.extend(_split_urls(configured_many))
    config_url = config.searxng_url.strip().rstrip("/")
    if config_url:
        candidates.extend(_split_urls(config_url))
    candidates.extend(config.searxng_urls)
    public_urls = os.environ.get(PUBLIC_SEARXNG_URLS_ENV, "").strip()
    if public_urls:
        candidates.extend(_split_urls(public_urls))
    candidates.extend(DEFAULT_SEARXNG_URLS)
    candidates.extend(discover_public_searxng_urls())
    normalized = _dedupe_urls(candidates)
    if not check:
        return normalized
    return [candidate for candidate in normalized if check_searxng(candidate, timeout=max(5, int(SEARXNG_TIMEOUT_SECONDS)))]


def discover_public_searxng_urls(*, limit: int | None = None) -> list[str]:
    config = load_config()
    discovery_enabled = os.environ.get(PUBLIC_SEARXNG_DISCOVERY_ENV)
    if discovery_enabled is None:
        discovery_enabled = "1" if config.searxng_public_discovery else "0"
    if discovery_enabled.strip().lower() in {"0", "false", "off", "no"}:
        return []
    max_urls = limit if limit is not None else int(os.environ.get(PUBLIC_SEARXNG_LIMIT_ENV, str(config.searxng_public_limit)))
    if max_urls <= 0:
        return []
    cache_path = _public_searxng_cache_path()
    cached = _read_public_searxng_cache(cache_path)
    if cached is not None:
        return cached[:max_urls]
    urls = _fetch_public_searxng_urls(max_urls=max_urls)
    _write_public_searxng_cache(cache_path, urls)
    return urls[:max_urls]


def _public_searxng_cache_path() -> Path:
    data_dir = Path(load_config().data_dir)
    return data_dir / "searxng_public_instances.json"


def _read_public_searxng_cache(path: Path) -> list[str] | None:
    max_age = int(os.environ.get(PUBLIC_SEARXNG_CACHE_SECONDS_ENV, str(24 * 60 * 60)))
    if max_age <= 0 or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    cached_at = float(payload.get("cached_at", 0)) if isinstance(payload, dict) else 0
    if time.time() - cached_at > max_age:
        return None
    urls = payload.get("urls", []) if isinstance(payload, dict) else []
    return _dedupe_urls([str(url) for url in urls])


def _write_public_searxng_cache(path: Path, urls: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"cached_at": time.time(), "urls": urls}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        return


def _fetch_public_searxng_urls(*, max_urls: int) -> list[str]:
    config = load_config()
    instances_url = os.environ.get(PUBLIC_SEARXNG_INSTANCES_URL_ENV, config.searxng_public_instances_url or DEFAULT_PUBLIC_SEARXNG_INSTANCES_URL).strip()
    if not instances_url:
        return []
    req = Request(instances_url, headers={"User-Agent": "ai-research-workflow/0.1", "Accept": "application/json"})
    try:
        with urlopen(req, timeout=min(2, SEARXNG_TIMEOUT_SECONDS)) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    instances = payload.get("instances", {}) if isinstance(payload, dict) else {}
    if not isinstance(instances, dict):
        return []
    urls: list[str] = []
    for url, meta in instances.items():
        if not _public_searxng_instance_looks_usable(meta):
            continue
        urls.append(str(url).strip().rstrip("/"))
        if len(urls) >= max_urls:
            break
    return _dedupe_urls(urls)


def _public_searxng_instance_looks_usable(meta: object) -> bool:
    if not isinstance(meta, dict):
        return False
    http = meta.get("http", {})
    if isinstance(http, dict):
        if http.get("status_code") not in (None, 200):
            return False
        if http.get("error") not in (None, "", False):
            return False
    if meta.get("comments"):
        return False
    network_type = str(meta.get("network_type") or meta.get("network") or "").lower()
    if network_type and network_type not in {"normal", "clearnet", "internet"}:
        return False
    return True


def _split_urls(value: str) -> list[str]:
    return [item.strip().rstrip("/") for item in value.replace(";", ",").split(",") if item.strip()]


def _dedupe_urls(urls: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = str(url).strip().rstrip("/")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def check_searxng(base_url: str, *, timeout: int = 10) -> bool:
    base = base_url.rstrip("/")
    params = urlencode({"q": "SearXNG", "format": "json"})
    req = Request(f"{base}/search?{params}", headers={"User-Agent": "ai-research-workflow/0.1"})
    try:
        with urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return False
    return isinstance(payload, dict) and "results" in payload


def search_searxng(base_url: str, query: str, max_results: int = 10) -> list[Source]:
    base = base_url.rstrip("/")
    params = urlencode({"q": query, "format": "json"})
    req = Request(f"{base}/search?{params}", headers={"User-Agent": "ai-research-workflow/0.1"})
    try:
        with urlopen(req, timeout=SEARXNG_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"SearXNG query failed: {exc}") from exc

    results = payload.get("results", [])
    sources: list[Source] = []
    for result in results[:max_results]:
        url = result.get("url") or ""
        title = result.get("title") or ""
        snippet = _normalize_text(result.get("content") or result.get("snippet") or "")
        engine = result.get("engine") or "searxng"
        source = Source(
            id=stable_id(url, title, prefix="src_"),
            url=url,
            title=title,
            source_type=infer_source_type(url),
            publisher=infer_publisher(url),
            search_engine=engine,
            snippet=snippet,
        )
        sources.append(source)
    if len(sources) < max_results:
        sources.extend(_sources_from_infoboxes(payload.get("infoboxes", []), max_results=max_results - len(sources)))
    return sources


def search_brave(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return []
    params = urlencode({"q": query, "count": min(max_results, 20)})
    payload = _json_request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    rows = payload.get("web", {}).get("results", [])
    return _sources_from_rows(rows, provider="brave", max_results=max_results, snippet_keys=("description", "snippet"))


def search_exa(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("EXA_API_KEY", "").strip()
    if not api_key:
        return []
    payload = _json_request(
        "https://api.exa.ai/search",
        method="POST",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        body={
            "query": query,
            "numResults": min(max_results, 20),
            "contents": {"highlights": True},
        },
    )
    rows = payload.get("results", [])
    return _sources_from_rows(rows, provider="exa", max_results=max_results, snippet_keys=("summary", "text", "highlights"))


def search_jina(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("JINA_API_KEY", "").strip()
    if not api_key:
        return []
    payload = _json_request(
        "https://s.jina.ai/",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
        body={"q": query, "num": min(max_results, 10)},
    )
    rows = payload.get("data") or payload.get("results") or []
    return _sources_from_rows(rows, provider="jina", max_results=max_results, snippet_keys=("content", "description", "snippet"))


def search_kagi(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("KAGI_API_KEY", "").strip()
    if not api_key:
        return []
    params = urlencode({"q": query, "limit": max_results})
    payload = _json_request(
        f"https://kagi.com/api/v1/search?{params}",
        headers={"Authorization": f"Bot {api_key}", "Accept": "application/json"},
    )
    rows = payload.get("data") or payload.get("results") or []
    return _sources_from_rows(rows, provider="kagi", max_results=max_results, snippet_keys=("snippet", "description"))


def search_metaso(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("METASO_API_KEY", "").strip()
    if not api_key:
        return []
    payload = _json_request(
        "https://metaso.cn/api/v1/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", "Content-Type": "application/json"},
        body={
            "q": query,
            "scope": "webpage",
            "includeSummary": False,
            "includeRawContent": False,
            "conciseSnippet": False,
            "size": str(min(max_results, 20)),
        },
    )
    if payload.get("errCode") not in (None, 0, "0"):
        raise SearchError(f"Metaso API error: {payload.get('errMsg') or payload.get('errCode')}")
    rows = payload.get("webpages") or payload.get("results") or []
    return _sources_from_rows(rows, provider="metaso", max_results=max_results, snippet_keys=("snippet", "summary", "description"))


def search_perplexity(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return []
    payload = _json_request(
        "https://api.perplexity.ai/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body={"query": query, "max_results": min(max_results, 20), "snippet_mode": "medium"},
    )
    rows = payload.get("results", [])
    return _sources_from_rows(rows, provider="perplexity", max_results=max_results, snippet_keys=("snippet",))


def search_tavily(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    payload = _json_request(
        "https://api.tavily.com/search",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        body={
            "query": query,
            "max_results": min(max_results, 20),
            "include_answer": False,
            "include_raw_content": False,
            "search_depth": "basic",
        },
    )
    rows = payload.get("results", [])
    return _sources_from_rows(rows, provider="tavily", max_results=max_results, snippet_keys=("content", "snippet"))


def search_serpapi(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not api_key:
        return []
    params = urlencode({"engine": "google", "q": query, "api_key": api_key, "num": min(max_results, 20)})
    payload = _json_request(f"https://serpapi.com/search?{params}", headers={"Accept": "application/json"})
    rows = payload.get("organic_results", [])
    return _sources_from_rows(rows, provider="serpapi", max_results=max_results, snippet_keys=("snippet", "description"))


def search_you(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("YOU_API_KEY", "").strip()
    if not api_key:
        return []
    params = urlencode({"query": query, "count": min(max_results, 20)})
    payload = _json_request(
        f"https://ydc-index.io/v1/search?{params}",
        headers={"X-API-Key": api_key, "Accept": "application/json"},
    )
    results = payload.get("results", {})
    rows = results.get("web", []) if isinstance(results, dict) else results
    return _sources_from_rows(rows, provider="you", max_results=max_results, snippet_keys=("description", "snippets", "snippet"))


def search_firecrawl_developer(query: str, max_results: int = 10) -> list[Source]:
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = _json_request(
        "https://api.firecrawl.dev/v2/search/developer",
        method="POST",
        headers=headers,
        body={"query": sanitize_cloud_query(query), "k": min(max_results, 100)},
    )
    rows = payload.get("results") or []
    sources: list[Source] = []
    for row in rows[:max_results]:
        url = str(row.get("url") or "").strip()
        artifact_id = str(row.get("id") or "").strip()
        artifact_type = str(row.get("type") or artifact_id.partition(":")[0] or "").strip()
        passages = row.get("passages") if isinstance(row.get("passages"), list) else []
        normalized_passages: list[dict[str, str]] = []
        for item in passages:
            if isinstance(item, dict):
                text = _normalize_text(str(item.get("text") or ""))
                citation_url = str(item.get("citation_url") or item.get("citationUrl") or "").strip()
            else:
                text = _normalize_text(str(item))
                citation_url = ""
            if text:
                normalized_passages.append({"text": text, "citation_url": citation_url})
        if not url:
            continue
        sources.append(
            Source(
                id=stable_id("firecrawl_developer", artifact_id or url, prefix="src_"),
                url=url,
                title=str(row.get("title") or url),
                source_type=infer_source_type(url),
                publisher=infer_publisher(url),
                search_engine="firecrawl_developer",
                snippet=" ".join(item["text"] for item in normalized_passages)[:4000],
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                provider_metadata={
                    "repository": row.get("repository") or row.get("repo") or "",
                    "license": row.get("license") or "",
                    "passages": normalized_passages,
                    "coverage": payload.get("coverage") or {},
                    "reranked": payload.get("reranked"),
                },
            )
        )
    return sources


def search_github_cli(query: str, max_results: int = 10) -> list[Source]:
    executable = shutil.which("gh")
    if not executable:
        return []
    command = [
        executable,
        "search",
        "repos",
        query,
        "--limit",
        str(min(max_results, 100)),
        "--json",
        "fullName,url,description,updatedAt",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=PROVIDER_TIMEOUT_SECONDS, check=False)
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"GitHub CLI search failed: {exc}") from exc
    if completed.returncode != 0:
        detail = _normalize_text(completed.stderr)[:240]
        raise SearchError(f"GitHub CLI search failed: {detail or 'non-zero exit'}")
    try:
        rows = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SearchError("GitHub CLI search returned invalid JSON") from exc
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("fullName") and not row.get("title"):
            row["title"] = row["fullName"]
    return _sources_from_rows(rows if isinstance(rows, list) else [], provider="github_cli", max_results=max_results, snippet_keys=("description",))


def sanitize_cloud_query(query: str) -> str:
    text = str(query)
    text = re.sub(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*[^\s,;]+", "[REDACTED_CREDENTIAL]", text)
    text = re.sub(r"(?i)\b(?:sk|tvly|ydc|jina|mk|bsa|fc)-[A-Za-z0-9_-]{12,}\b", "[REDACTED_CREDENTIAL]", text)
    text = re.sub(r"(?i)\b[A-Z]:\\[^\r\n\t]+", "[REDACTED_LOCAL_PATH]", text)
    text = re.sub(r"(?<!\w)/(?:Users|home|mnt)/[^\s]+", "[REDACTED_LOCAL_PATH]", text)
    private_repos = [item.strip() for item in os.environ.get("RESEARCH_PRIVATE_REPOSITORIES", "").split(",") if item.strip()]
    for repo in private_repos:
        text = re.sub(re.escape(repo), "[REDACTED_PRIVATE_REPOSITORY]", text, flags=re.IGNORECASE)
    return _normalize_text(text)


def infer_publisher(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return host.removeprefix("www.")


def infer_source_type(url: str) -> SourceType:
    host = infer_publisher(url)
    lowered = url.lower()
    if not host:
        return SourceType.UNKNOWN
    if host.endswith(".gov") or "docs." in host or "/docs/" in lowered:
        return SourceType.OFFICIAL
    if any(token in host for token in ["arxiv.org", "semanticscholar.org", "pubmed", "aclweb.org", "aclanthology.org"]):
        return SourceType.ACADEMIC
    if "github.com" in host or "gitlab.com" in host:
        return SourceType.PROJECT
    if any(token in host for token in ["reddit.com", "news.ycombinator.com", "medium.com", "substack.com"]):
        return SourceType.COMMUNITY
    if any(token in host for token in ["nature.com", "science.org", "stanford.edu", "mit.edu", "openai.com", "google.com"]):
        return SourceType.INSTITUTIONAL
    return SourceType.UNKNOWN


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    seen: set[str] = set()
    deduped: list[Source] = []
    for source in sources:
        key = source.artifact_id or source.url.rstrip("/")
        if key and key not in seen:
            seen.add(key)
            deduped.append(source)
    return deduped


def _normalize_text(text: str) -> str:
    return " ".join(str(text).replace("\xa0", " ").replace("\u2060", "").split())


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> dict:
    data = None
    request_headers = {"User-Agent": "ai-research-workflow/0.1"}
    request_headers.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        detail = _http_error_detail(exc)
        raise SearchError(f"Search provider request failed for {urlparse(url).netloc}: HTTP {exc.code} {detail}".strip()) from exc
    except Exception as exc:  # noqa: BLE001
        raise SearchError(f"Search provider request failed for {urlparse(url).netloc}: {exc}") from exc


def _http_error_detail(error: HTTPError) -> str:
    try:
        raw = error.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        raw = ""
    text = _normalize_text(raw)[:240]
    if error.code == HTTPStatus.TOO_MANY_REQUESTS:
        return f"rate limit or quota exceeded {text}".strip()
    if error.code in {HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.FORBIDDEN}:
        return f"quota, billing, or authorization issue {text}".strip()
    if error.code == HTTPStatus.UNAUTHORIZED:
        return f"authentication failed {text}".strip()
    return text


def _sources_from_rows(
    rows: list[dict],
    *,
    provider: str,
    max_results: int,
    snippet_keys: tuple[str, ...],
) -> list[Source]:
    sources: list[Source] = []
    for row in rows[:max_results]:
        url = str(row.get("url") or row.get("link") or "").strip()
        title = str(row.get("title") or row.get("name") or "").strip()
        snippet = ""
        for key in snippet_keys:
            if row.get(key):
                value = row.get(key)
                if isinstance(value, list):
                    value = " ".join(str(item) for item in value)
                snippet = _normalize_text(str(value))
                break
        published_at = str(row.get("published_at") or row.get("publishedDate") or row.get("updatedAt") or row.get("page_age") or row.get("date") or "").strip()
        if not url:
            continue
        sources.append(
            Source(
                id=stable_id(provider, url, title, prefix="src_"),
                url=url,
                title=title or url,
                source_type=infer_source_type(url),
                publisher=infer_publisher(url),
                published_at=published_at,
                search_engine=provider,
                snippet=snippet,
            )
        )
    return sources


def _sources_from_infoboxes(rows: list[dict], *, max_results: int) -> list[Source]:
    sources: list[Source] = []
    for row in rows[:max_results]:
        urls = row.get("urls") if isinstance(row.get("urls"), list) else []
        first_url = urls[0] if urls and isinstance(urls[0], dict) else {}
        url = str(row.get("id") or row.get("url") or first_url.get("url") or "").strip()
        if not url:
            continue
        title = str(row.get("infobox") or row.get("title") or first_url.get("title") or url).strip()
        snippet = _normalize_text(str(row.get("content") or ""))
        engine = str(row.get("engine") or "searxng-infobox")
        sources.append(
            Source(
                id=stable_id(engine, url, title, prefix="src_"),
                url=url,
                title=title or url,
                source_type=infer_source_type(url),
                publisher=infer_publisher(url),
                search_engine=engine,
                snippet=snippet,
            )
        )
    return sources
