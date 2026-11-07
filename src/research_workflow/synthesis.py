from __future__ import annotations

import re

from .models import Evidence


def synthesize_claim_text(topic: str, evidence: list[Evidence], *, max_claims: int = 8) -> str:
    """Create an extractive, evidence-backed draft for verification."""
    candidates: list[str] = []
    topic_tokens = _topic_tokens(topic)
    for item in evidence:
        for sentence in _sentences(item.quote_or_paraphrase):
            if not _looks_useful(sentence):
                continue
            if topic_tokens and not any(token in sentence.lower() for token in topic_tokens):
                continue
            candidates.append(sentence)
            if len(candidates) >= max_claims:
                break
        if len(candidates) >= max_claims:
            break

    lines = [f"# {topic}", "", "## Evidence-backed candidate claims", ""]
    if candidates:
        lines.extend(f"- {candidate}" for candidate in _dedupe(candidates))
    else:
        lines.append("- No evidence-backed candidate claims could be synthesized yet.")
    return "\n".join(lines) + "\n"


def _sentences(text: str) -> list[str]:
    normalized = text.replace("\xa0", " ").replace("\u2060", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+([.!?。！？])", r"\1", normalized)
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+", normalized)
    return [part.strip(" -") for part in parts if part.strip(" -")]


def _looks_useful(sentence: str) -> bool:
    if len(sentence) < 24:
        return False
    if "..." in sentence or "·" in sentence:
        return False
    if sentence.lower().startswith("manual source recorded"):
        return False
    if sentence[0].islower():
        return False
    if len(sentence.split()) < 6:
        return False
    factual_markers = [
        r"\b(is|are|was|were|has|have|supports|uses|aggregates|provides|includes|evaluates|forked)\b",
        r"(是|为|有|支持|使用|聚合|提供|包含|评估|来自|属于)",
        r"\b\d{4}\b",
    ]
    return any(re.search(pattern, sentence, flags=re.I) for pattern in factual_markers)


def _topic_tokens(topic: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[a-z0-9][a-z0-9._-]+|[\u4e00-\u9fff]{2,}", topic.lower())
        if len(token) > 1
    ][:5]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
