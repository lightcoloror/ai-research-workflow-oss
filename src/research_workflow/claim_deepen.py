from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .evidence import extract_evidence_from_sources
from .factcheck import verify_claims
from .models import Claim, Evidence, Source, Verdict
from .query_expansion import expand_claim_queries
from .search import SearchError, multi_source_search
from .sparse_retrieval import retrieve_evidence_bm25


@dataclass(slots=True)
class ClaimDeepenAttempt:
    claim_id: str
    claim: str
    round_index: int
    query: str
    before_verdict: Verdict
    after_verdict: Verdict
    new_source_ids: list[str] = field(default_factory=list)
    new_evidence_ids: list[str] = field(default_factory=list)
    warning: str = ""

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "round_index": self.round_index,
            "query": self.query,
            "before_verdict": str(self.before_verdict),
            "after_verdict": str(self.after_verdict),
            "new_source_ids": self.new_source_ids,
            "new_evidence_ids": self.new_evidence_ids,
            "warning": self.warning,
        }


@dataclass(slots=True)
class ClaimDeepenResult:
    claims: list[Claim]
    sources: list[Source]
    evidence: list[Evidence]
    attempts: list[ClaimDeepenAttempt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def deepen_uncertain_claims(
    *,
    claims: list[Claim],
    sources: list[Source],
    evidence: list[Evidence],
    mode: str = "daily",
    max_rounds: int = 1,
    max_results: int = 3,
    no_network: bool = False,
    fetch_urls: bool = False,
    searxng_url: str = "",
    search_providers: list[str] | None = None,
    stop_verdicts: set[Verdict] | None = None,
    expand_queries: bool = True,
    local_sparse_first: bool = True,
) -> ClaimDeepenResult:
    if max_rounds <= 0:
        return ClaimDeepenResult(claims=claims, sources=sources, evidence=evidence)
    stop_verdicts = stop_verdicts or {Verdict.SUPPORTED, Verdict.CONTRADICTED}
    working_claims = [deepcopy(claim) for claim in claims]
    working_sources = list(sources)
    working_evidence = list(evidence)
    attempts: list[ClaimDeepenAttempt] = []
    warnings: list[str] = []
    for claim_index, claim in enumerate(working_claims):
        if claim.verdict in stop_verdicts:
            continue
        expanded = expand_claim_queries(claim, max_queries=max_rounds, include_academic=mode == "academic") if expand_queries else None
        for round_index in range(1, max_rounds + 1):
            before = claim.verdict
            query = expanded.queries[round_index - 1] if expanded and len(expanded.queries) >= round_index else _query_for_claim(claim, round_index)
            if local_sparse_first and working_evidence:
                local_matches = retrieve_evidence_bm25(query, working_evidence, limit=max_results)
                if local_matches:
                    local_evidence = [match.evidence for match in local_matches]
                    local_claim = verify_claims([claim], local_evidence)[0]
                    if local_claim.verdict in stop_verdicts:
                        claim = local_claim
                        working_claims[claim_index] = claim
                        attempts.append(
                            ClaimDeepenAttempt(
                                claim_id=claim.id,
                                claim=claim.text,
                                round_index=round_index,
                                query=query,
                                before_verdict=before,
                                after_verdict=claim.verdict,
                                new_evidence_ids=[item.id for item in local_evidence if item.id],
                                warning="local_sparse_retrieval",
                            )
                        )
                        break
            try:
                new_sources = multi_source_search(
                    query,
                    mode=mode,
                    max_results=max_results,
                    no_network=no_network,
                    searxng_url=searxng_url,
                    providers=search_providers,
                )
            except SearchError as exc:
                warning = f"claim deepen search failed for {claim.id or claim.text}: {exc}"
                warnings.append(warning)
                attempts.append(
                    ClaimDeepenAttempt(
                        claim_id=claim.id,
                        claim=claim.text,
                        round_index=round_index,
                        query=query,
                        before_verdict=before,
                        after_verdict=claim.verdict,
                        warning=warning,
                    )
                )
                break
            new_sources = _dedupe_sources(new_sources, existing=working_sources)
            new_evidence = extract_evidence_from_sources(new_sources, fetch=fetch_urls) if new_sources else []
            working_sources = _merge_sources(working_sources, new_sources)
            working_evidence = _merge_evidence(working_evidence, new_evidence)
            claim = verify_claims([claim], working_evidence)[0]
            working_claims[claim_index] = claim
            attempts.append(
                ClaimDeepenAttempt(
                    claim_id=claim.id,
                    claim=claim.text,
                    round_index=round_index,
                    query=query,
                    before_verdict=before,
                    after_verdict=claim.verdict,
                    new_source_ids=[source.id for source in new_sources],
                    new_evidence_ids=[item.id for item in new_evidence],
                )
            )
            if claim.verdict in stop_verdicts:
                break
    return ClaimDeepenResult(
        claims=working_claims,
        sources=working_sources,
        evidence=working_evidence,
        attempts=attempts,
        warnings=warnings,
    )


def _query_for_claim(claim: Claim, round_index: int) -> str:
    suffix = " evidence source official documentation" if round_index == 1 else " independent source contradiction"
    return f"{claim.text} {suffix}".strip()


def _dedupe_sources(sources: list[Source], *, existing: list[Source]) -> list[Source]:
    seen = {source.id or source.url for source in existing}
    output: list[Source] = []
    for source in sources:
        key = source.id or source.url
        if key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def _merge_sources(existing: list[Source], incoming: list[Source]) -> list[Source]:
    return [*existing, *_dedupe_sources(incoming, existing=existing)]


def _merge_evidence(existing: list[Evidence], incoming: list[Evidence]) -> list[Evidence]:
    seen = {item.id for item in existing}
    merged = list(existing)
    for item in incoming:
        if item.id in seen:
            continue
        seen.add(item.id)
        merged.append(item)
    return merged
