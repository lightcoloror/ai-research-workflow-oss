from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from research_workflow.cli import main as cli_main
from research_workflow.evidence_bundle_reference import (
    CANONICAL_SCHEMA_SHA256,
    RECEIPT_SCHEMA,
    verify_evidence_bundle_reference,
)


FIXTURE_SHA256 = "27ae62971f6754009859be87f07a47d9673484c5dea32db0f48e13ce0779a97c"
FIXTURE = Path(__file__).parent / "fixtures" / "evidence-bundle" / "research-output.v1.json"


def _schema_path() -> Path:
    try:
        from question_research_poc.research_output import research_output_schema_path

        return research_output_schema_path()
    except ImportError:
        sibling = Path(__file__).resolve().parents[2] / "question-research-poc" / "schemas" / "research-output.v1.schema.json"
        if sibling.is_file():
            return sibling
        pytest.fail("install the pinned QRP test dependency or provide its sibling checkout")


def _payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _normalize_line_endings(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _write_payload(tmp_path: Path, payload: dict, name: str = "bundle.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _verify(path: Path, tmp_path: Path, *, expected_hash: str | None = None, receipt_name: str = "receipt.json") -> dict:
    return verify_evidence_bundle_reference(
        bundle_path=path,
        schema_path=_schema_path(),
        receipt_path=tmp_path / receipt_name,
        expected_bundle_sha256=expected_hash,
    )


def test_canonical_qrp_fixture_is_accepted_as_a_reference_and_is_idempotent(tmp_path: Path) -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    schema_bytes = _normalize_line_endings(_schema_path().read_bytes())
    assert hashlib.sha256(schema_bytes).hexdigest() == CANONICAL_SCHEMA_SHA256
    receipt_path = tmp_path / "receipt.json"

    first = _verify(FIXTURE, tmp_path, expected_hash=FIXTURE_SHA256)
    first_bytes = receipt_path.read_bytes()
    second = _verify(FIXTURE, tmp_path, expected_hash=FIXTURE_SHA256)

    assert first == second
    assert receipt_path.read_bytes() == first_bytes
    assert first["ok"] is True
    assert first["status"] == "accepted_reference"
    assert first["contract"]["facade_mode"] == "direct_canonical"
    assert first["authoritative_schema_ref"]["hash_mode"] == "utf8_bytes_lf_normalized"
    assert first["authoritative_schema_ref"]["schema_copied_into_arw"] is False
    assert first["imported_into_arw_state"] is False
    assert first["research_truth_effect"] == "none"
    assert first["evidence_status_change"] == "none"
    assert first["candidate_promoted"] is False
    assert str(tmp_path) not in first_bytes.decode("utf-8")


def test_candidate_evidence_never_becomes_reviewed_or_verified(tmp_path: Path) -> None:
    payload = _payload()
    support = next(item for item in payload["evidence"] if item["relation"] == "supports")
    support["evidence_status"] = "candidate"
    support["review_status"] = "candidate"
    next(item for item in payload["evidence_links"] if item["relation"] == "supports")["evidence_status"] = "candidate"
    payload["claims"][0]["review_status"] = "candidate"
    payload["claims"][0]["evidence_status"] = "candidate"
    payload["evidence_status"] = {
        "overall": "candidate",
        "counts": {"candidate": 1, "verified": 0, "rejected": 1, "contradictions": 0},
    }
    path = _write_payload(tmp_path, payload)

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert "candidate_evidence:evidence_fixture_support" in result["blockers"]
    assert result["candidate_promoted"] is False
    assert result["evidence_status_change"] == "none"


def test_missing_support_and_dangling_guarded_refs_fail_closed(tmp_path: Path) -> None:
    payload = _payload()
    payload["evidence"] = [item for item in payload["evidence"] if item["relation"] != "supports"]
    payload["evidence_links"] = [item for item in payload["evidence_links"] if item["relation"] != "supports"]
    path = _write_payload(tmp_path, payload)

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert "claim_missing_verified_support:claim_fixture_contract" in result["blockers"]
    assert "guarded_answer_evidence_ref_missing" in result["blockers"]


def test_verified_contradiction_fails_closed(tmp_path: Path) -> None:
    payload = _payload()
    contradiction = next(item for item in payload["evidence"] if item["relation"] == "contradicts")
    contradiction["evidence_status"] = "verified"
    next(item for item in payload["evidence_links"] if item["relation"] == "contradicts")["evidence_status"] = "verified"
    payload["contradictions"][0]["evidence_status"] = "verified"
    payload["contradictions"][0]["resolution_status"] = "unresolved"
    payload["evidence_status"] = {
        "overall": "contradicted",
        "counts": {"candidate": 0, "verified": 2, "rejected": 0, "contradictions": 1},
    }
    path = _write_payload(tmp_path, payload)

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert "unresolved_verified_contradiction:evidence_fixture_rejected_contradiction" in result["blockers"]
    assert "contradiction_unresolved:evidence_fixture_rejected_contradiction" in result["blockers"]


@pytest.mark.parametrize(
    ("mutation", "expected_blocker"),
    [
        (lambda payload: payload["sources"][0].update({"freshness": "stale"}), "source_stale:source_fixture_primary"),
        (
            lambda payload: payload["sources"][0]["hash"].update({"status": "mismatch"}),
            "source_hash_mismatch:source_fixture_primary",
        ),
        (
            lambda payload: payload["provenance"].update({"model_memory_used_as_evidence": True}),
            "model_memory_used_as_evidence",
        ),
        (
            lambda payload: next(item for item in payload["evidence"] if item["relation"] == "supports").update({"ai_synthesis": True}),
            "ai_synthesis_marked_verified:evidence_fixture_support",
        ),
    ],
)
def test_semantic_negative_cases_fail_closed(tmp_path: Path, mutation, expected_blocker: str) -> None:
    payload = copy.deepcopy(_payload())
    mutation(payload)
    path = _write_payload(tmp_path, payload)

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert expected_blocker in result["blockers"]
    assert result["imported_into_arw_state"] is False


def test_corrupt_bundle_is_a_deterministic_blocked_receipt(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_bytes(b'{"schema":"research-output.v1",')

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert result["blockers"] == ["bundle_invalid_json"]
    assert result["bundle_ref"]["research_id"] is None


def test_schema_hash_drift_blocks_before_using_the_schema(tmp_path: Path) -> None:
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    schema["$id"] = "https://example.invalid/drifted.schema.json"
    drifted_schema = tmp_path / "research-output.v1.schema.json"
    drifted_schema.write_text(json.dumps(schema), encoding="utf-8")

    result = verify_evidence_bundle_reference(
        bundle_path=FIXTURE,
        schema_path=drifted_schema,
        receipt_path=tmp_path / "receipt.json",
    )

    assert result["ok"] is False
    assert result["blockers"] == ["authoritative_schema_hash_drift"]


def test_schema_hash_accepts_platform_newline_conversion(tmp_path: Path) -> None:
    schema = _normalize_line_endings(_schema_path().read_bytes())
    converted = tmp_path / "research-output.v1.schema.json"
    converted.write_bytes(schema.replace(b"\n", b"\r\n"))

    result = verify_evidence_bundle_reference(
        bundle_path=FIXTURE,
        schema_path=converted,
        receipt_path=tmp_path / "receipt.json",
        expected_bundle_sha256=FIXTURE_SHA256,
    )

    assert result["ok"] is True
    assert result["authoritative_schema_ref"]["actual_sha256"] == CANONICAL_SCHEMA_SHA256
    assert result["authoritative_schema_ref"]["hash_mode"] == "utf8_bytes_lf_normalized"


def test_expected_bundle_hash_drift_fails_closed(tmp_path: Path) -> None:
    result = _verify(FIXTURE, tmp_path, expected_hash="0" * 64)

    assert result["ok"] is False
    assert result["blockers"] == ["bundle_hash_drift"]


def test_schema_version_drift_is_rejected_by_the_authoritative_schema(tmp_path: Path) -> None:
    payload = _payload()
    payload["schema_version"] = "2.0.0"
    path = _write_payload(tmp_path, payload)

    result = _verify(path, tmp_path)

    assert result["ok"] is False
    assert "schema_validation_error:/schema_version" in result["blockers"]


def test_receipt_path_rejects_request_drift_without_overwrite(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    first = _verify(FIXTURE, tmp_path, expected_hash=FIXTURE_SHA256)
    first_bytes = receipt_path.read_bytes()
    changed = _payload()
    changed["question"] = "A different synthetic question"
    changed_path = _write_payload(tmp_path, changed, "changed.json")

    with pytest.raises(ValueError, match="request drift"):
        _verify(changed_path, tmp_path, expected_hash=None)

    assert receipt_path.read_bytes() == first_bytes
    assert first["schema"] == RECEIPT_SCHEMA


def test_cli_verifies_reference_without_creating_a_research_data_dir(tmp_path: Path, capsys) -> None:
    receipt_path = tmp_path / "cli-receipt.json"
    data_dir = tmp_path / "must-not-exist"

    exit_code = cli_main(
        [
            "--data-dir",
            str(data_dir),
            "evidence-bundle-verify",
            str(FIXTURE),
            "--schema",
            str(_schema_path()),
            "--receipt",
            str(receipt_path),
            "--expected-bundle-sha256",
            FIXTURE_SHA256,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "accepted_reference"
    assert receipt_path.is_file()
    assert not data_dir.exists()
