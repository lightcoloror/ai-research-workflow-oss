from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "agent-tool-manifest.v1.json"
RECEIPT_SCHEMA = "agent-tool-owner-synthetic-receipt.v1"


class DoctorError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DoctorError("missing_file", str(path)) from exc
    except json.JSONDecodeError as exc:
        raise DoctorError("invalid_json", f"{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise DoctorError("json_object_required", str(path))
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_inside(root: Path, relative: str) -> Path:
    candidate_path = Path(relative)
    if candidate_path.is_absolute():
        raise DoctorError("absolute_path_forbidden", relative)
    resolved_root = root.resolve()
    candidate = (resolved_root / candidate_path).resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise DoctorError("path_escape", relative)
    return candidate


def _validate_manifest(payload: dict[str, Any], tool_id: str) -> None:
    required = {
        "schema_version",
        "id",
        "manifest_version",
        "interfaces",
        "health",
        "provenance",
        "notTrustedAsInstruction",
        "executionAuthority",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise DoctorError("manifest_required_field_missing", ",".join(missing))
    if payload.get("schema_version") != "agent-tool-manifest.v1":
        raise DoctorError("manifest_schema_mismatch", str(payload.get("schema_version")))
    if payload.get("id") != tool_id:
        raise DoctorError("manifest_tool_id_mismatch", str(payload.get("id")))
    if payload.get("notTrustedAsInstruction") is not True:
        raise DoctorError("manifest_trust_boundary_invalid", "notTrustedAsInstruction")
    if payload.get("executionAuthority") != "none_metadata_only":
        raise DoctorError("manifest_execution_authority_invalid", str(payload.get("executionAuthority")))
    if payload.get("health", {}).get("proves_current_online") is not False:
        raise DoctorError("manifest_online_claim_invalid", "health.proves_current_online")
    if "registry_sha256" in payload.get("provenance", {}):
        raise DoctorError("volatile_registry_hash_forbidden", "provenance.registry_sha256")
    serialized = json.dumps(payload.get("interfaces", {}), ensure_ascii=False)
    if re.search(r"(?i)(?<![a-z])[a-z]:[/\\]|/Users/|/home/", serialized):
        raise DoctorError("machine_path_in_interface", "interfaces")


def _check_path(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    relative = str(item.get("path", ""))
    path = resolve_inside(root, relative)
    kind = item.get("kind", "file")
    exists = path.is_file() if kind == "file" else path.is_dir() if kind == "directory" else False
    contains = item.get("contains")
    content_matches = None
    if exists and contains is not None and path.is_file():
        content_matches = str(contains) in path.read_text(encoding="utf-8", errors="replace")
    passed = exists and content_matches is not False
    return {
        "id": item.get("id"),
        "kind": kind,
        "path": relative.replace("\\", "/"),
        "required": item.get("required", True),
        "status": "passed" if passed else "missing_or_mismatch",
    }


def _validate_receipt(receipt_path: Path | None, tool_id: str, ttl_hours: int) -> dict[str, Any]:
    if receipt_path is None:
        return {
            "value": None,
            "status": "not_assessed",
            "ttl_hours": ttl_hours,
            "reason_codes": ["synthetic_receipt_not_provided"],
        }
    try:
        payload = read_json(receipt_path.resolve())
        checked_at = datetime.fromisoformat(str(payload.get("checked_at", "")))
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        artifact = payload.get("artifact", {})
        artifact_path = resolve_inside(receipt_path.resolve().parent, str(artifact.get("path", "")))
        hash_matches = artifact_path.is_file() and sha256_file(artifact_path) == artifact.get("sha256")
        expired = datetime.now(timezone.utc) - checked_at.astimezone(timezone.utc) > timedelta(hours=ttl_hours)
        truth = payload.get("truth", {})
        valid = bool(
            payload.get("schema_version") == RECEIPT_SCHEMA
            and payload.get("tool_id") == tool_id
            and payload.get("status") == "passed"
            and truth.get("fresh_e2e_pass") is True
            and truth.get("real_usage_proof") is False
            and truth.get("current_online") is False
            and hash_matches
            and not expired
        )
        reasons = []
        if not hash_matches:
            reasons.append("artifact_hash_drift")
        if expired:
            reasons.append("receipt_stale")
        if not valid and not reasons:
            reasons.append("receipt_contract_invalid")
        return {
            "value": valid,
            "status": "passed_current" if valid else "rejected",
            "ttl_hours": ttl_hours,
            "reason_codes": reasons,
        }
    except (DoctorError, OSError, ValueError) as exc:
        return {
            "value": False,
            "status": "rejected",
            "ttl_hours": ttl_hours,
            "reason_codes": [getattr(exc, "code", "receipt_invalid")],
        }


def doctor(root: Path, tool_id: str, receipt_path: Path | None = None, ttl_hours: int = 168) -> dict[str, Any]:
    resolved_root = root.resolve()
    manifest_path = resolved_root / MANIFEST_NAME
    manifest = read_json(manifest_path)
    _validate_manifest(manifest, tool_id)
    provenance = manifest.get("provenance", {})
    contract_checks = [_check_path(resolved_root, item) for item in provenance.get("no_write_checks", [])]
    runtime_checks = [_check_path(resolved_root, item) for item in provenance.get("runtime_checks", [])]
    contract_failures = [item["id"] for item in contract_checks if item["required"] and item["status"] != "passed"]
    runtime_failures = [item["id"] for item in runtime_checks if item["required"] and item["status"] != "passed"]
    contract_ready = not contract_failures
    runtime_ready = contract_ready and not runtime_failures
    fresh = _validate_receipt(receipt_path, tool_id, ttl_hours)
    status = "ready_metadata_only"
    if not contract_ready:
        status = "blocked_contract"
    elif not runtime_ready:
        status = "blocked_runtime"
    recovery = list(provenance.get("restore_sequence", []))
    return {
        "schema_version": "agent-tool-owner-doctor.v1",
        "tool_id": tool_id,
        "status": status,
        "truth": {
            "contract_ready": {"value": contract_ready, "status": "passed_current" if contract_ready else "blocked"},
            "runtime_ready": {"value": runtime_ready, "status": "derived_local_static" if runtime_ready else "blocked"},
            "fresh_e2e_pass": fresh,
            "real_usage_proof": {"value": False, "status": "not_proved_by_synthetic_or_metadata"},
            "current_online": {"value": False, "status": "not_probed"},
        },
        "manifest": {"path": MANIFEST_NAME, "sha256": sha256_file(manifest_path), "valid": True},
        "checks": {"contract": contract_checks, "runtime": runtime_checks},
        "failures": {"contract": contract_failures, "runtime": runtime_failures},
        "recovery": recovery,
        "runtime_probe_performed": False,
        "network_performed": False,
        "secret_values_read": False,
        "write_performed": False,
        "metadata_authorizes_execution": False,
        "silent_fallback": False,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate an owner manifest without network or writes.")
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result.add_argument("--tool-id", required=True)
    result.add_argument("--receipt", type=Path)
    result.add_argument("--ttl-hours", type=int, default=168)
    result.add_argument("--format", choices=("json",), default="json")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = doctor(args.root, args.tool_id, args.receipt, args.ttl_hours)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "ready_metadata_only" else 2
    except DoctorError as exc:
        error = {
            "schema_version": "agent-tool-owner-doctor-error.v1",
            "status": "blocked",
            "error": {"code": exc.code, "detail": str(exc)},
            "network_performed": False,
            "secret_values_read": False,
            "write_performed": False,
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
