from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evidence import source_from_url
from .models import Claim, Evidence, Source, SourceType, Verdict
from .search import infer_publisher, infer_source_type
from .store import stable_id


@dataclass(slots=True)
class ExternalAdapterRecords:
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    text_to_verify: str = ""
    warnings: list[str] = field(default_factory=list)


def load_external_payload(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("external payload must be a JSON object")
    return payload


def load_external_payloads(paths: list[str] | None) -> list[dict[str, Any]]:
    return [load_external_payload(path) for path in paths or []]


def normalize_external_payload(payload: dict[str, Any], *, topic: str = "", adapter_name: str = "") -> ExternalAdapterRecords:
    if not isinstance(payload, dict):
        raise ValueError("external payload must be a JSON object")
    adapter = _clean(payload.get("adapter") or payload.get("tool") or adapter_name or "external")
    records = ExternalAdapterRecords()
    sources_by_url: dict[str, Source] = {}
    sources_by_external_id: dict[str, Source] = {}

    for row in _list(payload.get("sources")):
        if not isinstance(row, dict):
            records.warnings.append(f"{adapter}: skipped non-object source")
            continue
        source = _source_from_external(row, topic=topic, adapter=adapter)
        if not source:
            records.warnings.append(f"{adapter}: skipped source without url")
            continue
        records.sources.append(source)
        sources_by_url[source.url] = source
        external_id = _clean(row.get("id") or row.get("source_id"))
        if external_id:
            sources_by_external_id[external_id] = source

    for row in _list(payload.get("evidence")):
        if not isinstance(row, dict):
            records.warnings.append(f"{adapter}: skipped non-object evidence")
            continue
        source = _source_for_evidence(row, topic=topic, adapter=adapter, sources_by_url=sources_by_url, sources_by_external_id=sources_by_external_id)
        text = _clean(row.get("quote_or_paraphrase") or row.get("quote") or row.get("text") or row.get("snippet") or row.get("content"))
        if not source or not text:
            records.warnings.append(f"{adapter}: skipped evidence without source or text")
            continue
        if source not in records.sources:
            records.sources.append(source)
        records.evidence.append(
            Evidence(
                id=_clean(row.get("id")) or stable_id(adapter, source.id, text, prefix="ev_"),
                source_id=source.id,
                quote_or_paraphrase=text,
                claim_relevance=_clean(row.get("claim_relevance") or row.get("relevance") or "external adapter evidence"),
                support_direction=_verdict(row.get("support_direction") or row.get("verdict"), Verdict.PARTIAL),
                confidence=_confidence(row.get("confidence"), 0.7),
                extraction_method=f"external:{adapter}",
                text_length=len(text),
                source_quote_type=_clean(row.get("source_quote_type") or "external"),
            )
        )

    for row in _list(payload.get("claims")):
        if not isinstance(row, dict):
            records.warnings.append(f"{adapter}: skipped non-object claim")
            continue
        text = _clean(row.get("text") or row.get("claim"))
        if not text:
            records.warnings.append(f"{adapter}: skipped claim without text")
            continue
        records.claims.append(
            Claim(
                id=_clean(row.get("id")) or stable_id(adapter, text, prefix="claim_"),
                text=text,
                claim_type=_clean(row.get("claim_type") or "factual"),
                risk_level=_clean(row.get("risk_level") or "normal"),
                verdict=_verdict(row.get("verdict"), Verdict.NOT_FOUND),
                confidence=_confidence(row.get("confidence"), 0.0),
                evidence_ids=[_clean(item) for item in _list(row.get("evidence_ids")) if _clean(item)],
            )
        )

    records.text_to_verify = _external_text_to_verify(payload, records.claims)
    return records


def _source_from_external(row: dict[str, Any], *, topic: str, adapter: str) -> Source | None:
    url = _clean(row.get("url") or row.get("link"))
    if not url:
        return None
    source = source_from_url(
        url,
        search_queries=[topic] if topic else [],
        search_buckets=[_clean(row.get("bucket") or "external adapter")],
        search_purposes=[_clean(row.get("purpose") or "external reused project result")],
        search_stages=[f"external:{adapter}"],
    )
    source.id = _clean(row.get("source_id") or row.get("id")) or source.id
    source.title = _clean(row.get("title")) or source.title
    source.source_type = _source_type(row.get("source_type"), url)
    source.publisher = _clean(row.get("publisher")) or infer_publisher(url)
    source.published_at = _clean(row.get("published_at") or row.get("date"))
    source.search_engine = adapter
    source.snippet = _clean(row.get("snippet") or row.get("summary") or row.get("description"))
    return source


def _source_for_evidence(
    row: dict[str, Any],
    *,
    topic: str,
    adapter: str,
    sources_by_url: dict[str, Source],
    sources_by_external_id: dict[str, Source],
) -> Source | None:
    source_id = _clean(row.get("source_id"))
    if source_id and source_id in sources_by_external_id:
        return sources_by_external_id[source_id]
    url = _clean(row.get("url") or row.get("source_url"))
    if url and url in sources_by_url:
        return sources_by_url[url]
    if url:
        source = _source_from_external({"url": url, "title": row.get("title"), "source_type": row.get("source_type")}, topic=topic, adapter=adapter)
        if source:
            sources_by_url[url] = source
            if source_id:
                source.id = source_id
                sources_by_external_id[source_id] = source
        return source
    return None


def _external_text_to_verify(payload: dict[str, Any], claims: list[Claim]) -> str:
    text = _clean(payload.get("text_to_verify") or payload.get("report_markdown") or payload.get("report") or payload.get("answer") or payload.get("summary"))
    if text:
        return text
    return "\n".join(claim.text for claim in claims)


def _source_type(value: Any, url: str) -> SourceType:
    cleaned = _clean(value)
    if cleaned:
        try:
            return SourceType(cleaned)
        except ValueError:
            pass
    return infer_source_type(url)


def _verdict(value: Any, default: Verdict) -> Verdict:
    cleaned = _clean(value)
    if cleaned:
        try:
            return Verdict(cleaned)
        except ValueError:
            pass
    return default


def _confidence(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())
