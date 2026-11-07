from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any


Requester = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


def run_agent_smoke(
    base_url: str,
    *,
    query: str = "agent contract smoke",
    timeout_seconds: float = 60,
    interval_seconds: float = 1,
    requester: Requester | None = None,
) -> dict[str, Any]:
    """Check the stable async HTTP contract used by external agents."""
    requester = requester or _request_json
    base = base_url.rstrip("/")
    steps: list[dict[str, Any]] = []

    def record(name: str, status: str, **details: Any) -> None:
        steps.append({"name": name, "status": status, **details})

    try:
        health = requester("GET", f"{base}/v1/health", None, min(timeout_seconds, 15))
        if health.get("status") != "ok":
            record("health", "error", response=health)
            return _agent_smoke_result("error", base, steps)
        record("health", "ok", api_version=health.get("api_version"))

        capabilities = requester("GET", f"{base}/v1/capabilities", None, min(timeout_seconds, 15))
        stable = set(capabilities.get("stable_endpoints") or [])
        required = {
            "POST /v1/agent/research",
            "GET /v1/agent/jobs/{job_id}",
            "GET /v1/agent/jobs/{job_id}/events",
            "POST /v1/agent/jobs/{job_id}/retry",
        }
        missing = sorted(required - stable)
        if missing:
            record("capabilities", "error", missing=missing)
            return _agent_smoke_result("error", base, steps)
        record("capabilities", "ok", required_endpoints=sorted(required))

        discovery = requester("GET", f"{base}/v1/discovery", None, min(timeout_seconds, 15))
        preferred = str((discovery.get("preferred_order") or [{}, {}])[1].get("entrypoint", ""))
        if preferred != "POST /v1/agent/research":
            record("discovery", "error", preferred_entrypoint=preferred)
            return _agent_smoke_result("error", base, steps)
        record("discovery", "ok", preferred_entrypoint=preferred)

        accepted = requester(
            "POST",
            f"{base}/v1/agent/research",
            {"query": query, "no_network": True, "auto_verify": False, "max_results": 1},
            min(timeout_seconds, 30),
        )
        job_id = str(accepted.get("job_id") or "")
        links = accepted.get("links") or {}
        if not job_id or not _has_agent_links(links, job_id):
            record("submit", "error", response=accepted)
            return _agent_smoke_result("error", base, steps)
        record("submit", "ok", job_id=job_id, links=links)

        deadline = time.monotonic() + timeout_seconds
        final_job: dict[str, Any] = {}
        while True:
            final_job = requester("GET", str(links["job"]), None, min(timeout_seconds, 15))
            status = str(final_job.get("status") or "")
            if status in {"succeeded", "failed"}:
                break
            if time.monotonic() >= deadline:
                events = requester("GET", str(links["events"]), None, min(timeout_seconds, 15))
                record("poll", "error", reason="timeout", job_id=job_id, events=events.get("events", [])[-5:])
                return _agent_smoke_result("error", base, steps)
            time.sleep(interval_seconds)

        events = requester("GET", str(links["events"]), None, min(timeout_seconds, 15))
        event_count = len(events.get("events") or [])
        if final_job.get("status") != "succeeded":
            record("poll", "error", job_id=job_id, final_status=final_job.get("status"), error=final_job.get("error"), event_count=event_count)
            return _agent_smoke_result("error", base, steps, job=final_job, events=events)
        record("poll", "ok", job_id=job_id, final_status=final_job.get("status"), event_count=event_count)
        retry_accepted = requester(
            "POST",
            str(links["retry"]),
            {"background": False},
            min(timeout_seconds, 30),
        )
        retry_job_id = str(retry_accepted.get("job_id") or "")
        retry_links = retry_accepted.get("links") or {}
        if not retry_job_id or not _has_agent_links(retry_links, retry_job_id):
            record("retry", "error", response=retry_accepted)
            return _agent_smoke_result("error", base, steps, job=final_job, events=events)
        retry_job = requester("GET", str(retry_links["job"]), None, min(timeout_seconds, 15))
        if retry_job.get("status") != "succeeded":
            record("retry", "error", job_id=retry_job_id, final_status=retry_job.get("status"))
            return _agent_smoke_result("error", base, steps, job=retry_job, events=events)
        record("retry", "ok", job_id=retry_job_id, final_status=retry_job.get("status"))
        return _agent_smoke_result("ok", base, steps, job=final_job, events=events)
    except Exception as exc:  # noqa: BLE001
        record("exception", "error", error=str(exc))
        return _agent_smoke_result("error", base, steps)


def agent_smoke_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Agent Contract Smoke",
        "",
        f"- Overall: `{result.get('overall')}`",
        f"- Base URL: `{result.get('base_url')}`",
        "",
        "## Steps",
        "",
    ]
    for step in result.get("steps") or []:
        details = {key: value for key, value in step.items() if key not in {"name", "status"}}
        suffix = f" - `{json.dumps(details, ensure_ascii=False, sort_keys=True)}`" if details else ""
        lines.append(f"- `{step.get('status')}` {step.get('name')}{suffix}")
    return "\n".join(lines) + "\n"


def _agent_smoke_result(
    overall: str,
    base_url: str,
    steps: list[dict[str, Any]],
    *,
    job: dict[str, Any] | None = None,
    events: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "overall": overall,
        "base_url": base_url,
        "steps": steps,
        "job": job or {},
        "events": events or {},
    }


def _has_agent_links(links: dict[str, Any], job_id: str) -> bool:
    return all(
        str(links.get(key, "")).endswith(suffix)
        for key, suffix in {
            "job": f"/v1/agent/jobs/{job_id}",
            "events": f"/v1/agent/jobs/{job_id}/events",
            "retry": f"/v1/agent/jobs/{job_id}/retry",
        }.items()
    )


def _request_json(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    body = None
    headers = {"accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {method} {url}: {detail}") from exc
