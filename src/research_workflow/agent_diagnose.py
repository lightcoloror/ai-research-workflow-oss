from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_smoke import run_agent_smoke
from .config import ResearchConfig, config_fingerprint
from .contract import CONTRACT_VERSION
from .diagnostics import run_doctor
from .jobs import list_jobs
from .launcher import web_status
from .metadata import version_info
from .service_registry import read_service_record


def run_agent_diagnose(
    data_dir: Path,
    config: ResearchConfig,
    *,
    base_url: str,
    include_smoke: bool = True,
    job_limit: int = 10,
) -> dict[str, Any]:
    service_record = read_service_record(data_dir)
    launcher_status = web_status(data_dir, config)
    doctor = run_doctor(config, data_dir, include_pipeline=False)
    jobs = list_jobs(data_dir, limit=job_limit)
    smoke = run_agent_smoke(base_url, timeout_seconds=60, interval_seconds=1) if include_smoke else {"overall": "skipped"}
    integration_status = _integration_status(config, base_url)
    metadata = version_info(config)
    overall = _overall(launcher_status, doctor, smoke, integration_status)
    return {
        "overall": overall,
        "base_url": base_url.rstrip("/"),
        "version": metadata,
        "package_version": metadata.get("package_version", ""),
        "git_commit": metadata.get("git_commit", ""),
        "git_remote": metadata.get("git_remote", ""),
        "contract_version": CONTRACT_VERSION,
        "config_hash": config_fingerprint(config),
        "service_record": service_record,
        "launcher": launcher_status,
        "doctor": {
            "overall": doctor.get("overall"),
            "checks": [
                {"name": item.get("name"), "status": item.get("status"), "message": item.get("message", "")}
                for item in doctor.get("checks", [])
            ],
        },
        "agent_smoke": smoke,
        "integration_status": integration_status,
        "recommended_next_action": _recommended_next_action(launcher_status, doctor, smoke, integration_status, jobs),
        "recent_jobs": [
            {
                "id": job.get("id"),
                "kind": job.get("kind"),
                "status": job.get("status"),
                "stage": job.get("stage"),
                "updated_at": job.get("updated_at"),
                "client_request_id": (job.get("payload") or {}).get("_client_request_id") if isinstance(job.get("payload"), dict) else "",
                "error": job.get("error", ""),
                "result": job.get("result", {}),
            }
            for job in jobs
        ],
    }


def agent_diagnose_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Agent Diagnose",
        "",
        f"- Overall: `{result.get('overall')}`",
        f"- Base URL: `{result.get('base_url')}`",
        f"- Launcher running: `{str((result.get('launcher') or {}).get('running')).lower()}`",
        f"- Doctor: `{(result.get('doctor') or {}).get('overall')}`",
        f"- Agent smoke: `{(result.get('agent_smoke') or {}).get('overall')}`",
        f"- Integrations: `{(result.get('integration_status') or {}).get('overall')}`",
        f"- Recommended next action: {result.get('recommended_next_action', '')}",
        "",
        "## Recent Jobs",
        "",
    ]
    jobs = result.get("recent_jobs") or []
    if not jobs:
        lines.append("- No recent jobs.")
    for job in jobs:
        lines.append(f"- `{job.get('status')}` `{job.get('id')}` {job.get('kind')} / {job.get('stage')}")
    return "\n".join(lines) + "\n"


def agent_diagnose_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def _overall(launcher_status: dict[str, Any], doctor: dict[str, Any], smoke: dict[str, Any], integration_status: dict[str, Any]) -> str:
    if smoke.get("overall") == "error":
        return "error"
    if doctor.get("overall") == "error":
        return "error"
    if integration_status.get("overall") == "error":
        return "warn"
    if not launcher_status.get("running") and not (launcher_status.get("service_record") or {}).get("pid"):
        return "warn"
    if smoke.get("overall") in {"ok", "skipped"} and doctor.get("overall") in {"ok", "warn"} and integration_status.get("overall") in {"ok", "warn"}:
        return "ok"
    return "warn"


def _integration_status(config: ResearchConfig, base_url: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    integrations_dir = root / "integrations"
    manifest_path = integrations_dir / "research-workflow.integration.json"
    discovery_path = integrations_dir / "agent-discovery.json"
    python_client_path = integrations_dir / "research_workflow_client.py"
    js_client_path = integrations_dir / "research-workflow-client.mjs"
    expected_hash = config_fingerprint(config)
    checks: list[dict[str, Any]] = []
    manifest = _read_json(manifest_path)
    discovery = _read_json(discovery_path)
    checks.append(_metadata_check("manifest", manifest, expected_hash, base_url))
    checks.append(_metadata_check("discovery", discovery, expected_hash, base_url))
    checks.append(_sdk_text_check("python_client", python_client_path.read_text(encoding="utf-8") if python_client_path.exists() else ""))
    checks.append(_sdk_text_check("javascript_client", js_client_path.read_text(encoding="utf-8") if js_client_path.exists() else ""))
    overall = "error" if any(check["status"] == "error" for check in checks) else "warn" if any(check["status"] == "warn" for check in checks) else "ok"
    return {
        "overall": overall,
        "integrations_dir": str(integrations_dir),
        "expected_contract_version": CONTRACT_VERSION,
        "expected_config_hash": expected_hash,
        "checks": checks,
    }


def _metadata_check(name: str, payload: dict[str, Any], expected_hash: str, base_url: str) -> dict[str, Any]:
    if not payload:
        return {"name": name, "status": "error", "message": "Generated file missing or invalid JSON."}
    issues = []
    if payload.get("contract_version") != CONTRACT_VERSION:
        issues.append("contract_version_mismatch")
    if payload.get("config_hash") and payload.get("config_hash") != expected_hash:
        issues.append("config_hash_mismatch")
    if not payload.get("config_hash"):
        issues.append("config_hash_missing")
    if str(payload.get("base_url") or "").rstrip("/") != base_url.rstrip("/"):
        issues.append("base_url_mismatch")
    sdk_contract = payload.get("sdk_contract") or {}
    if sdk_contract.get("expected_contract_version") != CONTRACT_VERSION:
        issues.append("sdk_contract_mismatch")
    status = "ok" if not issues else "warn" if issues == ["config_hash_missing"] else "error"
    return {"name": name, "status": status, "issues": issues, "contract_version": payload.get("contract_version", ""), "config_hash": payload.get("config_hash", "")}


def _sdk_text_check(name: str, text: str) -> dict[str, Any]:
    if not text:
        return {"name": name, "status": "error", "message": "Generated SDK file is missing."}
    required = ["contract_mismatch", "job_failed", "timeout", "http_error", "invalid_response"]
    missing = [item for item in required if item not in text]
    return {"name": name, "status": "ok" if not missing else "error", "missing": missing}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _recommended_next_action(
    launcher_status: dict[str, Any],
    doctor: dict[str, Any],
    smoke: dict[str, Any],
    integration_status: dict[str, Any],
    jobs: list[dict[str, Any]],
) -> str:
    if not launcher_status.get("running"):
        return "Start the Web UI with scripts/start-ui.ps1 before HTTP callers use the service."
    if integration_status.get("overall") == "error":
        return "Regenerate integrations with python -m research_workflow.cli integrations --output integrations --format json."
    if smoke.get("overall") == "skipped":
        return "Run agent-diagnose without --skip-smoke or run agent-smoke --format json when live HTTP verification is needed."
    if smoke.get("overall") == "error":
        return "Inspect agent_smoke steps and retry after service health recovers."
    failed_jobs = [job for job in jobs if job.get("status") == "failed"]
    if failed_jobs:
        return "Inspect recent failed job events with GET /v1/agent/jobs/{job_id}/events."
    if doctor.get("overall") == "error":
        return "Run research doctor --format json and fix the failing local dependency."
    return "Service and generated integrations look ready for external agents."
