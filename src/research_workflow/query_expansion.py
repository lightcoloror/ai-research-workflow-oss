from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Claim


@dataclass(slots=True)
class ExpandedClaimQueries:
    claim_id: str
    claim: str
    queries: list[str] = field(default_factory=list)
    strategy: str = "fathom_hyde_light"

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "claim": self.claim,
            "queries": self.queries,
            "strategy": self.strategy,
        }


def expand_claim_queries(claim: Claim, *, max_queries: int = 5, include_academic: bool = False) -> ExpandedClaimQueries:
    core = _clean_claim(claim.text)
    entities = _entity_terms(core)
    queries = [
        core,
        f"{core} official source",
        f"{core} evidence",
        f"{core} contradiction OR correction",
        f"{core} fact check",
    ]
    if entities:
        queries.append(f"{' '.join(entities[:4])} official statement data")
    if include_academic or claim.risk_level == "high":
        queries.extend(
            [
                f"{core} paper benchmark dataset",
                f"{core} site:doi.org OR site:arxiv.org",
            ]
        )
    return ExpandedClaimQueries(claim_id=claim.id, claim=claim.text, queries=_dedupe(queries)[:max_queries])


def expand_claims(claims: list[Claim], *, max_queries_per_claim: int = 5, include_academic: bool = False) -> list[ExpandedClaimQueries]:
    return [expand_claim_queries(claim, max_queries=max_queries_per_claim, include_academic=include_academic) for claim in claims]


def _clean_claim(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = cleaned.strip(" .。")
    return cleaned


def _entity_terms(text: str) -> list[str]:
    tokens = re.findall(r"[A-Z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text)
    return _dedupe(tokens)


def _dedupe(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = " ".join(str(item).split())
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output
