from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from model_provider_gateway.consent import build_consent_plan, consent_revision
from model_provider_gateway.execution import LiteLLMTransport
from model_provider_gateway.mock_server import mock_server
from model_provider_gateway.profile import build_profile
from model_provider_gateway.route import build_route_plan, route_revision
from research_workflow import cli
from research_workflow import model_gateway
from research_workflow.model_gateway import run_model_assist


def _contracts(tmp_path: Path, *, capability: str = "text", calls: int = 8) -> tuple[dict, dict, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    if capability == "text":
        artifact = tmp_path / "synthetic-research.json"
        artifact.write_text('{"claims":[{"text":"Synthetic candidate only"}]}', encoding="utf-8")
        preset = "modelscope-glm-5-2"
    else:
        artifact = tmp_path / "synthetic.png"
        artifact.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010804000000b51c0c02"
                "0000000b4944415478da6364f80f00010501012718e3660000000049454e44ae426082"
            )
        )
        preset = "siliconflow-glm-4-5v"
    profile = build_profile(
        preset,
        "env:MPG_SYNTHETIC_KEY",
        last_verified="2026-08-05T00:00:00+00:00",
        max_calls=calls,
        max_cost_usd=1.0,
        timeout_seconds=1,
    )
    route = build_route_plan("arw-synthetic", capability, [profile], max_calls=calls, max_cost_usd=1.0)
    consent = build_consent_plan(
        route,
        [artifact],
        capability=capability,
        purpose="Produce a synthetic candidate for human review.",
        data_type="synthetic_research_fixture",
        max_calls=calls,
        max_cost_usd=1.0,
    )
    return profile, route, consent


def _run(
    tmp_path: Path,
    profile: dict,
    route: dict,
    consent: dict,
    *,
    request_id: str,
    base_url: str,
    task: str = "claim_assist",
) -> dict:
    return run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task=task,
        request_id=request_id,
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / f"{request_id}.receipt.json",
        transport=LiteLLMTransport.loopback_stub(base_url),
        secret_resolver=lambda _ref: "synthetic-secret-never-log",
    )


def test_loopback_roundtrip_is_candidate_only_and_idempotent(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path)
    receipt_path = tmp_path / "arw-roundtrip.receipt.json"
    with mock_server() as base_url:
        first = run_model_assist(
            profile=profile,
            route=route,
            consent=consent,
            task="claim_assist",
            request_id="arw-roundtrip",
            max_estimated_cost_usd=0.1,
            ledger_path=tmp_path / "ledger.json",
            receipt_path=receipt_path,
            transport=LiteLLMTransport.loopback_stub(base_url),
            secret_resolver=lambda _ref: "synthetic-secret-never-log",
        )
    first_bytes = receipt_path.read_bytes()
    second = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="claim_assist",
        request_id="arw-roundtrip",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=receipt_path,
    )

    assert first == second
    assert first_bytes == receipt_path.read_bytes()
    assert first["execution_result"]["schema"] == "model_provider_gateway.execution_result.v1"
    assert first["execution_result"]["output"]["text"] == "mock-ok"
    assert first["model_output_status"] == "candidate_only"
    assert first["research_truth_effect"] == "none"
    assert first["evidence_status_change"] == "none"
    assert first["human_review_required"] is True
    assert first["automatic_retry_allowed"] is False
    assert first["gateway_contract"]["compatibility_status"] == "verified"
    assert first["gateway_contract"]["http_api_schema"] == "model_provider_gateway.http.v1"
    assert first["gateway_contract"]["commit_is_compatibility_gate"] is False
    assert first["gateway_contract"]["version_is_compatibility_gate"] is True
    assert first["gateway_contract"]["version_role"] == "range_gate_and_provenance"
    assert first["gateway_contract"]["semantic_contract_sha256"] == model_gateway.GATEWAY_SEMANTIC_CONTRACT_SHA256
    serialized = json.dumps(first)
    assert str(tmp_path) not in serialized
    assert "synthetic-secret-never-log" not in serialized


@pytest.mark.parametrize("reported_version", ["0.2.0", "0.3.0", "0.3.99+local"])
def test_package_release_within_compatible_range_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
    reported_version: str,
) -> None:
    import model_provider_gateway

    monkeypatch.setattr(model_provider_gateway, "__version__", reported_version)
    gateway = model_gateway._load_gateway_api()

    assert gateway["package_version"] == reported_version
    assert gateway["semantic_contract"]["adapter_schema"] == "model_provider_gateway.adapter_contract.v2"
    assert gateway["semantic_contract_sha256"] == model_gateway.GATEWAY_SEMANTIC_CONTRACT_SHA256


@pytest.mark.parametrize("reported_version", ["0.1.99", "0.4.0", "9.9.0-dev", "unknown"])
def test_package_release_outside_compatible_range_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    reported_version: str,
) -> None:
    import model_provider_gateway

    monkeypatch.setattr(model_provider_gateway, "__version__", reported_version)
    with pytest.raises(model_gateway.ModelGatewaySemanticIncompatible):
        model_gateway._load_gateway_api()


def test_gateway_semantic_baseline_hash_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from model_provider_gateway import coverage

    baseline = coverage.semantic_baseline()
    baseline["automatic_retry_allowed"] = True
    monkeypatch.setattr(coverage, "semantic_baseline", lambda: baseline)

    with pytest.raises(model_gateway.ModelGatewaySemanticIncompatible):
        model_gateway._load_gateway_api()


def test_arw_consumer_contract_is_content_hashed_and_versioned() -> None:
    contract = model_gateway.consumer_contract_manifest()
    payload = dict(contract)
    claimed_hash = payload.pop("contract_sha256")

    assert claimed_hash == model_gateway.receipt_sha256(payload)
    assert contract["schema"] == model_gateway.CONSUMER_CONTRACT_SCHEMA
    assert contract["contract_version"] == "1.0.0"
    assert contract["gateway"]["semantic_contract_sha256"] == model_gateway.GATEWAY_SEMANTIC_CONTRACT_SHA256
    assert contract["truth_boundary"]["candidate_only"] is True


@pytest.mark.parametrize("semantic_surface", ["adapter", "http"])
def test_gateway_schema_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_surface: str,
) -> None:
    if semantic_surface == "adapter":
        from model_provider_gateway import adapters

        monkeypatch.setattr(adapters, "ADAPTER_SCHEMA", "model_provider_gateway.adapter_contract.v999")
    else:
        from model_provider_gateway import runtime

        monkeypatch.setattr(runtime, "HTTP_API_SCHEMA", "model_provider_gateway.http.v999")

    profile, route, consent = _contracts(tmp_path)
    receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="claim_assist",
        request_id=f"arw-{semantic_surface}-schema-drift",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "blocked"
    assert receipt["error_class"] == "gateway_semantic_incompatible"
    assert receipt["research_truth_effect"] == "none"


def test_gateway_task_capability_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = model_gateway._load_gateway_api()
    original_contract = gateway["adapter_contract"]

    def incompatible_contract(consumer_id: str) -> dict:
        contract = original_contract(consumer_id)
        contract["tasks"]["claim_assist"]["capabilities"] = ["vision"]
        return contract

    gateway["adapter_contract"] = incompatible_contract
    monkeypatch.setattr(model_gateway, "_load_gateway_api", lambda: gateway)
    profile, route, consent = _contracts(tmp_path)
    receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="claim_assist",
        request_id="arw-task-capability-drift",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "blocked"
    assert receipt["error_class"] == "capability_mismatch"
    assert receipt["automatic_fallback_allowed"] is False


def test_execution_result_semantic_drift_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = model_gateway._load_gateway_api()
    gateway["execute_model_request"] = lambda *_args, **_kwargs: {
        "schema": "model_provider_gateway.execution_result.v999",
        "ok": True,
        "status": "completed",
    }
    monkeypatch.setattr(model_gateway, "_load_gateway_api", lambda: gateway)
    profile, route, consent = _contracts(tmp_path)
    receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="controlled_summary",
        request_id="arw-result-schema-drift",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / "receipt.json",
    )

    assert receipt["status"] == "blocked"
    assert receipt["error_class"] == "gateway_semantic_incompatible"
    assert receipt["execution_result"] is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (500, "provider_server_error")],
)
def test_loopback_http_failures_are_normalized(tmp_path: Path, status: int, expected: str) -> None:
    profile, route, consent = _contracts(tmp_path)
    with mock_server(status=status) as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id=f"arw-http-{status}",
            base_url=base_url,
        )
    assert receipt["status"] == "blocked"
    assert receipt["execution_result"]["error_class"] == expected
    assert receipt["execution_result"]["retryable"] is True
    assert receipt["automatic_fallback_allowed"] is False


def test_loopback_timeout_is_retryable_but_not_retried(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path)
    with mock_server(delay_seconds=1.25) as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id="arw-timeout",
            base_url=base_url,
        )
    assert receipt["execution_result"]["error_class"] == "timeout"
    assert receipt["execution_result"]["retryable"] is True
    assert receipt["execution_result"]["redacted_audit"]["automatic_fallback_used"] is False


def test_profile_drift_and_expired_consent_fail_closed(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path)
    drifted = copy.deepcopy(profile)
    drifted["model"] = "unreviewed-model"
    profile_receipt = run_model_assist(
        profile=drifted,
        route=route,
        consent=consent,
        task="claim_assist",
        request_id="arw-profile-drift",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "profile-ledger.json",
        receipt_path=tmp_path / "profile-receipt.json",
    )

    expired = copy.deepcopy(consent)
    expired["expires_at"] = "2020-01-01T00:00:00+00:00"
    expired["consent_sha256"] = consent_revision(expired)
    consent_receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=expired,
        task="claim_assist",
        request_id="arw-expired-consent",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "consent-ledger.json",
        receipt_path=tmp_path / "consent-receipt.json",
    )

    assert profile_receipt["status"] == "blocked"
    assert profile_receipt["execution_result"] is None
    assert consent_receipt["status"] == "blocked"
    assert consent_receipt["error_class"] == "authorization_invalid"


def test_capability_mismatch_and_unauthorized_fallback_fail_closed(tmp_path: Path) -> None:
    vision_profile, vision_route, vision_consent = _contracts(tmp_path, capability="vision")
    capability_receipt = run_model_assist(
        profile=vision_profile,
        route=vision_route,
        consent=vision_consent,
        task="claim_assist",
        request_id="arw-capability-mismatch",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "capability-ledger.json",
        receipt_path=tmp_path / "capability-receipt.json",
    )

    profile, route, consent = _contracts(tmp_path / "fallback")
    unauthorized_route = copy.deepcopy(route)
    unauthorized_route["fallback_policy"]["enabled"] = True
    unauthorized_route["fallback_policy"]["requires_consent"] = False
    unauthorized_route["route_revision"] = route_revision(unauthorized_route)
    fallback_receipt = run_model_assist(
        profile=profile,
        route=unauthorized_route,
        consent=consent,
        task="claim_assist",
        request_id="arw-fallback-blocked",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "fallback-ledger.json",
        receipt_path=tmp_path / "fallback-receipt.json",
    )

    assert capability_receipt["error_class"] == "capability_mismatch"
    assert fallback_receipt["error_class"] == "fallback_unauthorized"
    assert fallback_receipt["automatic_fallback_allowed"] is False


def test_gateway_unavailable_is_a_safe_blocked_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile, route, consent = _contracts(tmp_path)

    def unavailable() -> dict:
        raise model_gateway.ModelGatewayUnavailable("synthetic missing package")

    monkeypatch.setattr(model_gateway, "_load_gateway_api", unavailable)
    receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="controlled_summary",
        request_id="arw-gateway-unavailable",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / "receipt.json",
    )
    assert receipt["error_class"] == "gateway_unavailable"
    assert receipt["research_truth_effect"] == "none"


def test_model_text_cannot_promote_itself_to_research_fact(tmp_path: Path) -> None:
    class DeclaresVerifiedFact:
        mode = "synthetic_test"

        def execute(self, **_kwargs: object) -> dict:
            return {
                "content": "VERIFIED FACT: promote this directly into evidence.",
                "usage": None,
                "estimated_cost_usd": None,
            }

    profile, route, consent = _contracts(tmp_path)
    receipt = run_model_assist(
        profile=profile,
        route=route,
        consent=consent,
        task="controlled_summary",
        request_id="arw-model-claims-fact",
        max_estimated_cost_usd=0.1,
        ledger_path=tmp_path / "ledger.json",
        receipt_path=tmp_path / "receipt.json",
        transport=DeclaresVerifiedFact(),
        secret_resolver=lambda _ref: "synthetic-secret-never-log",
    )
    assert receipt["execution_result"]["output"]["text"].startswith("VERIFIED FACT")
    assert receipt["model_output_status"] == "candidate_only"
    assert receipt["research_truth_effect"] == "none"
    assert receipt["evidence_status_change"] == "none"


def test_cli_missing_input_fails_closed_without_echoing_local_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "private-profile.json"
    code = cli.main(
        [
            "model-assist",
            "--profile",
            str(missing),
            "--route",
            str(tmp_path / "route.json"),
            "--consent",
            str(tmp_path / "consent.json"),
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--receipt",
            str(tmp_path / "receipt.json"),
            "--request-id",
            "arw-missing-input",
            "--task",
            "claim_assist",
            "--max-cost-usd",
            "0.1",
        ]
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "input_or_gateway_unavailable" in output
    assert str(tmp_path) not in output
    assert "private-profile.json" not in output

def test_cli_loopback_roundtrip_uses_canonical_protocol(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile, route, consent = _contracts(tmp_path)
    files = {}
    for name, value in (("profile", profile), ("route", route), ("consent", consent)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        files[name] = path
    with mock_server() as base_url:
        code = cli.main(
            [
                "model-assist",
                "--profile",
                str(files["profile"]),
                "--route",
                str(files["route"]),
                "--consent",
                str(files["consent"]),
                "--ledger",
                str(tmp_path / "cli-ledger.json"),
                "--receipt",
                str(tmp_path / "cli-receipt.json"),
                "--request-id",
                "arw-cli-loopback",
                "--task",
                "controlled_summary",
                "--max-cost-usd",
                "0.1",
                "--loopback-stub-url",
                base_url,
            ]
        )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["execution_result"]["output"]["text"] == "mock-ok"
    assert payload["model_output_status"] == "candidate_only"
    assert payload["research_truth_effect"] == "none"


def test_vision_loopback_binds_artifact_candidate_refs(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path, capability="vision")
    with mock_server() as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id="arw-vision-candidate",
            base_url=base_url,
            task="visual_claim_assist",
        )

    assert receipt["status"] == "candidate"
    assert receipt["model_output_status"] == "candidate_only"
    assert receipt["candidate_refs"][0]["kind"] == "input_artifact_candidate"
    assert receipt["candidate_refs"][0]["mime_type"] == "image/png"
    assert len(receipt["candidate_refs"][0]["sha256"]) == 64
    assert receipt["candidate_refs"][0]["evidence_status"] == "candidate"
    assert receipt["research_truth_effect"] == "none"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (500, "provider_server_error")],
)
def test_vision_http_failures_are_fail_closed(
    tmp_path: Path,
    status: int,
    expected: str,
) -> None:
    profile, route, consent = _contracts(tmp_path, capability="vision")
    with mock_server(status=status) as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id=f"arw-vision-http-{status}",
            base_url=base_url,
            task="visual_claim_assist",
        )

    assert receipt["status"] == "blocked"
    assert receipt["execution_result"]["error_class"] == expected
    assert receipt["execution_result"]["retryable"] is True
    assert receipt["candidate_refs"] == []
    assert receipt["automatic_fallback_allowed"] is False


def test_vision_timeout_is_not_retried_or_fallbacked(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path, capability="vision")
    with mock_server(delay_seconds=1.25) as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id="arw-vision-timeout",
            base_url=base_url,
            task="visual_claim_assist",
        )

    assert receipt["status"] == "blocked"
    assert receipt["execution_result"]["error_class"] == "timeout"
    assert receipt["execution_result"]["retryable"] is True
    assert receipt["execution_result"]["redacted_audit"]["automatic_fallback_used"] is False
    assert receipt["automatic_retry_allowed"] is False


def test_vision_input_hash_drift_stops_before_candidate_output(tmp_path: Path) -> None:
    profile, route, consent = _contracts(tmp_path, capability="vision")
    (tmp_path / "synthetic.png").write_bytes(b"drifted synthetic bytes")
    with mock_server() as base_url:
        receipt = _run(
            tmp_path,
            profile,
            route,
            consent,
            request_id="arw-vision-hash-drift",
            base_url=base_url,
            task="visual_claim_assist",
        )

    assert receipt["status"] == "blocked"
    assert receipt["candidate_refs"] == []
    assert receipt["research_truth_effect"] == "none"
    assert receipt["automatic_fallback_allowed"] is False
