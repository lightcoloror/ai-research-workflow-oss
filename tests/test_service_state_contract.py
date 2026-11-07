from __future__ import annotations

import json
import time
from pathlib import Path

from research_workflow.service_state_contract import build_service_state_contract


FIXTURES = Path(__file__).parent / "fixtures" / "service_state_contract_cases.json"


def test_service_state_contract_cases_are_explicit_and_offline() -> None:
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert [case["name"] for case in cases] == ["stopped", "stale_pid", "config_mismatch", "healthy_mock", "health_timeout"]
    for case in cases:
        started = time.perf_counter()
        result = build_service_state_contract(case["status"])
        assert time.perf_counter() - started < 0.1
        for key, value in case["expected"].items():
            assert result[key] == value, f"{case['name']}: {key}"
        assert result["configured_endpoint_is_online_proof"] is False
        assert result["evaluation"] == {"pure": True, "performed_io": False, "started_service": False, "changed_state": False}


def test_recovery_drafts_never_execute_and_start_requires_approval() -> None:
    case = json.loads(FIXTURES.read_text(encoding="utf-8"))[1]
    result = build_service_state_contract(case["status"])
    commands = result["recovery_command_drafts"]
    assert commands
    assert all(command["executed"] is False for command in commands)
    start = next(command for command in commands if command["purpose"] == "start_service_from_single_config_source")
    assert start["requires_approval"] is True
    assert result["interface_patch_proposal"]["proposal_only"] is True
    assert result["interface_patch_proposal"]["applied"] is False
