from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


OBSERVATION_SCHEMA_VERSION = "research-service-observation.v1"
OBSERVATION_TTL_SECONDS = 30
PERSISTED_RECORD_TTL_SECONDS = 300


def build_service_observation(
    *,
    config_url: str,
    host: str,
    port: int,
    data_dir: Path,
    service_record_path: Path,
    service_record: dict[str, Any],
    legacy_status: str,
    pid: int | None,
    process_alive: bool,
    live_listener: bool,
    health: dict[str, Any],
    stale_reasons: list[str],
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Build a fresh, read-only runtime observation from current probe facts."""
    checked_at = checked_at or now_iso()
    runtime_state = classify_runtime_state(
        record_status=str(service_record.get("status") or ""),
        service_record_present=bool(service_record),
        process_alive=process_alive,
        live_listener=live_listener,
        health_ok=bool(health.get("ok")),
    )
    checked = datetime.fromisoformat(checked_at)
    updated_at = str(service_record.get("updated_at") or "")
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "service": "ai-research-workflow",
        "runtime_state": runtime_state,
        "legacy_status": legacy_status,
        "checked_at": checked_at,
        "ttl_seconds": OBSERVATION_TTL_SECONDS,
        "fresh_until": (checked + timedelta(seconds=OBSERVATION_TTL_SECONDS)).isoformat(timespec="seconds"),
        "freshness": "fresh",
        "configured": bool(config_url and port),
        "listening": live_listener,
        "healthy": bool(live_listener and health.get("ok")),
        "needs_manual_start": runtime_state == "needs_manual_start",
        "source_ref": {
            "config": "loaded_research_config",
            "configured_base_url": config_url,
            "data_dir": str(data_dir),
            "service_record": str(service_record_path),
            "probes": ["pid", "tcp_listener", "http_v1_health"],
        },
        "runtime": {
            "host": host,
            "port": int(port),
            "pid": pid,
            "process_alive": process_alive,
            "health": health,
        },
        "persisted_record": {
            "present": bool(service_record),
            "status": service_record.get("status"),
            "pid": service_record.get("pid"),
            "started_at": service_record.get("started_at"),
            "updated_at": updated_at or None,
            **timestamp_freshness(updated_at, PERSISTED_RECORD_TTL_SECONDS),
        },
        "stale_reasons": list(stale_reasons),
    }


def classify_runtime_state(
    *,
    record_status: str,
    service_record_present: bool,
    process_alive: bool,
    live_listener: bool,
    health_ok: bool,
) -> str:
    if live_listener and health_ok:
        return "online"
    if live_listener and not health_ok:
        return "failed"
    if process_alive and not live_listener:
        return "failed"
    if record_status in {"failed", "error", "degraded"}:
        return "failed"
    if not service_record_present or record_status == "stopped":
        return "stopped_by_design"
    return "needs_manual_start"


def recommended_service_action(runtime_state: str, degraded_reasons: list[str]) -> str:
    if runtime_state == "online":
        if degraded_reasons:
            return "The service is online, but one or more optional dependencies are degraded."
        return "Service is ready for external agents."
    if runtime_state == "needs_manual_start":
        return "The configured service is not listening. Review the read-only service proposal, then start it manually if HTTP access is required."
    if runtime_state == "stopped_by_design":
        return "The service is stopped by design. Start the Web UI manually only when HTTP access is required."
    return "A process or listener exists without a healthy /v1/health response. Inspect ownership and logs before any restart."


def timestamp_freshness(timestamp: str, ttl_seconds: int, *, now: datetime | None = None) -> dict[str, Any]:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return {"ttl_seconds": ttl_seconds, "age_seconds": None, "freshness": "missing"}
    current = now or datetime.now()
    age_seconds = max(0, int((current - parsed).total_seconds()))
    return {
        "ttl_seconds": ttl_seconds,
        "age_seconds": age_seconds,
        "freshness": "fresh" if age_seconds <= ttl_seconds else "expired",
    }


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
