from __future__ import annotations

import json
from pathlib import Path

from .pipeline import get_run
from .store import write_text


def export_run_to_obsidian(
    *,
    data_dir: Path,
    run_id: str,
    vault: str | Path,
    folder: str = "AI Research",
    overwrite: bool = False,
) -> dict:
    record = get_run(data_dir, run_id)
    if not record:
        raise ValueError(f"Run not found: {run_id}")
    vault_path = Path(vault)
    if not str(vault_path).strip():
        raise ValueError("Obsidian vault path is required")
    target_dir = vault_path / _safe_relative_folder(folder)
    filename = f"{_safe_filename(str(record.get('created_at', '')))}-{_safe_filename(str(record.get('topic', 'Research')))}.md"
    target = target_dir / filename
    if target.exists() and not overwrite:
        raise FileExistsError(f"Obsidian note already exists: {target}")
    content = obsidian_note_from_run(record)
    write_text(target, content)
    return {
        "path": str(target),
        "run_id": run_id,
        "topic": record.get("topic", ""),
        "created_at": record.get("created_at", ""),
    }


def obsidian_note_from_run(record: dict) -> str:
    markdown = str(record.get("report_markdown") or "")
    payload = _report_payload(record)
    quality = _quality_summary(record, payload)
    reference_checks = _reference_checks(record, payload)
    frontmatter = {
        "title": str(record.get("topic", "Research")),
        "run_id": str(record.get("id", "")),
        "workflow_kind": str(record.get("kind", "research")),
        "created_at": str(record.get("created_at", "")),
        "mode": str(record.get("mode", "")),
        "audit_overall": str((record.get("audit") or {}).get("overall", "not_run")),
        "source_quality": str((record.get("source_quality") or {}).get("overall", "")),
        "evidence_quality": str(quality.get("overall", "unknown") if quality else "unknown"),
        "weak_claim_count": quality.get("weak_claims", 0) if quality else 0,
        "reference_check_overall": str((reference_checks.get("validation_summary") or {}).get("overall", "unknown") if reference_checks else "unknown"),
        "quality_reviewed_at": str(record.get("created_at", "")),
        "source_count": record.get("source_count", len(record.get("sources", []) or [])),
        "evidence_count": record.get("evidence_count", len(record.get("evidence", []) or [])),
        "claim_count": record.get("claim_count", 0),
        "supported_count": record.get("supported_count", 0),
        "partial_count": record.get("partial_count", 0),
        "contradicted_count": record.get("contradicted_count", 0),
        "not_found_count": record.get("not_found_count", 0),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.extend(["tags:", "  - ai-research", "  - fact-check", "---", ""])
    lines.extend(["## Research Report", ""])
    lines.append(markdown.strip())
    lines.extend(_evidence_quality_review(record, payload))
    lines.extend(_claim_citation_table(record))
    lines.extend(_source_table(record))
    lines.extend(_unverified_claim_table(record))
    lines.extend(_claim_deepen_suggestions(record, payload))
    lines.extend(_references_check(record, payload))
    lines.extend(_next_search_suggestions(record))
    lines.extend(
        [
            "",
            "## Run Metadata",
            "",
            f"- Run ID: `{record.get('id', '')}`",
            f"- Workflow kind: `{record.get('kind', 'research')}`",
            f"- Audit: `{(record.get('audit') or {}).get('overall', 'not_run')}`",
            f"- Source quality: `{(record.get('source_quality') or {}).get('overall', '')}`",
            f"- Evidence quality: `{quality.get('overall', 'unknown') if quality else 'unknown'}`",
            f"- Report path: `{record.get('report_path', '')}`",
            f"- Warning: {record.get('warning') or 'none'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _source_table(record: dict) -> list[str]:
    sources = record.get("sources") or []
    lines = ["", "## Source Table", ""]
    if not sources:
        return [*lines, "No sources recorded.", ""]
    lines.append("| Source | Type | Publisher | Search | URL |")
    lines.append("|---|---|---|---|---|")
    for source in sources:
        title = str(source.get("title") or source.get("publisher") or source.get("url") or "source")
        source_type = str(source.get("source_type") or "")
        publisher = str(source.get("publisher") or "")
        engine = str(source.get("search_engine") or "")
        url = str(source.get("url") or "")
        lines.append(f"| {_md_cell(title)} | {_md_cell(source_type)} | {_md_cell(publisher)} | {_md_cell(engine)} | {_md_link(url)} |")
    lines.append("")
    return lines


def _claim_citation_table(record: dict) -> list[str]:
    rows = record.get("claim_citations") or []
    lines = ["", "## Claim Citation Table", ""]
    if not rows:
        return [*lines, "No claim citation rows recorded.", ""]
    lines.append("| Verdict | Confidence | Claim | Evidence IDs | Citations |")
    lines.append("|---|---:|---|---|---|")
    for row in rows:
        evidence_ids = ", ".join(str(item) for item in row.get("evidence_ids") or []) or "none"
        citations = " ; ".join(_md_link(str(item)) for item in row.get("citations") or []) or "none"
        lines.append(
            f"| {_md_cell(str(row.get('verdict') or ''))} | {_md_cell(str(row.get('confidence') or 0))} | {_md_cell(str(row.get('claim') or ''))} | {_md_cell(evidence_ids)} | {_md_cell(citations)} |"
        )
    lines.append("")
    return lines


def _evidence_quality_review(record: dict, payload: dict) -> list[str]:
    quality = _quality_summary(record, payload)
    lines = ["", "## Evidence Quality Review", ""]
    if not quality:
        return [*lines, "- No evidence quality summary recorded.", ""]
    lines.extend(
        [
            f"- Overall: `{quality.get('overall', 'unknown')}`",
            f"- Finding citation coverage: `{quality.get('finding_citation_coverage', 0)}`",
            f"- Weak claims: `{quality.get('weak_claims', 0)}`",
            f"- Single-source supported claims: `{quality.get('single_source_claims', 0)}`",
            f"- Weak evidence rows: `{len(quality.get('weak_evidence') or [])}`",
        ]
    )
    recommendations = quality.get("recommendations") or []
    if recommendations:
        lines.append("")
        lines.extend(f"- Recommendation: {item}" for item in recommendations[:8])
    lines.append("")
    return lines


def _unverified_claim_table(record: dict) -> list[str]:
    claims = record.get("claims") or record.get("claim_table") or []
    open_claims = [claim for claim in claims if str(claim.get("verdict") or "") in {"partial", "not_found", "contradicted"}]
    lines = ["", "## Unverified And Disputed Claims", ""]
    if not open_claims:
        return [*lines, "No partial, contradicted, or not_found claims recorded.", ""]
    lines.append("| Verdict | Confidence | Risk | Claim | Evidence IDs |")
    lines.append("|---|---:|---|---|---|")
    for claim in open_claims:
        evidence_ids = ", ".join(str(item) for item in claim.get("evidence_ids") or [])
        lines.append(
            f"| {_md_cell(str(claim.get('verdict') or ''))} | {_md_cell(str(claim.get('confidence') or 0))} | {_md_cell(str(claim.get('risk_level') or ''))} | {_md_cell(str(claim.get('text') or ''))} | {_md_cell(evidence_ids or 'none')} |"
        )
    lines.append("")
    return lines


def _claim_deepen_suggestions(record: dict, payload: dict) -> list[str]:
    suggestions = record.get("deepen_suggestions") or payload.get("deepen_suggestions") or []
    lines = ["", "## Claim Deepen Suggestions", ""]
    if not suggestions:
        return [*lines, "- No claim deepen suggestions recorded.", ""]
    for item in suggestions[:8]:
        lines.append(f"- `{item.get('verdict', 'review')}` {item.get('claim', '')}")
        if item.get("missing_evidence_reason"):
            lines.append(f"  - Gap: {item.get('missing_evidence_reason')}")
        queries = item.get("next_search_queries") or []
        if queries:
            lines.append(f"  - Query: {queries[0]}")
    lines.append("")
    return lines


def _references_check(record: dict, payload: dict) -> list[str]:
    checks = _reference_checks(record, payload)
    lines = ["", "## References Check", ""]
    if not checks:
        return [*lines, "- No reference checks recorded.", ""]
    summary = checks.get("validation_summary") or {}
    counts = summary.get("counts") or {}
    lines.extend(
        [
            f"- Overall: `{summary.get('overall', 'unknown')}`",
            f"- Valid: `{counts.get('valid', 0)}`",
            f"- Partial: `{counts.get('partial', 0)}`",
            f"- Not found: `{counts.get('not_found', 0)}`",
            f"- Metadata mismatch: `{counts.get('metadata_mismatch', 0)}`",
        ]
    )
    references = checks.get("references") or []
    if references:
        lines.extend(["", "| Reference | Status | Notes |", "|---|---|---|"])
        for item in references[:12]:
            identifier = item.get("doi") or item.get("arxiv_id") or item.get("title") or item.get("raw") or "reference"
            notes = "; ".join(str(note) for note in item.get("notes") or [])
            lines.append(f"| {_md_cell(str(identifier))} | {_md_cell(str(item.get('status') or ''))} | {_md_cell(notes)} |")
    lines.append("")
    return lines


def _next_search_suggestions(record: dict) -> list[str]:
    suggestions: list[str] = []
    suggestions.extend(str(item) for item in record.get("coverage_gaps") or [])
    suggestions.extend(str(item) for item in record.get("unverified_seed_queries") or [])
    payload = _report_payload(record)
    suggestions.extend(str(item) for item in payload.get("coverage_gaps") or [])
    suggestions.extend(str(item) for item in payload.get("unverified_seed_queries") or [])
    suggestions.extend(str(item) for item in payload.get("next_search_steps") or [])
    for item in record.get("deepen_suggestions") or payload.get("deepen_suggestions") or []:
        suggestions.extend(str(query) for query in item.get("next_search_queries") or [])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in suggestions:
        cleaned = " ".join(item.split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            deduped.append(cleaned)
    lines = ["", "## Next Search Suggestions", ""]
    if not deduped:
        return [*lines, "- No immediate follow-up search suggestions recorded.", ""]
    lines.extend(f"- {item}" for item in deduped[:12])
    lines.append("")
    return lines


def _report_payload(record: dict) -> dict:
    report_json = record.get("report_json")
    if isinstance(report_json, str) and report_json.strip():
        try:
            payload = json.loads(report_json)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return report_json if isinstance(report_json, dict) else {}


def _quality_summary(record: dict, payload: dict) -> dict:
    value = record.get("quality_summary") or payload.get("quality_summary") or {}
    return value if isinstance(value, dict) else {}


def _reference_checks(record: dict, payload: dict) -> dict:
    value = record.get("reference_checks") or payload.get("reference_checks") or {}
    return value if isinstance(value, dict) else {}


def _safe_relative_folder(folder: str) -> Path:
    cleaned = Path(folder.strip() or "AI Research")
    if cleaned.is_absolute() or any(part == ".." for part in cleaned.parts):
        raise ValueError("Obsidian folder must be relative to the vault")
    return cleaned


def _safe_filename(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "research"


def _yaml_scalar(value: object) -> str:
    if isinstance(value, int | float):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _md_cell(value: str) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


def _md_link(url: str) -> str:
    if not url:
        return ""
    safe = str(url).replace(")", "%29").replace("|", "%7C")
    return f"[link]({safe})"
