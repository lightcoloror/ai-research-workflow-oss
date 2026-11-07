"""Small stdlib client for the local AI Research Workflow API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

EXPECTED_CONTRACT_VERSION = "2026-06-06.v1"
SDK_ERROR_CATEGORIES = (
    "service_unavailable",
    "contract_mismatch",
    "job_failed",
    "timeout",
    "http_error",
    "invalid_response",
)


class ResearchWorkflowError(RuntimeError):
    def __init__(self, category: str, message: str, *, details: Any | None = None):
        super().__init__(message)
        self.category = category
        self.details = details


@dataclass(slots=True)
class ResearchWorkflowClient:
    base_url: str = "http://127.0.0.1:9082"
    timeout: int = 180

    def health(self) -> dict[str, Any]:
        return self._get("/v1/health")

    def capabilities(self) -> dict[str, Any]:
        return self._get("/v1/capabilities")

    def route(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Plan provider stages without executing search or changing service state."""
        return self._post("/v1/route", {"topic": query, **kwargs})

    def contract_info(self) -> dict[str, Any]:
        health = self.health()
        return {
            "contract_version": health.get("contract_version", ""),
            "config_hash": health.get("config_hash", ""),
            "base_url": health.get("base_url", self.base_url.rstrip("/")),
            "status": health.get("status", ""),
        }

    def assert_contract(self, expected_contract_version: str = EXPECTED_CONTRACT_VERSION) -> dict[str, Any]:
        info = self.contract_info()
        if info.get("contract_version") != expected_contract_version:
            raise ResearchWorkflowError(
                "contract_mismatch",
                f"Expected contract {expected_contract_version}, got {info.get('contract_version')}",
                details=info,
            )
        return info

    def agent_research(
        self,
        query: str,
        *,
        mode: str = "auto",
        max_results: int = 10,
        no_network: bool = False,
        providers: list[str] | None = None,
        urls: list[str] | None = None,
        fetch: bool = False,
        auto_verify: bool = True,
        client_request_id: str = "",
        profile: str = "default",
        routing_strategy: str = "adaptive_staged",
        external_payloads: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Submit a stable async research job for OpenClaw/Hermes/Codex-style agents."""
        return self._post(
            "/v1/agent/research",
            {
                "query": query,
                "mode": mode,
                "max_results": max_results,
                "no_network": no_network,
                "providers": providers or [],
                "urls": urls or [],
                "fetch": fetch,
                "auto_verify": auto_verify,
                "client_request_id": client_request_id,
                "profile": profile,
                "routing_strategy": routing_strategy,
                "external_payloads": external_payloads or [],
            },
        )

    def agent_verify(self, text: str = "", *, topic: str = "Research Report", run_id: str = "", client_request_id: str = "") -> dict[str, Any]:
        return self._post("/v1/agent/verify", {"topic": topic, "text": text, "run_id": run_id, "client_request_id": client_request_id})

    def agent_fetch(self, urls: list[str] | str, *, fetch: bool = True, client_request_id: str = "") -> dict[str, Any]:
        return self._post("/v1/agent/fetch", {"urls": urls, "fetch": fetch, "client_request_id": client_request_id})

    def agent_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/agent/jobs/{urllib.parse.quote(job_id)}")

    def agent_events(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/v1/agent/jobs/{urllib.parse.quote(job_id)}/events")

    def agent_retry(self, job_id: str, *, payload: dict[str, Any] | None = None, background: bool = True) -> dict[str, Any]:
        return self._post(
            f"/v1/agent/jobs/{urllib.parse.quote(job_id)}/retry",
            {"payload": payload or {}, "background": background},
        )

    def wait_for_agent_job(self, job_id: str, *, interval: float = 1.5, timeout_seconds: float = 300) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = self.agent_job(job_id)
            if job.get("status") == "succeeded":
                return job
            if job.get("status") == "failed":
                raise ResearchWorkflowError("job_failed", f"Research Workflow job {job_id} failed.", details=self.failure_details(job_id, job))
            if time.monotonic() >= deadline:
                raise ResearchWorkflowError("timeout", f"Research Workflow job {job_id} did not finish.", details=self.failure_details(job_id, job))
            time.sleep(interval)

    def failure_details(self, job_id: str, job: dict[str, Any] | None = None) -> dict[str, Any]:
        current = job or self.agent_job(job_id)
        events = self.agent_events(job_id)
        return {
            "job": current,
            "recent_events": (events.get("events") or [])[-5:],
            "error": current.get("error", ""),
        }

    def agent_research_and_wait(self, query: str, **kwargs: Any) -> dict[str, Any]:
        accepted = self.agent_research(query, **kwargs)
        return self.wait_for_agent_job(str(accepted["job_id"]))

    def search(
        self,
        topic: str,
        *,
        mode: str = "auto",
        max_results: int = 10,
        no_network: bool = False,
        providers: list[str] | None = None,
        urls: list[str] | str | None = None,
        fetch: bool = False,
        auto_verify: bool = False,
        profile: str = "default",
        routing_strategy: str = "adaptive_staged",
        external_payloads: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/v1/search",
            {
                "topic": topic,
                "mode": mode,
                "max_results": max_results,
                "no_network": no_network,
                "providers": providers or [],
                "urls": urls or [],
                "fetch": fetch,
                "auto_verify": auto_verify,
                "profile": profile,
                "routing_strategy": routing_strategy,
                "external_payloads": external_payloads or [],
            },
        )

    def deepen(self, topic: str, **kwargs: Any) -> dict[str, Any]:
        payload = {"topic": topic, **kwargs}
        return self._post("/v1/deepen", payload)

    def verify(self, text: str, *, topic: str = "Research Report") -> dict[str, Any]:
        return self._post("/v1/verify", {"topic": topic, "text": text})

    def ingest(self, urls: list[str] | str, *, fetch: bool = False) -> dict[str, Any]:
        return self._post("/v1/ingest", {"urls": urls, "fetch": fetch})

    def run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/runs/{urllib.parse.quote(run_id)}")

    def report_markdown(self, run_id: str) -> str:
        return self._request_text("GET", f"/v1/runs/{urllib.parse.quote(run_id)}/report.md")

    def report_json(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/runs/{urllib.parse.quote(run_id)}/report.json")

    def events(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/runs/{urllib.parse.quote(run_id)}/events")

    def _get(self, path: str) -> dict[str, Any]:
        text = self._request_text("GET", path)
        return self._loads(text, path)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        text = self._request_text("POST", path, payload)
        return self._loads(text, path)

    def _loads(self, text: str, path: str) -> dict[str, Any]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ResearchWorkflowError("invalid_response", f"Invalid JSON response for {path}.", details=text[:500]) from exc
        if not isinstance(data, dict):
            raise ResearchWorkflowError("invalid_response", f"Expected JSON object for {path}.", details=data)
        return data

    def _request_text(self, method: str, path: str, payload: dict[str, Any] | None = None) -> str:
        url = self.base_url.rstrip("/") + path
        data = None
        headers = {"accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ResearchWorkflowError("http_error", f"Research Workflow HTTP {exc.code} for {path}.", details=detail) from exc
        except urllib.error.URLError as exc:
            raise ResearchWorkflowError("service_unavailable", f"Research Workflow service is unavailable for {path}.", details=str(exc)) from exc


if __name__ == "__main__":
    client = ResearchWorkflowClient()
    print(json.dumps(client.health(), ensure_ascii=False, indent=2))
