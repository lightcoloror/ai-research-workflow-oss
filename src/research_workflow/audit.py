from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .pipeline import get_run
from .quality import quality_for_report


@dataclass(slots=True)
class AuditCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.details is None:
            data.pop("details")
        return data


def audit_run(data_dir: Path, run_id: str) -> dict[str, Any]:
    record = get_run(data_dir, run_id)
    if not record:
        return {
            "run_id": run_id,
            "overall": "error",
            "checks": [AuditCheck("run_exists", "error", f"Run not found: {run_id}").to_dict()],
        }

    checks: list[AuditCheck] = [AuditCheck("run_exists", "ok", "Run record exists.")]
    report = _parse_report_json(record, checks)
    if report:
        checks.extend(
            [
                _check_counts(record, report),
                _check_report_file(record),
                _check_report_sections(record, report),
                _check_claim_evidence_links(report),
                _check_claim_citations(report),
                _check_evidence_source_links(report),
                _check_high_risk_policy(report),
                _check_findings_are_supported(report),
                _check_quality_summary(report),
            ]
        )
    return {
        "run_id": run_id,
        "topic": record.get("topic", ""),
        "overall": _overall_status(checks),
        "checks": [check.to_dict() for check in checks],
    }


def audit_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Run Audit: {result.get('topic') or result.get('run_id')}",
        "",
        f"Overall: `{result.get('overall', 'unknown')}`",
        "",
    ]
    for check in result.get("checks", []):
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**: {check.get('message')}")
    return "\n".join(lines) + "\n"


def _parse_report_json(record: dict, checks: list[AuditCheck]) -> dict | None:
    raw = record.get("report_json")
    if not raw:
        checks.append(AuditCheck("report_json", "error", "Run record has no report_json."))
        return None
    try:
        report = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception as exc:  # noqa: BLE001
        checks.append(AuditCheck("report_json", "error", f"report_json is invalid: {exc}"))
        return None
    if not isinstance(report, dict):
        checks.append(AuditCheck("report_json", "error", "report_json is not an object."))
        return None
    checks.append(AuditCheck("report_json", "ok", "report_json parsed successfully."))
    return report


def _check_counts(record: dict, report: dict) -> AuditCheck:
    claims = report.get("claim_table", []) or []
    sources = report.get("sources", []) or []
    evidence = report.get("evidence", []) or []
    expected = {
        "claim_count": len(claims),
        "source_count": len(sources),
        "evidence_count": len(evidence),
        "supported_count": sum(1 for claim in claims if claim.get("verdict") == "supported"),
        "partial_count": sum(1 for claim in claims if claim.get("verdict") == "partial"),
        "contradicted_count": sum(1 for claim in claims if claim.get("verdict") == "contradicted"),
        "not_found_count": sum(1 for claim in claims if claim.get("verdict") == "not_found"),
    }
    mismatches = {key: {"record": record.get(key), "actual": value} for key, value in expected.items() if record.get(key) != value}
    if mismatches:
        return AuditCheck("counts_match", "error", "Run counters do not match report_json.", mismatches)
    return AuditCheck("counts_match", "ok", "Run counters match report_json.")


def _check_report_file(record: dict) -> AuditCheck:
    report_path = str(record.get("report_path") or "").strip()
    if not report_path:
        return AuditCheck("report_file", "error", "Run record has no report_path.")
    path = Path(report_path)
    if not path.exists():
        return AuditCheck("report_file", "warn", "Report file path is recorded but not found.", {"path": report_path})
    return AuditCheck("report_file", "ok", "Report Markdown file exists.", {"path": str(path)})


def _check_report_sections(record: dict, report: dict) -> AuditCheck:
    markdown = str(record.get("report_markdown") or "")
    required = ["## Short Answer", "## Evidence Policy", "## Evidence Table", "## Claim Citations", "## Source Notes", "## Not Verified"]
    if report.get("evidence"):
        required.append("## Evidence Notes")
    missing = [section for section in required if section not in markdown]
    if missing:
        return AuditCheck("report_sections", "error", f"Report Markdown is missing sections: {', '.join(missing)}")
    return AuditCheck("report_sections", "ok", "Report Markdown contains required audit sections.")


def _check_claim_evidence_links(report: dict) -> AuditCheck:
    evidence_ids = {item.get("id") for item in report.get("evidence", []) if item.get("id")}
    broken: list[dict] = []
    unsupported_supported: list[str] = []
    for claim in report.get("claim_table", []) or []:
        refs = list(claim.get("evidence_ids") or [])
        missing_refs = [ref for ref in refs if ref not in evidence_ids]
        if missing_refs:
            broken.append({"claim": claim.get("id") or claim.get("text"), "missing_evidence": missing_refs})
        if claim.get("verdict") == "supported" and not refs:
            unsupported_supported.append(str(claim.get("text") or claim.get("id")))
    if broken or unsupported_supported:
        return AuditCheck(
            "claim_evidence_links",
            "error",
            "Claim evidence references are incomplete.",
            {"broken": broken, "supported_without_evidence": unsupported_supported},
        )
    return AuditCheck("claim_evidence_links", "ok", "Claim evidence references resolve.")


def _check_claim_citations(report: dict) -> AuditCheck:
    claims = report.get("claim_table", []) or []
    citation_rows = report.get("claim_citations", []) or []
    if claims and not citation_rows:
        return AuditCheck("claim_citations", "error", "Report has claims but no claim_citations table.")
    evidence_ids = {item.get("id") for item in report.get("evidence", []) or [] if item.get("id")}
    source_ids = {item.get("id") for item in report.get("sources", []) or [] if item.get("id")}
    claims_by_id = {claim.get("id"): claim for claim in claims if claim.get("id")}
    claims_by_text = {claim.get("text"): claim for claim in claims if claim.get("text")}
    broken: list[dict] = []
    unsupported_citations: list[str] = []
    for row in citation_rows:
        claim = claims_by_id.get(row.get("claim_id")) or claims_by_text.get(row.get("claim"))
        if not claim:
            broken.append({"claim_id": row.get("claim_id"), "claim": row.get("claim"), "reason": "claim not found"})
            continue
        missing_evidence = [ref for ref in row.get("evidence_ids", []) or [] if ref not in evidence_ids]
        missing_sources = [ref for ref in row.get("source_ids", []) or [] if ref not in source_ids]
        if missing_evidence or missing_sources:
            broken.append(
                {
                    "claim_id": row.get("claim_id"),
                    "missing_evidence": missing_evidence,
                    "missing_sources": missing_sources,
                }
            )
        if claim.get("verdict") == "supported" and (not row.get("evidence_ids") or not row.get("citations")):
            unsupported_citations.append(str(claim.get("id") or claim.get("text")))
    if broken or unsupported_citations:
        return AuditCheck(
            "claim_citations",
            "error",
            "Claim citation rows are incomplete.",
            {"broken": broken, "supported_without_citations": unsupported_citations},
        )
    return AuditCheck("claim_citations", "ok", "Claim citation rows resolve to claims, evidence, and sources.")


def _check_evidence_source_links(report: dict) -> AuditCheck:
    source_ids = {item.get("id") for item in report.get("sources", []) if item.get("id")}
    broken = [
        {"evidence": item.get("id"), "source_id": item.get("source_id")}
        for item in report.get("evidence", []) or []
        if item.get("source_id") not in source_ids
    ]
    if broken:
        return AuditCheck("evidence_source_links", "error", "Evidence records reference missing sources.", {"broken": broken})
    return AuditCheck("evidence_source_links", "ok", "Evidence source references resolve.")


def _check_high_risk_policy(report: dict) -> AuditCheck:
    evidence_by_id = {item.get("id"): item for item in report.get("evidence", []) or []}
    violations: list[dict] = []
    for claim in report.get("claim_table", []) or []:
        if claim.get("risk_level") != "high" or claim.get("verdict") != "supported":
            continue
        source_ids = {
            evidence_by_id.get(ref, {}).get("source_id")
            for ref in claim.get("evidence_ids", []) or []
            if evidence_by_id.get(ref, {}).get("source_id")
        }
        if len(source_ids) < 2:
            violations.append({"claim": claim.get("id") or claim.get("text"), "source_count": len(source_ids)})
    if violations:
        return AuditCheck("high_risk_policy", "error", "Supported high-risk claims need at least two independent sources.", {"violations": violations})
    return AuditCheck("high_risk_policy", "ok", "High-risk supported claims satisfy the independent-source rule.")


def _check_findings_are_supported(report: dict) -> AuditCheck:
    supported = {str(claim.get("text")) for claim in report.get("claim_table", []) or [] if claim.get("verdict") == "supported"}
    findings = [str(item) for item in report.get("findings", []) or []]
    unsupported_findings = [finding for finding in findings if finding not in supported]
    if unsupported_findings:
        return AuditCheck("findings_supported", "error", "Findings include claims not marked supported.", {"findings": unsupported_findings})
    return AuditCheck("findings_supported", "ok", "Every finding is backed by a supported claim.")


def _check_quality_summary(report: dict) -> AuditCheck:
    quality = quality_for_report(report)
    summary = quality.get("quality_summary", {})
    warnings: list[dict] = []
    uncited = summary.get("uncited_findings") or []
    if uncited:
        warnings.append({"kind": "uncited_findings", "items": uncited})
    single_source = [
        item
        for item in quality.get("claim_quality", [])
        if item.get("verdict") == "supported" and item.get("risk_level") == "high" and int(item.get("source_diversity") or 0) < 2
    ]
    if single_source:
        warnings.append({"kind": "single_source_supported_claims", "items": single_source})
    weak_evidence = summary.get("weak_evidence") or []
    if weak_evidence:
        warnings.append({"kind": "weak_evidence_text", "items": weak_evidence})
    if warnings:
        return AuditCheck("evidence_quality", "warn", "Evidence quality has advisory gaps.", {"warnings": warnings, "quality_summary": summary})
    return AuditCheck("evidence_quality", "ok", "Evidence quality summary has no advisory gaps.", {"quality_summary": summary})


def _overall_status(checks: list[AuditCheck]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"
