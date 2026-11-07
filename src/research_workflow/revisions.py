from __future__ import annotations

from .models import Claim, Evidence, RevisionSuggestion, Verdict


def suggest_revisions(claims: list[Claim], evidence: list[Evidence]) -> list[RevisionSuggestion]:
    evidence_by_id = {item.id: item for item in evidence}
    suggestions: list[RevisionSuggestion] = []
    for claim in claims:
        if claim.verdict == Verdict.SUPPORTED:
            continue
        matched = [evidence_by_id[item] for item in claim.evidence_ids if item in evidence_by_id]
        if claim.verdict == Verdict.CONTRADICTED:
            replacement = _replacement_from_evidence(matched) or f"Remove or rewrite unsupported claim: {claim.text}"
            suggestions.append(
                RevisionSuggestion(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict=claim.verdict,
                    action="replace",
                    failure_reason="Claim appears contradicted by available evidence.",
                    evidence_ids=claim.evidence_ids,
                    suggested_replacement=replacement,
                )
            )
        elif claim.verdict == Verdict.NOT_FOUND:
            suggestions.append(
                RevisionSuggestion(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict=claim.verdict,
                    action="needs_more_sources",
                    failure_reason="No local evidence met the verification threshold.",
                    evidence_ids=[],
                    suggested_replacement=f"Mark as unverified until a source is added: {claim.text}",
                )
            )
        elif claim.verdict == Verdict.PARTIAL:
            action = "keep_with_caveat" if claim.evidence_ids else "needs_more_sources"
            suggestions.append(
                RevisionSuggestion(
                    claim_id=claim.id,
                    claim_text=claim.text,
                    verdict=claim.verdict,
                    action=action,
                    failure_reason="Only partial or insufficiently independent evidence is available.",
                    evidence_ids=claim.evidence_ids,
                    suggested_replacement=f"Use cautious wording or add sources before stating this firmly: {claim.text}",
                )
            )
    return suggestions


def _replacement_from_evidence(evidence: list[Evidence]) -> str:
    for item in evidence:
        text = " ".join(item.quote_or_paraphrase.split())
        if text:
            return text[:240]
    return ""
