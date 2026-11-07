from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from research_workflow.config import ResearchConfig, config_fingerprint
from research_workflow.contract import CONTRACT_VERSION
from research_workflow.diagnostics import run_doctor
from research_workflow.service_observation import (
    build_service_observation,
    classify_runtime_state,
    timestamp_freshness,
)
from research_workflow.service_registry import write_service_record
from research_workflow.service_status import run_service_status


FIXTURES = Path(__file__).parent / "fixtures" / "service_observation_cases.json"


def test_runtime_state_fixture_matrix() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert len(cases) == 6
    for case in cases:
        assert classify_runtime_state(**case["input"]) == case["expected"], case["name"]


def test_expired_persisted_record_has_ttl_and_age() -> None:
    result = timestamp_freshness(
        "2026-07-08 12:10:24",
        300,
        now=datetime(2026, 7, 16, 15, 0, 0),
    )
    assert result == {"ttl_seconds": 300, "age_seconds": 701376, "freshness": "expired"}


def test_observation_contains_stable_consumer_fields(tmp_path) -> None:
    result = build_service_observation(
        config_url="http://127.0.0.1:9082",
        host="127.0.0.1",
        port=9082,
        data_dir=tmp_path,
        service_record_path=tmp_path / "service.json",
        service_record={"status": "running", "pid": 38960, "updated_at": "2026-07-08 12:10:24"},
        legacy_status="stale_record",
        pid=38960,
        process_alive=False,
        live_listener=False,
        health={"ok": False, "error": "timed out"},
        stale_reasons=["pid_not_alive", "no_live_listener", "health_failed"],
        checked_at="2026-07-16T15:00:00+08:00",
    )
    assert result["runtime_state"] == "needs_manual_start"
    assert result["freshness"] == "fresh"
    assert result["ttl_seconds"] == 30
    assert result["persisted_record"]["freshness"] == "expired"
    assert result["source_ref"]["probes"] == ["pid", "tcp_listener", "http_v1_health"]
    assert result["configured"] is True
    assert result["listening"] is False
    assert result["healthy"] is False


def test_status_is_idempotent_and_does_not_rewrite_persisted_record(tmp_path, monkeypatch) -> None:
    import research_workflow.service_status as module

    config = ResearchConfig(web_port=9082, search_providers=[])
    write_service_record(
        tmp_path,
        base_url="http://127.0.0.1:9082",
        pid=38960,
        extra={"contract_version": CONTRACT_VERSION, "config_hash": config_fingerprint(config)},
    )
    path = tmp_path / "service.json"
    before = path.read_bytes()
    monkeypatch.setattr(module, "now_iso", lambda: "2026-07-16T15:00:00+08:00")
    monkeypatch.setattr(module, "_process_exists", lambda pid: False)
    monkeypatch.setattr(module, "_can_connect", lambda host, port, timeout_seconds: False)
    monkeypatch.setattr(module, "_fetch_json", lambda url, timeout_seconds: {"ok": False, "error": "timed out"})

    first = run_service_status(config, tmp_path, timeout_seconds=0.01)
    second = run_service_status(config, tmp_path, timeout_seconds=0.01)

    assert first == second
    assert path.read_bytes() == before
    assert first["status"] == "stale_record"
    assert first["runtime_state"] == "needs_manual_start"
    assert first["observed_service_record"]["persisted_record"]["present"] is True
    assert "configured service is not listening" in first["recommended_action"].lower()


def test_listener_without_health_is_failed(tmp_path, monkeypatch) -> None:
    import research_workflow.service_status as module

    config = ResearchConfig(web_port=9082, search_providers=[])
    write_service_record(
        tmp_path,
        base_url="http://127.0.0.1:9082",
        pid=38960,
        extra={"contract_version": CONTRACT_VERSION, "config_hash": config_fingerprint(config)},
    )
    monkeypatch.setattr(module, "_process_exists", lambda pid: False)
    monkeypatch.setattr(module, "_can_connect", lambda host, port, timeout_seconds: True)
    monkeypatch.setattr(module, "_fetch_json", lambda url, timeout_seconds: {"ok": False, "error": "invalid health response"})

    result = run_service_status(config, tmp_path, timeout_seconds=0.01)

    assert result["runtime_state"] == "failed"
    assert result["live_listener"] is True
    assert result["observed_service_record"]["healthy"] is False
    assert "inspect ownership and logs" in result["recommended_action"].lower()


def test_doctor_exposes_same_observation_shape(tmp_path, monkeypatch) -> None:
    import research_workflow.diagnostics as module

    observation = {
        "schema_version": "research-service-observation.v1",
        "runtime_state": "stopped_by_design",
        "checked_at": "2026-07-16T15:00:00+08:00",
        "ttl_seconds": 30,
        "freshness": "fresh",
    }
    monkeypatch.setattr(
        module,
        "run_service_status",
        lambda config, data_dir, timeout_seconds: {
            "runtime_state": "stopped_by_design",
            "observed_service_record": observation,
        },
    )
    monkeypatch.setattr(module, "_check_searxng", lambda config: module.DoctorCheck("searxng", "ok", "fixture"))
    result = run_doctor(ResearchConfig(search_providers=[]), tmp_path, include_pipeline=False)

    assert result["observed_service_record"] == observation
    service_check = next(check for check in result["checks"] if check["name"] == "service_runtime")
    assert service_check["status"] == "ok"
    assert "stopped by design" in service_check["message"].lower()
