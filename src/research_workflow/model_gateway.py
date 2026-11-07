"""Thin, fail-closed consumer for the canonical model-provider gateway."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .metadata import PACKAGE_VERSION


RECEIPT_SCHEMA = "ai_research_workflow.model_assist_receipt.v1"
CONSUMER_CONTRACT_SCHEMA = "ai_research_workflow.gateway_consumer_contract.v1"
CONSUMER_CONTRACT_VERSION = "1.0.0"
GATEWAY_REVIEWED_VERSION = "0.3.0"
CANONICAL_GATEWAY_COMMIT = "7f97e784332999de699a0544cfe7147bad440312"
GATEWAY_COMPATIBLE_VERSION_RANGE = {
    "minimum_inclusive": "0.2.0",
    "maximum_exclusive": "0.4.0",
}
GATEWAY_SEMANTIC_CONTRACT_SHA256 = "f9bf3bab27e767b093a761da5e04760b097a29c8e60aaee00f5b1ffe120bafa5"
GATEWAY_SEMANTIC_CONTRACT = {
    "http_api_schema": "model_provider_gateway.http.v1",
    "adapter_schema": "model_provider_gateway.adapter_contract.v2",
    "adapter_request_schema": "model_provider_gateway.adapter_request.v2",
    "execution_request_schema": "model_provider_gateway.execution_request.v1",
    "execution_result_schema": "model_provider_gateway.execution_result.v1",
    "consent_schema": "model_provider_gateway.consent_manifest.v2",
    "error_fields": ["error_class", "error_message", "retryable"],
    "silent_fallback_allowed": False,
    "automatic_retry_allowed": False,
}
SUPPORTED_CONSUMERS = {"arw", "qrp"}
SUPPORTED_TASKS = {"research_query", "claim_assist", "controlled_summary", "visual_claim_assist"}
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+].*)?$")


class ModelGatewayUnavailable(RuntimeError):
    """Raised internally when the separately installed gateway is unavailable."""


class ModelGatewaySemanticIncompatible(RuntimeError):
    """Raised when the installed gateway does not satisfy the reviewed contract."""


def run_model_assist(
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    task: str,
    request_id: str,
    max_estimated_cost_usd: float,
    ledger_path: str | Path,
    receipt_path: str | Path,
    consumer_id: str = "arw",
    transport: Any | None = None,
    secret_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Run one explicitly authorized model-assist call and persist its receipt.

    The returned text is always candidate-only. This module deliberately has no
    dependency on ARW Source, Evidence, Claim, or Verdict models.
    """
    if consumer_id not in SUPPORTED_CONSUMERS:
        raise ValueError("unsupported model gateway consumer")
    if task not in SUPPORTED_TASKS:
        raise ValueError("unsupported model-assist task")
    if not request_id:
        raise ValueError("model-assist request_id is required")

    receipt_file = Path(receipt_path)
    fingerprint = _request_fingerprint(
        profile=profile,
        route=route,
        consent=consent,
        task=task,
        request_id=request_id,
        max_estimated_cost_usd=max_estimated_cost_usd,
        consumer_id=consumer_id,
    )
    with _receipt_lock(receipt_file):
        existing = _read_existing_receipt(receipt_file, fingerprint)
        if existing is not None:
            return existing

        gateway: dict[str, Any] | None = None
        try:
            gateway = _load_gateway_api()
            request = gateway["build_execution_request"](
                profile,
                route,
                consent,
                consumer_id=consumer_id,
                task=task,
                max_estimated_cost_usd=max_estimated_cost_usd,
                request_id=request_id,
            )
            if request.get("schema") != GATEWAY_SEMANTIC_CONTRACT["execution_request_schema"]:
                raise ModelGatewaySemanticIncompatible("gateway execution request schema drift")
            if consent.get("schema") != GATEWAY_SEMANTIC_CONTRACT["consent_schema"]:
                raise ModelGatewaySemanticIncompatible("gateway consent schema drift")
            contract = gateway["adapter_contract"](consumer_id)
            _validate_consumer_contract(
                contract,
                consumer_id=consumer_id,
                task=task,
                capability=str(request["capability"]),
            )
            adapter_request = {
                "schema": GATEWAY_SEMANTIC_CONTRACT["adapter_request_schema"],
                "consumer_id": consumer_id,
                "task": task,
                "required_capabilities": [request["capability"]],
                "route_id": request["route_id"],
                "route_revision": request["route_revision"],
                "consent_hash": request["consent_hash"],
                "input_manifest_hash": request["input_manifest_hash"],
            }
            gateway["validate_adapter_request"](
                adapter_request,
                contract,
            )
            kwargs: dict[str, Any] = {
                "profile": profile,
                "route": route,
                "consent": consent,
                "ledger_path": ledger_path,
            }
            if transport is not None:
                kwargs["transport"] = transport
            if secret_resolver is not None:
                kwargs["secret_resolver"] = secret_resolver
            result = gateway["execute_model_request"](request, **kwargs)
            _validate_execution_result(result)
            receipt = _result_receipt(
                consumer_id=consumer_id,
                task=task,
                request_id=request_id,
                fingerprint=fingerprint,
                profile=profile,
                route=route,
                consent=consent,
                result=result,
                gateway=gateway,
            )
        except Exception as exc:  # All preflight detail is reduced to a stable safe class.
            receipt = _blocked_receipt(
                consumer_id=consumer_id,
                task=task,
                request_id=request_id,
                fingerprint=fingerprint,
                error_class=_safe_error_class(exc),
                gateway=gateway,
            )

        _write_receipt(receipt_file, receipt)
        return receipt


def receipt_sha256(receipt: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(receipt)).hexdigest()


def consumer_contract_manifest() -> dict[str, Any]:
    """Return the versioned ARW delegation contract consumed by QRP."""
    payload = {
        "schema": CONSUMER_CONTRACT_SCHEMA,
        "contract_version": CONSUMER_CONTRACT_VERSION,
        "package_version": PACKAGE_VERSION,
        "receipt_schemas": {
            "model_assist": RECEIPT_SCHEMA,
            "embedding_candidate": "ai_research_workflow.embedding_candidate_receipt.v1",
        },
        "consumers": sorted(SUPPORTED_CONSUMERS),
        "tasks": sorted(SUPPORTED_TASKS | {"embedding_candidate"}),
        "gateway": {
            "compatible_version_range": dict(GATEWAY_COMPATIBLE_VERSION_RANGE),
            "semantic_contract_sha256": GATEWAY_SEMANTIC_CONTRACT_SHA256,
            "git_commit_role": "provenance_only",
        },
        "truth_boundary": {
            "research_truth_effect": "none",
            "evidence_status_change": "none",
            "candidate_only": True,
            "silent_fallback_allowed": False,
        },
    }
    return {**payload, "contract_sha256": hashlib.sha256(_canonical_bytes(payload)).hexdigest()}


def _load_gateway_api() -> dict[str, Any]:
    try:
        import model_provider_gateway
        from model_provider_gateway.adapters import (
            ADAPTER_REQUEST_SCHEMA,
            ADAPTER_SCHEMA,
            adapter_contract,
            validate_adapter_request,
        )
        from model_provider_gateway.consent import CONSENT_SCHEMA
        from model_provider_gateway.coverage import semantic_baseline
        from model_provider_gateway.execution import (
            EXECUTION_REQUEST_SCHEMA,
            EXECUTION_RESULT_SCHEMA,
            build_execution_request,
            execute_model_request,
        )
    except ImportError as exc:
        raise ModelGatewayUnavailable("canonical model gateway is not installed") from exc

    try:
        from model_provider_gateway.runtime import HTTP_API_SCHEMA
    except ImportError:
        HTTP_API_SCHEMA = None

    package_version = str(getattr(model_provider_gateway, "__version__", "unknown"))
    release_contract = _validate_gateway_release_contract(
        package_version=package_version,
        semantic_contract=semantic_baseline(),
    )
    observed_contract = release_contract["semantic_contract"]
    constants = {
        "adapter_schema": ADAPTER_SCHEMA,
        "adapter_request_schema": ADAPTER_REQUEST_SCHEMA,
        "execution_request_schema": EXECUTION_REQUEST_SCHEMA,
        "execution_result_schema": EXECUTION_RESULT_SCHEMA,
        "consent_schema": CONSENT_SCHEMA,
    }
    for field, value in constants.items():
        if value != observed_contract[field]:
            raise ModelGatewaySemanticIncompatible(f"gateway {field} drift")
    if HTTP_API_SCHEMA is not None and HTTP_API_SCHEMA != GATEWAY_SEMANTIC_CONTRACT["http_api_schema"]:
        raise ModelGatewaySemanticIncompatible("gateway HTTP API schema drift")
    return {
        "adapter_contract": adapter_contract,
        "validate_adapter_request": validate_adapter_request,
        "build_execution_request": build_execution_request,
        "execute_model_request": execute_model_request,
        "package_version": package_version,
        "semantic_contract": observed_contract,
        "semantic_contract_sha256": release_contract["semantic_contract_sha256"],
        "compatible_version_range": release_contract["compatible_version_range"],
    }


def _validate_gateway_release_contract(
    *,
    package_version: str,
    semantic_contract: object,
) -> dict[str, Any]:
    if not isinstance(semantic_contract, dict) or semantic_contract != GATEWAY_SEMANTIC_CONTRACT:
        raise ModelGatewaySemanticIncompatible("gateway semantic baseline drift")
    semantic_hash = hashlib.sha256(_canonical_bytes(semantic_contract)).hexdigest()
    if semantic_hash != GATEWAY_SEMANTIC_CONTRACT_SHA256:
        raise ModelGatewaySemanticIncompatible("gateway semantic baseline hash drift")
    _require_compatible_version(package_version, GATEWAY_COMPATIBLE_VERSION_RANGE, label="Gateway")
    return {
        "semantic_contract": dict(semantic_contract),
        "semantic_contract_sha256": semantic_hash,
        "compatible_version_range": dict(GATEWAY_COMPATIBLE_VERSION_RANGE),
    }


def _require_compatible_version(value: object, version_range: dict[str, str], *, label: str) -> None:
    parsed = _parse_version(value)
    minimum = _parse_version(version_range["minimum_inclusive"])
    maximum = _parse_version(version_range["maximum_exclusive"])
    if parsed < minimum or parsed >= maximum:
        raise ModelGatewaySemanticIncompatible(f"{label} version is outside the compatible range")


def _parse_version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise ModelGatewaySemanticIncompatible("semantic version is missing")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise ModelGatewaySemanticIncompatible("semantic version is invalid")
    return tuple(int(part) for part in match.groups())


def _validate_consumer_contract(
    contract: dict[str, Any],
    *,
    consumer_id: str,
    task: str,
    capability: str,
) -> None:
    if contract.get("schema") != GATEWAY_SEMANTIC_CONTRACT["adapter_schema"]:
        raise ModelGatewaySemanticIncompatible("gateway adapter contract schema drift")
    if contract.get("consumer_id") != consumer_id:
        raise ModelGatewaySemanticIncompatible("gateway adapter consumer drift")
    task_contract = (contract.get("tasks") or {}).get(task)
    if not isinstance(task_contract, dict):
        raise ModelGatewaySemanticIncompatible("gateway adapter task contract drift")
    if task_contract.get("execution_status") != "enabled":
        raise ModelGatewaySemanticIncompatible("gateway adapter task is not enabled")
    if task_contract.get("control_policy") != "shared_consent":
        raise ModelGatewaySemanticIncompatible("gateway adapter consent control drift")
    if capability not in set(task_contract.get("capabilities") or []):
        raise ValueError("gateway adapter task capability mismatch")
    forbidden = set(contract.get("forbidden_fields") or [])
    if not {"provider_url", "api_key", "headers", "raw_json", "fallback"} <= forbidden:
        raise ModelGatewaySemanticIncompatible("gateway adapter safety contract drift")


def _validate_execution_result(result: object) -> None:
    if not isinstance(result, dict):
        raise ModelGatewaySemanticIncompatible("gateway execution result is not an object")
    if result.get("schema") != GATEWAY_SEMANTIC_CONTRACT["execution_result_schema"]:
        raise ModelGatewaySemanticIncompatible("gateway execution result schema drift")
    for field in GATEWAY_SEMANTIC_CONTRACT["error_fields"]:
        if field not in result:
            raise ModelGatewaySemanticIncompatible("gateway execution error semantics drift")
    if not isinstance(result.get("retryable"), bool):
        raise ModelGatewaySemanticIncompatible("gateway retryable semantics drift")
    audit = result.get("redacted_audit")
    if not isinstance(audit, dict) or audit.get("automatic_fallback_used") is not False:
        raise ModelGatewaySemanticIncompatible("gateway fallback safety semantics drift")


def _gateway_contract_receipt(gateway: dict[str, Any] | None) -> dict[str, Any]:
    observed = dict((gateway or {}).get("semantic_contract") or GATEWAY_SEMANTIC_CONTRACT)
    return {
        **observed,
        "interface_mode": "direct_python_projection",
        "compatibility_status": "verified" if gateway is not None else "unavailable",
        "compatibility_basis": "semantic_contract_not_git_identity",
        "semantic_contract_sha256": (gateway or {}).get(
            "semantic_contract_sha256", GATEWAY_SEMANTIC_CONTRACT_SHA256
        ),
        "compatible_version_range": dict(
            (gateway or {}).get("compatible_version_range") or GATEWAY_COMPATIBLE_VERSION_RANGE
        ),
        "commit": CANONICAL_GATEWAY_COMMIT,
        "version": (gateway or {}).get("package_version"),
        "reviewed_version": GATEWAY_REVIEWED_VERSION,
        "commit_role": "provenance_only",
        "version_role": "range_gate_and_provenance",
        "commit_is_compatibility_gate": False,
        "version_is_compatibility_gate": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
        "request_schema": observed["execution_request_schema"],
        "result_schema": observed["execution_result_schema"],
    }


def _request_fingerprint(**values: Any) -> str:
    return hashlib.sha256(_canonical_bytes(values)).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")



def _candidate_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for artifact in result.get("artifact_refs") or []:
        if not isinstance(artifact, dict):
            continue
        refs.append(
            {
                "kind": "input_artifact_candidate",
                "artifact_id": artifact.get("artifact_id"),
                "sha256": artifact.get("sha256"),
                "bytes": artifact.get("bytes"),
                "mime_type": artifact.get("mime_type"),
                "purpose": artifact.get("purpose"),
                "evidence_status": "candidate",
            }
        )
    output = result.get("output")
    text = output.get("text") if isinstance(output, dict) else None
    if isinstance(text, str):
        refs.append(
            {
                "kind": "model_output_candidate",
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "evidence_status": "candidate",
            }
        )
    return refs

def _result_receipt(
    *,
    consumer_id: str,
    task: str,
    request_id: str,
    fingerprint: str,
    profile: dict[str, Any],
    route: dict[str, Any],
    consent: dict[str, Any],
    result: dict[str, Any],
    gateway: dict[str, Any],
) -> dict[str, Any]:
    ok = bool(result.get("ok")) and result.get("status") == "completed"
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": ok,
        "status": "candidate" if ok else "blocked",
        "consumer_id": consumer_id,
        "task": task,
        "request_id": request_id,
        "request_fingerprint": fingerprint,
        "gateway_contract": _gateway_contract_receipt(gateway),
        "profile_ref": {
            "profile_id": profile.get("profile_id"),
            "profile_revision": profile.get("route_revision"),
        },
        "route_receipt": {
            "route_id": route.get("route_id"),
            "route_revision": route.get("route_revision"),
        },
        "consent_receipt": {
            "consent_hash": consent.get("consent_sha256"),
            "input_manifest_hash": (consent.get("input_policy") or {}).get("input_manifest_sha256"),
        },
        "execution_result": result,
        "candidate_refs": _candidate_refs(result) if ok else [],
        "model_output_status": "candidate_only" if ok else "unavailable",
        "research_truth_effect": "none",
        "evidence_status_change": "none",
        "human_review_required": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }


def _blocked_receipt(
    *,
    consumer_id: str,
    task: str,
    request_id: str,
    fingerprint: str,
    error_class: str,
    gateway: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "ok": False,
        "status": "blocked",
        "consumer_id": consumer_id,
        "task": task,
        "request_id": request_id,
        "request_fingerprint": fingerprint,
        "gateway_contract": _gateway_contract_receipt(gateway),
        "profile_ref": None,
        "route_receipt": None,
        "consent_receipt": None,
        "execution_result": None,
        "candidate_refs": [],
        "error_class": error_class,
        "error_message": "model-assist preflight failed closed",
        "model_output_status": "unavailable",
        "research_truth_effect": "none",
        "evidence_status_change": "none",
        "human_review_required": True,
        "automatic_fallback_allowed": False,
        "automatic_retry_allowed": False,
    }


def _safe_error_class(exc: Exception) -> str:
    if isinstance(exc, ModelGatewayUnavailable):
        return "gateway_unavailable"
    if isinstance(exc, ModelGatewaySemanticIncompatible):
        return "gateway_semantic_incompatible"
    text = str(exc).lower()
    if "fallback" in text:
        return "fallback_unauthorized"
    if "capabilit" in text:
        return "capability_mismatch"
    if "consent" in text or "expired" in text or "authorization" in text:
        return "authorization_invalid"
    if "profile" in text or "revision" in text or "drift" in text:
        return "profile_or_route_drift"
    return "consumer_preflight_blocked"


def _read_existing_receipt(path: Path, fingerprint: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("existing model-assist receipt is unreadable") from exc
    if not isinstance(value, dict) or value.get("schema") != RECEIPT_SCHEMA:
        raise ValueError("existing model-assist receipt schema is invalid")
    if value.get("request_fingerprint") != fingerprint:
        raise ValueError("model-assist receipt request drift detected")
    return value


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_canonical_bytes(value) + b"\n")
    os.replace(temporary, path)


@contextmanager
def _receipt_lock(path: Path, *, timeout_seconds: float = 5.0) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            lock.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("model-assist receipt lock timed out")
            time.sleep(0.02)
    try:
        yield
    finally:
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass
