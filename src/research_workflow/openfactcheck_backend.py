from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .external_adapters import ExternalAdapterRecords, normalize_external_payload


@dataclass(slots=True)
class OpenFactCheckBackendStatus:
    available: bool
    module_path: str = ""
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "module_path": self.module_path,
            "warning": self.warning,
        }


@dataclass(slots=True)
class OpenFactCheckBackendResult:
    records: ExternalAdapterRecords = field(default_factory=ExternalAdapterRecords)
    status: OpenFactCheckBackendStatus = field(default_factory=lambda: OpenFactCheckBackendStatus(False))


def check_openfactcheck_available(extra_paths: list[str | Path] | None = None) -> OpenFactCheckBackendStatus:
    added = _add_import_paths(extra_paths)
    try:
        import openfactcheck  # type: ignore[import-not-found]

        module_path = str(getattr(openfactcheck, "__file__", "") or "")
        return OpenFactCheckBackendStatus(available=True, module_path=module_path)
    except Exception as exc:  # noqa: BLE001
        return OpenFactCheckBackendStatus(available=False, warning=f"openfactcheck unavailable: {exc}")
    finally:
        _remove_import_paths(added)


def run_openfactcheck_optional(
    *,
    text: str,
    topic: str = "",
    backend: Callable[..., Any] | None = None,
    extra_paths: list[str | Path] | None = None,
) -> OpenFactCheckBackendResult:
    status = check_openfactcheck_available(extra_paths)
    if backend is None:
        records = ExternalAdapterRecords(warnings=[status.warning or "openfactcheck backend callable not configured"])
        return OpenFactCheckBackendResult(records=records, status=status)
    try:
        payload = _call_backend(backend, text=text, topic=topic)
    except Exception as exc:  # noqa: BLE001
        records = ExternalAdapterRecords(warnings=[f"openfactcheck backend failed: {exc}"])
        return OpenFactCheckBackendResult(records=records, status=status)
    records = normalize_openfactcheck_result(payload, topic=topic)
    if status.warning:
        records.warnings.append(status.warning)
    return OpenFactCheckBackendResult(records=records, status=status)


def normalize_openfactcheck_result(result: Any, *, topic: str = "") -> ExternalAdapterRecords:
    payload = _result_to_payload(result)
    payload["adapter"] = payload.get("adapter") or "openfactcheck"
    return normalize_external_payload(payload, topic=topic)


def _call_backend(backend: Callable[..., Any], *, text: str, topic: str) -> Any:
    try:
        result = backend(text=text, topic=topic)
    except TypeError:
        result = backend(text)
    if inspect.isawaitable(result):
        import asyncio

        return asyncio.run(result)
    return result


def _result_to_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    claims = [_claim_to_dict(item) for item in _as_list(_get(result, "claims"))]
    sources: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for evidence in _as_list(_get(result, "evidence")):
        claim_text = _get(_get(evidence, "claim"), "text") or _get(evidence, "claim")
        for source in _as_list(_get(evidence, "sources")):
            source_row = _source_to_dict(source)
            if source_row:
                sources.append(source_row)
                evidence_rows.append(
                    {
                        "source_id": source_row.get("id"),
                        "url": source_row.get("url"),
                        "text": source_row.get("snippet") or source_row.get("content") or source_row.get("title"),
                        "claim_relevance": claim_text or "openfactcheck evidence",
                        "confidence": _get(evidence, "confidence") or 0.7,
                    }
                )
    verdicts = _as_list(_get(result, "verdicts"))
    for verdict in verdicts:
        claim = _get(verdict, "claim")
        claim_text = _get(claim, "text") or _get(verdict, "claim")
        if claim_text:
            claims.append(
                {
                    "text": claim_text,
                    "verdict": _verdict_label(_get(verdict, "label")),
                    "confidence": _get(verdict, "confidence") or 0.0,
                }
            )
    return {
        "adapter": "openfactcheck",
        "claims": claims,
        "sources": sources,
        "evidence": evidence_rows,
        "text_to_verify": _get(result, "text") or _get(result, "summary") or "",
    }


def _claim_to_dict(claim: Any) -> dict[str, Any]:
    if isinstance(claim, dict):
        return dict(claim)
    text = _get(claim, "text")
    return {"text": text} if text else {}


def _source_to_dict(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        return dict(source)
    url = _get(source, "url") or _get(source, "uri") or _get(source, "link")
    content = _get(source, "content") or _get(source, "text")
    title = _get(source, "title") or url or "OpenFactCheck source"
    if not url and not content:
        return {}
    return {
        "id": _get(source, "id") or "",
        "url": url or f"openfactcheck-source://{abs(hash(content or title))}",
        "title": title,
        "snippet": content or _get(source, "snippet") or "",
    }


def _verdict_label(label: Any) -> str:
    cleaned = str(label or "").strip().lower()
    return {
        "refuted": "contradicted",
        "false": "contradicted",
        "nei": "not_found",
        "insufficient": "not_found",
        "unsupported": "not_found",
        "supported": "supported",
        "true": "supported",
    }.get(cleaned, cleaned or "not_found")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _add_import_paths(paths: list[str | Path] | None) -> list[str]:
    added: list[str] = []
    for path in paths or []:
        text = str(path)
        if text and text not in sys.path:
            sys.path.insert(0, text)
            added.append(text)
    return added


def _remove_import_paths(paths: list[str]) -> None:
    for path in paths:
        try:
            sys.path.remove(path)
        except ValueError:
            pass
