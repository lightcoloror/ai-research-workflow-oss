from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable

from .claim_deepen import ClaimDeepenAttempt, deepen_uncertain_claims
from .academic_reference_check import check_academic_references
from .evidence import extract_evidence_from_sources, source_from_url
from .external_adapters import normalize_external_payload
from .factcheck import extract_atomic_claims, verify_claims
from .insights import claim_citations_from_claims
from .models import Claim, Evidence, QueryPlan, Report, ReportInsight, RevisionSuggestion, Source, Verdict, now_iso
from .openfactcheck_backend import run_openfactcheck_optional
from .planner import plan_query
from .quality import deepen_claim_suggestions, quality_for_report
from .report import draft_report, report_to_json, report_to_markdown
from .revisions import suggest_revisions
from .sqlite_store import sync_jsonl_to_sqlite_quietly
from .search import SearchError, multi_source_search, multi_source_search_with_status
from .routing import build_routing_plan, coverage_gate
from .store import append_jsonl, ensure_data_dir, read_jsonl, stable_id, write_text
from .synthesis import synthesize_claim_text


@dataclass(slots=True)
class PipelineResult:
    run_id: str
    plan: QueryPlan
    sources: list[Source]
    evidence: list[Evidence]
    claims: list[Claim]
    claim_citations: list[ReportInsight]
    claim_deepen_attempts: list[ClaimDeepenAttempt]
    deepen_suggestions: list[dict]
    quality_summary: dict
    reference_checks: dict
    revision_suggestions: list[RevisionSuggestion]
    report_markdown: str
    report_json: str
    warning: str = ""
    report_path: str = ""
    audit: dict | None = None
    routing_plan: dict = field(default_factory=dict)
    routing_trace: list[dict] = field(default_factory=list)
    coverage_gate: dict = field(default_factory=dict)
    provider_budget: dict = field(default_factory=dict)
    external_acquisition: list[dict] = field(default_factory=list)
    stop_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "plan": self.plan.to_dict(),
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "claims": [claim.to_dict() for claim in self.claims],
            "claim_citations": [item.to_dict() for item in self.claim_citations],
            "claim_deepen_attempts": [item.to_dict() for item in self.claim_deepen_attempts],
            "deepen_suggestions": self.deepen_suggestions,
            "quality_summary": self.quality_summary,
            "reference_checks": self.reference_checks,
            "revision_suggestions": [item.to_dict() for item in self.revision_suggestions],
            "report_markdown": self.report_markdown,
            "report_json": self.report_json,
            "warning": self.warning,
            "report_path": self.report_path,
            "audit": self.audit or {},
            "routing_plan": self.routing_plan or {},
            "routing_trace": self.routing_trace or [],
            "coverage_gate": self.coverage_gate or {},
            "provider_budget": self.provider_budget or {},
            "external_acquisition": self.external_acquisition or [],
            "stop_reason": self.stop_reason,
        }


def run_research_pipeline(
    *,
    topic: str,
    data_dir: Path,
    mode: str = "auto",
    max_results: int = 10,
    no_network: bool = False,
    urls: list[str] | None = None,
    manual_evidence: list[dict] | None = None,
    text_to_verify: str = "",
    fetch_urls: bool = False,
    searxng_url: str = "",
    search_providers: list[str] | None = None,
    profile: str = "default",
    routing_strategy: str = "legacy_fanout",
    providers_explicit: bool = False,
    external_payloads: list[dict] | None = None,
    openfactcheck_backend: Callable[..., object] | None = None,
    openfactcheck_paths: list[str | Path] | None = None,
    claim_deepen_rounds: int = 0,
) -> PipelineResult:
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")

    data_dir = ensure_data_dir(data_dir)
    plan = plan_query(topic, mode)
    warnings: list[str] = []
    collected_sources: list[Source] = []
    collected_evidence: list[Evidence] = []
    external_texts: list[str] = []
    route_plan = build_routing_plan(
        topic,
        mode=plan.mode,
        profile=profile,
        routing_strategy=routing_strategy,
        provider_pool=search_providers,
        explicit_providers=search_providers if providers_explicit else None,
    )
    routing_trace: list[dict] = []
    route_gate: dict = {}
    stop_reason = ""

    if route_plan.routing_strategy == "adaptive_staged":
        route_gate, stop_reason = _run_adaptive_pipeline_search(
            route_plan=route_plan,
            topic=plan.topic,
            mode=plan.mode,
            max_results=max_results,
            no_network=no_network,
            searxng_url=searxng_url,
            provider_stats_path=str(data_dir / "provider_stats.json"),
            sources=collected_sources,
            warnings=warnings,
            routing_trace=routing_trace,
        )
    elif route_plan.stages:
        try:
            collected_sources.extend(
                multi_source_search(
                    plan.topic,
                    mode=plan.mode,
                    max_results=max_results,
                    no_network=no_network,
                    searxng_url=searxng_url,
                    providers=search_providers,
                    provider_stats_path=str(data_dir / "provider_stats.json"),
                    route_kind=route_plan.route_kind,
                )
            )
        except SearchError as exc:
            warnings.append(str(exc))
        route_gate = coverage_gate(route_plan.route_kind, collected_sources, [])
        stop_reason = "manual_provider_run_complete" if route_plan.routing_strategy == "manual" else "legacy_fanout_complete"
    else:
        route_gate = coverage_gate(route_plan.route_kind, [], [])
        stop_reason = "external_acquisition_required" if route_plan.external_acquisition else "no_native_route"

    if urls:
        collected_sources.extend(
            source_from_url(
                url.strip(),
                search_queries=[topic],
                search_buckets=["manual URLs"],
                search_purposes=["user supplied source"],
                search_stages=["manual_ingest"],
            )
            for url in urls
            if url.strip()
        )

    collected_sources = _dedupe_sources(collected_sources)
    if collected_sources:
        collected_evidence.extend(extract_evidence_from_sources(collected_sources, fetch=fetch_urls))

    if manual_evidence:
        manual_sources, manual_items = manual_evidence_records(manual_evidence)
        collected_sources = _dedupe_sources([*collected_sources, *manual_sources])
        collected_evidence.extend(manual_items)

    for payload in external_payloads or []:
        records = normalize_external_payload(payload, topic=plan.topic)
        collected_sources = _dedupe_sources([*collected_sources, *records.sources])
        collected_evidence.extend(records.evidence)
        if records.text_to_verify:
            external_texts.append(records.text_to_verify)
        warnings.extend(records.warnings)

    if openfactcheck_backend:
        backend_result = run_openfactcheck_optional(
            text=text_to_verify or topic,
            topic=plan.topic,
            backend=openfactcheck_backend,
            extra_paths=openfactcheck_paths,
        )
        records = backend_result.records
        collected_sources = _dedupe_sources([*collected_sources, *records.sources])
        collected_evidence.extend(records.evidence)
        if records.text_to_verify:
            external_texts.append(records.text_to_verify)
        elif records.claims:
            external_texts.append("\n".join(claim.text for claim in records.claims))
        warnings.extend(records.warnings)

    if collected_sources:
        append_jsonl(data_dir / "sources.jsonl", collected_sources)
    if collected_evidence:
        append_jsonl(data_dir / "evidence.jsonl", collected_evidence)

    stored_sources = [_source_from_row(row) for row in dedupe_rows(read_jsonl(data_dir / "sources.jsonl"))]
    stored_evidence = [_evidence_from_row(row) for row in dedupe_rows(read_jsonl(data_dir / "evidence.jsonl"))]
    verification_sources = collected_sources or stored_sources
    verification_evidence = _dedupe_evidence(collected_evidence) or stored_evidence

    draft = draft_report(plan.topic, plan, collected_sources)
    external_verify_text = "\n".join(text for text in external_texts if text.strip()).strip()
    verify_text = text_to_verify.strip() or external_verify_text or synthesize_claim_text(plan.topic, verification_evidence) or draft
    claims = extract_atomic_claims(verify_text)
    verified = verify_claims(claims, verification_evidence)
    claim_deepen_attempts: list[ClaimDeepenAttempt] = []
    if claim_deepen_rounds > 0 and verified:
        deepen_result = deepen_uncertain_claims(
            claims=verified,
            sources=verification_sources,
            evidence=verification_evidence,
            mode=plan.mode,
            max_rounds=claim_deepen_rounds,
            max_results=max(1, min(3, max_results)),
            no_network=no_network,
            fetch_urls=fetch_urls,
            searxng_url=searxng_url,
            search_providers=search_providers,
        )
        verified = deepen_result.claims
        verification_sources = deepen_result.sources
        verification_evidence = deepen_result.evidence
        claim_deepen_attempts = deepen_result.attempts
        warnings.extend(deepen_result.warnings)
    claim_citations = claim_citations_from_claims(verified, verification_evidence, verification_sources)
    revision_suggestions = suggest_revisions(verified, verification_evidence)
    if verified:
        append_jsonl(data_dir / "claims.jsonl", verified)

    report = Report(
        topic=plan.topic,
        summary=_summary_from_claims(verified),
        findings=[claim.text for claim in verified if str(claim.verdict) == "supported"],
        claim_table=verified,
        gaps=[claim.text for claim in verified if str(claim.verdict) == "not_found"],
        sources=verification_sources,
        evidence=verification_evidence,
        claim_citations=claim_citations,
        claim_deepen_attempts=[item.to_dict() for item in claim_deepen_attempts],
        revision_suggestions=revision_suggestions,
    )
    quality = quality_for_report(report.to_dict())
    report.quality_summary = quality["quality_summary"]
    report.deepen_suggestions = deepen_claim_suggestions(report.to_dict(), quality, max_queries=5)
    if plan.mode == "academic":
        report.reference_checks = check_academic_references(verify_text).to_dict()
    routing_metadata = {
        "routing_plan": route_plan.to_dict(),
        "routing_trace": routing_trace,
        "coverage_gate": route_gate,
        "provider_budget": route_plan.provider_budget,
        "external_acquisition": route_plan.external_acquisition,
        "stop_reason": stop_reason,
    }
    report_markdown = _append_routing_markdown(report_to_markdown(report), routing_metadata)
    report_json = _inject_routing_json(report_to_json(report), routing_metadata)
    run_record = persist_run(
        data_dir=data_dir,
        plan=plan,
        report_markdown=report_markdown,
        report_json=report_json,
        claims=verified,
        claim_citations=claim_citations,
        claim_deepen_attempts=claim_deepen_attempts,
        deepen_suggestions=report.deepen_suggestions,
        quality_summary=report.quality_summary,
        reference_checks=report.reference_checks,
        sources=verification_sources,
        evidence=verification_evidence,
        revision_suggestions=revision_suggestions,
        warning="; ".join(warnings),
        routing_metadata=routing_metadata,
    )
    from .audit import audit_run

    audit = audit_run(data_dir, run_record["id"])
    run_record["audit"] = audit
    append_jsonl(data_dir / "runs.jsonl", [run_record])
    sync_jsonl_to_sqlite_quietly(data_dir)
    return PipelineResult(
        run_id=run_record["id"],
        plan=plan,
        sources=verification_sources,
        evidence=verification_evidence,
        claims=verified,
        claim_citations=claim_citations,
        claim_deepen_attempts=claim_deepen_attempts,
        deepen_suggestions=report.deepen_suggestions,
        quality_summary=report.quality_summary,
        reference_checks=report.reference_checks,
        revision_suggestions=revision_suggestions,
        report_markdown=report_markdown,
        report_json=report_json,
        warning="; ".join(warnings),
        report_path=run_record["report_path"],
        audit=audit,
        routing_plan=route_plan.to_dict(),
        routing_trace=routing_trace,
        coverage_gate=route_gate,
        provider_budget=route_plan.provider_budget,
        external_acquisition=route_plan.external_acquisition,
        stop_reason=stop_reason,
    )


def persist_run(
    *,
    data_dir: Path,
    plan: QueryPlan,
    report_markdown: str,
    report_json: str,
    claims: list[Claim],
    claim_citations: list[ReportInsight],
    claim_deepen_attempts: list[ClaimDeepenAttempt],
    deepen_suggestions: list[dict],
    quality_summary: dict,
    reference_checks: dict,
    sources: list[Source],
    evidence: list[Evidence],
    revision_suggestions: list[RevisionSuggestion],
    warning: str = "",
    routing_metadata: dict | None = None,
) -> dict:
    created_at = now_iso()
    run_id = stable_id(created_at, plan.topic, report_json, prefix="run_")
    report_file = data_dir / "reports" / f"{_safe_filename(created_at)}-{_safe_filename(plan.topic)}.md"
    write_text(report_file, report_markdown)
    routing_metadata = routing_metadata or {}
    record = {
        "id": run_id,
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
        "claims": [claim.to_dict() for claim in claims],
        "sources": [source.to_dict() for source in sources],
        "evidence": [item.to_dict() for item in evidence],
        "claim_citations": [item.to_dict() for item in claim_citations],
        "claim_deepen_attempts": [item.to_dict() for item in claim_deepen_attempts],
        "deepen_suggestions": deepen_suggestions,
        "quality_summary": quality_summary,
        "reference_checks": reference_checks,
        "revision_suggestions": [item.to_dict() for item in revision_suggestions],
        "routing_plan": routing_metadata.get("routing_plan", {}),
        "routing_trace": routing_metadata.get("routing_trace", []),
        "coverage_gate": routing_metadata.get("coverage_gate", {}),
        "provider_budget": routing_metadata.get("provider_budget", {}),
        "external_acquisition": routing_metadata.get("external_acquisition", []),
        "stop_reason": routing_metadata.get("stop_reason", ""),
    }
    append_jsonl(data_dir / "runs.jsonl", [record])
    return record


def list_runs(data_dir: Path, limit: int = 20) -> list[dict]:
    rows = dedupe_rows(read_jsonl(data_dir / "runs.jsonl"))
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows[:limit]


def get_run(data_dir: Path, run_id: str) -> dict | None:
    for row in reversed(read_jsonl(data_dir / "runs.jsonl")):
        if row.get("id") == run_id:
            return row
    return None


def manual_evidence_records(items: list[dict]) -> tuple[list[Source], list[Evidence]]:
    sources: list[Source] = []
    evidence: list[Evidence] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        url = str(item.get("url", "")).strip()
        source = source_from_url(
            url,
            search_queries=["manual evidence"],
            search_buckets=["manual evidence"],
            search_purposes=["user supplied evidence"],
            search_stages=["manual_evidence"],
        ) if url else Source(
            id=stable_id(text, prefix="src_note_"),
            url=f"manual-note://{stable_id(text)}",
            title="Manual evidence note",
            publisher="local note",
            search_engine="manual",
            snippet=text[:240],
            search_queries=["manual evidence"],
            search_buckets=["manual evidence"],
            search_purposes=["user supplied evidence"],
            search_stages=["manual_evidence"],
        )
        sources.append(source)
        evidence.append(
            Evidence(
                id=stable_id(source.id, text, prefix="ev_"),
                source_id=source.id,
                quote_or_paraphrase=text,
                claim_relevance=str(item.get("claim_relevance", "manual evidence note")),
                support_direction=Verdict.PARTIAL,
                confidence=float(item.get("confidence", 0.75)),
                extraction_method="manual_evidence",
                text_length=len(text),
                source_quote_type="manual_note",
            )
        )
    return sources, evidence


def dedupe_rows(rows: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for index, row in enumerate(rows):
        key = str(row.get("id") or row.get("url") or row.get("text") or index)
        deduped[key] = row
    return list(deduped.values())


def _dedupe_sources(sources: list[Source]) -> list[Source]:
    deduped: dict[str, Source] = {}
    for source in sources:
        key = source.artifact_id or source.id or source.url
        if key in deduped:
            _merge_source_metadata(deduped[key], source)
        else:
            deduped[key] = source
    return list(deduped.values())


def _merge_source_metadata(target: Source, incoming: Source) -> None:
    if incoming.snippet and not target.snippet:
        target.snippet = incoming.snippet
    if incoming.published_at and not target.published_at:
        target.published_at = incoming.published_at
    if incoming.search_engine and incoming.search_engine not in target.search_engine.split("+"):
        target.search_engine = "+".join([item for item in [target.search_engine, incoming.search_engine] if item])
    target.search_queries = _merge_labels(target.search_queries, incoming.search_queries)
    target.search_buckets = _merge_labels(target.search_buckets, incoming.search_buckets)
    target.search_purposes = _merge_labels(target.search_purposes, incoming.search_purposes)
    target.search_stages = _merge_labels(target.search_stages, incoming.search_stages)


def _merge_labels(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*first, *second]:
        label = " ".join(str(item or "").split())
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        merged.append(label)
    return merged


def _dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    deduped: dict[str, Evidence] = {}
    for item in evidence:
        deduped[item.id] = item
    return list(deduped.values())


def _source_from_row(row: dict) -> Source:
    return Source(**row)


def _evidence_from_row(row: dict) -> Evidence:
    return Evidence(**row)


def _summary_from_claims(claims: list[Claim]) -> str:
    supported = sum(1 for claim in claims if str(claim.verdict) == "supported")
    partial = sum(1 for claim in claims if str(claim.verdict) == "partial")
    contradicted = sum(1 for claim in claims if str(claim.verdict) == "contradicted")
    not_found = sum(1 for claim in claims if str(claim.verdict) == "not_found")
    return f"Claim check complete: {supported} supported, {partial} partial, {contradicted} contradicted, {not_found} not found."


def _run_adaptive_pipeline_search(
    *,
    route_plan,
    topic: str,
    mode: str,
    max_results: int,
    no_network: bool,
    searxng_url: str,
    provider_stats_path: str,
    sources: list[Source],
    warnings: list[str],
    routing_trace: list[dict],
) -> tuple[dict, str]:
    if not route_plan.stages:
        reason = "external_acquisition_required" if route_plan.external_acquisition else "no_native_route"
        return coverage_gate(route_plan.route_kind, sources, []), reason

    statuses: list[dict] = []
    suffixes = {
        "developer": (" API documentation GitHub", " issue pull request error", " official docs changelog"),
        "academic": (" systematic review paper", " dataset benchmark DOI", " official research institution"),
        "high_risk_factcheck": (" official guideline", " systematic review independent evidence", " regulator consensus"),
        "deep_research": ("", " primary sources evidence", " limitations independent review"),
        "chinese_web": ("", " 中文 官方", " 深度 证据"),
        "daily_web": ("", " official source"),
    }
    variants = suffixes.get(route_plan.route_kind, ("",))
    for index, stage in enumerate(route_plan.stages):
        stage_name = f"adaptive_{stage.id}"
        query = f"{topic}{variants[min(index, len(variants) - 1)]}".strip()
        if no_network:
            routing_trace.append({"stage": stage_name, "providers": stage.providers, "status": "skipped", "reason": "no_network"})
            continue
        try:
            result = multi_source_search_with_status(
                query,
                mode="daily",
                max_results=min(max_results, stage.max_results_per_provider * max(1, len(stage.providers))),
                searxng_url=searxng_url,
                providers=stage.providers,
                provider_stats_path=provider_stats_path,
                route_kind=route_plan.route_kind,
            )
        except SearchError as exc:
            warning = str(exc)
            warnings.append(warning)
            routing_trace.append({"stage": stage_name, "providers": stage.providers, "status": "error", "reason": warning})
            continue
        for source in result.sources:
            source.search_queries = _merge_labels(source.search_queries, [query])
            source.search_stages = _merge_labels(source.search_stages, [stage_name])
            source.search_purposes = _merge_labels(source.search_purposes, [f"adaptive route {route_plan.route_kind}"])
        sources.extend(result.sources)
        statuses.extend(result.provider_statuses)
        warnings.extend(result.warnings)
        gate = coverage_gate(route_plan.route_kind, _dedupe_sources(sources), statuses)
        routing_trace.append(
            {
                "stage": stage_name,
                "providers": stage.providers,
                "status": "ok" if result.sources else "empty",
                "source_count": len(result.sources),
                "coverage_passed": gate["passed"],
                "reason": stage.reason,
            }
        )
        if gate["passed"]:
            return gate, "coverage_gate_passed"
    gate = coverage_gate(route_plan.route_kind, _dedupe_sources(sources), statuses)
    if no_network:
        return gate, "network_disabled"
    return gate, "coverage_gate_passed" if gate["passed"] else "provider_budget_exhausted"


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


def _safe_filename(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "run"
