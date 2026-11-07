from __future__ import annotations

import json
from pathlib import Path

from .audit import audit_run
from .academic_reference_check import check_academic_references as _check_academic_references
from .bootstrap import run_bootstrap
from .config import load_config, web_base_url
from .diagnostics import run_doctor
from .evidence import extract_evidence_from_sources, source_from_url
from .evals import run_evals
from .factcheck import extract_atomic_claims as _extract_atomic_claims
from .factcheck import verify_claims as _verify_claims
from .insights import claim_citations_from_claims
from .longtracer_backend import verify_with_longtracer as _verify_with_longtracer
from .models import Claim, Evidence, Report, Source
from .obsidian_export import export_run_to_obsidian
from .pipeline import get_run as _get_run
from .pipeline import list_runs as _list_runs
from .pipeline import run_research_pipeline
from .planner import plan_query
from .prompt_registry import get_claim_verification_prompt as _get_claim_verification_prompt
from .prompt_registry import load_claim_verification_prompts as _load_claim_verification_prompts
from .query_expansion import expand_claim_queries as _expand_claim_queries
from .quality import deepen_claim_suggestions_for_run, run_quality
from .readiness import run_readiness
from .report import report_to_markdown
from .revisions import suggest_revisions
from .search import multi_source_search as _multi_source_search
from .search_pipeline import run_search_pipeline
from .routing import build_routing_plan
from .selftest import run_selftest
from .sqlite_store import sync_jsonl_to_sqlite_quietly
from .store import append_jsonl


def multi_source_search(query: str, mode: str = "daily", max_results: int = 10, providers: list[str] | None = None) -> list[dict]:
    return [source.to_dict() for source in _multi_source_search(query, mode=mode, max_results=max_results, providers=providers)]


def extract_evidence(urls: list[str]) -> list[dict]:
    sources = [source_from_url(url) for url in urls]
    return [item.to_dict() for item in extract_evidence_from_sources(sources)]


def extract_atomic_claims(text: str) -> list[dict]:
    return [claim.to_dict() for claim in _extract_atomic_claims(text)]


def verify_claims(claims: list[dict], evidence_policy: dict | None = None) -> list[dict]:
    evidence_policy = evidence_policy or {}
    evidence = [Evidence(**item) for item in evidence_policy.get("evidence", [])]
    claim_objects = [Claim(**item) for item in claims]
    return [claim.to_dict() for claim in _verify_claims(claim_objects, evidence)]


def verify_with_longtracer(response: str, sources: list[dict], evidence: list[dict] | None = None, threshold: float = 0.5) -> dict:
    source_objects = [Source(**item) for item in sources]
    evidence_objects = [Evidence(**item) for item in evidence or []]
    return _verify_with_longtracer(response=response, sources=source_objects, evidence=evidence_objects, threshold=threshold).to_dict()


def check_academic_references(text: str) -> dict:
    return _check_academic_references(text).to_dict()


def research_check_references(text: str = "", run_id: str = "", data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    actual_data_dir = Path(data_dir or config.data_dir)
    if run_id and not text:
        record = _get_run(actual_data_dir, run_id)
        if not record:
            raise ValueError(f"Run not found: {run_id}")
        text = str(record.get("report_markdown") or record.get("report_json") or "")
    if not text:
        raise ValueError("text or run_id is required")
    return _check_academic_references(text).to_dict()


def expand_claim_search_queries(claim: dict, max_queries: int = 5, include_academic: bool = False) -> dict:
    return _expand_claim_queries(Claim(**claim), max_queries=max_queries, include_academic=include_academic).to_dict()


def list_claim_verification_prompts() -> dict:
    return {"prompts": {name: prompt.to_dict() for name, prompt in _load_claim_verification_prompts().items()}}


def get_claim_verification_prompt(name: str) -> dict:
    return {"name": name, "text": _get_claim_verification_prompt(name)}


def write_research_report(topic: str, findings: list[str], claims: list[dict]) -> str:
    plan = plan_query(topic)
    claim_objects = [Claim(**item) for item in claims]
    claim_citations = claim_citations_from_claims(claim_objects, [], [])
    report = Report(
        topic=topic,
        summary="; ".join(findings[:3]) if findings else "No supported findings yet.",
        findings=findings,
        claim_table=claim_objects,
        gaps=plan.source_targets,
        sources=[],
        claim_citations=claim_citations,
    )
    return report_to_markdown(report)


def save_report(path: str, markdown: str) -> None:
    Path(path).write_text(markdown, encoding="utf-8")


def run_research(
    topic: str,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    manual_evidence: list[dict] | None = None,
    text_to_verify: str = "",
    fetch_urls: bool = False,
    data_dir: str = "",
    config_path: str = "",
    search_providers: list[str] | None = None,
    external_payloads: list[dict] | None = None,
    claim_deepen_rounds: int = 0,
    profile: str = "",
    routing_strategy: str = "",
) -> dict:
    config = load_config(config_path or None)
    return run_research_pipeline(
        topic=topic,
        data_dir=Path(data_dir or config.data_dir),
        mode=mode,
        max_results=max_results or config.max_results,
        no_network=no_network,
        urls=urls or [],
        manual_evidence=manual_evidence or [],
        text_to_verify=text_to_verify,
        fetch_urls=fetch_urls or config.fetch_urls,
        searxng_url=config.searxng_url,
        search_providers=search_providers or config.search_providers,
        profile=profile or config.search_profile,
        routing_strategy="manual" if search_providers else (routing_strategy or config.routing_strategy),
        providers_explicit=bool(search_providers),
        external_payloads=external_payloads or [],
        claim_deepen_rounds=claim_deepen_rounds,
    ).to_dict()


def run_search(
    topic: str,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    fetch_urls: bool = False,
    data_dir: str = "",
    config_path: str = "",
    search_providers: list[str] | None = None,
    profile: str = "",
    routing_strategy: str = "",
    external_payloads: list[dict] | None = None,
) -> dict:
    config = load_config(config_path or None)
    return run_search_pipeline(
        topic=topic,
        data_dir=Path(data_dir or config.data_dir),
        mode=mode,
        max_results=max_results or config.max_results,
        no_network=no_network,
        urls=urls or [],
        fetch_urls=fetch_urls or config.fetch_urls,
        searxng_url=config.searxng_url,
        search_providers=search_providers or config.search_providers,
        profile=profile or config.search_profile,
        routing_strategy="manual" if search_providers else (routing_strategy or config.routing_strategy),
        providers_explicit=bool(search_providers),
        external_payloads=external_payloads or [],
    ).to_dict()


def research_search(
    query: str,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    fetch_urls: bool = False,
    data_dir: str = "",
    config_path: str = "",
    search_providers: list[str] | None = None,
    auto_verify: bool = False,
    profile: str = "",
    routing_strategy: str = "",
    external_payloads: list[dict] | None = None,
) -> dict:
    config = load_config(config_path or None)
    return run_search_pipeline(
        topic=query,
        data_dir=Path(data_dir or config.data_dir),
        mode=mode,
        max_results=max_results or config.max_results,
        no_network=no_network,
        urls=urls or [],
        fetch_urls=fetch_urls or config.fetch_urls,
        searxng_url=config.searxng_url,
        search_providers=search_providers or config.search_providers,
        auto_verify=auto_verify,
        profile=profile or config.search_profile,
        routing_strategy="manual" if search_providers else (routing_strategy or config.routing_strategy),
        providers_explicit=bool(search_providers),
        external_payloads=external_payloads or [],
    ).to_dict()


def research_deepen(
    query: str,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    fetch_urls: bool = False,
    data_dir: str = "",
    config_path: str = "",
    search_providers: list[str] | None = None,
    auto_verify: bool = False,
    profile: str = "",
    routing_strategy: str = "",
    external_payloads: list[dict] | None = None,
) -> dict:
    config = load_config(config_path or None)
    return run_search_pipeline(
        topic=query,
        data_dir=Path(data_dir or config.data_dir),
        mode=mode,
        max_results=max_results or config.max_results,
        no_network=no_network,
        urls=urls or [],
        fetch_urls=fetch_urls or config.fetch_urls,
        searxng_url=config.searxng_url,
        search_providers=search_providers or config.search_providers,
        deep=True,
        auto_verify=auto_verify,
        profile=profile or config.search_profile,
        routing_strategy="manual" if search_providers else (routing_strategy or config.routing_strategy),
        providers_explicit=bool(search_providers),
        external_payloads=external_payloads or [],
    ).to_dict()


def research_route(
    query: str,
    mode: str = "auto",
    profile: str = "",
    routing_strategy: str = "",
    search_providers: list[str] | None = None,
    data_dir: str = "",
    config_path: str = "",
    deep: bool = False,
) -> dict:
    del data_dir
    config = load_config(config_path or None)
    return build_routing_plan(
        query,
        mode=mode,
        profile=profile or config.search_profile,
        routing_strategy="manual" if search_providers else (routing_strategy or config.routing_strategy),
        provider_pool=config.search_providers,
        explicit_providers=search_providers,
        deep=deep,
    ).to_dict()


def research_verify(text_or_run_id: str, data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    actual_data_dir = Path(data_dir or config.data_dir)
    text = text_or_run_id
    record = _get_run(actual_data_dir, text_or_run_id)
    if record:
        text = str(record.get("report_markdown") or record.get("report_json") or "")
    claims = _extract_atomic_claims(text)
    evidence = [Evidence(**item) for item in _dedupe_run_rows(actual_data_dir, "evidence.jsonl")]
    verified = _verify_claims(claims, evidence)
    revision_suggestions = suggest_revisions(verified, evidence)
    if verified:
        append_jsonl(actual_data_dir / "claims.jsonl", verified)
        sync_jsonl_to_sqlite_quietly(actual_data_dir)
    return {
        "claims": [claim.to_dict() for claim in verified],
        "revision_suggestions": [item.to_dict() for item in revision_suggestions],
        "source": "run" if record else "text",
    }


def research_ingest(urls: list[str], fetch_urls: bool = False, data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    actual_data_dir = Path(data_dir or config.data_dir)
    sources = [
        source_from_url(
            str(url).strip(),
            search_queries=["mcp ingest"],
            search_buckets=["manual URLs"],
            search_purposes=["agent supplied source"],
            search_stages=["mcp_ingest"],
        )
        for url in urls
        if str(url).strip()
    ]
    evidence = extract_evidence_from_sources(sources, fetch=fetch_urls or config.fetch_urls)
    if sources:
        append_jsonl(actual_data_dir / "sources.jsonl", sources)
    if evidence:
        append_jsonl(actual_data_dir / "evidence.jsonl", evidence)
    sync_jsonl_to_sqlite_quietly(actual_data_dir)
    return {"sources": [source.to_dict() for source in sources], "evidence": [item.to_dict() for item in evidence]}


def research_get_report(run_id: str, format: str = "md", data_dir: str = "", config_path: str = "") -> dict:
    record = get_run(run_id=run_id, data_dir=data_dir, config_path=config_path)
    if format == "json":
        report_json = record.get("report_json")
        if isinstance(report_json, str):
            try:
                report_json = json.loads(report_json)
            except Exception:  # noqa: BLE001
                pass
        return {"run_id": run_id, "format": "json", "content": report_json or record}
    return {"run_id": run_id, "format": "md", "content": str(record.get("report_markdown") or "")}


def research_status(run_id: str = "", data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    actual_data_dir = Path(data_dir or config.data_dir)
    if run_id:
        record = _get_run(actual_data_dir, run_id)
        if not record:
            raise ValueError(f"Run not found: {run_id}")
        return {
            "run_id": run_id,
            "kind": record.get("kind", "research"),
            "topic": record.get("topic", ""),
            "created_at": record.get("created_at", ""),
            "source_count": record.get("source_count", 0),
            "evidence_count": record.get("evidence_count", 0),
            "claim_count": record.get("claim_count", 0),
            "audit": record.get("audit", {"overall": "not_run"}),
            "report_path": record.get("report_path", ""),
        }
    return {
        "service": "ai-research-workflow",
        "data_dir": str(actual_data_dir),
        "run_count": len(_list_runs(actual_data_dir, limit=1000)),
        "capabilities": [
            "research_search",
            "research_deepen",
            "research_verify",
            "research_ingest",
            "research_get_report",
            "research_status",
            "research_quality",
            "research_deepen_claims",
            "research_check_references",
            "verify_with_longtracer",
            "check_academic_references",
            "expand_claim_search_queries",
            "list_claim_verification_prompts",
            "get_claim_verification_prompt",
        ],
    }


def doctor(data_dir: str = "", config_path: str = "", include_pipeline: bool = True) -> dict:
    config = load_config(config_path or None)
    return run_doctor(config, Path(data_dir or config.data_dir), include_pipeline=include_pipeline)


def list_runs(data_dir: str = "", config_path: str = "", limit: int = 20) -> dict:
    config = load_config(config_path or None)
    return {"runs": _list_runs(Path(data_dir or config.data_dir), limit=limit)}


def get_run(run_id: str, data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    record = _get_run(Path(data_dir or config.data_dir), run_id)
    if not record:
        raise ValueError(f"Run not found: {run_id}")
    return record


def export_obsidian(
    run_id: str,
    data_dir: str = "",
    config_path: str = "",
    vault: str = "",
    folder: str = "",
    overwrite: bool = False,
) -> dict:
    config = load_config(config_path or None)
    return export_run_to_obsidian(
        data_dir=Path(data_dir or config.data_dir),
        run_id=run_id,
        vault=vault or config.obsidian_vault,
        folder=folder or config.obsidian_folder,
        overwrite=overwrite,
    )


def audit(run_id: str, data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    return audit_run(Path(data_dir or config.data_dir), run_id)


def research_quality(run_id: str, data_dir: str = "", config_path: str = "") -> dict:
    config = load_config(config_path or None)
    return run_quality(Path(data_dir or config.data_dir), run_id)


def research_deepen_claims(run_id: str, data_dir: str = "", config_path: str = "", max_queries: int = 5) -> dict:
    config = load_config(config_path or None)
    return deepen_claim_suggestions_for_run(Path(data_dir or config.data_dir), run_id, max_queries=max_queries)


def selftest(config_path: str = "", work_dir: str = "") -> dict:
    config = load_config(config_path or None)
    return run_selftest(config, Path(work_dir) if work_dir else None)


def evals(config_path: str = "", work_dir: str = "") -> dict:
    config = load_config(config_path or None)
    return run_evals(config, Path(work_dir) if work_dir else None)


def bootstrap(
    data_dir: str = "",
    config_path: str = "research.config.json",
    integrations_dir: str = "integrations",
    base_url: str = "",
    skip_selftest: bool = False,
) -> dict:
    config = load_config(config_path or None)
    return run_bootstrap(
        config=config,
        data_dir=Path(data_dir or config.data_dir),
        config_path=Path(config_path or "research.config.json"),
        integrations_dir=Path(integrations_dir or "integrations"),
        base_url=base_url or web_base_url(config),
        skip_selftest=skip_selftest,
    )


def readiness(config_path: str = "", work_dir: str = "", skip_slow: bool = False) -> dict:
    config = load_config(config_path or None)
    return run_readiness(config, Path(work_dir) if work_dir else None, skip_slow=skip_slow)


def _dedupe_run_rows(data_dir: Path, filename: str) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    path = data_dir / filename
    if not path.exists():
        return []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key = line.strip()
        if key in seen:
            continue
        seen.add(key)
        rows.append(json.loads(line))
    return rows
