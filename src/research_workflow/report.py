from __future__ import annotations

import json

from .models import Claim, QueryPlan, Report, Source, Verdict
from .quality import quality_for_report


def draft_report(topic: str, plan: QueryPlan, sources: list[Source], claims: list[Claim] | None = None) -> str:
    claims = claims or []
    lines = [
        f"# {topic}",
        "",
        "## Short Answer",
        "",
        "This is a research draft. Treat every conclusion as provisional until the claim table is verified.",
        "",
        "## Query Plan",
        "",
        f"- Core question: {plan.core_question}",
        f"- Mode: {plan.mode}",
        "",
        "## Subquestions",
        "",
    ]
    lines.extend(f"- {item}" for item in plan.subquestions)
    lines.extend(["", "## Keywords", "", f"- Chinese: {', '.join(plan.zh_keywords)}", f"- English: {', '.join(plan.en_keywords)}"])
    lines.extend(["", "## Sources", ""])
    if sources:
        for source in sources:
            lines.append(f"- [{source.title or source.url}]({source.url}) - {source.source_type}; {_source_provenance(source)}; {source.snippet}")
    else:
        lines.append("- No live sources collected yet. Use `research ingest urls.txt` or configure `RESEARCH_SEARXNG_URL`.")
    lines.extend(["", "## Evidence Table", "", "| Claim | Risk | Verdict | Confidence | Evidence |", "| --- | --- | --- | --- | --- |"])
    if claims:
        for claim in claims:
            lines.append(f"| {claim.text} | {claim.risk_level} | {claim.verdict} | {claim.confidence:.2f} | {', '.join(claim.evidence_ids)} |")
    else:
        lines.append("| Not extracted yet | normal | not_found | 0.00 | |")
    lines.extend(["", "## Not Verified", "", "- Fill this section after `research verify`.", "", "## Next Search Steps", ""])
    lines.extend(f"- Search: {target}" for target in plan.source_targets)
    return "\n".join(lines) + "\n"


def report_to_markdown(report: Report) -> str:
    source_by_id = {source.id: source for source in report.sources}
    quality = report.quality_summary or quality_for_report(report.to_dict()).get("quality_summary", {})
    lines = [
        f"# {report.topic}",
        "",
        "## Short Answer",
        "",
        report.summary,
        "",
        "## Key Findings",
        "",
    ]
    lines.extend(f"- {finding}" for finding in report.findings)
    lines.extend(
        [
            "",
            "## Evidence Policy",
            "",
            "- High-risk claims require at least two independent source IDs before they can be marked supported.",
            "- Unsupported claims remain not_found rather than being promoted from model prior knowledge.",
        ]
    )
    lines.extend(["", "## Evidence Table", "", "| Claim | Risk | Verdict | Confidence | Evidence |", "| --- | --- | --- | --- | --- |"])
    for claim in report.claim_table:
        evidence_refs = ", ".join(claim.evidence_ids) or "none"
        lines.append(f"| {claim.text} | {claim.risk_level} | {claim.verdict} | {claim.confidence:.2f} | {evidence_refs} |")
    lines.extend(["", "## Claim Citations", "", "| Claim | Verdict | Confidence | Evidence IDs | Citations |", "| --- | --- | --- | --- | --- |"])
    if report.claim_citations:
        for item in report.claim_citations:
            evidence_refs = ", ".join(item.evidence_ids) or "none"
            citations = " ; ".join(_md_link(citation) for citation in item.citations) or "none"
            lines.append(f"| {_md_cell(item.claim)} | {item.verdict} | {item.confidence:.2f} | {_md_cell(evidence_refs)} | {_md_cell(citations)} |")
    else:
        lines.append("| No claim citations recorded | not_found | 0.00 | none | none |")
    lines.extend(
        [
            "",
            "## Evidence Quality Summary",
            "",
            f"- Overall: `{quality.get('overall', 'unknown')}`",
            f"- Finding citation coverage: {float(quality.get('finding_citation_coverage') or 0):.2f}",
            f"- Weak claims: {quality.get('weak_claims', 0)}",
            f"- Single-source supported claims: {quality.get('single_source_claims', 0)}",
        ]
    )
    recommendations = quality.get("recommendations") or []
    if recommendations:
        lines.append("- Recommendations: " + "; ".join(str(item) for item in recommendations))
    if report.claim_deepen_attempts:
        lines.extend(["", "## Claim Deepen Attempts", "", "| Claim | Round | Query | Before | After | New Evidence | Warning |", "| --- | --- | --- | --- | --- | --- | --- |"])
        for item in report.claim_deepen_attempts:
            lines.append(
                f"| {_md_cell(item.get('claim', ''))} | {item.get('round_index', '')} | {_md_cell(item.get('query', ''))} | {item.get('before_verdict', '')} | {item.get('after_verdict', '')} | {_md_cell(', '.join(item.get('new_evidence_ids') or []) or 'none')} | {_md_cell(item.get('warning', '') or 'none')} |"
            )
    if report.deepen_suggestions:
        lines.extend(["", "## Claim Deepen Suggestions", ""])
        for item in report.deepen_suggestions:
            queries = "; ".join(item.get("next_search_queries") or [])
            lines.append(f"- {_md_cell(item.get('claim', ''))}: {_md_cell(item.get('missing_evidence_reason', ''))}")
            if queries:
                lines.append(f"  - Queries: {_md_cell(queries)}")
    if report.reference_checks:
        checks = report.reference_checks.get("references") or report.reference_checks.get("reference_checks") or []
        if checks:
            lines.extend(["", "## References Check", "", "| Reference | Status | Confidence | Notes |", "| --- | --- | --- | --- |"])
            for item in checks:
                identifier = item.get("doi") or item.get("arxiv_id") or item.get("title") or item.get("raw") or "reference"
                lines.append(f"| {_md_cell(identifier)} | {item.get('status', '')} | {float(item.get('confidence') or 0):.2f} | {_md_cell('; '.join(item.get('notes') or []))} |")
    if report.evidence:
        lines.extend(["", "## Evidence Notes", ""])
        for item in report.evidence:
            source = source_by_id.get(item.source_id)
            label = source.title if source and source.title else item.source_id
            url = source.url if source else ""
            source_label = f"[{label}]({url})" if url else label
            quote_type = item.source_quote_type or "unknown"
            method = item.extraction_method or "unknown"
            lines.append(f"- `{item.id}` from {source_label} (`{quote_type}`, `{method}`): {_clip(item.quote_or_paraphrase, 360)}")
    if report.revision_suggestions:
        lines.extend(["", "## Revision Suggestions", ""])
        for item in report.revision_suggestions:
            evidence_refs = ", ".join(item.evidence_ids) or "none"
            lines.append(f"- `{item.action}` `{item.verdict}`: {item.claim_text}")
            lines.append(f"  - Reason: {item.failure_reason}")
            lines.append(f"  - Evidence: {evidence_refs}")
            if item.suggested_replacement:
                lines.append(f"  - Suggestion: {_clip(item.suggested_replacement, 360)}")
    lines.extend(["", "## Source Notes", ""])
    for source in report.sources:
        lines.append(f"- [{source.title or source.url}]({source.url}) - {source.source_type}; retrieved {source.retrieved_at}")
        lines.append(f"  - Retrieved by: {_source_provenance(source)}")
    lines.extend(["", "## Disputed or Weak Evidence", ""])
    weak = [claim for claim in report.claim_table if claim.verdict in {Verdict.PARTIAL, Verdict.CONTRADICTED}]
    lines.extend(f"- {claim.text} ({claim.verdict})" for claim in weak)
    lines.extend(["", "## Not Verified", ""])
    not_found = [claim for claim in report.claim_table if claim.verdict == Verdict.NOT_FOUND]
    lines.extend(f"- {claim.text}" for claim in not_found)
    if not not_found:
        lines.append("- None recorded.")
    lines.extend(["", "## Next Search Steps", ""])
    lines.extend(f"- {gap}" for gap in report.gaps)
    return "\n".join(lines) + "\n"


def report_to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _clip(text: str, max_length: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _source_provenance(source: Source) -> str:
    buckets = ", ".join(source.search_buckets[:3]) if source.search_buckets else "unknown"
    query = "; ".join(source.search_queries[:2]) if source.search_queries else "unknown"
    return f"{buckets}; query: {query}"


def _md_cell(value: object) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


def _md_link(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        safe = value.replace(")", "%29")
        return f"[link]({safe})"
    return value
