"""
title: Research Workflow
author: local
version: 0.1.0
description: Calls a local AI Research Workflow backend for evidence-first deep search and atomic fact checking.
"""

import json
import urllib.request

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        base_url: str = Field(default="http://127.0.0.1:9082", description="Local Research Workflow Web UI base URL")

    def __init__(self):
        self.valves = self.Valves()

    def research_run(
        self,
        topic: str,
        mode: str = "daily",
        no_network: bool = True,
        text_to_verify: str = "",
    ) -> str:
        """Run local multi-source research and atomic fact checking. Returns the Markdown report."""
        payload = {
            "topic": topic,
            "mode": mode,
            "no_network": no_network,
            "text_to_verify": text_to_verify,
        }
        data = self._post("/api/run", payload, timeout=180)
        audit = data.get("audit", {}).get("overall", "unknown")
        report = data.get("report_markdown") or json.dumps(data, ensure_ascii=False, indent=2)
        return f"Audit: {audit}\nRun ID: {data.get('run_id', '')}\n\n{report}"

    def research_search(
        self,
        topic: str,
        mode: str = "daily",
        no_network: bool = True,
    ) -> str:
        """Run local search-only planning, retrieval, evidence extraction, and report persistence."""
        payload = {
            "topic": topic,
            "mode": mode,
            "no_network": no_network,
        }
        data = self._post("/api/search", payload, timeout=120)
        report = data.get("report_markdown") or json.dumps(data, ensure_ascii=False, indent=2)
        return f"Kind: search\nRun ID: {data.get('run_id', '')}\n\n{report}"

    def research_add_evidence(self, text: str, url: str = "") -> str:
        """Add one source-backed evidence note to the local evidence store."""
        data = self._post("/api/evidence", {"text": text, "url": url}, timeout=60)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def research_selftest(self) -> str:
        """Run the local acceptance selftest for pipeline, audit, history, MCP, and Obsidian export."""
        data = self._post("/api/selftest", {}, timeout=180)
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _post(self, path: str, payload: dict, timeout: int = 120) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.valves.base_url.rstrip("/") + path,
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
