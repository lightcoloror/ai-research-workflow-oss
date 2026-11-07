"""Read-only, fail-closed consumer for QRP's canonical research output."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


RECEIPT_SCHEMA = "ai_research_workflow.evidence_bundle_reference_receipt.v1"
FACADE_CONTRACT = "evidence-bundle.v1"
NATIVE_CONTRACT = "research-output.v1"
NATIVE_SCHEMA_VERSION = "1.0.0"
CANONICAL_QRP_COMMIT = "2d83fc7bb0114417b045726de69f7a3f6b46242b"
CANONICAL_SCHEMA_SHA256 = "3c2e0e9a05034b1ffa3693735da128c7d3e114ddb155f11668120b974fee1009"


def verify_evidence_bundle_reference(
    *,
    bundle_path: str | Path,
    schema_path: str | Path,
    receipt_path: str | Path | None = None,
    expected_bundle_sha256: str | None = None,
    expected_schema_sha256: str = CANONICAL_SCHEMA_SHA256,
) -> dict[str, Any]:
    """Validate one QRP bundle without importing it into ARW research state.

    The authoritative JSON Schema is referenced by path and hash. This adapter
    deliberately does not construct ARW Source, Evidence, Claim, or Verdict
    records, so candidate material cannot be promoted as a side effect.
    """
    bundle_file = Path(bundle_path)
    schema_file = Path(schema_path)
    bundle_bytes, bundle_read_error = _read_bytes(bundle_file)
    schema_bytes, schema_read_error = _read_bytes(schema_file)
    bundle_sha256 = _sha256(bundle_bytes) if bundle_bytes is not None else None
    schema_sha256 = _sha256(_normalize_schema_line_endings(schema_bytes)) if schema_bytes is not None else None
    expected_bundle = _normalized_sha256(expected_bundle_sha256)
    expected_schema = _normalized_sha256(expected_schema_sha256)
    fingerprint = _sha256(
        _canonical_bytes(
            {
                "bundle_sha256": bundle_sha256,
                "expected_bundle_sha256": expected_bundle,
                "schema_sha256": schema_sha256,
                "expected_schema_sha256": expected_schema,
            }
        )
    )

    blockers: list[str] = []
    warnings: list[str] = []
    payload: dict[str, Any] | None = None
    schema: dict[str, Any] | None = None

    if bundle_read_error:
        blockers.append("bundle_unreadable")
    if schema_read_error:
        blockers.append("authoritative_schema_unreadable")
    if expected_bundle and bundle_sha256 != expected_bundle:
        blockers.append("bundle_hash_drift")
    if not expected_schema or schema_sha256 != expected_schema:
        blockers.append("authoritative_schema_hash_drift")

    if bundle_bytes is not None:
        payload = _load_json_object(bundle_bytes, "bundle", blockers)
    if schema_bytes is not None:
        schema = _load_json_object(schema_bytes, "authoritative_schema", blockers)

    if payload is not None and schema is not None and "authoritative_schema_hash_drift" not in blockers:
        blocker_count = len(blockers)
        _validate_json_schema(payload, schema, blockers)
        if len(blockers) == blocker_count:
            _validate_semantics(payload, blockers, warnings)

    receipt = _build_receipt(
        fingerprint=fingerprint,
        bundle_name=bundle_file.name,
        bundle_sha256=bundle_sha256,
        expected_bundle_sha256=expected_bundle,
        schema_name=schema_file.name,
        schema_sha256=schema_sha256,
        expected_schema_sha256=expected_schema,
        payload=payload,
        blockers=blockers,
        warnings=warnings,
    )
    if receipt_path is None:
        return receipt

    receipt_file = Path(receipt_path)
    with _receipt_lock(receipt_file):
        existing = _read_existing_receipt(receipt_file, fingerprint)
        if existing is not None:
            return existing
        _write_receipt(receipt_file, receipt)
    return receipt


def receipt_sha256(receipt: dict[str, Any]) -> str:
    return _sha256(_canonical_bytes(receipt))


def _validate_json_schema(payload: dict[str, Any], schema: dict[str, Any], blockers: list[str]) -> None:
    try:
        import jsonschema
    except ImportError:
        blockers.append("json_schema_validator_unavailable")
        return
    try:
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    except Exception:
        blockers.append("authoritative_schema_invalid")
        return
    for error in errors:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        blockers.append(f"schema_validation_error:{path or '/'}")


def _validate_semantics(payload: dict[str, Any], blockers: list[str], warnings: list[str]) -> None:
    sources = _dict_rows(payload, "sources")
    claims = _dict_rows(payload, "claims")
    evidence = _dict_rows(payload, "evidence")
    links = _dict_rows(payload, "evidence_links")
    contradictions = _dict_rows(payload, "contradictions")
    unknowns = _dict_rows(payload, "unknowns")

    source_by_id = _index_unique(sources, "source_id", "duplicate_source_id", blockers)
    claim_by_id = _index_unique(claims, "claim_id", "duplicate_claim_id", blockers)
    evidence_by_id = _index_unique(evidence, "evidence_id", "duplicate_evidence_id", blockers)

    for source in sources:
        source_id = str(source.get("source_id") or "")
        if source.get("review_status") != "reviewed":
            blockers.append(f"source_unreviewed:{source_id}")
        if source.get("ref_status") == "missing":
            blockers.append(f"source_ref_missing:{source_id}")
        freshness = source.get("freshness")
        if freshness == "stale":
            blockers.append(f"source_stale:{source_id}")
        elif freshness == "unknown":
            warnings.append(f"source_freshness_unknown:{source_id}")
        hash_status = (source.get("hash") or {}).get("status")
        if hash_status == "mismatch":
            blockers.append(f"source_hash_mismatch:{source_id}")
        elif hash_status in {"missing", "recorded"}:
            warnings.append(f"source_hash_not_verified:{source_id}")

    evidence_for_claim: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        claim_id = str(item.get("claim_id") or "")
        source_id = str(item.get("source_id") or "")
        evidence_for_claim.setdefault(claim_id, []).append(item)
        if claim_id not in claim_by_id:
            blockers.append(f"evidence_claim_ref_missing:{evidence_id}")
        if source_id not in source_by_id:
            blockers.append(f"evidence_source_ref_missing:{evidence_id}")
        if item.get("evidence_status") == "candidate":
            blockers.append(f"candidate_evidence:{evidence_id}")
        if item.get("review_status") != "reviewed":
            blockers.append(f"evidence_unreviewed:{evidence_id}")
        if item.get("ai_synthesis"):
            if item.get("evidence_status") == "verified":
                blockers.append(f"ai_synthesis_marked_verified:{evidence_id}")
            else:
                warnings.append(f"ai_synthesis_candidate_only:{evidence_id}")
        if item.get("relation") == "contradicts" and item.get("evidence_status") == "verified":
            blockers.append(f"unresolved_verified_contradiction:{evidence_id}")

    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        linked = evidence_for_claim.get(claim_id, [])
        linked_ids = {str(item.get("evidence_id") or "") for item in linked}
        supporting_ids = {str(item.get("evidence_id") or "") for item in linked if item.get("relation") == "supports"}
        contradicting_ids = {str(item.get("evidence_id") or "") for item in linked if item.get("relation") == "contradicts"}
        context_ids = {str(item.get("evidence_id") or "") for item in linked if item.get("relation") == "context"}
        source_ids = {str(item.get("source_id") or "") for item in linked}
        if set(claim.get("evidence_ids") or []) != linked_ids:
            blockers.append(f"claim_evidence_links_drift:{claim_id}")
        if set(claim.get("supporting_evidence_ids") or []) != supporting_ids:
            blockers.append(f"claim_support_links_drift:{claim_id}")
        if set(claim.get("contradicting_evidence_ids") or []) != contradicting_ids:
            blockers.append(f"claim_contradiction_links_drift:{claim_id}")
        if set(claim.get("context_evidence_ids") or []) != context_ids:
            blockers.append(f"claim_context_links_drift:{claim_id}")
        if set(claim.get("source_ids") or []) != source_ids:
            blockers.append(f"claim_source_links_drift:{claim_id}")
        verified_support = [
            item
            for item in linked
            if item.get("relation") == "supports"
            and item.get("evidence_status") == "verified"
            and item.get("review_status") == "reviewed"
            and not item.get("ai_synthesis")
        ]
        if not verified_support:
            blockers.append(f"claim_missing_verified_support:{claim_id}")
        if claim.get("review_status") != "verified" or claim.get("evidence_status") != "verified":
            blockers.append(f"claim_not_verified:{claim_id}")

    expected_links = Counter(
        (
            str(item.get("evidence_id") or ""),
            str(item.get("claim_id") or ""),
            str(item.get("source_id") or ""),
            str(item.get("relation") or ""),
            str(item.get("evidence_status") or ""),
        )
        for item in evidence
    )
    actual_links = Counter(
        (
            str(item.get("evidence_id") or ""),
            str(item.get("claim_id") or ""),
            str(item.get("source_id") or ""),
            str(item.get("relation") or ""),
            str(item.get("evidence_status") or ""),
        )
        for item in links
    )
    if actual_links != expected_links:
        blockers.append("evidence_links_summary_drift")

    expected_contradictions = Counter(
        (
            str(item.get("claim_id") or ""),
            str(item.get("evidence_id") or ""),
            str(item.get("source_id") or ""),
            str(item.get("evidence_status") or ""),
        )
        for item in evidence
        if item.get("relation") == "contradicts"
    )
    actual_contradictions = Counter(
        (
            str(item.get("claim_id") or ""),
            str(item.get("evidence_id") or ""),
            str(item.get("source_id") or ""),
            str(item.get("evidence_status") or ""),
        )
        for item in contradictions
    )
    if actual_contradictions != expected_contradictions:
        blockers.append("contradictions_summary_drift")
    for item in contradictions:
        evidence_id = str(item.get("evidence_id") or "")
        evidence_item = evidence_by_id.get(evidence_id)
        if evidence_item is None:
            blockers.append(f"contradiction_evidence_ref_missing:{evidence_id}")
        if item.get("resolution_status") != "rejected" or item.get("evidence_status") != "rejected":
            blockers.append(f"contradiction_unresolved:{evidence_id}")

    for item in unknowns:
        code = str(item.get("code") or "unknown")
        if item.get("severity") == "blocker":
            blockers.append(f"upstream_blocker:{code}")
        else:
            warnings.append(f"upstream_warning:{code}")

    _validate_summaries(payload, evidence, contradictions, blockers)
    _validate_guarded_answer(payload, source_by_id, claim_by_id, evidence_by_id, blockers)


def _validate_summaries(
    payload: dict[str, Any],
    evidence: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
    blockers: list[str],
) -> None:
    review_status = payload.get("review_status") or {}
    evidence_status = payload.get("evidence_status") or {}
    completeness = payload.get("completeness") or {}
    guarded = payload.get("guarded_final_answer") or {}
    if review_status.get("overall") != "verified" or review_status.get("human_review_required") is not False:
        blockers.append("review_gate_not_verified")
    expected_counts = {
        "candidate": sum(item.get("evidence_status") == "candidate" for item in evidence),
        "verified": sum(item.get("evidence_status") == "verified" for item in evidence),
        "rejected": sum(item.get("evidence_status") == "rejected" for item in evidence),
        "contradictions": sum(item.get("resolution_status") == "unresolved" for item in contradictions),
    }
    if evidence_status.get("counts") != expected_counts:
        blockers.append("evidence_counts_drift")
    if evidence_status.get("overall") != "verified":
        blockers.append("evidence_summary_not_verified")
    if completeness.get("ready_for_answer") is not True or completeness.get("blocking_gates"):
        blockers.append("completeness_gate_blocked")
    if guarded.get("status") != "verified" or guarded.get("ready") is not True or guarded.get("blocking_gates"):
        blockers.append("guarded_answer_not_verified")
    if (payload.get("provenance") or {}).get("model_memory_used_as_evidence") is not False:
        blockers.append("model_memory_used_as_evidence")


def _validate_guarded_answer(
    payload: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
    claim_by_id: dict[str, dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    blockers: list[str],
) -> None:
    guarded = payload.get("guarded_final_answer") or {}
    guarded_claim_ids = set(guarded.get("claim_ids") or [])
    guarded_evidence_ids = set(guarded.get("evidence_ids") or [])
    guarded_source_ids = set(guarded.get("source_ids") or [])
    if not guarded_claim_ids or guarded.get("answer") in {None, ""}:
        blockers.append("guarded_answer_empty")
    if not guarded_claim_ids.issubset(claim_by_id):
        blockers.append("guarded_answer_claim_ref_missing")
    if not guarded_evidence_ids.issubset(evidence_by_id):
        blockers.append("guarded_answer_evidence_ref_missing")
    if not guarded_source_ids.issubset(source_by_id):
        blockers.append("guarded_answer_source_ref_missing")
    expected_evidence: set[str] = set()
    expected_sources: set[str] = set()
    for claim_id in guarded_claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None or claim.get("review_status") != "verified" or claim.get("evidence_status") != "verified":
            blockers.append(f"guarded_answer_claim_not_verified:{claim_id}")
            continue
        for evidence_id in claim.get("supporting_evidence_ids") or []:
            item = evidence_by_id.get(str(evidence_id))
            if (
                item is not None
                and item.get("evidence_status") == "verified"
                and item.get("review_status") == "reviewed"
                and not item.get("ai_synthesis")
            ):
                expected_evidence.add(str(evidence_id))
                expected_sources.add(str(item.get("source_id") or ""))
    if guarded_evidence_ids != expected_evidence:
        blockers.append("guarded_answer_evidence_links_drift")
    if guarded_source_ids != expected_sources:
        blockers.append("guarded_answer_source_links_drift")


def _build_receipt(
    *,
    fingerprint: str,
    bundle_name: str,
    bundle_sha256: str | None,
    expected_bundle_sha256: str | None,
    schema_name: str,
    schema_sha256: str | None,
    expected_schema_sha256: str | None,
    payload: dict[str, Any] | None,
    blockers: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    unique_blockers = sorted(set(blockers))
    unique_warnings = sorted(set(warnings))
    ok = not unique_blockers
    rows = payload or {}
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": ok,
        "status": "accepted_reference" if ok else "blocked",
        "request_fingerprint": fingerprint,
        "contract": {
            "facade": FACADE_CONTRACT,
            "native": NATIVE_CONTRACT,
            "schema_version": NATIVE_SCHEMA_VERSION,
            "facade_mode": "direct_canonical",
        },
        "authoritative_schema_ref": {
            "owner": "question-research-poc",
            "commit": CANONICAL_QRP_COMMIT,
            "name": schema_name,
            "expected_sha256": expected_schema_sha256,
            "actual_sha256": schema_sha256,
            "hash_mode": "utf8_bytes_lf_normalized",
            "schema_copied_into_arw": False,
        },
        "bundle_ref": {
            "name": bundle_name,
            "expected_sha256": expected_bundle_sha256,
            "actual_sha256": bundle_sha256,
            "research_id": rows.get("research_id"),
        },
        "summary": {
            "source_count": len(rows.get("sources") or []),
            "claim_count": len(rows.get("claims") or []),
            "evidence_count": len(rows.get("evidence") or []),
            "contradiction_count": len(rows.get("contradictions") or []),
            "unknown_count": len(rows.get("unknowns") or []),
            "review_status": (rows.get("review_status") or {}).get("overall"),
            "evidence_status": (rows.get("evidence_status") or {}).get("overall"),
            "guarded_answer_status": (rows.get("guarded_final_answer") or {}).get("status"),
        },
        "blockers": unique_blockers,
        "warnings": unique_warnings,
        "consumer_effect": "reference_validation_only",
        "imported_into_arw_state": False,
        "research_truth_effect": "none",
        "evidence_status_change": "none",
        "candidate_promoted": False,
        "human_review_required": not ok,
    }


def _dict_rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _index_unique(
    rows: list[dict[str, Any]],
    key: str,
    duplicate_code: str,
    blockers: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in rows:
        identifier = str(item.get(key) or "")
        if identifier in result:
            blockers.append(f"{duplicate_code}:{identifier}")
        result[identifier] = item
    return result


def _load_json_object(raw: bytes, label: str, blockers: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        blockers.append(f"{label}_invalid_json")
        return None
    if not isinstance(value, dict):
        blockers.append(f"{label}_not_object")
        return None
    return value


def _read_bytes(path: Path) -> tuple[bytes | None, bool]:
    try:
        return path.read_bytes(), False
    except OSError:
        return None, True


def _normalized_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_schema_line_endings(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_existing_receipt(path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("existing evidence-bundle receipt is unreadable") from exc
    if not isinstance(value, dict) or value.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("existing evidence-bundle receipt schema is invalid")
    if value.get("request_fingerprint") != fingerprint:
        raise ValueError("evidence-bundle receipt request drift detected")
    return value


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_canonical_bytes(value) + b"\n")
    os.replace(temporary, path)


@contextmanager
def _receipt_lock(path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("evidence-bundle receipt lock timed out")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass
