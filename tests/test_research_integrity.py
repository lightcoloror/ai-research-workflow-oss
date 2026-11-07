from __future__ import annotations

import os

import pytest

import research_workflow.research_integrity as integrity_module
from research_workflow.quality import quality_for_report
from research_workflow.research_integrity import (
    REPLAY_SCHEMA,
    REPLAY_SCHEMA_VERSION,
    build_replay_bundle,
    evaluate_research_integrity,
    load_replay_bundle,
    validate_multihop_case,
    validate_replay_bundle,
    write_replay_bundle,
)


def _report() -> dict:
    return {
        "topic": "local integrity",
        "generated_at": "2026-07-30T00:00:00+00:00",
        "sources": [
            {
                "id": "s1",
                "url": "https://example.test/source",
                "verification_status": "verified",
                "published_at": "2026-07-01T00:00:00+00:00",
                "retrieved_at": "2026-07-30T00:00:00+00:00",
                "local_path": "local://fixtures/source-s1.json",
                "artifact_sha256": "source-fixture-hash",
            }
        ],
        "evidence": [
            {
                "id": "e1",
                "source_id": "s1",
                "support_direction": "supported",
                "quote_or_paraphrase": "The source supports the claim.",
                "verification_status": "verified",
                "observed_at": "2026-07-30T00:01:00+00:00",
            }
        ],
        "claim_table": [
            {
                "id": "c1",
                "text": "A locally supported claim.",
                "verdict": "supported",
                "risk_level": "normal",
                "confidence": 0.9,
                "evidence_ids": ["e1"],
            }
        ],
        "claim_citations": [
            {
                "claim_id": "c1",
                "evidence_ids": ["e1"],
                "source_ids": ["s1"],
                "citations": ["https://example.test/source"],
            }
        ],
        "findings": ["A locally supported claim."],
    }


def test_integrity_gate_separates_citation_record_and_existence() -> None:
    result = evaluate_research_integrity(_report())

    assert result["status"] == "pass"
    assert result["deliverable_ready"] is True
    assert result["claim_checks"][0]["citation_record"] == "present"
    assert result["claim_checks"][0]["citation_existence"] == "verified"
    assert result["passport"]["schema"] == "research.material-passport.v1"
    source = result["passport"]["source_manifest"][0]
    evidence = result["passport"]["evidence_manifest"][0]
    assert source["published_at"] == "2026-07-01T00:00:00+00:00"
    assert source["retrieved_at"] == "2026-07-30T00:00:00+00:00"
    assert source["local_path"] == "local://fixtures/source-s1.json"
    assert source["artifact_sha256"] == "source-fixture-hash"
    assert evidence["verification_status"] == "verified"
    assert evidence["observed_at"] == "2026-07-30T00:01:00+00:00"


def test_integrity_gate_fails_closed_with_material_gap() -> None:
    report = _report()
    report["evidence"] = []

    result = evaluate_research_integrity(report, previous_score=1.0)

    assert result["status"] == "blocked"
    assert result["deliverable_ready"] is False
    assert result["material_gaps"]
    assert result["score_trajectory"]["regressed"] is True


def test_integrity_gate_rejects_evidence_with_missing_source_record() -> None:
    report = _report()
    report["sources"] = []

    result = evaluate_research_integrity(report)

    assert result["status"] == "blocked"
    assert result["deliverable_ready"] is False
    assert "[MATERIAL GAP: source for c1]" in result["material_gaps"]
    assert "source_record_missing" in result["claim_checks"][0]["reasons"]


def test_quality_summary_exposes_integrity_gate_additively() -> None:
    result = quality_for_report(_report())

    assert result["quality_summary"]["integrity_gate"]["status"] == "pass"


def test_multihop_checks_require_every_independent_verdict() -> None:
    complete = {
        "id": "case-1",
        "checks": {
            "factual_correctness": True,
            "answer_uniqueness": True,
            "temporal_stability": True,
            "entity_dependency": True,
            "independent_reproduction": True,
        },
    }
    incomplete = {
        "id": "case-2",
        "checks": {
            "factual_correctness": True,
        },
    }

    assert validate_multihop_case(complete)["status"] == "pass"
    blocked = validate_multihop_case(incomplete)
    assert blocked["status"] == "blocked"
    assert len(blocked["material_gaps"]) == 4


def test_cached_tool_replay_is_deterministic_and_tamper_evident() -> None:
    case = {
        "id": "case-1",
        "checks": {
            name: True
            for name in (
                "factual_correctness",
                "answer_uniqueness",
                "temporal_stability",
                "entity_dependency",
                "independent_reproduction",
            )
        },
    }
    bundle = build_replay_bundle(case, [{"tool": "search", "result": ["a", "b"]}])

    assert validate_replay_bundle(bundle)["validation"]["status"] == "pass"
    bundle["payload"]["tool_responses"][0]["result"].append("tampered")
    assert validate_replay_bundle(bundle) == {
        "status": "blocked",
        "reason": "replay_hash_mismatch",
    }


@pytest.mark.parametrize(
    ("target", "status", "blocker_kind"),
    [
        ("evidence", "candidate", "evidence_not_verified"),
        ("source", "stale", "source_not_verified"),
        ("evidence", "corrupt", "evidence_not_verified"),
    ],
)
def test_integrity_gate_fails_closed_for_degraded_evidence(
    target: str,
    status: str,
    blocker_kind: str,
) -> None:
    report = _report()
    rows = report["evidence"] if target == "evidence" else report["sources"]
    rows[0]["verification_status"] = status

    result = evaluate_research_integrity(report)

    assert result["status"] == "blocked"
    assert result["deliverable_ready"] is False
    assert blocker_kind in {item["kind"] for item in result["blockers"]}
    assert status in result["claim_checks"][0][f"{target}_states"]


def test_replay_v1_migrates_and_duplicate_writes_are_stable(tmp_path) -> None:
    bundle = build_replay_bundle(
        {
            "id": "legacy-case",
            "checks": {name: True for name in integrity_module.REQUIRED_MULTIHOP_CHECKS},
        },
        [{"tool": "local-fixture", "result": {"status": "verified"}}],
    )
    legacy = dict(bundle)
    legacy["schema"] = "research.tool-replay.v1"
    legacy.pop("schema_version", None)
    legacy.pop("bundle_id", None)

    migrated = validate_replay_bundle(legacy)

    assert migrated["status"] == "pass"
    assert migrated["schema"] == REPLAY_SCHEMA
    assert migrated["schema_version"] == REPLAY_SCHEMA_VERSION
    assert migrated["migrations"] == ["research.tool-replay.v1->research.tool-replay.v2"]
    assert migrated["migration_hashes"] == {
        "before": bundle["payload_hash"],
        "after": bundle["payload_hash"],
    }
    assert migrated["payload_hash"] == bundle["payload_hash"]

    path = tmp_path / "replay.json"
    write_replay_bundle(path, bundle)
    first = path.read_bytes()
    write_replay_bundle(path, bundle)
    assert path.read_bytes() == first
    assert validate_replay_bundle(load_replay_bundle(path))["status"] == "pass"


def test_replay_write_recovers_after_partial_failure(tmp_path, monkeypatch) -> None:
    bundle = build_replay_bundle(
        {"id": "retry-case", "checks": {name: True for name in integrity_module.REQUIRED_MULTIHOP_CHECKS}},
        [{"tool": "local-fixture", "result": {"status": "candidate"}}],
    )
    path = tmp_path / "replay.json"
    original_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("synthetic replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(integrity_module.os, "replace", flaky_replace)

    with pytest.raises(OSError, match="synthetic replace failure"):
        write_replay_bundle(path, bundle)
    assert path.exists() is False
    assert list(tmp_path.glob("replay.json.tmp.*")) == []

    write_replay_bundle(path, bundle)
    assert validate_replay_bundle(load_replay_bundle(path))["status"] == "pass"


def test_corrupt_replay_loads_as_fail_closed_state(tmp_path) -> None:
    path = tmp_path / "corrupt-replay.json"
    path.write_text("{", encoding="utf-8")

    loaded = load_replay_bundle(path)

    assert loaded["load_error"] == "corrupt_replay"
    assert validate_replay_bundle(loaded) == {
        "status": "blocked",
        "reason": "corrupt_replay",
    }
def test_contradicting_evidence_is_not_a_material_gap() -> None:
    report = _report()
    report["claim_table"][0]["verdict"] = "contradicted"
    report["evidence"][0]["support_direction"] = "contradicted"

    result = evaluate_research_integrity(report)

    assert result["status"] == "pass"
    assert result["deliverable_ready"] is False
    assert result["material_gaps"] == []
    assert result["claim_checks"][0]["claim_support"] == "contradicted"
    assert result["non_supported_claims"] == ["c1"]
