"""Candidate-only ARW consumer for Gateway owner embedding receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model_gateway import (
    CANONICAL_GATEWAY_COMMIT,
    GATEWAY_COMPATIBLE_VERSION_RANGE,
    GATEWAY_SEMANTIC_CONTRACT_SHA256,
    ModelGatewaySemanticIncompatible,
    SUPPORTED_CONSUMERS,
    _canonical_bytes,
    _receipt_lock,
    _validate_gateway_release_contract,
    _write_receipt,
)

EMBEDDING_RECEIPT_SCHEMA = "ai_research_workflow.embedding_candidate_receipt.v1"
EMBEDDING_TASK = "embedding_candidate"
EXPECTED_GATEWAY_CONTRACT = {
    "adapter_schema": "model_provider_gateway.adapter_contract.v2",
    "owner_plan_schema": "model_provider_gateway.owner_capability_plan.v1",
    "owner_receipt_schema": "model_provider_gateway.owner_capability_receipt.v1",
    "owner_input_manifest_schema": "model_provider_gateway.owner_input_manifest.v1",
    "owner_gate_schema": "model_provider_gateway.owner_capability_gate.v1",
    "owner_run_report_schema": "model_provider_gateway.owner_runtime_run_report.v1",
}


class EmbeddingGatewayUnavailable(RuntimeError):
    """Raised when the separately installed Gateway owner contract is unavailable."""


class EmbeddingGatewaySemanticIncompatible(RuntimeError):
    """Raised when Gateway owner semantics drift from the reviewed contract."""


class EmbeddingOwnerExecutionBlocked(RuntimeError):
    """Carries one stable fail-closed owner execution category."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class


def run_embedding_candidate(
    *,
    artifact_paths: list[str | Path],
    purpose: str,
    request_id: str,
    receipt_path: str | Path,
    consumer_id: str = "arw",
    mime_types: list[str] | None = None,
    embedding_python: str | Path | None = None,
    embedding_model: str | Path | None = None,
    owner_output_dir: str | Path | None = None,
    embedding_device: str = "cpu",
    expected_dimensions: int = 384,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Run the canonical local owner adapter and expose candidate metadata only."""
    if consumer_id not in SUPPORTED_CONSUMERS:
        raise ValueError("unsupported embedding consumer")
    if not request_id or not purpose:
        raise ValueError("embedding request_id and purpose are required")
    paths = [Path(value).expanduser().resolve() for value in artifact_paths]
    gateway = _load_embedding_gateway_api()
    manifest = gateway["build_owner_input_manifest"](
        paths,
        purpose=purpose,
        data_classification="synthetic_fixture",
        mime_types=mime_types,
    )
    gateway["validate_owner_input_manifest"](manifest, artifact_paths=paths)
    runtime_binding = _runtime_binding(
        embedding_python=embedding_python,
        embedding_model=embedding_model,
        embedding_device=embedding_device,
        expected_dimensions=expected_dimensions,
        timeout_seconds=timeout_seconds,
    )
    fingerprint = hashlib.sha256(
        _canonical_bytes(
            {
                "consumer_id": consumer_id,
                "request_id": request_id,
                "purpose": purpose,
                "manifest_revision": manifest["manifest_revision"],
                "execution_mode": "gateway_owner_run_local",
                "runtime_binding": runtime_binding,
            }
        )
    ).hexdigest()
    target = Path(receipt_path).expanduser().resolve()
    run_root = (
        Path(owner_output_dir).expanduser().resolve()
        if owner_output_dir is not None
        else target.parent / f"{target.stem}.owner-run"
    )
    with _receipt_lock(target):
        existing = _read_existing(target, fingerprint=fingerprint, artifact_paths=paths)
        if existing is not None:
            return existing
        try:
            _require_local_runtime(runtime_binding)
            contract = gateway["adapter_contract"](consumer_id)
            _validate_owner_task(contract, consumer_id=consumer_id)
            bundle = gateway["execute_owner_capability"](
                "embedding",
                "sentence-transformers-local",
                paths,
                output_dir=run_root,
                consumer_id=consumer_id,
                purpose=purpose,
                data_classification="synthetic_fixture",
                runtime={
                    "embedding_python": str(Path(embedding_python).expanduser().resolve()),
                    "embedding_model": str(Path(embedding_model).expanduser().resolve()),
                    "embedding_device": embedding_device,
                    "model_revision": runtime_binding["model_revision"],
                },
                timeout_seconds=timeout_seconds,
            )
            if bundle.get("manifest") != manifest:
                raise ValueError("embedding input manifest drift detected")
            plan = bundle["plan"]
            gate = bundle["gate"]
            owner_receipt = gateway["validate_owner_capability_receipt"](
                bundle["receipt"],
                plan=plan,
            )
            gateway["validate_owner_gate"](gate, plan=plan, manifest=manifest)
            report = bundle["report"]
            if owner_receipt.get("status") != "completed" or report.get("status") != "completed":
                error = owner_receipt.get("error") or report.get("error") or {}
                raise EmbeddingOwnerExecutionBlocked(str(error.get("category") or "owner_execution_failed"))
            output = owner_receipt.get("output") or {}
            if output.get("dimensions") != expected_dimensions:
                raise EmbeddingOwnerExecutionBlocked("dimension_drift")
            if output.get("item_count") != len(paths):
                raise EmbeddingOwnerExecutionBlocked("item_count_drift")
            if output.get("model_revision") != runtime_binding["model_revision"]:
                raise EmbeddingOwnerExecutionBlocked("model_hash_drift")
            if report.get("receipt_hash") != gateway["sha256_json"](owner_receipt):
                raise EmbeddingOwnerExecutionBlocked("receipt_hash_drift")
            receipt = _candidate_receipt(
                consumer_id=consumer_id,
                request_id=request_id,
                fingerprint=fingerprint,
                manifest=manifest,
                plan=plan,
                gate=gate,
                owner_receipt=owner_receipt,
                report=report,
                runtime_binding=runtime_binding,
                gateway=gateway,
            )
            validate_embedding_candidate_receipt(receipt, artifact_paths=paths)
        except Exception as exc:
            receipt = _blocked_receipt(
                consumer_id=consumer_id,
                request_id=request_id,
                fingerprint=fingerprint,
                error_class=_safe_error_class(exc),
                gateway=gateway,
            )
        _write_receipt(target, receipt)
        return receipt


def validate_embedding_candidate_receipt(
    receipt: dict[str, Any],
    *,
    artifact_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    if receipt.get("schema") != EMBEDDING_RECEIPT_SCHEMA:
        raise EmbeddingGatewaySemanticIncompatible("embedding receipt schema drift")
    if receipt.get("research_truth_effect") != "none" or receipt.get("evidence_status_change") != "none":
        raise ValueError("embedding candidate may not mutate research truth")
    if receipt.get("automatic_fallback_allowed") is not False:
        raise ValueError("embedding fallback must remain disabled")
    if any(field in receipt for field in ("claims", "verified_facts", "vectors")):
        raise ValueError("embedding receipt may not contain research facts or raw vectors")
    if receipt.get("status") == "blocked":
        if receipt.get("ok") is not False or receipt.get("candidate_refs"):
            raise ValueError("blocked embedding receipt cannot expose candidates")
        return receipt
    if receipt.get("status") != "candidate" or receipt.get("ok") is not True:
        raise ValueError("embedding receipt must be candidate or blocked")

    gateway = _load_embedding_gateway_api()
    manifest = receipt.get("input_manifest")
    plan = receipt.get("owner_plan")
    gate = receipt.get("owner_gate")
    owner_receipt = receipt.get("owner_receipt")
    owner_report = receipt.get("owner_run_receipt")
    runtime_binding = receipt.get("runtime_binding")
    if not all(isinstance(value, dict) for value in (manifest, plan, gate, owner_receipt, owner_report, runtime_binding)):
        raise ValueError("embedding receipt contract bundle is incomplete")
    if artifact_paths is None:
        raise ValueError("artifact paths are required to revalidate embedding input hashes")
    gateway["validate_owner_input_manifest"](
        manifest,
        artifact_paths=[Path(value).expanduser().resolve() for value in artifact_paths],
    )
    gateway["validate_owner_capability_receipt"](owner_receipt, plan=plan)
    gateway["validate_owner_gate"](gate, plan=plan, manifest=manifest)
    _validate_owner_task(gateway["adapter_contract"](receipt.get("consumer_id")), consumer_id=receipt.get("consumer_id"))
    if plan.get("input_manifest_hash") != manifest.get("manifest_revision"):
        raise ValueError("embedding route drift detected")
    profile_ref = receipt.get("profile_ref") or {}
    route_receipt = receipt.get("route_receipt") or {}
    consent_receipt = receipt.get("consent_receipt") or {}
    if profile_ref.get("adapter_id") != plan.get("adapter_id"):
        raise ValueError("embedding profile drift detected")
    if route_receipt.get("plan_revision") != plan.get("plan_revision"):
        raise ValueError("embedding route drift detected")
    if (
        consent_receipt.get("status") != "granted_local"
        or consent_receipt.get("local_execution_authorized") is not True
        or consent_receipt.get("provider_call_authorized") is not False
        or consent_receipt.get("external_io_authorized") is not False
        or consent_receipt.get("owner_gate_receipt_hash") != gate.get("gate_revision")
        or consent_receipt.get("input_manifest_hash") != manifest.get("manifest_revision")
    ):
        raise ValueError("embedding consent drift detected")
    if owner_receipt.get("status") != "completed":
        raise ValueError("embedding candidate requires a completed local owner receipt")
    if (
        owner_receipt.get("provider_called") is not False
        or owner_receipt.get("external_io_performed") is not False
        or owner_receipt.get("automatic_fallback_used") is not False
    ):
        raise ValueError("embedding local execution may not claim provider, external I/O, or fallback")
    runtime = owner_report.get("runtime") or {}
    if (
        owner_report.get("status") != "completed"
        or owner_report.get("receipt_hash") != gateway["sha256_json"](owner_receipt)
        or runtime.get("provider_called") is not False
        or runtime.get("external_io_performed") is not False
        or runtime.get("automatic_retry_count") != 0
        or runtime.get("automatic_fallback_used") is not False
    ):
        raise ValueError("embedding owner run receipt drift detected")
    output = owner_receipt.get("output") or {}
    if output.get("dimensions") != runtime_binding.get("expected_dimensions"):
        raise ValueError("embedding dimension drift detected")
    if output.get("model_revision") != runtime_binding.get("model_revision"):
        raise ValueError("embedding model hash drift detected")
    candidate_refs = receipt.get("candidate_refs") or []
    if not candidate_refs or any(
        candidate_refs[0].get(key) != output.get(key)
        for key in ("kind", "vectors_sha256", "dimensions", "item_count", "model_revision")
    ):
        raise ValueError("embedding candidate reference drift detected")
    if any("vectors" in ref for ref in candidate_refs if isinstance(ref, dict)):
        raise ValueError("raw embedding vectors are forbidden")
    return receipt


def _load_embedding_gateway_api() -> dict[str, Any]:
    try:
        import model_provider_gateway
        from model_provider_gateway.adapters import ADAPTER_SCHEMA, adapter_contract
        from model_provider_gateway.capability_control import (
            OWNER_PLAN_SCHEMA,
            OWNER_RECEIPT_SCHEMA,
            validate_owner_capability_receipt,
        )
        from model_provider_gateway.owner_execution import (
            OWNER_GATE_SCHEMA,
            OWNER_INPUT_MANIFEST_SCHEMA,
            OWNER_RUN_REPORT_SCHEMA,
            build_owner_input_manifest,
            execute_owner_capability,
            validate_owner_gate,
            validate_owner_input_manifest,
        )
        from model_provider_gateway.canonical import sha256_json
        from model_provider_gateway.coverage import semantic_baseline
    except ImportError as exc:
        raise EmbeddingGatewayUnavailable("canonical embedding owner contract is not installed") from exc
    observed = {
        "adapter_schema": ADAPTER_SCHEMA,
        "owner_plan_schema": OWNER_PLAN_SCHEMA,
        "owner_receipt_schema": OWNER_RECEIPT_SCHEMA,
        "owner_input_manifest_schema": OWNER_INPUT_MANIFEST_SCHEMA,
        "owner_gate_schema": OWNER_GATE_SCHEMA,
        "owner_run_report_schema": OWNER_RUN_REPORT_SCHEMA,
    }
    if observed != EXPECTED_GATEWAY_CONTRACT:
        raise EmbeddingGatewaySemanticIncompatible("Gateway embedding owner schema drift")
    package_version = str(getattr(model_provider_gateway, "__version__", "unknown"))
    try:
        release_contract = _validate_gateway_release_contract(
            package_version=package_version,
            semantic_contract=semantic_baseline(),
        )
    except ModelGatewaySemanticIncompatible as exc:
        raise EmbeddingGatewaySemanticIncompatible("Gateway release contract drift") from exc
    return {
        "adapter_contract": adapter_contract,
        "validate_owner_capability_receipt": validate_owner_capability_receipt,
        "build_owner_input_manifest": build_owner_input_manifest,
        "execute_owner_capability": execute_owner_capability,
        "validate_owner_gate": validate_owner_gate,
        "validate_owner_input_manifest": validate_owner_input_manifest,
        "sha256_json": sha256_json,
        "semantic_contract": observed,
        "semantic_contract_sha256": release_contract["semantic_contract_sha256"],
        "compatible_version_range": release_contract["compatible_version_range"],
        "package_version": package_version,
    }


def _validate_owner_task(contract: dict[str, Any], *, consumer_id: str) -> None:
    if contract.get("schema") != EXPECTED_GATEWAY_CONTRACT["adapter_schema"]:
        raise EmbeddingGatewaySemanticIncompatible("Gateway adapter schema drift")
    if contract.get("consumer_id") != consumer_id:
        raise EmbeddingGatewaySemanticIncompatible("Gateway embedding consumer drift")
    task = (contract.get("tasks") or {}).get(EMBEDDING_TASK)
    if not isinstance(task, dict):
        raise EmbeddingGatewaySemanticIncompatible("Gateway embedding task is missing")
    if task.get("execution_status") != "blocked" or task.get("control_policy") != "none":
        raise EmbeddingGatewaySemanticIncompatible("embedding must remain owner-receipt-only")
    if task.get("capabilities") != ["embedding"]:
        raise EmbeddingGatewaySemanticIncompatible("Gateway embedding capability drift")


def _candidate_receipt(
    *,
    consumer_id: str,
    request_id: str,
    fingerprint: str,
    manifest: dict[str, Any],
    plan: dict[str, Any],
    gate: dict[str, Any],
    owner_receipt: dict[str, Any],
    report: dict[str, Any],
    runtime_binding: dict[str, Any],
    gateway: dict[str, Any],
) -> dict[str, Any]:
    output = owner_receipt["output"]
    return {
        "schema": EMBEDDING_RECEIPT_SCHEMA,
        "ok": True,
        "status": "candidate",
        "consumer_id": consumer_id,
        "task": EMBEDDING_TASK,
        "request_id": request_id,
        "request_fingerprint": fingerprint,
        "gateway_contract": {
            **gateway["semantic_contract"],
            "semantic_contract_sha256": gateway["semantic_contract_sha256"],
            "compatible_version_range": gateway["compatible_version_range"],
            "package_version": gateway["package_version"],
            "canonical_commit": CANONICAL_GATEWAY_COMMIT,
            "commit_role": "provenance_only",
            "commit_is_compatibility_gate": False,
            "version_role": "range_gate_and_provenance",
            "version_is_compatibility_gate": True,
        },
        "profile_ref": {"mode": "owner_adapter", "adapter_id": plan["adapter_id"], "capability": "embedding"},
        "route_receipt": {
            "plan_id": plan["plan_id"],
            "plan_revision": plan["plan_revision"],
            "owner_gate_receipt_hash": gate["gate_revision"],
        },
        "consent_receipt": {
            "status": "granted_local",
            "local_execution_authorized": True,
            "provider_call_authorized": False,
            "external_io_authorized": False,
            "owner_gate_receipt_hash": gate["gate_revision"],
            "input_manifest_hash": manifest["manifest_revision"],
        },
        "input_manifest": manifest,
        "owner_plan": plan,
        "owner_gate": gate,
        "owner_receipt": owner_receipt,
        "owner_run_receipt": {
            "schema": report["schema"],
            "status": report["status"],
            "capability": report["capability"],
            "adapter_id": report["adapter_id"],
            "plan_revision": report["plan_revision"],
            "input_manifest_hash": report["input_manifest_hash"],
            "gate_revision": report["gate_revision"],
            "receipt_hash": report["receipt_hash"],
            "runtime": report["runtime"],
            "output_refs": report["output_refs"],
            "error": report["error"],
        },
        "runtime_binding": runtime_binding,
        "evidence_refs": [
            {**row, "evidence_status": "candidate"}
            for row in manifest["artifacts"]
        ],
        "candidate_refs": [
            {
                "kind": output["kind"],
                "vectors_sha256": output["vectors_sha256"],
                "dimensions": output["dimensions"],
                "item_count": output["item_count"],
                "model_revision": output["model_revision"],
                "evidence_status": "candidate",
            }
        ],
        "capability_truth": {
            "contract_ready": True,
            "runtime_ready": True,
            "fresh_e2e_pass": True,
            "real_usage_proof": False,
            "current_online": False,
            "runtime_status": "ready_local_on_demand_isolated_sentence_transformers",
            "runtime_reason": "canonical local owner adapter completed for synthetic input",
        },
        "execution_performed": True,
        "provider_called": False,
        "external_io_performed": False,
        "model_output_status": "candidate_only",
        "research_truth_effect": "none",
        "evidence_status_change": "none",
        "human_review_required": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }


def _blocked_receipt(
    *,
    consumer_id: str,
    request_id: str,
    fingerprint: str,
    error_class: str,
    gateway: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema": EMBEDDING_RECEIPT_SCHEMA,
        "ok": False,
        "status": "blocked",
        "consumer_id": consumer_id,
        "task": EMBEDDING_TASK,
        "request_id": request_id,
        "request_fingerprint": fingerprint,
        "gateway_contract": {
            **dict((gateway or {}).get("semantic_contract") or EXPECTED_GATEWAY_CONTRACT),
            "semantic_contract_sha256": (gateway or {}).get(
                "semantic_contract_sha256", GATEWAY_SEMANTIC_CONTRACT_SHA256
            ),
            "compatible_version_range": dict(
                (gateway or {}).get("compatible_version_range") or GATEWAY_COMPATIBLE_VERSION_RANGE
            ),
            "commit_is_compatibility_gate": False,
            "version_is_compatibility_gate": True,
        },
        "error_class": error_class,
        "error_message": "embedding candidate contract failed closed",
        "candidate_refs": [],
        "execution_performed": False,
        "provider_called": False,
        "external_io_performed": False,
        "model_output_status": "unavailable",
        "research_truth_effect": "none",
        "evidence_status_change": "none",
        "human_review_required": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }


def _read_existing(
    path: Path,
    *,
    fingerprint: str,
    artifact_paths: list[Path],
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("existing embedding receipt is unreadable") from exc
    if value.get("request_fingerprint") != fingerprint:
        raise ValueError("embedding receipt request drift detected")
    return validate_embedding_candidate_receipt(value, artifact_paths=artifact_paths)


def _safe_error_class(exc: Exception) -> str:
    if isinstance(exc, EmbeddingOwnerExecutionBlocked):
        return exc.error_class
    if isinstance(exc, EmbeddingGatewayUnavailable):
        return "gateway_unavailable"
    if isinstance(exc, EmbeddingGatewaySemanticIncompatible):
        return "gateway_semantic_incompatible"
    if isinstance(exc, TimeoutError):
        return "timeout"
    text = str(exc).lower()
    if "fallback" in text:
        return "fallback_unauthorized"
    if "consent" in text or "gate" in text or "authoriz" in text:
        return "authorization_invalid"
    if "mime" in text:
        return "mime_mismatch"
    if "hash" in text:
        return "input_hash_mismatch"
    if "dependency" in text or "tokenizers" in text or "transformers" in text:
        return "dependency_conflict"
    if "route" in text or "profile" in text or "revision" in text or "drift" in text:
        return "route_or_profile_drift"
    return "embedding_contract_blocked"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_binding(
    *,
    embedding_python: str | Path | None,
    embedding_model: str | Path | None,
    embedding_device: str,
    expected_dimensions: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    python_path = Path(embedding_python).expanduser().resolve() if embedding_python else None
    model_path = Path(embedding_model).expanduser().resolve() if embedding_model else None
    modules_path = model_path / "modules.json" if model_path else None
    return {
        "runtime_path_provided": embedding_python is not None,
        "runtime_sha256": _sha256_file(python_path) if python_path and python_path.is_file() else None,
        "model_path_provided": embedding_model is not None,
        "model_revision": _sha256_file(modules_path) if modules_path and modules_path.is_file() else None,
        "model_source": "explicit_local_files_only",
        "device": embedding_device,
        "expected_dimensions": int(expected_dimensions),
        "timeout_seconds": int(timeout_seconds),
    }


def _require_local_runtime(binding: dict[str, Any]) -> None:
    if not binding.get("runtime_path_provided") or not binding.get("runtime_sha256"):
        raise EmbeddingOwnerExecutionBlocked("runtime_missing")
    if not binding.get("model_path_provided") or not binding.get("model_revision"):
        raise EmbeddingOwnerExecutionBlocked("model_missing")
    if binding.get("device") not in {"cpu", "cuda"}:
        raise EmbeddingOwnerExecutionBlocked("runtime_device_invalid")
    if int(binding.get("expected_dimensions") or 0) <= 0:
        raise EmbeddingOwnerExecutionBlocked("dimension_contract_invalid")
    if int(binding.get("timeout_seconds") or 0) <= 0:
        raise EmbeddingOwnerExecutionBlocked("timeout_contract_invalid")
