from __future__ import annotations

from typing import Any


SERVICE_STATE_CONTRACT_VERSION = "service-state.v1"


def build_service_state_contract(status: dict[str, Any]) -> dict[str, Any]:
    """Normalize a status snapshot without probing, starting, or changing a service."""
    config = status.get("config") or {}
    configured = bool(config.get("base_url") and config.get("port"))
    listening = bool(status.get("live_listener"))
    healthy = bool(listening and (status.get("health") or {}).get("ok"))
    stale_record = str(status.get("status") or "") in {"stale_record", "config_mismatch", "stale_smoke"}
    explicit_runtime_state = str(status.get("runtime_state") or (status.get("observed_service_record") or {}).get("runtime_state") or "")
    runtime_state = explicit_runtime_state or ("online" if healthy else "stopped_by_design")
    stopped_by_design = (
        runtime_state == "stopped_by_design"
        if explicit_runtime_state
        else bool(configured and not listening and not healthy)
    )
    needs_manual_start = runtime_state == "needs_manual_start"
    failed = runtime_state == "failed"
    recovery_proposed = runtime_state != "online"
    observed_status = (
        "healthy" if healthy else "stale_record" if stale_record else "stopped"
    ) if not explicit_runtime_state else ("healthy" if runtime_state == "online" else runtime_state)

    return {
        "contract_version": SERVICE_STATE_CONTRACT_VERSION,
        "service": status.get("service") or "ai-research-workflow",
        "observed_status": observed_status,
        "configured": configured,
        "listening": listening,
        "healthy": healthy,
        "stale_record": stale_record,
        "stopped_by_design": stopped_by_design,
        "runtime_state": runtime_state,
        "needs_manual_start": needs_manual_start,
        "failed": failed,
        "recovery_proposed": recovery_proposed,
        "configured_endpoint_is_online_proof": False,
        "source_status": status.get("status"),
        "stale_reasons": list(status.get("stale_reasons") or []),
        "checked_at": status.get("checked_at"),
        "ttl_seconds": status.get("ttl_seconds"),
        "source_ref": status.get("source_ref") or {},
        "health_error": None if healthy else (status.get("health") or {}).get("error"),
        "evaluation": {
            "pure": True,
            "performed_io": False,
            "started_service": False,
            "changed_state": False,
        },
        "interface_patch_proposal": _interface_patch_proposal(),
        "recovery_command_drafts": _recovery_command_drafts(config, recovery_proposed),
    }


def service_state_contract_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Research Workflow Service State Contract",
        "",
        f"- Observed status: `{result.get('observed_status')}`",
        f"- Configured: `{str(result.get('configured')).lower()}`",
        f"- Listening: `{str(result.get('listening')).lower()}`",
        f"- Healthy: `{str(result.get('healthy')).lower()}`",
        f"- Stale record: `{str(result.get('stale_record')).lower()}`",
        f"- Stopped by design: `{str(result.get('stopped_by_design')).lower()}`",
        f"- Recovery proposed: `{str(result.get('recovery_proposed')).lower()}`",
        "- Configured endpoint proves online: `false`",
        "- Applied changes: `false`",
    ]
    return "\n".join(lines) + "\n"


def _interface_patch_proposal() -> dict[str, Any]:
    return {
        "proposal_only": True,
        "applied": False,
        "interfaces": [
            {"kind": "cli", "name": "research service contract", "status": "proposed_not_applied"},
            {"kind": "http", "name": "GET /v1/service-contract", "status": "proposed_not_applied"},
            {"kind": "mcp", "name": "research_service_contract", "status": "proposed_not_applied"},
            {"kind": "discovery", "name": "service_state_contract", "status": "proposed_not_applied"},
        ],
    }


def _recovery_command_drafts(config: dict[str, Any], required: bool) -> list[dict[str, Any]]:
    if not required:
        return []
    return [
        {
            "purpose": "refresh_read_only_status",
            "command": "python -m research_workflow.cli service status --format json",
            "requires_approval": False,
            "executed": False,
        },
        {
            "purpose": "start_service_from_single_config_source",
            "command": "powershell -ExecutionPolicy Bypass -File scripts\\start-ui.ps1",
            "configured_base_url": config.get("base_url"),
            "requires_approval": True,
            "executed": False,
        },
    ]
