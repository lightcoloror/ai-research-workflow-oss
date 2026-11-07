from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .models import Claim, Evidence, Verdict
from .store import stable_id


HIGH_RISK_PATTERNS = [
    r"\b\d{4}\b",
    r"\b\d+(\.\d+)?\s?%",
    r"\b(version|release|benchmark|cost|price|legal|medical|financial|safety)\b",
    r"(版本|发布|维护|停止|离线|价格|成本|法律|医学|金融|安全|审查)",
]

STOPWORDS = {
    "the",
    "and",
    "or",
    "to",
    "of",
    "in",
    "a",
    "is",
    "are",
    "for",
    "with",
    "that",
    "this",
    "can",
    "one",
    "一个",
    "可以",
    "以及",
    "但是",
    "因为",
    "如果",
    "这个",
    "相关",
}


def extract_atomic_claims(text: str) -> list[Claim]:
    cleaned = _strip_markdown_noise(text)
    candidates = re.split(r"(?<=[。！？.!?])\s+|\n+", cleaned)
    claims: list[Claim] = []
    for candidate in candidates:
        sentence = candidate.strip(" -\t\r\n")
        if not sentence or len(sentence) < 12:
            continue
        if _looks_like_heading(sentence):
            continue
        parts = _split_compound_claim(sentence)
        for part in parts:
            part = part.strip(" -;；,，")
            if _is_checkable_claim(part):
                risk = "high" if any(re.search(pattern, part, flags=re.I) for pattern in HIGH_RISK_PATTERNS) else "normal"
                claims.append(
                    Claim(
                        id=stable_id(part, prefix="cl_"),
                        text=part,
                        risk_level=risk,
                    )
                )
    return _dedupe_claims(claims)


def verify_claims(
    claims: list[Claim],
    evidence: list[Evidence],
    *,
    min_score: float = 0.18,
    high_risk_min_sources: int = 2,
) -> list[Claim]:
    verified: list[Claim] = []
    for claim in claims:
        scored = sorted(
            ((_similarity(claim.text, ev.quote_or_paraphrase), ev) for ev in evidence),
            key=lambda item: item[0],
            reverse=True,
        )
        relevant = [(score, ev) for score, ev in scored if score >= min_score]
        if not relevant:
            claim.verdict = Verdict.NOT_FOUND
            claim.confidence = 0.0
            claim.evidence_ids = []
        else:
            best_score = relevant[0][0]
            evidence_ids = [ev.id for _, ev in relevant[:3]]
            claim.evidence_ids = evidence_ids
            claim.confidence = min(0.95, round(best_score, 2))
            claim.verdict = _infer_verdict(claim.text, [ev.quote_or_paraphrase for _, ev in relevant[:3]], best_score)
            if claim.risk_level == "high" and claim.verdict == Verdict.SUPPORTED:
                independent_sources = _independent_source_count(relevant)
                if independent_sources < high_risk_min_sources:
                    claim.verdict = Verdict.PARTIAL
                    claim.confidence = min(claim.confidence, 0.54)
        verified.append(claim)
    return verified


def _strip_markdown_noise(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    lines: list[str] = []
    skip_section = False
    structural_sections = {
        "evidence table",
        "evidence notes",
        "source notes",
        "disputed or weak evidence",
        "not verified",
        "next search steps",
    }
    for line in text.splitlines():
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            title = heading.group(1).strip().lower()
            skip_section = title in structural_sections
            if skip_section:
                continue
        if skip_section:
            continue
        if stripped.startswith("|"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _looks_like_heading(sentence: str) -> bool:
    return sentence.startswith("#") or (len(sentence) < 80 and sentence.endswith(":"))


def _split_compound_claim(sentence: str) -> list[str]:
    if len(sentence) < 140:
        return [sentence]
    return [p for p in re.split(r"[；;]|\s+and\s+|\s+but\s+", sentence) if p.strip()]


def _is_checkable_claim(text: str) -> bool:
    if "?" in text or "？" in text:
        return False
    factual_markers = [
        r"\b(is|are|was|were|has|have|supports|uses|used|released|published|offline)\b",
        r"\b(host|hosts|break|breaks|retrieve|retrieves|revise|revises|detect|detects|evaluate|evaluates)\b",
        r"\b(provide|provides|offer|offers|expose|exposes|return|returns|store|stores|allow|allows|help|helps)\b",
        r"\b(collect|collects|need|needs|start|starts|useful|can)\b",
        r"\b(mark|marks|rank|ranks|preserve|preserves|continue|continues|require|requires|collapse|collapses)\b",
        r"\b(should|must)\b",
        r"(是|为|有|支持|使用|发布|停止|离线|维护|聚合|来自|属于)",
        r"\b\d{4}\b",
        r"https?://",
    ]
    return any(re.search(pattern, text, flags=re.I) for pattern in factual_markers)


def _dedupe_claims(claims: Iterable[Claim]) -> list[Claim]:
    seen: set[str] = set()
    deduped: list[Claim] = []
    for claim in claims:
        key = claim.text.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(claim)
    return deduped


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9][a-z0-9._-]+", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    tokens = latin + cjk
    return [token for token in tokens if token not in STOPWORDS and len(token) > 1]


def _similarity(a: str, b: str) -> float:
    a_tokens = Counter(_tokens(a))
    b_tokens = Counter(_tokens(b))
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = sum((a_tokens & b_tokens).values())
    return overlap / max(1, sum(a_tokens.values()))


def _infer_verdict(claim: str, evidence_texts: list[str], score: float) -> Verdict:
    joined = " ".join(evidence_texts).lower()
    claim_lower = claim.lower()
    contradiction_markers = ["no longer", "offline", "stopped", "停止", "离线", "不再", "并非"]
    stale_status_markers = ["still", "currently", "active", "best", "仍然", "目前", "活跃"]
    if any(marker in joined for marker in contradiction_markers) and any(marker in claim_lower for marker in stale_status_markers):
        if score >= 0.18:
            return Verdict.CONTRADICTED
    if any(marker in joined for marker in contradiction_markers) and not any(marker in claim_lower for marker in contradiction_markers):
        if score >= 0.35:
            return Verdict.CONTRADICTED
    if score >= 0.55:
        return Verdict.SUPPORTED
    return Verdict.PARTIAL


def _independent_source_count(relevant: list[tuple[float, Evidence]]) -> int:
    return len({ev.source_id for _, ev in relevant if ev.source_id})
