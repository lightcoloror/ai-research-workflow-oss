from __future__ import annotations

from .models import Claim, Evidence, ReportInsight, Source, Verdict
from .quality import quality_for_report


def claim_citations_from_claims(
    claims: list[Claim],
    evidence: list[Evidence],
    sources: list[Source],
) -> list[ReportInsight]:
    evidence_by_id = {item.id: item for item in evidence if item.id}
    source_by_id = {source.id: source for source in sources if source.id}
    insights: list[ReportInsight] = []
    quality = quality_for_report(
        {
            "claim_table": [claim.to_dict() for claim in claims],
            "evidence": [item.to_dict() for item in evidence],
            "sources": [source.to_dict() for source in sources],
        }
    )
    quality_by_id = {str(item.get("claim_id") or ""): item for item in quality.get("claim_quality", [])}
    quality_by_text = {str(item.get("claim") or ""): item for item in quality.get("claim_quality", [])}
    for claim in claims:
        evidence_refs: list[str] = []
        source_ids: list[str] = []
        citations: list[str] = []
        for evidence_id in claim.evidence_ids:
            item = evidence_by_id.get(evidence_id)
            if not item:
                continue
            evidence_refs.append(evidence_id)
            if item.source_id and item.source_id not in source_ids:
                source_ids.append(item.source_id)
            source = source_by_id.get(item.source_id)
            citation = _citation_label(source, item.source_id)
            if citation and citation not in citations:
                citations.append(citation)
        quality_row = quality_by_id.get(claim.id) or quality_by_text.get(claim.text) or {}
        insights.append(
            ReportInsight(
                claim_id=claim.id,
                claim=claim.text,
                citations=citations,
                evidence_ids=evidence_refs,
                source_ids=source_ids,
                verdict=claim.verdict,
                confidence=claim.confidence,
                risk_level=claim.risk_level,
                citation_coverage=float(quality_row.get("citation_coverage") or 0.0),
                source_diversity=int(quality_row.get("source_diversity") or 0),
                evidence_strength=str(quality_row.get("evidence_strength") or "weak"),
            )
        )
    return insights


def _citation_label(source: Source | None, fallback_source_id: str = "") -> str:
    if not source:
        return fallback_source_id
    if source.url:
        return source.url
    return source.title or source.publisher or source.id or fallback_source_id
