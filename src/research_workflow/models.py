from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class SourceType(StrEnum):
    OFFICIAL = "official"
    ACADEMIC = "academic"
    INSTITUTIONAL = "institutional"
    PROJECT = "project"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    NOT_FOUND = "not_found"


@dataclass(slots=True)
class Source:
    url: str
    title: str = ""
    source_type: SourceType = SourceType.UNKNOWN
    publisher: str = ""
    published_at: str = ""
    retrieved_at: str = field(default_factory=now_iso)
    search_engine: str = "manual"
    snippet: str = ""
    id: str = ""
    canonical_url: str = ""
    duplicate_count: int = 1
    merged_urls: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    search_buckets: list[str] = field(default_factory=list)
    search_purposes: list[str] = field(default_factory=list)
    search_stages: list[str] = field(default_factory=list)
    quality_score: int = 0
    quality_reasons: list[str] = field(default_factory=list)
    artifact_id: str = ""
    artifact_type: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_type"] = str(self.source_type)
        return data


@dataclass(slots=True)
class Evidence:
    source_id: str
    quote_or_paraphrase: str
    claim_relevance: str = ""
    support_direction: Verdict = Verdict.NOT_FOUND
    confidence: float = 0.0
    id: str = ""
    extraction_method: str = ""
    text_length: int = 0
    source_quote_type: str = ""
    evidence_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["support_direction"] = str(self.support_direction)
        return data


@dataclass(slots=True)
class Claim:
    text: str
    claim_type: str = "factual"
    risk_level: str = "normal"
    verdict: Verdict = Verdict.NOT_FOUND
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = str(self.verdict)
        return data


@dataclass(slots=True)
class RevisionSuggestion:
    claim_id: str
    claim_text: str
    verdict: Verdict
    action: str
    failure_reason: str
    evidence_ids: list[str] = field(default_factory=list)
    suggested_replacement: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = str(self.verdict)
        return data


@dataclass(slots=True)
class ReportInsight:
    claim_id: str
    claim: str
    citations: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    verdict: Verdict = Verdict.NOT_FOUND
    confidence: float = 0.0
    risk_level: str = "normal"
    citation_coverage: float = 0.0
    source_diversity: int = 0
    evidence_strength: str = "weak"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["verdict"] = str(self.verdict)
        return data


@dataclass(slots=True)
class QueryPlan:
    topic: str
    mode: str
    core_question: str
    subquestions: list[str]
    zh_keywords: list[str]
    en_keywords: list[str]
    source_targets: list[str]
    high_risk_watchlist: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Report:
    topic: str
    summary: str
    findings: list[str]
    claim_table: list[Claim]
    gaps: list[str]
    sources: list[Source]
    evidence: list[Evidence] = field(default_factory=list)
    claim_citations: list[ReportInsight] = field(default_factory=list)
    claim_deepen_attempts: list[dict[str, Any]] = field(default_factory=list)
    deepen_suggestions: list[dict[str, Any]] = field(default_factory=list)
    quality_summary: dict[str, Any] = field(default_factory=dict)
    reference_checks: dict[str, Any] = field(default_factory=dict)
    revision_suggestions: list[RevisionSuggestion] = field(default_factory=list)
    generated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "findings": self.findings,
            "claim_table": [claim.to_dict() for claim in self.claim_table],
            "gaps": self.gaps,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "claim_citations": [item.to_dict() for item in self.claim_citations],
            "claim_deepen_attempts": self.claim_deepen_attempts,
            "deepen_suggestions": self.deepen_suggestions,
            "quality_summary": self.quality_summary,
            "reference_checks": self.reference_checks,
            "revision_suggestions": [item.to_dict() for item in self.revision_suggestions],
            "generated_at": self.generated_at,
        }
