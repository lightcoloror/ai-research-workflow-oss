from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .models import Claim, Evidence, Source, Verdict
from .store import stable_id


@dataclass(slots=True)
class LongTracerStatus:
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
class LongTracerVerification:
    trust_score: float = 0.0
    hallucination_count: int = 0
    all_supported: bool = False
    verdict: str = ""
    claims: list[Claim] = field(default_factory=list)
    flagged_claims: list[dict[str, Any]] = field(default_factory=list)
    hallucinations: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    status: LongTracerStatus = field(default_factory=lambda: LongTracerStatus(False))
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trust_score": self.trust_score,
            "hallucination_count": self.hallucination_count,
            "all_supported": self.all_supported,
            "verdict": self.verdict,
            "claims": [claim.to_dict() for claim in self.claims],
            "flagged_claims": self.flagged_claims,
            "hallucinations": self.hallucinations,
            "raw": self.raw,
            "status": self.status.to_dict(),
            "warning": self.warning,
        }


def check_longtracer_available(extra_paths: list[str | Path] | None = None) -> LongTracerStatus:
    added = _add_import_paths(extra_paths)
    try:
        import longtracer  # type: ignore[import-not-found]

        return LongTracerStatus(available=True, module_path=str(getattr(longtracer, "__file__", "") or ""))
    except Exception as exc:  # noqa: BLE001
        return LongTracerStatus(available=False, warning=f"longtracer unavailable: {exc}")
    finally:
        _remove_import_paths(added)


def verify_with_longtracer(
    *,
    response: str,
    sources: list[Source],
    evidence: list[Evidence] | None = None,
    threshold: float = 0.5,
    checker: Callable[..., Any] | None = None,
    extra_paths: list[str | Path] | None = None,
) -> LongTracerVerification:
    status = check_longtracer_available(extra_paths)
    source_texts, metadata = _source_texts_and_metadata(sources, evidence or [])
    if not source_texts:
        return LongTracerVerification(status=status, warning="no source text available for LongTracer verification")
    try:
        result = _call_longtracer(
            response=response,
            source_texts=source_texts,
            metadata=metadata,
            threshold=threshold,
            checker=checker,
            extra_paths=extra_paths,
        )
    except Exception as exc:  # noqa: BLE001
        return LongTracerVerification(status=status, warning=f"longtracer verification failed: {exc}")
    payload = _result_to_dict(result)
    claims = [_claim_from_longtracer(row) for row in _list(payload.get("claims")) if isinstance(row, dict)]
    return LongTracerVerification(
        trust_score=_float(payload.get("trust_score"), 0.0),
        hallucination_count=int(payload.get("hallucination_count") or 0),
        all_supported=bool(payload.get("all_supported", False)),
        verdict=str(payload.get("verdict") or ""),
        claims=claims,
        flagged_claims=[dict(row) for row in _list(payload.get("flagged_claims")) if isinstance(row, dict)],
        hallucinations=[dict(row) for row in _list(payload.get("hallucinations")) if isinstance(row, dict)],
        raw=payload,
        status=status,
    )


def _call_longtracer(
    *,
    response: str,
    source_texts: list[str],
    metadata: list[dict[str, Any]],
    threshold: float,
    checker: Callable[..., Any] | None,
    extra_paths: list[str | Path] | None,
) -> Any:
    if checker is not None:
        try:
            return checker(response=response, sources=source_texts, source_metadata=metadata, threshold=threshold)
        except TypeError:
            return checker(response, source_texts)
    added = _add_import_paths(extra_paths)
    try:
        from longtracer import check  # type: ignore[import-not-found]

        return check(response, source_texts, source_metadata=metadata, threshold=threshold)
    finally:
        _remove_import_paths(added)


def _source_texts_and_metadata(sources: list[Source], evidence: list[Evidence]) -> tuple[list[str], list[dict[str, Any]]]:
    source_by_id = {source.id: source for source in sources}
    texts: list[str] = []
    metadata: list[dict[str, Any]] = []
    if evidence:
        for item in evidence:
            text = " ".join(item.quote_or_paraphrase.split())
            if not text:
                continue
            source = source_by_id.get(item.source_id)
            texts.append(text)
            metadata.append(
                {
                    "evidence_id": item.id,
                    "source_id": item.source_id,
                    "url": source.url if source else "",
                    "title": source.title if source else "",
                }
            )
        return texts, metadata
    for source in sources:
        text = " ".join((source.snippet or source.title or source.url).split())
        if not text:
            continue
        texts.append(text)
        metadata.append({"source_id": source.id, "url": source.url, "title": source.title})
    return texts, metadata


def _claim_from_longtracer(row: dict[str, Any]) -> Claim:
    text = str(row.get("claim") or row.get("text") or "").strip()
    supported = bool(row.get("supported", False))
    hallucination = bool(row.get("is_hallucination", False))
    verdict = Verdict.SUPPORTED if supported else Verdict.CONTRADICTED if hallucination else Verdict.NOT_FOUND
    return Claim(
        id=stable_id("longtracer", text, prefix="claim_"),
        text=text,
        claim_type="factual",
        risk_level="normal",
        verdict=verdict,
        confidence=_float(row.get("score") or row.get("best_score"), 0.0),
    )


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    data: dict[str, Any] = {}
    for key in ["trust_score", "claims", "flagged_claims", "hallucinations", "all_supported", "hallucination_count", "verdict", "summary"]:
        if hasattr(result, key):
            data[key] = getattr(result, key)
    return data


def _float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


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
