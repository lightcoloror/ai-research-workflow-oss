from __future__ import annotations

import hashlib
import json
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_MULTIHOP_CHECKS = (
    "factual_correctness",
    "answer_uniqueness",
    "temporal_stability",
    "entity_dependency",
    "independent_reproduction",
)
REPLAY_SCHEMA = "research.tool-replay.v2"
REPLAY_SCHEMA_VERSION = 2
_VERIFIED_STATES = {"verified", "accepted", "exists", "resolved"}


def build_material_passport(report: dict[str, Any]) -> dict[str, Any]:
    """Build a portable, local-only manifest for one research artifact."""
    sources = [dict(item) for item in report.get("sources", []) if isinstance(item, dict)]
    claims = [
        dict(item)
        for item in (report.get("claim_table") or report.get("claims") or [])
        if isinstance(item, dict)
    ]
    evidence = [dict(item) for item in report.get("evidence", []) if isinstance(item, dict)]
    payload = {
        "topic": str(report.get("topic") or ""),
        "sources": sources,
        "claims": claims,
        "evidence": evidence,
    }
    return {
        "schema": "research.material-passport.v1",
        "artifact_id": _stable_hash(payload)[:16],
        "topic": payload["topic"],
        "generated_at": str(report.get("generated_at") or _now_iso()),
        "source_manifest": [
            {
                "id": str(item.get("id") or ""),
                "url": str(item.get("url") or item.get("canonical_url") or ""),
                "verification_status": _verification_state(item),
                "published_at": str(item.get("published_at") or ""),
                "retrieved_at": str(item.get("retrieved_at") or ""),
                "local_path": str(item.get("local_path") or item.get("source_path") or ""),
                "artifact_sha256": str(item.get("artifact_sha256") or item.get("content_hash") or ""),
            }
            for item in sources
        ],
        "evidence_manifest": [
            {
                "id": str(item.get("id") or ""),
                "source_id": str(item.get("source_id") or ""),
                "verification_status": _verification_state(item),
                "observed_at": str(item.get("observed_at") or item.get("retrieved_at") or ""),
                "artifact_sha256": str(item.get("artifact_sha256") or item.get("content_hash") or ""),
            }
            for item in evidence
        ],
        "claim_manifest": [
            {
                "id": str(item.get("id") or ""),
                "text": str(item.get("text") or ""),
                "verdict": str(item.get("verdict") or "not_found"),
                "risk_level": str(item.get("risk_level") or "normal"),
                "evidence_ids": [str(value) for value in item.get("evidence_ids", []) if value],
            }
            for item in claims
        ],
        "input_hash": _stable_hash(payload),
    }


def evaluate_research_integrity(
    report: dict[str, Any],
    *,
    previous_score: float | None = None,
) -> dict[str, Any]:
    """Evaluate local evidence integrity without pretending to verify the open web."""
    sources = [dict(item) for item in report.get("sources", []) if isinstance(item, dict)]
    evidence = [dict(item) for item in report.get("evidence", []) if isinstance(item, dict)]
    claims = [
        dict(item)
        for item in (report.get("claim_table") or report.get("claims") or [])
        if isinstance(item, dict)
    ]
    citation_rows = [
        dict(item) for item in report.get("claim_citations", []) if isinstance(item, dict)
    ]
    source_by_id = {
        str(item.get("id") or ""): item for item in sources if str(item.get("id") or "")
    }
    evidence_by_id = {
        str(item.get("id") or ""): item for item in evidence if str(item.get("id") or "")
    }
    citations_by_claim: dict[str, dict[str, Any]] = {}
    for row in citation_rows:
        for key in (row.get("claim_id"), row.get("claim"), row.get("text")):
            if key:
                citations_by_claim[str(key)] = row

    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    material_gaps: list[str] = []
    passed_checks = 0
    total_checks = 0

    for claim in claims:
        claim_id = str(claim.get("id") or "")
        claim_text = str(claim.get("text") or "")
        citation_row = citations_by_claim.get(claim_id) or citations_by_claim.get(claim_text) or {}
        evidence_ids = _dedupe(
            [
                *[str(value) for value in claim.get("evidence_ids", []) if value],
                *[str(value) for value in citation_row.get("evidence_ids", []) if value],
            ]
        )
        missing_evidence = [value for value in evidence_ids if value not in evidence_by_id]
        resolved_evidence = [evidence_by_id[value] for value in evidence_ids if value in evidence_by_id]
        source_ids = _dedupe(
            [
                *[str(item.get("source_id") or "") for item in resolved_evidence if item.get("source_id")],
                *[str(value) for value in citation_row.get("source_ids", []) if value],
            ]
        )
        missing_sources = [value for value in source_ids if value not in source_by_id]
        supporting = [
            item
            for item in resolved_evidence
            if str(item.get("support_direction") or item.get("direction") or "")
            in {"supported", "partial"}
        ]
        contradicting = [
            item
            for item in resolved_evidence
            if str(item.get("support_direction") or item.get("direction") or "")
            == "contradicted"
        ]
        local_citation_present = bool(citation_row.get("citations") or source_ids)
        externally_verified = [
            value
            for value in source_ids
            if _verification_state(source_by_id.get(value, {})) in _VERIFIED_STATES
        ]
        source_states = _dedupe([_verification_state(source_by_id[value]) for value in source_ids if value in source_by_id])
        evidence_states = _dedupe([_verification_state(item) for item in resolved_evidence])
        degraded_sources = [value for value in source_states if value not in _VERIFIED_STATES]
        degraded_evidence = [value for value in evidence_states if value not in _VERIFIED_STATES]
        verdict = str(claim.get("verdict") or "not_found")
        risk_level = str(claim.get("risk_level") or "normal")
        reasons: list[str] = []

        total_checks += 3
        direction_evidence = (
            supporting
            if verdict == "supported"
            else contradicting
            if verdict == "contradicted"
            else resolved_evidence
        )
        if evidence_ids and not missing_evidence and direction_evidence:
            passed_checks += 1
        else:
            reasons.append("claim_evidence_missing")
        if source_ids and not missing_sources:
            passed_checks += 1
        else:
            reasons.append("source_record_missing")
        if local_citation_present:
            passed_checks += 1
        else:
            reasons.append("citation_record_missing")

        if verdict == "supported" and reasons:
            blockers.append(
                {
                    "claim_id": claim_id,
                    "kind": "supported_claim_integrity_failure",
                    "reasons": reasons,
                }
            )
        if verdict == "supported" and degraded_sources:
            blockers.append(
                {
                    "claim_id": claim_id,
                    "kind": "source_not_verified",
                    "reasons": degraded_sources,
                }
            )
        if verdict == "supported" and degraded_evidence:
            blockers.append(
                {
                    "claim_id": claim_id,
                    "kind": "evidence_not_verified",
                    "reasons": degraded_evidence,
                }
            )
        if verdict == "supported" and risk_level == "high" and len(source_ids) < 2:
            blockers.append(
                {
                    "claim_id": claim_id,
                    "kind": "high_risk_source_diversity",
                    "reasons": ["high_risk_claim_needs_two_sources"],
                }
            )
        if not evidence_ids or missing_evidence:
            material_gaps.append(f"[MATERIAL GAP: evidence for {claim_id or claim_text}]")
        if not source_ids or missing_sources:
            material_gaps.append(f"[MATERIAL GAP: source for {claim_id or claim_text}]")

        rows.append(
            {
                "claim_id": claim_id,
                "claim": claim_text,
                "verdict": verdict,
                "claim_support": "present" if supporting and not missing_evidence else "contradicted" if contradicting and not missing_evidence else "missing",
                "citation_record": "present" if local_citation_present else "missing",
                "citation_existence": "verified"
                if externally_verified and len(externally_verified) == len(source_ids)
                else "not_checked",
                "source_ids": source_ids,
                "evidence_ids": evidence_ids,
                "source_states": source_states,
                "evidence_states": evidence_states,
                "reasons": _dedupe(reasons),
            }
        )

    score = round(passed_checks / total_checks, 4) if total_checks else 0.0
    previous = None if previous_score is None else round(float(previous_score), 4)
    trajectory = {
        "previous": previous,
        "current": score,
        "delta": None if previous is None else round(score - previous, 4),
        "regressed": False if previous is None else score < previous,
    }
    return {
        "schema": "research.integrity-gate.v1",
        "status": "blocked" if blockers or material_gaps else "pass",
        "deliverable_ready": not blockers and not material_gaps and bool(claims) and all(str(item.get("verdict") or "not_found") == "supported" for item in claims),
        "score": score,
        "score_trajectory": trajectory,
        "claim_checks": rows,
        "blockers": blockers,
        "non_supported_claims": [
            str(item.get("id") or item.get("text") or "")
            for item in claims
            if str(item.get("verdict") or "not_found") != "supported"
        ],
        "material_gaps": _dedupe(material_gaps),
        "passport": build_material_passport(report),
        "limits": [
            "citation_existence is not_checked unless a source carries explicit verification_status",
            "this gate uses local artifacts only and never substitutes model memory for evidence",
        ],
    }


def validate_multihop_case(case: dict[str, Any]) -> dict[str, Any]:
    """Apply MTA-inspired checks supplied by independent validators."""
    checks = dict(case.get("checks") or {})
    results: list[dict[str, Any]] = []
    gaps: list[str] = []
    for name in REQUIRED_MULTIHOP_CHECKS:
        raw = checks.get(name)
        if isinstance(raw, bool):
            status = "pass" if raw else "fail"
            reason = ""
        elif isinstance(raw, dict):
            verdict = raw.get("passed")
            status = "pass" if verdict is True else "fail" if verdict is False else "missing"
            reason = str(raw.get("reason") or "")
        else:
            status = "missing"
            reason = ""
        if status == "missing":
            gaps.append(f"[MATERIAL GAP: multihop check {name}]")
        results.append({"check": name, "status": status, "reason": reason})
    passed = all(item["status"] == "pass" for item in results)
    return {
        "schema": "research.multihop-validation.v1",
        "case_id": str(case.get("id") or ""),
        "status": "pass" if passed else "blocked",
        "passed": passed,
        "checks": results,
        "material_gaps": gaps,
    }


def build_replay_bundle(
    case: dict[str, Any],
    tool_responses: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = {
        "case": deepcopy(case),
        "tool_responses": deepcopy(tool_responses),
    }
    payload_hash = _stable_hash(payload)
    return {
        "schema": REPLAY_SCHEMA,
        "schema_version": REPLAY_SCHEMA_VERSION,
        "bundle_id": payload_hash[:16],
        "created_at": _now_iso(),
        "payload": payload,
        "payload_hash": payload_hash,
    }


def validate_replay_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if bundle.get("load_error"):
        return {"status": "blocked", "reason": str(bundle["load_error"])}
    before_hash = str(bundle.get("payload_hash") or "")
    migrated, migrations = _migrate_replay_bundle(bundle)
    payload = migrated.get("payload")
    if (
        migrated.get("schema") != REPLAY_SCHEMA
        or migrated.get("schema_version") != REPLAY_SCHEMA_VERSION
        or not isinstance(payload, dict)
    ):
        return {"status": "blocked", "reason": "invalid_replay_schema"}
    after_hash = _stable_hash(payload)
    if after_hash != str(migrated.get("payload_hash") or ""):
        return {"status": "blocked", "reason": "replay_hash_mismatch"}
    case = payload.get("case")
    if not isinstance(case, dict):
        return {"status": "blocked", "reason": "missing_replay_case"}
    return {
        "status": "pass",
        "reason": "",
        "schema": REPLAY_SCHEMA,
        "schema_version": REPLAY_SCHEMA_VERSION,
        "bundle_id": str(migrated.get("bundle_id") or ""),
        "migrations": migrations,
        "payload_hash": after_hash,
        "migration_hashes": {"before": before_hash, "after": after_hash},
        "validation": validate_multihop_case(case),
        "tool_response_count": len(payload.get("tool_responses") or []),
    }


def write_replay_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_replay_bundle(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": "research.tool-replay.invalid", "load_error": "missing_replay"}
    except (json.JSONDecodeError, UnicodeError):
        return {"schema": "research.tool-replay.invalid", "load_error": "corrupt_replay"}
    except OSError:
        return {"schema": "research.tool-replay.invalid", "load_error": "unreadable_replay"}
    if not isinstance(loaded, dict):
        return {"schema": "research.tool-replay.invalid", "load_error": "invalid_replay_schema"}
    return loaded


def _migrate_replay_bundle(bundle: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    migrated = deepcopy(bundle)
    migrations: list[str] = []
    if migrated.get("schema") == "research.tool-replay.v1":
        migrated["schema"] = REPLAY_SCHEMA
        migrated["schema_version"] = REPLAY_SCHEMA_VERSION
        migrated["bundle_id"] = str(migrated.get("payload_hash") or "")[:16]
        migrations.append("research.tool-replay.v1->research.tool-replay.v2")
    return migrated, migrations


def _verification_state(item: dict[str, Any]) -> str:
    return str(
        item.get("verification_status")
        or item.get("review_status")
        or item.get("status")
        or "not_checked"
    ).strip().lower()
def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
