from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Claim, Evidence, Source, Verdict
from .query_expansion import expand_claim_queries
from .research_integrity import evaluate_research_integrity


def run_quality(data_dir: Path, run_id: str) -> dict[str, Any]:
    from .pipeline import get_run

    record = get_run(data_dir, run_id)
    if not record:
        return {"run_id": run_id, "overall": "error", "error": f"Run not found: {run_id}"}
    report = _report_from_record(record)
    result = quality_for_report(report)
    result["run_id"] = run_id
    result["topic"] = record.get("topic", report.get("topic", ""))
    return result


def quality_for_report(report: dict[str, Any]) -> dict[str, Any]:
    claims = list(report.get("claim_table") or report.get("claims") or [])
    evidence = list(report.get("evidence") or [])
    sources = list(report.get("sources") or [])
    citation_rows = list(report.get("claim_citations") or [])
    findings = [str(item) for item in report.get("findings", []) or []]

    evidence_by_id = {str(item.get("id")): item for item in evidence if item.get("id")}
    source_by_id = {str(item.get("id")): item for item in sources if item.get("id")}
    citation_by_claim = _citation_rows_by_claim(citation_rows)
    claim_quality: list[dict[str, Any]] = []

    for claim in claims:
        row = citation_by_claim.get(str(claim.get("id") or "")) or citation_by_claim.get(str(claim.get("text") or "")) or {}
        evidence_ids = [str(item) for item in (row.get("evidence_ids") or claim.get("evidence_ids") or []) if item]
        source_ids = _source_ids_for_evidence(evidence_ids, evidence_by_id)
        if row.get("source_ids"):
            source_ids = _dedupe([*source_ids, *[str(item) for item in row.get("source_ids") or [] if item]])
        text_lengths = [_evidence_text_length(evidence_by_id.get(item, {})) for item in evidence_ids]
        citations = list(row.get("citations") or [])
        verdict = str(claim.get("verdict") or "not_found")
        citation_coverage = _citation_coverage(verdict, evidence_ids, citations)
        strength = _evidence_strength(
            verdict=verdict,
            confidence=float(claim.get("confidence") or 0),
            evidence_ids=evidence_ids,
            source_ids=source_ids,
            text_lengths=text_lengths,
        )
        claim_quality.append(
            {
                "claim_id": claim.get("id", ""),
                "claim": claim.get("text", ""),
                "verdict": verdict,
                "risk_level": claim.get("risk_level", "normal"),
                "confidence": float(claim.get("confidence") or 0),
                "citation_coverage": citation_coverage,
                "source_diversity": len(set(source_ids)),
                "evidence_strength": strength,
                "evidence_ids": evidence_ids,
                "source_ids": source_ids,
                "citations": citations,
                "weak_reasons": _weak_reasons(
                    verdict=verdict,
                    risk_level=str(claim.get("risk_level") or "normal"),
                    evidence_ids=evidence_ids,
                    source_ids=source_ids,
                    text_lengths=text_lengths,
                    citations=citations,
                ),
            }
        )

    finding_quality = _finding_quality(findings, claim_quality)
    weak_evidence = _weak_evidence_rows(evidence, source_by_id)
    summary = {
        "overall": _overall(claim_quality, finding_quality, weak_evidence),
        "claim_count": len(claim_quality),
        "finding_count": len(findings),
        "finding_citation_coverage": _ratio(sum(1 for item in finding_quality if item["citation_coverage"] > 0), len(finding_quality)),
        "supported_claims": sum(1 for item in claim_quality if item["verdict"] == "supported"),
        "weak_claims": sum(1 for item in claim_quality if item["evidence_strength"] == "weak"),
        "single_source_claims": sum(1 for item in claim_quality if item["verdict"] == "supported" and item["source_diversity"] < 2),
        "uncited_findings": [item for item in finding_quality if item["citation_coverage"] <= 0],
        "weak_evidence": weak_evidence,
        "recommendations": _recommendations(claim_quality, finding_quality, weak_evidence),
        "integrity_gate": evaluate_research_integrity(report),
    }
    return {
        "overall": summary["overall"],
        "quality_summary": summary,
        "claim_quality": claim_quality,
        "finding_quality": finding_quality,
    }


def deepen_claim_suggestions_for_run(data_dir: Path, run_id: str, *, max_queries: int = 5) -> dict[str, Any]:
    from .pipeline import get_run

    record = get_run(data_dir, run_id)
    if not record:
        return {"run_id": run_id, "overall": "error", "error": f"Run not found: {run_id}", "deepen_suggestions": []}
    report = _report_from_record(record)
    quality = quality_for_report(report)
    suggestions = deepen_claim_suggestions(report, quality, max_queries=max_queries)
    return {
        "run_id": run_id,
        "topic": record.get("topic", report.get("topic", "")),
        "overall": "ok",
        "deepen_suggestions": suggestions,
    }


def deepen_claim_suggestions(report: dict[str, Any], quality: dict[str, Any] | None = None, *, max_queries: int = 5) -> list[dict[str, Any]]:
    quality = quality or quality_for_report(report)
    quality_by_id = {str(item.get("claim_id") or ""): item for item in quality.get("claim_quality", [])}
    quality_by_text = {str(item.get("claim") or ""): item for item in quality.get("claim_quality", [])}
    suggestions: list[dict[str, Any]] = []
    mode = str(report.get("mode") or ("academic" if _looks_academic(report) else "daily"))
    for row in report.get("claim_table", []) or report.get("claims", []) or []:
        claim_quality = quality_by_id.get(str(row.get("id") or "")) or quality_by_text.get(str(row.get("text") or "")) or {}
        verdict = str(row.get("verdict") or "not_found")
        risk_level = str(row.get("risk_level") or "normal")
        should_deepen = verdict in {"partial", "not_found"} or (risk_level == "high" and int(claim_quality.get("source_diversity") or 0) < 2)
        if not should_deepen:
            continue
        claim = Claim(
            text=str(row.get("text") or ""),
            claim_type=str(row.get("claim_type") or "factual"),
            risk_level=risk_level,
            verdict=Verdict(verdict) if verdict in {str(item) for item in Verdict} else Verdict.NOT_FOUND,
            confidence=float(row.get("confidence") or 0),
            evidence_ids=list(row.get("evidence_ids") or []),
            id=str(row.get("id") or ""),
        )
        expanded = expand_claim_queries(claim, max_queries=max_queries, include_academic=mode == "academic")
        suggestions.append(
            {
                "claim_id": claim.id,
                "claim": claim.text,
                "verdict": verdict,
                "risk_level": risk_level,
                "missing_evidence_reason": _missing_evidence_reason(claim_quality, verdict, risk_level),
                "recommended_source_types": _recommended_source_types(claim, mode),
                "next_search_queries": expanded.queries,
                "query_strategy": expanded.strategy,
            }
        )
    return suggestions


def quality_to_markdown(result: dict[str, Any]) -> str:
    summary = result.get("quality_summary", {})
    lines = [
        f"# Evidence Quality: {result.get('topic') or result.get('run_id', '')}",
        "",
        f"Overall: `{result.get('overall', 'unknown')}`",
        "",
        "## Summary",
        "",
        f"- Claims: {summary.get('claim_count', 0)}",
        f"- Findings: {summary.get('finding_count', 0)}",
        f"- Finding citation coverage: {summary.get('finding_citation_coverage', 0):.2f}",
        f"- Weak claims: {summary.get('weak_claims', 0)}",
        f"- Single-source supported claims: {summary.get('single_source_claims', 0)}",
        "",
        "## Claim Quality",
        "",
        "| Claim | Verdict | Coverage | Sources | Strength | Weak Reasons |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in result.get("claim_quality", []):
        lines.append(
            f"| {_cell(item.get('claim', ''))} | {item.get('verdict', '')} | {float(item.get('citation_coverage') or 0):.2f} | {item.get('source_diversity', 0)} | {item.get('evidence_strength', '')} | {_cell('; '.join(item.get('weak_reasons') or []) or 'none')} |"
        )
    lines.extend(["", "## Recommendations", ""])
    recommendations = summary.get("recommendations") or []
    lines.extend(f"- {item}" for item in recommendations)
    if not recommendations:
        lines.append("- No immediate quality gaps detected.")
    return "\n".join(lines) + "\n"


def deepen_suggestions_to_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Claim Deepen Suggestions: {result.get('topic') or result.get('run_id', '')}", ""]
    suggestions = result.get("deepen_suggestions", []) or []
    if not suggestions:
        lines.append("- No deepen suggestions. Current claims are either supported or contradicted with enough evidence.")
        return "\n".join(lines) + "\n"
    for item in suggestions:
        lines.append(f"## {item.get('claim')}")
        lines.append("")
        lines.append(f"- Verdict: `{item.get('verdict')}`")
        lines.append(f"- Risk: `{item.get('risk_level')}`")
        lines.append(f"- Missing evidence: {item.get('missing_evidence_reason')}")
        lines.append(f"- Recommended sources: {', '.join(item.get('recommended_source_types') or [])}")
        lines.append("- Next queries:")
        lines.extend(f"  - {query}" for query in item.get("next_search_queries") or [])
        lines.append("")
    return "\n".join(lines) + "\n"


def _report_from_record(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("report_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    if isinstance(raw, dict):
        return raw
    return record


def _citation_rows_by_claim(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("claim_id"):
            output[str(row.get("claim_id"))] = row
        if row.get("claim"):
            output[str(row.get("claim"))] = row
    return output


def _source_ids_for_evidence(evidence_ids: list[str], evidence_by_id: dict[str, dict[str, Any]]) -> list[str]:
    return _dedupe(str(evidence_by_id.get(item, {}).get("source_id") or "") for item in evidence_ids)


def _evidence_text_length(item: dict[str, Any]) -> int:
    explicit = int(item.get("text_length") or 0)
    if explicit:
        return explicit
    return len(str(item.get("quote_or_paraphrase") or "").strip())


def _citation_coverage(verdict: str, evidence_ids: list[str], citations: list[str]) -> float:
    if verdict not in {"supported", "partial", "contradicted"}:
        return 0.0
    if not evidence_ids:
        return 0.0
    if citations:
        return 1.0
    return 0.5


def _evidence_strength(*, verdict: str, confidence: float, evidence_ids: list[str], source_ids: list[str], text_lengths: list[int]) -> str:
    if verdict == "supported" and len(set(source_ids)) >= 2 and max(text_lengths or [0]) >= 80:
        return "strong"
    if evidence_ids and max(text_lengths or [0]) >= 40 and confidence >= 0.4:
        return "moderate"
    return "weak"


def _weak_reasons(*, verdict: str, risk_level: str, evidence_ids: list[str], source_ids: list[str], text_lengths: list[int], citations: list[str]) -> list[str]:
    reasons: list[str] = []
    if verdict in {"supported", "partial", "contradicted"} and not citations:
        reasons.append("missing_citation")
    if not evidence_ids:
        reasons.append("no_evidence")
    if evidence_ids and max(text_lengths or [0]) < 40:
        reasons.append("evidence_text_too_short")
    if verdict == "supported" and len(set(source_ids)) < 2:
        reasons.append("single_source")
    if risk_level == "high" and len(set(source_ids)) < 2:
        reasons.append("high_risk_needs_two_sources")
    return reasons


def _finding_quality(findings: list[str], claim_quality: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_claim = {str(item.get("claim")): item for item in claim_quality}
    output: list[dict[str, Any]] = []
    for finding in findings:
        item = by_claim.get(finding)
        if not item:
            output.append({"finding": finding, "citation_coverage": 0.0, "source_diversity": 0, "evidence_strength": "weak", "weak_reasons": ["finding_not_in_claim_table"]})
            continue
        output.append(
            {
                "finding": finding,
                "citation_coverage": item.get("citation_coverage", 0.0),
                "source_diversity": item.get("source_diversity", 0),
                "evidence_strength": item.get("evidence_strength", "weak"),
                "weak_reasons": item.get("weak_reasons", []),
            }
        )
    return output


def _weak_evidence_rows(evidence: list[dict[str, Any]], source_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in evidence:
        text = str(item.get("quote_or_paraphrase") or "").strip()
        source = source_by_id.get(str(item.get("source_id") or ""), {})
        reasons = []
        if len(text) < 40:
            reasons.append("short_text")
        if text.startswith("http://") or text.startswith("https://") or (not text and source.get("url")):
            reasons.append("url_only")
        if reasons:
            rows.append({"evidence_id": item.get("id", ""), "source_id": item.get("source_id", ""), "reasons": reasons})
    return rows


def _overall(claim_quality: list[dict[str, Any]], finding_quality: list[dict[str, Any]], weak_evidence: list[dict[str, Any]]) -> str:
    if any(item.get("weak_reasons") for item in finding_quality):
        return "warn"
    if any(item.get("verdict") == "supported" and item.get("evidence_strength") == "weak" for item in claim_quality):
        return "warn"
    if weak_evidence:
        return "warn"
    return "ok"


def _recommendations(claim_quality: list[dict[str, Any]], finding_quality: list[dict[str, Any]], weak_evidence: list[dict[str, Any]]) -> list[str]:
    recommendations: list[str] = []
    if any(item.get("citation_coverage", 0) <= 0 for item in finding_quality):
        recommendations.append("Add citations or downgrade uncited findings to gaps.")
    if any("single_source" in (item.get("weak_reasons") or []) for item in claim_quality):
        recommendations.append("Find an independent second source for single-source supported claims.")
    if weak_evidence:
        recommendations.append("Replace short or URL-only evidence rows with quote/paraphrase snippets.")
    if any(item.get("verdict") in {"partial", "not_found"} for item in claim_quality):
        recommendations.append("Run deepen-claims for partial and not_found claims.")
    return _dedupe(recommendations)


def _missing_evidence_reason(claim_quality: dict[str, Any], verdict: str, risk_level: str) -> str:
    reasons = list(claim_quality.get("weak_reasons") or [])
    if verdict == "not_found":
        reasons.append("no_supporting_source_found")
    if verdict == "partial":
        reasons.append("evidence_only_partially_supports_claim")
    if risk_level == "high" and "high_risk_needs_two_sources" not in reasons:
        reasons.append("high_risk_needs_two_sources")
    return "; ".join(_dedupe(reasons)) or "additional independent evidence would improve confidence"


def _recommended_source_types(claim: Claim, mode: str) -> list[str]:
    if mode == "academic":
        return ["paper", "official dataset", "DOI/arXiv metadata", "institutional report"]
    if claim.risk_level == "high":
        return ["official source", "independent institution", "primary document"]
    return ["official source", "project documentation", "reputable secondary source"]


def _looks_academic(report: dict[str, Any]) -> bool:
    text = json.dumps(report, ensure_ascii=False).lower()
    return "doi" in text or "arxiv" in text or "academic" in text


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)


def _dedupe(items: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _cell(value: object) -> str:
    return " ".join(str(value).replace("|", "\\|").split())
