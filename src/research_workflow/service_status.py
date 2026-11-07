from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ResearchConfig, config_fingerprint, web_base_url
from .contract import CONTRACT_VERSION
from .launcher import _process_exists, web_paths
from .service_registry import read_service_record, service_record_path
from .service_observation import build_service_observation, now_iso, recommended_service_action


def run_service_status(config: ResearchConfig, data_dir: Path, *, timeout_seconds: float = 2) -> dict[str, Any]:
    checked_at = now_iso()
    config_url = web_base_url(config)
    service_record = read_service_record(data_dir)
    record_status = str(service_record.get("status") or "")
    service_url = str(service_record.get("base_url") or "").rstrip("/")
    effective_url = config_url
    config_hash = config_fingerprint(config)
    service_config_hash = str(service_record.get("config_hash") or "")
    service_contract_version = str(service_record.get("contract_version") or "")
    host, port = _host_port_from_url(effective_url)
    pid = _pid(service_record.get("pid"))
    process_alive = bool(pid and _process_exists(pid))
    live_listener = _can_connect(host, port, timeout_seconds=timeout_seconds) if host and port else False
    health = _fetch_json(f"{effective_url}/v1/health", timeout_seconds=timeout_seconds) if effective_url else {"ok": False, "error": "missing base url"}
    dependencies = {
        "searxng": _fetch_searxng_health(config.searxng_url, enabled="searxng" in (config.search_providers or []), timeout_seconds=max(timeout_seconds, 0.1))
    }
    config_matches_service = not service_url or service_url == config_url
    config_hash_matches_service = not service_config_hash or service_config_hash == config_hash
    contract_version_matches_service = not service_contract_version or service_contract_version == CONTRACT_VERSION
    smoke_status = _smoke_status(service_record)

    stale_reasons: list[str] = []
    degraded_reasons: list[str] = []
    record_is_stopped = record_status == "stopped"
    if service_record and not record_is_stopped and not config_matches_service:
        stale_reasons.append("service_record_base_url_mismatch")
    if service_record and not record_is_stopped and not config_hash_matches_service:
        stale_reasons.append("service_record_config_hash_mismatch")
    if service_record and not record_is_stopped and not contract_version_matches_service:
        stale_reasons.append("service_record_contract_version_mismatch")
    if pid and not record_is_stopped and not process_alive:
        stale_reasons.append("pid_not_alive")
    if service_record and not record_is_stopped and not live_listener:
        stale_reasons.append("no_live_listener")
    if service_record and not record_is_stopped and not health.get("ok"):
        stale_reasons.append("health_failed")
    if service_record and not record_is_stopped and smoke_status["status"] in {"not_run_after_start", "stale", "failed"}:
        stale_reasons.append(f"agent_smoke_{smoke_status['status']}")
    if dependencies["searxng"]["enabled"] and not dependencies["searxng"]["ok"]:
        degraded_reasons.append("dependency_degraded:searxng")

    running = bool(live_listener and health.get("ok"))
    record_matches = config_matches_service and config_hash_matches_service and contract_version_matches_service
    status = _status(
        running=running,
        record_matches=record_matches,
        record_is_stopped=record_is_stopped,
        stale_reasons=stale_reasons,
        smoke_status=smoke_status,
        degraded_reasons=degraded_reasons,
    )
    observed_service_record = build_service_observation(
        config_url=config_url,
        host=config.web_host,
        port=int(config.web_port),
        data_dir=data_dir,
        service_record_path=service_record_path(data_dir),
        service_record=service_record,
        legacy_status=status,
        pid=pid,
        process_alive=process_alive,
        live_listener=live_listener,
        health=health,
        stale_reasons=stale_reasons,
        checked_at=checked_at,
    )


    return {
        "service": "ai-research-workflow",
        "status": status,
        "running": running,
        "runtime_state": observed_service_record["runtime_state"],
        "checked_at": checked_at,
        "ttl_seconds": observed_service_record["ttl_seconds"],
        "freshness": observed_service_record["freshness"],
        "config": {
            "base_url": config_url,
            "config_hash": config_hash,
            "contract_version": CONTRACT_VERSION,
            "host": config.web_host,
            "port": int(config.web_port),
            "data_dir": str(data_dir),
        },
        "service_record": service_record,
        "service_record_path": str(service_record_path(data_dir)),
        "service_record_matches_config": config_matches_service,
        "service_record_config_hash_matches_config": config_hash_matches_service,
        "service_record_contract_version_matches_current": contract_version_matches_service,
        "effective_base_url": effective_url,
        "pid": pid,
        "process_alive": process_alive,
        "live_listener": live_listener,
        "health": health,
        "dependencies": dependencies,
        "smoke_status": smoke_status,
        "stale_reasons": stale_reasons,
        "degraded_reasons": degraded_reasons,
        "observed_service_record": observed_service_record,
        "source_ref": observed_service_record["source_ref"],
        "recommended_action": recommended_service_action(observed_service_record["runtime_state"], degraded_reasons),
    }


def service_status_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Research Workflow Service Status",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Config URL: `{result.get('config', {}).get('base_url')}`",
        f"- Effective URL: `{result.get('effective_base_url')}`",
        f"- PID: `{result.get('pid') or ''}`",
        f"- Process alive: `{str(result.get('process_alive')).lower()}`",
        f"- Live listener: `{str(result.get('live_listener')).lower()}`",
        f"- Runtime state: `{result.get('runtime_state')}`",
        f"- Checked at: `{result.get('checked_at')}`",
        f"- Health: `{result.get('health', {}).get('status') or result.get('health', {}).get('error')}`",
        f"- Agent smoke: `{result.get('smoke_status', {}).get('status')}`",
        f"- SearXNG: `{result.get('dependencies', {}).get('searxng', {}).get('status')}`",
        f"- Recommended action: {result.get('recommended_action')}",
    ]
    if result.get("stale_reasons"):
        lines.extend(["", "## Stale Reasons"])
        lines.extend(f"- `{reason}`" for reason in result.get("stale_reasons", []))
    if result.get("degraded_reasons"):
        lines.extend(["", "## Degraded Reasons"])
        lines.extend(f"- `{reason}`" for reason in result.get("degraded_reasons", []))
    return "\n".join(lines) + "\n"


def build_service_recovery_proposal(
    status: dict[str, Any],
    data_dir: Path,
    config: ResearchConfig,
) -> dict[str, Any]:
    """Build a read-only recovery plan without changing files or processes."""
    paths = web_paths(data_dir)
    stale_reasons = list(status.get("stale_reasons") or [])
    actions: list[dict[str, Any]] = []
    mismatch_reasons = [
        reason
        for reason in stale_reasons
        if reason.startswith("service_record_") and reason.endswith("_mismatch")
    ]

    if status.get("live_listener") and mismatch_reasons:
        actions.append(
            {
                "action": "inspect_live_listener_before_stop",
                "host": config.web_host,
                "port": int(config.web_port),
                "safe_when": "verify process ownership and command line before any termination",
                "automatic": False,
            }
        )
    if "pid_not_alive" in stale_reasons and paths["pid"].exists():
        actions.append(
            {
                "action": "clear_stale_pid_file",
                "target": str(paths["pid"]),
                "safe_when": "recorded PID is dead and no project listener is present",
                "automatic": False,
            }
        )
    if mismatch_reasons:
        actions.append(
            {
                "action": "refresh_service_record_from_config",
                "target": str(paths["service"]),
                "reasons": mismatch_reasons,
                "automatic": False,
            }
        )
    if status.get("status") in {"stale_record", "config_mismatch"} and not status.get("live_listener"):
        actions.append(
            {
                "action": "mark_service_stopped",
                "target": str(paths["service"]),
                "safe_when": "recorded PID is dead and configured port has no listener",
                "automatic": False,
            }
        )
    if status.get("status") in {"stopped", "stale_record", "config_mismatch"}:
        actions.append(
            {
                "action": "start_from_project_config",
                "command": "powershell -ExecutionPolicy Bypass -File scripts\\start-ui.ps1",
                "base_url": status.get("config", {}).get("base_url"),
                "automatic": False,
            }
        )

    return {
        "service": "ai-research-workflow",
        "proposal_only": True,
        "applied": False,
        "status": status.get("status"),
        "stale_reasons": stale_reasons,
        "actions": actions,
        "safety": {
            "deletes_files": False,
            "stops_processes": False,
            "starts_service": False,
            "changes_config": False,
        },
    }


def service_recovery_proposal_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Research Workflow Service Recovery Proposal",
        "",
        f"- Current status: `{result.get('status')}`",
        "- Proposal only: `true`",
        "- Applied: `false`",
        "",
        "## Actions",
        "",
    ]
    actions = result.get("actions") or []
    if not actions:
        lines.append("- No recovery action is currently required.")
    for action in actions:
        detail = action.get("command") or action.get("target") or ""
        lines.append(f"- `{action.get('action')}`: {detail}")
    return "\n".join(lines) + "\n"


def _fetch_json(url: str, *, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": True, **payload}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc), "url": url}


def _fetch_searxng_health(base_url: str, *, enabled: bool, timeout_seconds: float) -> dict[str, Any]:
    checked_at = _now()
    base = str(base_url or "").strip().rstrip("/")
    if not enabled:
        return {"name": "searxng", "enabled": False, "ok": True, "status": "skipped", "checked_at": checked_at}
    if not base:
        return {"name": "searxng", "enabled": True, "ok": False, "status": "error", "checked_at": checked_at, "error": "missing searxng_url"}
    params = urllib.parse.urlencode({"q": "SearXNG", "format": "json"})
    url = f"{base}/search?{params}"
    request = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "ai-research-workflow/0.1"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        results = payload.get("results") if isinstance(payload, dict) else None
        result_count = len(results) if isinstance(results, list) else 0
        ok = response.status == 200 and isinstance(payload, dict) and "results" in payload
        return {
            "name": "searxng",
            "enabled": True,
            "ok": ok,
            "status": "ok" if ok else "error",
            "searxng_url": base,
            "json_url": url,
            "result_count": result_count,
            "checked_at": checked_at,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "searxng",
            "enabled": True,
            "ok": False,
            "status": "error",
            "searxng_url": base,
            "json_url": url,
            "checked_at": checked_at,
            "error": str(exc),
        }


def _can_connect(host: str, port: int, *, timeout_seconds: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _host_port_from_url(url: str) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname or "", int(parsed.port or (443 if parsed.scheme == "https" else 80))
    except Exception:
        return "", 0


def _pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _smoke_status(service_record: dict[str, Any]) -> dict[str, Any]:
    smoke = service_record.get("agent_smoke") or {}
    started_at = str(service_record.get("started_at") or "")
    checked_at = str(smoke.get("checked_at") or "")
    overall = str(smoke.get("overall") or "")
    if not service_record:
        status = "no_service_record"
    elif not smoke:
        status = "not_run_after_start"
    elif overall != "ok":
        status = "failed"
    elif _is_before(checked_at, started_at):
        status = "stale"
    else:
        status = "ok"
    return {
        "status": status,
        "overall": overall or None,
        "checked_at": checked_at or None,
        "started_at": started_at or None,
        "steps": smoke.get("steps") or [],
    }


def _is_before(left: str, right: str) -> bool:
    left_dt = _parse_timestamp(left)
    right_dt = _parse_timestamp(right)
    return bool(left_dt and right_dt and left_dt < right_dt)


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _status(
    *,
    running: bool,
    record_matches: bool,
    record_is_stopped: bool,
    stale_reasons: list[str],
    smoke_status: dict[str, Any],
    degraded_reasons: list[str],
) -> str:
    if record_is_stopped:
        return "stopped"
    if any(
        str(reason).startswith("service_record_") and str(reason).endswith("_mismatch")
        for reason in stale_reasons
    ):
        return "config_mismatch"
    if not running:
        return "stale_record" if stale_reasons else "stopped"
    if not record_matches:
        return "stale_record"
    if stale_reasons and all(str(reason).startswith("agent_smoke_") for reason in stale_reasons):
        return "stale_smoke"
    if stale_reasons:
        return "stale_record"
    if degraded_reasons:
        return "dependency_degraded"
    if smoke_status.get("status") == "ok":
        return "running"
    return "running"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _recommended_action(status: str, stale_reasons: list[str], degraded_reasons: list[str]) -> str:
    if status == "running":
        return "Service is ready for external agents."
    if status == "stopped":
        return "Start the Web UI before external agents call HTTP endpoints."
    if status == "dependency_degraded" or degraded_reasons:
        return "Check configured dependencies, starting with searxng-native health, then rerun contract-test --live."
    if status == "stale_smoke":
        return "Rerun agent-smoke or contract-test --live so service.json reflects the current process."
    if status == "config_mismatch":
        return "Inspect the live listener, then use service proposal before refreshing the record or restarting from project config."
    if (
        "service_record_base_url_mismatch" in stale_reasons
        or "service_record_config_hash_mismatch" in stale_reasons
        or "service_record_contract_version_mismatch" in stale_reasons
    ):
        return "Restart the service from the project script; do not call stale service_record URLs."
    if "pid_not_alive" in stale_reasons or "no_live_listener" in stale_reasons:
        return "Start the Web UI with scripts/start-ui.ps1 or install a managed service."
    if "health_failed" in stale_reasons:
        return "Check Web server logs and rerun contract-test after health recovers."
    return "Start the Web UI before external agents call HTTP endpoints."
