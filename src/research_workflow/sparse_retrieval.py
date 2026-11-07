from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .factcheck import _tokens
from .models import Evidence


@dataclass(slots=True)
class BM25EvidenceMatch:
    evidence: Evidence
    score: float
    query: str

    def to_dict(self) -> dict:
        return {
            "evidence": self.evidence.to_dict(),
            "score": self.score,
            "query": self.query,
        }


def retrieve_evidence_bm25(
    query: str,
    evidence: list[Evidence],
    *,
    limit: int = 5,
    min_score: float = 0.1,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[BM25EvidenceMatch]:
    documents = [_tokens(item.quote_or_paraphrase) for item in evidence]
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    average_length = sum(len(document) for document in documents) / max(1, len(documents))
    document_frequency = _document_frequency(documents)
    matches: list[BM25EvidenceMatch] = []
    for item, document in zip(evidence, documents, strict=True):
        score = _bm25_score(
            query_terms=query_terms,
            document=document,
            document_frequency=document_frequency,
            document_count=len(documents),
            average_length=average_length,
            k1=k1,
            b=b,
        )
        if score >= min_score:
            matches.append(BM25EvidenceMatch(evidence=item, score=round(score, 4), query=query))
    return sorted(matches, key=lambda item: item.score, reverse=True)[:limit]


def retrieve_evidence_for_queries(
    queries: list[str],
    evidence: list[Evidence],
    *,
    limit: int = 5,
    min_score: float = 0.1,
) -> list[BM25EvidenceMatch]:
    output: list[BM25EvidenceMatch] = []
    seen: set[str] = set()
    for query in queries:
        for match in retrieve_evidence_bm25(query, evidence, limit=limit, min_score=min_score):
            key = match.evidence.id or match.evidence.quote_or_paraphrase
            if key in seen:
                continue
            seen.add(key)
            output.append(match)
    return sorted(output, key=lambda item: item.score, reverse=True)[:limit]


def _document_frequency(documents: list[list[str]]) -> Counter[str]:
    frequencies: Counter[str] = Counter()
    for document in documents:
        frequencies.update(set(document))
    return frequencies


def _bm25_score(
    *,
    query_terms: list[str],
    document: list[str],
    document_frequency: Counter[str],
    document_count: int,
    average_length: float,
    k1: float,
    b: float,
) -> float:
    if not document:
        return 0.0
    term_frequency = Counter(document)
    document_length = len(document)
    score = 0.0
    for term in query_terms:
        frequency = term_frequency.get(term, 0)
        if frequency <= 0:
            continue
        idf = math.log(1 + (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        denominator = frequency + k1 * (1 - b + b * document_length / max(1.0, average_length))
        score += idf * (frequency * (k1 + 1)) / denominator
    return score
