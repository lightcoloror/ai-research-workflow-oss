from __future__ import annotations

import time
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from .store import append_jsonl, ensure_data_dir, read_jsonl


JOB_STATUSES = {"pending", "running", "failed", "succeeded"}
ACTIVE_JOB_STATUSES = {"pending", "running"}
DEFAULT_STALE_JOB_SECONDS = 6 * 60 * 60


def create_job(data_dir: Path, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    root = ensure_data_dir(data_dir)
    now = _now()
    job = {
        "id": f"job_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "status": "pending",
        "stage": "queued",
        "payload": _safe_payload(payload or {}),
        "created_at": now,
        "updated_at": now,
    }
    append_jsonl(root / "jobs.jsonl", [job])
    add_job_event(root, job["id"], "job", "queued", "pending", "Job queued.")
    return job


def update_job(
    data_dir: Path,
    job_id: str,
    *,
    status: str,
    stage: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    if status not in JOB_STATUSES:
        raise ValueError(f"Unknown job status: {status}")
    current = get_job(data_dir, job_id)
    if not current:
        raise ValueError("job not found")
    updated = dict(current)
    updated.update(
        {
            "status": status,
            "stage": stage,
            "updated_at": _now(),
        }
    )
    if result is not None:
        updated["result"] = result
    if error:
        updated["error"] = error
    append_jsonl(ensure_data_dir(data_dir) / "jobs.jsonl", [updated])
    return updated


def add_job_event(
    data_dir: Path,
    job_id: str,
    event_type: str,
    stage: str,
    status: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "job_id": job_id,
        "type": event_type,
        "stage": stage,
        "status": status,
        "message": message,
        "created_at": _now(),
    }
    if details:
        event["details"] = details
    append_jsonl(ensure_data_dir(data_dir) / "job_events.jsonl", [event])
    return event


def get_job(data_dir: Path, job_id: str) -> dict[str, Any] | None:
    latest: dict[str, Any] | None = None
    for row in read_jsonl(ensure_data_dir(data_dir) / "jobs.jsonl"):
        if row.get("id") == job_id:
            latest = row
    return latest


def list_jobs(data_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(ensure_data_dir(data_dir) / "jobs.jsonl"):
        if row.get("id"):
            by_id[str(row["id"])] = row
    rows = sorted(by_id.values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return rows[: max(1, limit)]


def job_events(data_dir: Path, job_id: str) -> list[dict[str, Any]]:
    return [row for row in read_jsonl(ensure_data_dir(data_dir) / "job_events.jsonl") if row.get("job_id") == job_id]


def mark_stale_jobs(data_dir: Path, *, max_age_seconds: int = DEFAULT_STALE_JOB_SECONDS, now_epoch: float | None = None) -> list[dict[str, Any]]:
    root = ensure_data_dir(data_dir)
    now_value = time.time() if now_epoch is None else now_epoch
    latest_by_id: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(root / "jobs.jsonl"):
        if row.get("id"):
            latest_by_id[str(row["id"])] = row
    stale: list[dict[str, Any]] = []
    for job in latest_by_id.values():
        status = str(job.get("status") or "").lower()
        if status not in ACTIVE_JOB_STATUSES:
            continue
        updated_epoch = _parse_epoch(str(job.get("updated_at") or job.get("created_at") or ""))
        if updated_epoch is None:
            continue
        age_seconds = max(0, int(now_value - updated_epoch))
        if age_seconds <= max_age_seconds:
            continue
        updated = update_job(
            root,
            str(job["id"]),
            status="failed",
            stage="stale",
            error=f"Job marked stale after {age_seconds}s without updates.",
        )
        details = {"previous_status": status, "age_seconds": age_seconds, "max_age_seconds": max_age_seconds}
        add_job_event(root, str(job["id"]), "error", "stale", "failed", "Job marked stale; original worker is no longer assumed active.", details)
        stale.append(updated)
    return stale


def run_tracked_job(
    data_dir: Path,
    kind: str,
    payload: dict[str, Any],
    runner: Callable[[], dict[str, Any]],
    *,
    event_builder: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    job = create_job(data_dir, kind, payload)
    job_id = str(job["id"])
    try:
        update_job(data_dir, job_id, status="running", stage="planning")
        add_job_event(data_dir, job_id, "stage", "planning", "running", "Planning the research task.")
        add_job_event(data_dir, job_id, "stage", "provider_search", "running", "Calling configured search providers.")
        result = runner()
        for event in (event_builder(result) if event_builder else _default_result_events(result)):
            add_job_event(
                data_dir,
                job_id,
                str(event.get("type") or "stage"),
                str(event.get("stage") or "result"),
                str(event.get("status") or "succeeded"),
                str(event.get("message") or "Stage complete."),
                event.get("details") if isinstance(event.get("details"), dict) else None,
            )
        summary = {
            "run_id": result.get("run_id", ""),
            "source_count": len(result.get("sources") or []),
            "evidence_count": len(result.get("evidence") or []),
            "claim_count": len(result.get("claims") or []),
        }
        final_job = update_job(data_dir, job_id, status="succeeded", stage="write_report", result=summary)
        add_job_event(data_dir, job_id, "job", "write_report", "succeeded", "Job completed.", summary)
        return {**result, "job_id": job_id, "job_status": final_job["status"], "job": final_job, "job_events": job_events(data_dir, job_id)}
    except Exception as exc:  # noqa: BLE001
        update_job(data_dir, job_id, status="failed", stage="failed", error=str(exc))
        add_job_event(data_dir, job_id, "error", "failed", "failed", str(exc))
        raise


def start_background_job(
    data_dir: Path,
    kind: str,
    payload: dict[str, Any],
    runner: Callable[[], dict[str, Any]],
    *,
    event_builder: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    job = create_job(data_dir, kind, payload)
    job_id = str(job["id"])
    thread = threading.Thread(
        target=_execute_existing_job,
        args=(data_dir, job_id, runner, event_builder),
        name=f"research-job-{job_id}",
        daemon=True,
    )
    thread.start()
    queued = get_job(data_dir, job_id) or job
    return {
        "job_id": job_id,
        "job_status": queued.get("status", "pending"),
        "job": queued,
        "job_events": job_events(data_dir, job_id),
    }


def _execute_existing_job(
    data_dir: Path,
    job_id: str,
    runner: Callable[[], dict[str, Any]],
    event_builder: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> None:
    try:
        update_job(data_dir, job_id, status="running", stage="planning")
        add_job_event(data_dir, job_id, "stage", "planning", "running", "Planning the research task.")
        add_job_event(data_dir, job_id, "stage", "provider_search", "running", "Calling configured search providers.")
        result = runner()
        for event in (event_builder(result) if event_builder else _default_result_events(result)):
            add_job_event(
                data_dir,
                job_id,
                str(event.get("type") or "stage"),
                str(event.get("stage") or "result"),
                str(event.get("status") or "succeeded"),
                str(event.get("message") or "Stage complete."),
                event.get("details") if isinstance(event.get("details"), dict) else None,
            )
        summary = {
            "run_id": result.get("run_id", ""),
            "source_count": len(result.get("sources") or []),
            "evidence_count": len(result.get("evidence") or []),
            "claim_count": len(result.get("claims") or []),
        }
        update_job(data_dir, job_id, status="succeeded", stage="write_report", result=summary)
        add_job_event(data_dir, job_id, "job", "write_report", "succeeded", "Job completed.", summary)
    except Exception as exc:  # noqa: BLE001
        update_job(data_dir, job_id, status="failed", stage="failed", error=str(exc))
        add_job_event(data_dir, job_id, "error", "failed", "failed", str(exc))


def _default_result_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    queries = result.get("search_queries") or []
    provider_health = result.get("provider_health") or []
    events = [
        {
            "type": "stage",
            "stage": "provider_search",
            "status": "succeeded",
            "message": f"Search finished with {len(queries)} query attempts.",
            "details": {"query_count": len(queries), "provider_health": provider_health},
        },
        {
            "type": "stage",
            "stage": "fetch_evidence",
            "status": "succeeded",
            "message": f"Collected {len(result.get('evidence') or [])} evidence snippets.",
            "details": {"source_count": len(result.get("sources") or []), "evidence_count": len(result.get("evidence") or [])},
        },
        {
            "type": "stage",
            "stage": "verify_claims",
            "status": "succeeded",
            "message": f"Checked {len(result.get('claims') or [])} atomic claims.",
            "details": {"claim_count": len(result.get("claims") or [])},
        },
        {
            "type": "stage",
            "stage": "write_report",
            "status": "succeeded",
            "message": "Report payload is ready.",
            "details": {"run_id": result.get("run_id", ""), "report_path": result.get("report_path", "")},
        },
    ]
    for query in queries:
        for provider_status in query.get("provider_statuses") or []:
            provider = provider_status.get("provider") or query.get("provider") or "unknown"
            status = provider_status.get("status") or query.get("status") or "unknown"
            events.append(
                {
                    "type": "provider",
                    "stage": "provider_search",
                    "status": _event_status(status),
                    "message": f"{provider}: {status}",
                    "details": {
                        "provider": provider,
                        "query": query.get("query", ""),
                        "status": status,
                        "error": provider_status.get("error", ""),
                        "source_count": provider_status.get("source_count", query.get("source_count", 0)),
                    },
                }
            )
    for source in result.get("sources") or []:
        events.append(
            {
                "type": "url",
                "stage": "fetch_evidence",
                "status": "succeeded",
                "message": str(source.get("url") or source.get("title") or "source"),
                "details": {"source_id": source.get("id", ""), "url": source.get("url", ""), "title": source.get("title", "")},
            }
        )
    for claim in result.get("claims") or []:
        events.append(
            {
                "type": "claim",
                "stage": "verify_claims",
                "status": _event_status(str(claim.get("verdict") or "")),
                "message": str(claim.get("text") or "claim"),
                "details": {
                    "claim_id": claim.get("id", ""),
                    "verdict": claim.get("verdict", ""),
                    "confidence": claim.get("confidence", 0),
                    "evidence_ids": claim.get("evidence_ids", []),
                },
            }
        )
    return events


def _event_status(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"ok", "supported", "succeeded", "success"}:
        return "succeeded"
    if normalized in {"partial", "empty", "limited", "not_found", "not configured", "not_configured"}:
        return "partial"
    if normalized in {"failed", "error", "auth_error", "network_error", "contradicted"}:
        return "failed"
    return normalized or "unknown"


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    for key in list(redacted):
        if "key" in str(key).lower() or "token" in str(key).lower() or "secret" in str(key).lower():
            redacted[key] = "***"
    return redacted


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _parse_epoch(value: str) -> float | None:
    if not value:
        return None
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
