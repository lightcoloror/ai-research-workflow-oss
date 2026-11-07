from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .store import ensure_data_dir


def service_record_path(data_dir: Path) -> Path:
    return ensure_data_dir(data_dir) / "service.json"


def write_service_record(
    data_dir: Path,
    *,
    base_url: str,
    pid: int | None = None,
    status: str = "running",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = service_record_path(data_dir)
    existing = read_service_record(data_dir)
    now = _now()
    previous_base_url = str(existing.get("base_url") or "").rstrip("/")
    next_base_url = base_url.rstrip("/")
    next_pid = int(pid if pid is not None else existing.get("pid") or os.getpid())
    previous_pid = existing.get("pid")
    try:
        previous_pid = int(previous_pid) if previous_pid is not None else None
    except (TypeError, ValueError):
        previous_pid = None
    previous_status = str(existing.get("status") or "")
    process_changed = previous_pid != next_pid or previous_base_url != next_base_url or previous_status != status
    record = {
        **existing,
        "service": "ai-research-workflow",
        "base_url": next_base_url,
        "pid": next_pid,
        "status": status,
        "updated_at": now,
    }
    if "started_at" not in record or (status == "running" and process_changed):
        record["started_at"] = now
    if extra:
        record.update(extra)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def update_service_smoke(data_dir: Path, *, base_url: str, smoke: dict[str, Any]) -> dict[str, Any]:
    return write_service_record(
        data_dir,
        base_url=base_url,
        status="running" if smoke.get("overall") == "ok" else "degraded",
        extra={
            "agent_smoke": {
                "overall": smoke.get("overall", "unknown"),
                "checked_at": _now(),
                "steps": [
                    {"name": item.get("name"), "status": item.get("status")}
                    for item in smoke.get("steps", [])
                ],
            }
        },
    )


def read_service_record(data_dir: Path) -> dict[str, Any]:
    path = service_record_path(data_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
