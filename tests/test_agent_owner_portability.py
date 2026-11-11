from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "owner_manifest_doctor.py"
SPEC = importlib.util.spec_from_file_location("arw_owner_manifest_doctor", SCRIPT)
assert SPEC and SPEC.loader
DOCTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCTOR)
TOOL_ID = "ai-research-workflow"


def _temp_dir() -> Path:
    base = Path(os.environ.get("TEST_TMP_ROOT", str(ROOT / ".test-runtime" / "agent-owner-portability")))
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="arw-owner-", dir=base))


def _relocate(parent: Path) -> Path:
    target = parent / "relocated"
    manifest = json.loads((ROOT / DOCTOR.MANIFEST_NAME).read_text(encoding="utf-8"))
    paths = {DOCTOR.MANIFEST_NAME}
    for group in ("no_write_checks", "runtime_checks"):
        paths.update(item["path"] for item in manifest["provenance"][group])
    for relative in paths:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def _receipt(parent: Path, checked_at: datetime | None = None) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    artifact = parent / "synthetic-result.json"
    artifact.write_text('{"candidate_only":true}\n', encoding="utf-8")
    receipt = parent / "receipt.json"
    payload = {
        "schema_version": DOCTOR.RECEIPT_SCHEMA,
        "tool_id": TOOL_ID,
        "status": "passed",
        "checked_at": (checked_at or datetime.now(timezone.utc)).isoformat(),
        "artifact": {"path": artifact.name, "sha256": DOCTOR.sha256_file(artifact)},
        "truth": {"fresh_e2e_pass": True, "real_usage_proof": False, "current_online": False},
    }
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    return receipt


def test_relocated_doctor_is_no_write_offline_and_idempotent() -> None:
    temp = _temp_dir()
    try:
        root = _relocate(temp)
        before = {p.relative_to(root): DOCTOR.sha256_file(p) for p in root.rglob("*") if p.is_file()}
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            first = DOCTOR.doctor(root, TOOL_ID)
            second = DOCTOR.doctor(root, TOOL_ID)
        after = {p.relative_to(root): DOCTOR.sha256_file(p) for p in root.rglob("*") if p.is_file()}
        assert first == second
        assert before == after
        assert first["status"] == "ready_metadata_only"
        assert first["truth"]["contract_ready"]["value"] is True
        assert first["truth"]["runtime_ready"]["value"] is True
        assert first["truth"]["fresh_e2e_pass"]["value"] is None
        assert first["truth"]["real_usage_proof"]["value"] is False
        assert first["truth"]["current_online"]["value"] is False
        assert first["network_performed"] is False
        assert first["write_performed"] is False
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def test_current_synthetic_receipt_passes_but_stale_and_hash_drift_fail_closed() -> None:
    temp = _temp_dir()
    try:
        root = _relocate(temp)
        current = _receipt(temp)
        assert DOCTOR.doctor(root, TOOL_ID, current)["truth"]["fresh_e2e_pass"]["value"] is True
        stale = _receipt(temp / "stale", datetime.now(timezone.utc) - timedelta(hours=169))
        stale_result = DOCTOR.doctor(root, TOOL_ID, stale)["truth"]["fresh_e2e_pass"]
        assert stale_result["value"] is False
        assert "receipt_stale" in stale_result["reason_codes"]
        artifact = current.parent / "synthetic-result.json"
        artifact.write_text("drift", encoding="utf-8")
        drift = DOCTOR.doctor(root, TOOL_ID, current)["truth"]["fresh_e2e_pass"]
        assert drift["value"] is False
        assert "artifact_hash_drift" in drift["reason_codes"]
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def test_corrupt_manifest_and_path_escape_fail_closed() -> None:
    temp = _temp_dir()
    try:
        root = _relocate(temp)
        manifest_path = root / DOCTOR.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["executionAuthority"] = "execute"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(DOCTOR.DoctorError, match="execute"):
            DOCTOR.doctor(root, TOOL_ID)
        with pytest.raises(DOCTOR.DoctorError, match="escape"):
            DOCTOR.resolve_inside(root, "../escape")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
