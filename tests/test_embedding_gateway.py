from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import jsonschema
import pytest

from research_workflow import embedding_gateway, model_gateway
from research_workflow.embedding_gateway import (
    EMBEDDING_RECEIPT_SCHEMA,
    run_embedding_candidate,
    validate_embedding_candidate_receipt,
)


def _artifact(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "synthetic-evidence.json"
    path.write_text('{"claim":"candidate only"}', encoding="utf-8")
    return path


def _configured_runtime() -> tuple[Path, Path]:
    python_path = Path(os.environ.get("MPG_EMBEDDING_PYTHON", ""))
    model_path = Path(os.environ.get("MPG_EMBEDDING_MODEL_PATH", ""))
    if not python_path.is_file() or not (model_path / "modules.json").is_file():
        pytest.skip("explicit local embedding runtime/model are not configured")
    from model_provider_gateway.capability_control import embedding_runtime_probe

    try:
        probe = embedding_runtime_probe()
    except ValueError as exc:
        pytest.skip(f"local embedding runtime doctor failed closed: {exc}")
    if not probe.get("runtime_ready"):
        pytest.skip(f"local embedding runtime blocker: {probe.get('status')}: {probe.get('reason')}")
    return python_path, model_path


@pytest.fixture(scope="module")
def real_candidate(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    python_path, model_path = _configured_runtime()
    root = tmp_path_factory.mktemp("real-embedding")
    artifact = _artifact(root)
    receipt_path = root / "embedding-receipt.json"
    owner_output_config = os.environ.get("ARW_EMBEDDING_TEST_OUTPUT_DIR", "")
    owner_output = Path(owner_output_config) if owner_output_config else root / "owner-run"
    kwargs = {
        "artifact_paths": [artifact],
        "purpose": "Index one synthetic research candidate for comparison only.",
        "request_id": "arw-embedding-real-local",
        "receipt_path": receipt_path,
        "embedding_python": python_path,
        "embedding_model": model_path,
        "owner_output_dir": owner_output,
        "expected_dimensions": 384,
        "timeout_seconds": 60,
    }

    first = run_embedding_candidate(**kwargs)
    first_bytes = receipt_path.read_bytes()
    second = run_embedding_candidate(**kwargs)

    assert first == second
    assert first_bytes == receipt_path.read_bytes()
    return artifact, first


def test_real_local_embedding_is_candidate_only_and_idempotent(real_candidate) -> None:
    artifact, receipt = real_candidate

    assert receipt["schema"] == EMBEDDING_RECEIPT_SCHEMA
    assert receipt["status"] == "candidate", receipt.get("error_class")
    assert receipt["owner_receipt"]["status"] == "completed"
    assert receipt["consent_receipt"]["status"] == "granted_local"
    assert receipt["consent_receipt"]["local_execution_authorized"] is True
    assert receipt["execution_performed"] is True
    assert receipt["provider_called"] is False
    assert receipt["external_io_performed"] is False
    assert receipt["capability_truth"] == {
        "contract_ready": True,
        "runtime_ready": True,
        "fresh_e2e_pass": True,
        "real_usage_proof": False,
        "current_online": False,
        "runtime_status": "ready_local_on_demand_isolated_sentence_transformers",
        "runtime_reason": "canonical local owner adapter completed for synthetic input",
    }
    assert receipt["candidate_refs"][0]["dimensions"] == 384
    assert all("vectors" not in ref for ref in receipt["candidate_refs"])
    assert receipt["gateway_contract"]["commit_role"] == "provenance_only"
    assert receipt["gateway_contract"]["commit_is_compatibility_gate"] is False
    assert receipt["gateway_contract"]["version_is_compatibility_gate"] is True
    assert receipt["gateway_contract"]["semantic_contract_sha256"] == model_gateway.GATEWAY_SEMANTIC_CONTRACT_SHA256
    assert validate_embedding_candidate_receipt(receipt, artifact_paths=[artifact]) is receipt


def test_embedding_gateway_semantic_baseline_drift_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from model_provider_gateway import coverage

    baseline = coverage.semantic_baseline()
    baseline["silent_fallback_allowed"] = True
    monkeypatch.setattr(coverage, "semantic_baseline", lambda: baseline)

    with pytest.raises(embedding_gateway.EmbeddingGatewaySemanticIncompatible):
        embedding_gateway._load_embedding_gateway_api()


def test_runtime_and_model_missing_fail_closed_precisely(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    missing_runtime = run_embedding_candidate(
        artifact_paths=[artifact],
        purpose="Synthetic missing runtime negative.",
        request_id="arw-embedding-runtime-missing",
        receipt_path=tmp_path / "runtime-missing.json",
        embedding_python=tmp_path / "missing-python.exe",
        embedding_model=tmp_path / "missing-model",
    )
    missing_model = run_embedding_candidate(
        artifact_paths=[artifact],
        purpose="Synthetic missing model negative.",
        request_id="arw-embedding-model-missing",
        receipt_path=tmp_path / "model-missing.json",
        embedding_python=sys.executable,
        embedding_model=tmp_path / "missing-model",
    )

    assert (missing_runtime["status"], missing_runtime["error_class"]) == (
        "blocked",
        "runtime_missing",
    )
    assert (missing_model["status"], missing_model["error_class"]) == (
        "blocked",
        "model_missing",
    )
    for result in (missing_runtime, missing_model):
        assert result["candidate_refs"] == []
        assert result["provider_called"] is False
        assert result["external_io_performed"] is False
        assert result["automatic_fallback_allowed"] is False


def test_timeout_from_canonical_owner_execution_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _artifact(tmp_path)
    model = tmp_path / "synthetic-model-contract"
    model.mkdir()
    (model / "modules.json").write_text("{}", encoding="utf-8")
    gateway = embedding_gateway._load_embedding_gateway_api()

    def timed_out(*_args, **_kwargs):
        raise TimeoutError("synthetic canonical owner execution timed out")

    gateway["execute_owner_capability"] = timed_out
    monkeypatch.setattr(embedding_gateway, "_load_embedding_gateway_api", lambda: gateway)
    result = run_embedding_candidate(
        artifact_paths=[artifact],
        purpose="Synthetic timeout negative.",
        request_id="arw-embedding-timeout",
        receipt_path=tmp_path / "timeout.json",
        embedding_python=sys.executable,
        embedding_model=model,
        timeout_seconds=1,
    )

    assert result["status"] == "blocked"
    assert result["error_class"] == "timeout"
    assert result["candidate_refs"] == []
    assert result["automatic_retry_allowed"] is False
    assert result["automatic_fallback_allowed"] is False


@pytest.fixture
def contract_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    artifact = _artifact(tmp_path)
    model = tmp_path / "synthetic-model-contract"
    model.mkdir()
    (model / "modules.json").write_text("{}", encoding="utf-8")
    gateway = embedding_gateway._load_embedding_gateway_api()

    def completed_bundle(
        capability,
        adapter_id,
        artifact_paths,
        *,
        output_dir,
        consumer_id,
        purpose,
        data_classification,
        runtime,
        timeout_seconds,
    ):
        from model_provider_gateway.capability_control import build_owner_capability_plan
        from model_provider_gateway.owner_execution import build_owner_gate

        manifest = gateway["build_owner_input_manifest"](
            artifact_paths,
            purpose=purpose,
            data_classification=data_classification,
        )
        plan = build_owner_capability_plan(
            capability,
            adapter_id,
            consumer_id=consumer_id,
            input_manifest_hash=manifest["manifest_revision"],
            options={"timeout_seconds": timeout_seconds, "no_retry": True, "no_fallback": True},
        )
        gate = build_owner_gate(plan, manifest)
        output = {
            "kind": "embedding_manifest",
            "vectors_sha256": "a" * 64,
            "dimensions": 384,
            "item_count": len(artifact_paths),
            "model_revision": runtime["model_revision"],
        }
        owner_receipt = {
            "schema": "model_provider_gateway.owner_capability_receipt.v1",
            "status": "completed",
            "plan_id": plan["plan_id"],
            "plan_revision": plan["plan_revision"],
            "consumer_id": consumer_id,
            "capability": capability,
            "adapter_id": adapter_id,
            "input_manifest_hash": manifest["manifest_revision"],
            "owner_gate_receipt_hash": gate["gate_revision"],
            "external_io_performed": False,
            "provider_called": False,
            "automatic_fallback_used": False,
            "output": output,
            "error": None,
        }
        gateway["validate_owner_capability_receipt"](owner_receipt, plan=plan)
        report = {
            "schema": "model_provider_gateway.owner_runtime_run_report.v1",
            "status": "completed",
            "capability": capability,
            "adapter_id": adapter_id,
            "plan_revision": plan["plan_revision"],
            "input_manifest_hash": manifest["manifest_revision"],
            "gate_revision": gate["gate_revision"],
            "receipt_hash": gateway["sha256_json"](owner_receipt),
            "runtime": {
                "execution_location": "local",
                "runtime_id": "synthetic-contract-only",
                "latency_ms": 0,
                "provider_called": False,
                "external_io_performed": False,
                "automatic_retry_count": 0,
                "automatic_fallback_used": False,
            },
            "output_refs": ["embedding-vectors.json"],
            "error": None,
        }
        return {"manifest": manifest, "plan": plan, "gate": gate, "receipt": owner_receipt, "report": report}

    gateway["execute_owner_capability"] = completed_bundle
    monkeypatch.setattr(embedding_gateway, "_load_embedding_gateway_api", lambda: gateway)
    receipt = run_embedding_candidate(
        artifact_paths=[artifact],
        purpose="Synthetic contract validation only.",
        request_id="arw-embedding-contract-fixture",
        receipt_path=tmp_path / "contract-receipt.json",
        embedding_python=sys.executable,
        embedding_model=model,
    )
    assert receipt["status"] == "candidate"
    return artifact, receipt


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["runtime_binding"].__setitem__("expected_dimensions", 768),
            "dimension drift",
        ),
        (
            lambda value: value["runtime_binding"].__setitem__("model_revision", "0" * 64),
            "model hash drift",
        ),
        (
            lambda value: value["owner_plan"].__setitem__("plan_revision", "0" * 64),
            "was expected|drift",
        ),
        (
            lambda value: value["owner_receipt"].__setitem__("automatic_fallback_used", True),
            "False was expected|fallback",
        ),
        (
            lambda value: value["candidate_refs"][0].__setitem__("vectors", [[0.1]]),
            "raw embedding vectors",
        ),
        (
            lambda value: value.__setitem__("claims", [{"text": "invented"}]),
            "research facts",
        ),
    ],
)
def test_dimension_model_route_fallback_and_fact_drift_fail_closed(
    contract_candidate,
    mutation,
    message: str,
) -> None:
    artifact, receipt = contract_candidate
    drifted = copy.deepcopy(receipt)
    mutation(drifted)

    with pytest.raises((ValueError, jsonschema.ValidationError), match=message):
        validate_embedding_candidate_receipt(drifted, artifact_paths=[artifact])


def test_input_hash_and_mime_drift_fail_closed(contract_candidate, tmp_path: Path) -> None:
    artifact, receipt = contract_candidate
    original = artifact.read_bytes()
    artifact.write_text('{"claim":"drifted"}', encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="input_hash_mismatch"):
            validate_embedding_candidate_receipt(receipt, artifact_paths=[artifact])
    finally:
        artifact.write_bytes(original)

    other = _artifact(tmp_path / "mime")
    with pytest.raises(ValueError, match="mime_mismatch"):
        run_embedding_candidate(
            artifact_paths=[other],
            mime_types=["image/png"],
            purpose="Synthetic MIME binding.",
            request_id="arw-embedding-mime",
            receipt_path=tmp_path / "mime-receipt.json",
        )
