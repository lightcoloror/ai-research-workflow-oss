from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from .config import load_config, web_base_url
from .contract import API_VERSION, CONTRACT_VERSION
from .metadata import package_version
from .routing import PROFILES, ROUTING_STRATEGIES, provider_catalog


INTEGRATION_NAMES = (
    "open-webui",
    "rest-client",
    "python-client",
    "manifest",
    "openclaw-prompt",
    "hermes-prompt",
    "bookmarklet",
    "n8n",
    "node-red",
    "dify",
    "agent-discovery",
)


@dataclass(slots=True)
class IntegrationFile:
    name: str
    path: Path
    description: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
        }


def default_base_url() -> str:
    return web_base_url(load_config(None))


def _resolve_base_url(base_url: str | None = None) -> str:
    return (base_url or default_base_url()).rstrip("/")


def write_integrations(output_dir: Path, base_url: str | None = None, *, config_hash: str = "") -> dict:
    base_url = _resolve_base_url(base_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    files = [
        _write_file(
            output_dir / "openwebui_research_workflow_tool.py",
            render_open_webui_tool(base_url),
            "Open WebUI Function/Tool wrapper for local research workflow endpoints.",
        ),
        _write_file(
            output_dir / "research-workflow-client.mjs",
            render_rest_client(base_url),
            "Small fetch-based JS client for Morphic, Perplexica, Vane, or custom frontends.",
        ),
        _write_file(
            output_dir / "research_workflow_client.py",
            render_python_client(base_url),
            "Small stdlib Python client for local agents and scripts.",
        ),
        _write_file(
            output_dir / "research-workflow.integration.json",
            json.dumps(integration_manifest(base_url, config_hash=config_hash), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "Machine-readable endpoint manifest for external UI adapters.",
        ),
        _write_file(
            output_dir / "openclaw-research-workflow.prompt.md",
            render_agent_prompt(base_url, agent_name="OpenClaw"),
            "OpenClaw system/tool-use prompt for stable Research Workflow calls.",
        ),
        _write_file(
            output_dir / "hermes-research-workflow.prompt.md",
            render_agent_prompt(base_url, agent_name="Hermes Agent"),
            "Hermes Agent system/tool-use prompt for stable Research Workflow calls.",
        ),
        _write_file(
            output_dir / "research-workflow-bookmarklet.md",
            render_bookmarklet_doc(base_url),
            "Bookmarklet for one-click ingest of the current browser page.",
        ),
        _write_file(
            output_dir / "n8n-research-workflow.json",
            json.dumps(render_n8n_workflow(base_url), ensure_ascii=False, indent=2) + "\n",
            "n8n importable workflow that calls /v1/search and returns run_id plus Markdown.",
        ),
        _write_file(
            output_dir / "node-red-research-workflow.json",
            json.dumps(render_node_red_flow(base_url), ensure_ascii=False, indent=2) + "\n",
            "Node-RED importable flow that calls /v1/search and returns run_id plus Markdown.",
        ),
        _write_file(
            output_dir / "dify-openapi-tool.md",
            render_dify_tool_doc(base_url),
            "Dify setup note for importing the local OpenAPI tool contract.",
        ),
        _write_file(
            output_dir / "AGENT_DISCOVERY.md",
            render_agent_discovery_doc(base_url),
            "Human-readable agent discovery guide for choosing MCP, HTTP, watch-folder, or browser ingest.",
        ),
        _write_file(
            output_dir / "agent-discovery.json",
            json.dumps(render_agent_discovery_json(base_url, config_hash=config_hash), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "Machine-readable agent discovery descriptor.",
        ),
    ]
    return {
        "output_dir": str(output_dir),
        "base_url": base_url,
        "files": [file.to_dict() for file in files],
    }


def integration_manifest(base_url: str | None = None, *, config_hash: str = "") -> dict:
    base_url = _resolve_base_url(base_url)
    sdk_contract = _sdk_contract()
    return {
        "name": "ai-research-workflow",
        "base_url": base_url.rstrip("/"),
        "package_version": package_version(),
        "api_version": API_VERSION,
        "contract_version": CONTRACT_VERSION,
        "config_hash": config_hash,
        "description": "Local-first multi-source retrieval and optional atomic fact-checking backend.",
        "preferred_entrypoint": "POST /v1/agent/research",
        "endpoints": {
            "health": {"method": "GET", "path": "/v1/health", "purpose": "Check service health and API version."},
            "capabilities": {"method": "GET", "path": "/v1/capabilities", "purpose": "Discover modes, providers, formats, and feature flags."},
            "discovery": {"method": "GET", "path": "/v1/discovery", "purpose": "Discover recommended agent interfaces and evidence policy."},
            "openapi": {"method": "GET", "path": "/v1/openapi.json", "purpose": "Machine-readable OpenAPI contract."},
            "agent_research": {"method": "POST", "path": "/v1/agent/research", "purpose": "Stable async agent contract for research jobs. Returns job_id and polling links."},
            "agent_verify": {"method": "POST", "path": "/v1/agent/verify", "purpose": "Stable async agent contract for atomic verification jobs."},
            "agent_fetch": {"method": "POST", "path": "/v1/agent/fetch", "purpose": "Stable async agent contract for URL fetch and evidence ingestion jobs."},
            "agent_job": {"method": "GET", "path": "/v1/agent/jobs/{job_id}", "purpose": "Poll stable agent job status and final result."},
            "agent_events": {"method": "GET", "path": "/v1/agent/jobs/{job_id}/events", "purpose": "Poll stable agent job events without using internal job endpoints."},
            "agent_retry": {"method": "POST", "path": "/v1/agent/jobs/{job_id}/retry", "purpose": "Retry a terminal agent job with optional payload overrides."},
            "v1_search": {"method": "POST", "path": "/v1/search", "purpose": "Stable agent-facing search-only research run."},
            "v1_route": {"method": "POST", "path": "/v1/route", "purpose": "Plan adaptive provider routing without executing search."},
            "v1_deepen": {"method": "POST", "path": "/v1/deepen", "purpose": "Stable agent-facing deepened search run."},
            "v1_verify": {"method": "POST", "path": "/v1/verify", "purpose": "Stable agent-facing atomic verification."},
            "v1_ingest": {"method": "POST", "path": "/v1/ingest", "purpose": "Stable agent-facing manual URL ingest."},
            "v1_run": {"method": "GET", "path": "/v1/runs/{run_id}", "purpose": "Fetch persisted run metadata."},
            "v1_report_md": {"method": "GET", "path": "/v1/runs/{run_id}/report.md", "purpose": "Fetch Markdown report."},
            "v1_report_json": {"method": "GET", "path": "/v1/runs/{run_id}/report.json", "purpose": "Fetch machine-readable report."},
            "v1_events": {"method": "GET", "path": "/v1/runs/{run_id}/events", "purpose": "Fetch run events as JSON or SSE."},
            "v1_quality": {"method": "POST", "path": "/v1/runs/{run_id}/quality", "purpose": "Summarize citation coverage, source diversity, and evidence strength."},
            "v1_deepen_claims": {"method": "POST", "path": "/v1/runs/{run_id}/deepen-claims", "purpose": "Generate next-search queries for partial, not_found, or high-risk weak claims."},
            "v1_references_check": {"method": "POST", "path": "/v1/references/check", "purpose": "Validate DOI/arXiv references from text or a persisted run."},
            "search": {"method": "POST", "path": "/api/search", "purpose": "Plan, retrieve, collect evidence snippets, persist a search-only report, and skip fact checking."},
            "run": {"method": "POST", "path": "/api/run", "purpose": "Plan, retrieve, collect evidence, verify claims, persist report, and audit."},
            "selftest": {"method": "POST", "path": "/api/selftest", "purpose": "Run local end-to-end acceptance checks."},
            "evidence": {"method": "POST", "path": "/api/evidence", "purpose": "Add a source-backed manual evidence note."},
            "audit": {"method": "GET", "path": "/api/audit?id=<run_id>", "purpose": "Re-check a persisted run."},
            "runs": {"method": "GET", "path": "/api/runs?limit=20", "purpose": "List persisted runs."},
            "obsidian": {"method": "POST", "path": "/api/obsidian", "purpose": "Export a saved run to Obsidian."},
            "doctor": {"method": "GET", "path": "/api/doctor", "purpose": "Check local configuration and runtime health."},
        },
        "payload_shape": {
            "topic": "string, required for /v1/search, /v1/deepen, /api/search, and /api/run",
            "mode": "daily | academic | tool_selection | auto",
            "max_results": "integer",
            "no_network": "boolean",
            "urls": "array of URLs or newline-delimited string",
            "manual_evidence": "array of {url?: string, text: string}",
            "text_to_verify": "optional draft/report text to split into atomic claims for /api/run",
            "providers": "array of provider names such as searxng, brave, kagi, tavily, serpapi",
            "profile": "default | anti_seo | technical_primary | medical_high_risk | policy_primary | product_feedback",
            "routing_strategy": "adaptive_staged | legacy_fanout | manual",
            "external_payloads": "normalized Agent Reach or other specialist acquisition payloads",
        },
        "recommended_mcp_tools": [
            "research_route",
            "research_search",
            "research_deepen",
            "research_verify",
            "research_ingest",
            "research_get_report",
            "research_status",
            "research_quality",
            "research_deepen_claims",
            "research_check_references",
        ],
        "watch_folder": {
            "inbox": ".research-jobs/inbox",
            "outbox": ".research-jobs/outbox",
            "failed": ".research-jobs/failed",
            "command": "python -m research_workflow.cli jobs --jobs-dir .research-jobs --data-dir .research --format json",
            "job_types": ["search", "deepen", "verify", "fetch", "ingest"],
            "status_fields": ["service", "api_version", "contract_version", "config_hash", "job_id", "type", "status", "created_outputs"],
        },
        "bookmarklet": {
            "purpose": "One-click ingest of the current browser page into /v1/ingest.",
            "generated_file": "research-workflow-bookmarklet.md",
            "requires_cors": True,
        },
        "clients": {
            "javascript": "research-workflow-client.mjs",
            "python": "research_workflow_client.py",
        },
        "sdk_contract": sdk_contract,
        "automation_adapters": {
            "n8n": "n8n-research-workflow.json",
            "node_red": "node-red-research-workflow.json",
            "dify": "dify-openapi-tool.md",
        },
        "agent_discovery": {
            "endpoint": "GET /v1/discovery",
            "markdown": "AGENT_DISCOVERY.md",
            "json": "agent-discovery.json",
        },
        "response_contract": {
            "run_id": "persisted run identifier",
            "kind": "search for search-only runs; omitted or research for full-chain runs",
            "search_queries": "planner-expanded query attempts with status and source_count for coverage review",
            "provider_health": "array of provider quota/error/timing summaries; inspect overall, *_count fields, avg_seconds, max_seconds, recent_status, rank_reason, cost_tier, limit_note, and warnings",
            "audit.overall": "ok | warn | error | not_run",
            "report_markdown": "Obsidian-ready Markdown report",
            "report_json": "machine-readable Report JSON",
            "quality_summary": "additive evidence quality summary; advisory only and safe for old clients to ignore",
            "deepen_suggestions": "additive next-search suggestions for unresolved or weak claims",
            "reference_checks": "additive academic bibliography validation for academic-mode reports",
            "routing_plan": "additive read-only plan containing route_kind, staged providers, budget, and stop conditions",
            "routing_trace": "additive executed/skipped provider stages and reasons",
            "coverage_gate": "additive observed coverage versus the route stop conditions",
            "external_acquisition": "additive handoff instructions for Agent Reach or web-content-fetcher",
            "stop_reason": "additive reason the adaptive search stopped",
            "claims": "verified atomic claims with verdict, confidence, and evidence_ids",
            "sources": "traceable source records; use search_queries/search_buckets/search_purposes/search_stages to audit where each source came from",
            "evidence": "traceable evidence records",
        },
        "routing_contract": {
            "strategies": list(ROUTING_STRATEGIES),
            "profiles": list(PROFILES),
            "provider_capabilities": provider_catalog(),
            "explicit_providers_are_manual": True,
            "configured_providers_are_pool_in_adaptive_mode": True,
        },
}


def render_bookmarklet(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    endpoint = f"{base_url.rstrip('/')}/v1/ingest"
    script = (
        "javascript:(()=>{"
        f"const endpoint={json.dumps(endpoint)};"
        "const payload={urls:[location.href],fetch:false};"
        "fetch(endpoint,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)})"
        ".then(async r=>{const text=await r.text();if(!r.ok)throw new Error(text||r.status);alert('Research ingest queued: '+location.href);})"
        ".catch(e=>alert('Research ingest failed: '+e.message));"
        "})()"
    )
    return script


def render_bookmarklet_doc(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    bookmarklet = render_bookmarklet(base_url)
    return dedent(
        f"""
        # Research Workflow Bookmarklet

        Use this bookmarklet to ingest the current browser page into the local Research Workflow evidence store.

        ## Install

        1. Create a new browser bookmark.
        2. Name it `Research Ingest`.
        3. Paste this entire one-line URL as the bookmark location:

        ```text
        {bookmarklet}
        ```

        ## Behavior

        - Sends the current `location.href` to `{base_url.rstrip('/')}/v1/ingest`.
        - Uses `fetch: false` by default so ingestion is fast and conservative.
        - The local API supports CORS/OPTIONS for this use case.
        - If ingestion succeeds, the browser shows a short alert.

        ## When To Use

        - You manually find a primary source during research.
        - Search recall is weak and you want to add a source by hand.
        - You want OpenClaw/Hermes or another agent to verify claims against a page you selected.
        """
    ).lstrip()


def render_n8n_workflow(base_url: str | None = None) -> dict:
    base_url = _resolve_base_url(base_url)
    base = base_url.rstrip("/")
    return {
        "name": "AI Research Workflow Search",
        "nodes": [
            {
                "parameters": {
                    "path": "ai-research-search",
                    "responseMode": "responseNode",
                    "options": {},
                },
                "id": "webhook-topic",
                "name": "Webhook Topic",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [240, 300],
            },
            {
                "parameters": {
                    "method": "POST",
                    "url": f"{base}/v1/search",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": '={{ JSON.stringify({ topic: $json.body.topic || $json.query.topic || $json.topic, mode: $json.body.mode || "auto", max_results: $json.body.max_results || 10, no_network: $json.body.no_network ?? false, providers: $json.body.providers || [] }) }}',
                    "options": {"timeout": 180000},
                },
                "id": "call-research-workflow",
                "name": "Call Research Workflow",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [500, 300],
            },
            {
                "parameters": {
                    "jsCode": "return [{ json: { run_id: $json.run_id, report_markdown: $json.report_markdown, provider_health: $json.provider_health || [], source_count: ($json.sources || []).length, evidence_count: ($json.evidence || []).length, claim_count: ($json.claims || []).length } }];",
                },
                "id": "shape-response",
                "name": "Shape Response",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [760, 300],
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json }}",
                    "options": {},
                },
                "id": "respond",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [1020, 300],
            },
        ],
        "connections": {
            "Webhook Topic": {"main": [[{"node": "Call Research Workflow", "type": "main", "index": 0}]]},
            "Call Research Workflow": {"main": [[{"node": "Shape Response", "type": "main", "index": 0}]]},
            "Shape Response": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "active": False,
        "settings": {"executionOrder": "v1"},
    }


def render_node_red_flow(base_url: str | None = None) -> list[dict]:
    base_url = _resolve_base_url(base_url)
    base = base_url.rstrip("/")
    return [
        {
            "id": "ai-research-tab",
            "type": "tab",
            "label": "AI Research Workflow",
            "disabled": False,
            "info": "POST topic JSON to /ai-research-search, then forward to local /v1/search.",
        },
        {
            "id": "ai-research-http-in",
            "type": "http in",
            "z": "ai-research-tab",
            "name": "Search webhook",
            "url": "/ai-research-search",
            "method": "post",
            "upload": False,
            "swaggerDoc": "",
            "x": 150,
            "y": 120,
            "wires": [["ai-research-shape-request"]],
        },
        {
            "id": "ai-research-shape-request",
            "type": "function",
            "z": "ai-research-tab",
            "name": "Shape /v1/search request",
            "func": "const body = msg.payload || {};\nmsg.headers = { 'content-type': 'application/json' };\nmsg.payload = {\n  topic: body.topic,\n  mode: body.mode || 'auto',\n  max_results: body.max_results || 10,\n  no_network: body.no_network === undefined ? false : body.no_network,\n  providers: body.providers || []\n};\nreturn msg;",
            "outputs": 1,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 390,
            "y": 120,
            "wires": [["ai-research-http-request"]],
        },
        {
            "id": "ai-research-http-request",
            "type": "http request",
            "z": "ai-research-tab",
            "name": "POST /v1/search",
            "method": "POST",
            "ret": "obj",
            "paytoqs": "ignore",
            "url": f"{base}/v1/search",
            "tls": "",
            "persist": False,
            "proxy": "",
            "authType": "",
            "senderr": False,
            "x": 650,
            "y": 120,
            "wires": [["ai-research-shape-response"]],
        },
        {
            "id": "ai-research-shape-response",
            "type": "function",
            "z": "ai-research-tab",
            "name": "Shape agent response",
            "func": "const data = msg.payload || {};\nmsg.payload = {\n  run_id: data.run_id,\n  report_markdown: data.report_markdown,\n  provider_health: data.provider_health || [],\n  source_count: (data.sources || []).length,\n  evidence_count: (data.evidence || []).length,\n  claim_count: (data.claims || []).length\n};\nreturn msg;",
            "outputs": 1,
            "noerr": 0,
            "initialize": "",
            "finalize": "",
            "libs": [],
            "x": 890,
            "y": 120,
            "wires": [["ai-research-http-response"]],
        },
        {
            "id": "ai-research-http-response",
            "type": "http response",
            "z": "ai-research-tab",
            "name": "Return JSON",
            "statusCode": "",
            "headers": {},
            "x": 1130,
            "y": 120,
            "wires": [],
        },
    ]


def render_dify_tool_doc(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    base = base_url.rstrip("/")
    return dedent(
        f"""
        # Dify Tool Setup

        Use Dify's OpenAPI tool import when the local Research Workflow service is reachable from the Dify runtime.

        ## Import

        OpenAPI URL:

        ```text
        {base}/v1/openapi.json
        ```

        Recommended operation:

        ```text
        researchSearch
        ```

        Recommended payload:

        ```json
        {{
          "topic": "{{{{ user_query }}}}",
          "mode": "auto",
          "max_results": 10,
          "no_network": false,
          "providers": []
        }}
        ```

        ## Agent Policy

        - Use `researchSearch` for ordinary deep search.
        - Use `researchDeepen` when `coverage_gaps` or weak source quality remain.
        - Use `researchVerify` before presenting factual conclusions that were written outside the report.
        - Treat `not_found` claims as unresolved, not false and not confirmed.
        - Return `run_id`, source URLs, provider failures, and unverified gaps to the user.

        ## Network Note

        If Dify runs in Docker, `127.0.0.1` points at the Dify container, not the Windows host. Use the host gateway address or expose the Research Workflow service on an explicit local network interface only when you accept that trust boundary.
        """
    ).lstrip()


def render_agent_discovery_json(base_url: str | None = None, *, config_hash: str = "") -> dict:
    base_url = _resolve_base_url(base_url)
    manifest = integration_manifest(base_url, config_hash=config_hash)
    return {
        "name": manifest["name"],
        "base_url": manifest["base_url"],
        "package_version": manifest.get("package_version", package_version()),
        "api_version": manifest["api_version"],
        "contract_version": manifest["contract_version"],
        "config_hash": manifest.get("config_hash", ""),
        "purpose": "Evidence-first deep search, source capture, and atomic fact checking.",
        "runtime_contract": {
            "configured_endpoint_only": True,
            "online_status": "unknown",
            "status_command": "research service status --format json",
            "rule": "Discovery declares capabilities and configuration, not current process liveness.",
        },
        "result_contract": {
            "schema": "research.search-result.v1",
            "field": "interface_contract",
            "task_statuses": ["succeeded"],
            "evidence_statuses": ["collected", "sources_only", "unavailable"],
            "source_trail_statuses": ["complete", "incomplete", "unavailable"],
            "shared_by": ["cli", "http", "mcp", "watch_folder"],
            "rule": "Evidence must retain URL/source provenance; unavailable search never falls back to model memory.",
        },
        "preferred_order": [
            {"interface": "mcp", "when": "Agent supports MCP tools.", "tools": manifest["recommended_mcp_tools"]},
            {"interface": "http", "when": "Agent can make local HTTP requests.", "entrypoint": manifest["preferred_entrypoint"]},
            {"interface": "watch_folder", "when": "Agent can write local files but cannot call tools or HTTP.", **manifest["watch_folder"]},
            {"interface": "bookmarklet", "when": "Human/browser agent selected a useful current page.", **manifest["bookmarklet"]},
            {
                "interface": "cli",
                "when": "Human or shell-capable agent is debugging locally.",
                "commands": [
                    "research service status --format json",
                    "research route <query> --format json",
                    "research contract-test --format json",
                    "research contract-test --live --format json",
                    "research search",
                    "research verify",
                    "research ingest",
                    "research quality <run_id> --format json",
                    "research deepen-claims <run_id> --format json",
                    "research references <path_or_run_id> --format json",
                    "research evidence-bundle-verify <bundle> --schema <schema> --receipt <receipt>",
                    "research web status",
                ],
            },
        ],
        "modes": ["auto", "daily", "academic", "tool_selection"],
        "routing_contract": manifest["routing_contract"],
        "evidence_policy": [
            "Do not use model memory as evidence.",
            "Do not present not_found claims as confirmed.",
            "Surface provider failures when they affect coverage.",
            "Use deepen or manual ingest when coverage gaps remain.",
            "Use source provenance fields before trusting source selection: search_queries, search_buckets, search_purposes, search_stages.",
        ],
        "evidence_bundle_reference": {
            "facade": "evidence-bundle.v1",
            "native_contract": "research-output.v1",
            "native_schema_version": "1.0.0",
            "owner": "question-research-poc",
            "owner_commit": "2d83fc7bb0114417b045726de69f7a3f6b46242b",
            "schema_sha256": "3c2e0e9a05034b1ffa3693735da128c7d3e114ddb155f11668120b974fee1009",
            "interface": "offline_cli_python",
            "command": "research evidence-bundle-verify <bundle> --schema <schema> --receipt <receipt>",
            "consumer_effect": "reference_validation_only",
            "research_truth_effect": "none",
            "evidence_status_change": "none",
            "candidate_promoted": False,
            "service_required": False,
        },
        "stable_http": {
            "health": manifest["endpoints"]["health"],
            "capabilities": manifest["endpoints"]["capabilities"],
            "discovery": manifest["endpoints"]["discovery"],
            "openapi": manifest["endpoints"]["openapi"],
            "agent_research": manifest["endpoints"]["agent_research"],
            "agent_verify": manifest["endpoints"]["agent_verify"],
            "agent_fetch": manifest["endpoints"]["agent_fetch"],
            "agent_job": manifest["endpoints"]["agent_job"],
            "agent_events": manifest["endpoints"]["agent_events"],
            "agent_retry": manifest["endpoints"]["agent_retry"],
            "search": manifest["endpoints"]["v1_search"],
            "route": manifest["endpoints"]["v1_route"],
            "deepen": manifest["endpoints"]["v1_deepen"],
            "verify": manifest["endpoints"]["v1_verify"],
            "ingest": manifest["endpoints"]["v1_ingest"],
            "events": manifest["endpoints"]["v1_events"],
            "quality": manifest["endpoints"]["v1_quality"],
            "deepen_claims": manifest["endpoints"]["v1_deepen_claims"],
            "references_check": manifest["endpoints"]["v1_references_check"],
        },
        "sdk_contract": manifest["sdk_contract"],
        "generated_files": {
            "manifest": "research-workflow.integration.json",
            "openclaw_prompt": "openclaw-research-workflow.prompt.md",
            "hermes_prompt": "hermes-research-workflow.prompt.md",
            **manifest["clients"],
            **manifest["automation_adapters"],
            **manifest["agent_discovery"],
        },
    }


def render_agent_discovery_doc(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    discovery = render_agent_discovery_json(base_url)
    base = discovery["base_url"]
    lines = [
        "# Agent Discovery: AI Research Workflow",
        "",
        "Use this local service for evidence-first deep search, source capture, and atomic fact checking.",
        "",
        "Base URL:",
        "",
        "```text",
        base,
        "```",
        "",
            "## Interface Priority",
            "",
            "1. MCP tools when available:",
        "",
        "This is the configured endpoint, not proof that a process is currently listening. Run research service status --format json before HTTP use.",
        "",
        "",
    ]
    lines.extend(f"- `{tool}`" for tool in discovery["preferred_order"][0]["tools"])
    lines.extend(
        [
            "",
            "2. HTTP API when the agent can call localhost:",
            "",
            "```text",
            f"GET  {base}/v1/health",
            f"GET  {base}/v1/capabilities",
            f"GET  {base}/v1/discovery",
            f"GET  {base}/v1/openapi.json",
            f"POST {base}/v1/agent/research",
            f"POST {base}/v1/agent/verify",
            f"POST {base}/v1/agent/fetch",
            f"GET  {base}/v1/agent/jobs/{{job_id}}",
            f"GET  {base}/v1/agent/jobs/{{job_id}}/events",
            f"POST {base}/v1/agent/jobs/{{job_id}}/retry",
            "```",
            "",
            "Before HTTP integration, check the local service state:",
            "",
            "```powershell",
            "python -m research_workflow.cli service status --format json",
            "```",
            "",
            "Before relying on this service from another project or agent, run the stable contract test:",
            "",
            "```powershell",
            "python -m research_workflow.cli contract-test --format json",
            "```",
            "",
            "Use `--live` only when the Web UI service is already running and you want to test real HTTP calls.",
            "",
            "3. Watch-folder fallback when the agent can write local files:",
            "",
            "```text",
            ".research-jobs/inbox",
            ".research-jobs/outbox",
            ".research-jobs/failed",
            "```",
            "",
            "Supported watch-folder job types: `search`, `deepen`, `verify`, `fetch`, and `ingest`.",
            "Use `fetch` or `ingest` when another agent only needs to hand off URLs:",
            "",
            "```json",
            '{"id":"job_url_001","type":"fetch","urls":["https://example.com"],"fetch":false}',
            "```",
            "",
            "Status files include `contract_version` and `config_hash` so callers can detect stale generated manifests or mismatched config.",
            "",
            "4. Bookmarklet/browser ingest when a human or browser agent selected a useful current page:",
            "",
            "```text",
            "research-workflow-bookmarklet.md",
            "```",
            "",
            "## Mode Selection",
            "",
            "- `tool_selection`: choosing tools, APIs, services, local infrastructure, or Inbox systems.",
            "- `academic`: papers, benchmarks, citations, datasets, and methods.",
            "- `daily`: general deep search.",
            "- `auto`: when uncertain.",
            "",
            "## Evidence Policy",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in discovery["evidence_policy"])
    lines.extend(
        [
            "",
            "## Expected Outputs",
            "",
            "- `run_id`",
            "- `report_markdown`",
            "- `sources`",
            "  - each source includes `search_queries`, `search_buckets`, `search_purposes`, and `search_stages` for provenance",
            "- `evidence`",
            "- `claims`",
            "- `provider_health`",
            "- `coverage_gaps`",
            "",
            "## Generated Companion Files",
            "",
            "- `research-workflow.integration.json`",
            "- `agent-discovery.json`",
            "- interface_contract",
            "  - schema: research.search-result.v1",
            "  - evidence status: collected, sources_only, or unavailable",
            "  - source trail status: complete, incomplete, or unavailable",
            "  - the same summary is emitted by CLI, HTTP, MCP, and watch-folder search results",
            "- `openclaw-research-workflow.prompt.md`",
            "- `hermes-research-workflow.prompt.md`",
            "- `research_workflow_client.py`",
            "- `research-workflow-client.mjs`",
            "- `n8n-research-workflow.json`",
            "- `node-red-research-workflow.json`",
            "- `dify-openapi-tool.md`",
            "- `research-workflow-bookmarklet.md`",
            "",
            "## QRP Evidence-Bundle Reference Boundary",
            "",
            "- Facade: `evidence-bundle.v1`; native payload: QRP `research-output.v1` 1.0.0.",
            "- Canonical owner: `question-research-poc` at `2d83fc7bb0114417b045726de69f7a3f6b46242b`.",
            "- Entry: `research evidence-bundle-verify`; Python: `research_workflow.evidence_bundle_reference.verify_evidence_bundle_reference`.",
            "- The caller supplies the authoritative QRP schema; its SHA-256 must be `3c2e0e9a05034b1ffa3693735da128c7d3e114ddb155f11668120b974fee1009`.",
            "- Success is a reference-validation receipt, not an ARW evidence import or truth transition.",
            "- Candidate, missing support, conflict, stale, corrupt, link/summary/schema/hash drift, AI-synthesis-as-fact, and model-memory-as-evidence states fail closed.",
            "- Every receipt keeps `research_truth_effect=none`, `evidence_status_change=none`, and `candidate_promoted=false`.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_agent_prompt(base_url: str | None = None, *, agent_name: str = "Agent") -> str:
    base_url = _resolve_base_url(base_url)
    base = base_url.rstrip("/")
    return dedent(
        f"""
        # {agent_name} Research Workflow Calling Policy

        You can call the local AI Research Workflow service for deep search, tool selection, evidence collection, and atomic fact checking.

        ## Preferred Interfaces

        1. Use MCP tools first when available:
           - `research_route`
           - `research_search`
           - `research_deepen`
           - `research_verify`
           - `research_ingest`
           - `research_get_report`
           - `research_status`
        2. If MCP is unavailable, call the local HTTP API at `{base}`.
        3. If both MCP and HTTP are unavailable, write a JSON job to `.research-jobs/inbox` and ask the host to run:

        ```powershell
        python -m research_workflow.cli jobs --jobs-dir .research-jobs --data-dir .research --format json
        ```
           Supported file job types are `search`, `deepen`, `verify`, `fetch`, and `ingest`.
           For URL handoff, write `{{"id":"job_url_001","type":"fetch","urls":["https://example.com"],"fetch":false}}`.

        ## HTTP Calls

        - Health: `GET {base}/v1/health`
        - Capabilities: `GET {base}/v1/capabilities`
        - OpenAPI: `GET {base}/v1/openapi.json`
        - Read-only route plan: `POST {base}/v1/route`
        - Research job: `POST {base}/v1/agent/research`
        - Verify job: `POST {base}/v1/agent/verify`
        - Fetch job: `POST {base}/v1/agent/fetch`
        - Poll job: `GET {base}/v1/agent/jobs/{{job_id}}`
        - Poll events: `GET {base}/v1/agent/jobs/{{job_id}}/events`
        - Retry job: `POST {base}/v1/agent/jobs/{{job_id}}/retry`
        - Synchronous search fallback: `POST {base}/v1/search`
        - Synchronous verify fallback: `POST {base}/v1/verify`
        - Report: `GET {base}/v1/runs/{{run_id}}/report.md`
        - Events: `GET {base}/v1/runs/{{run_id}}/events?format=sse`

        ## Mode Choice

        - Use `tool_selection` for choosing tools, APIs, services, local infrastructure, or Inbox systems.
        - Use `academic` for papers, benchmarks, citations, datasets, and methods.
        - Use `daily` for general deep search.
        - Use `auto` when uncertain.

        ## Required Behavior

        - Use ARW as the research control plane. Omit `providers` for adaptive routing; explicit providers mean exact manual routing.
        - Use Agent Reach only for social-platform acquisition and return normalized `external_payloads` to ARW.
        - Use `web-content-fetcher` through ARW fetch/ingest for known URLs and canonical page text.
        - Do not treat model memory as evidence.
        - Do not present `not_found` claims as facts.
        - Surface provider failures when they affect coverage.
        - Respect `coverage_gate`, `provider_budget`, and `stop_reason`; do not fan out after the gate passes.
        - If coverage is weak, call `research_deepen` or ask for manual URLs.
        - Before final factual conclusions, call `research_verify` or use verified claims from the run.
        - Final answers should separate confirmed findings, weak/unverified points, and next steps.
        - For important results, export or ask the host to export the run to Obsidian.
        """
    ).lstrip()


def render_open_webui_tool(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    return dedent(
        f'''
        """
        title: Research Workflow
        author: local
        version: {package_version()}
        description: Calls a local AI Research Workflow backend for evidence-first deep search and atomic fact checking.
        """

        import json
        import urllib.request

        from pydantic import BaseModel, Field


        class Tools:
            class Valves(BaseModel):
                base_url: str = Field(default="{base_url.rstrip('/')}", description="Local Research Workflow Web UI base URL")

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
                payload = {{
                    "topic": topic,
                    "mode": mode,
                    "no_network": no_network,
                    "text_to_verify": text_to_verify,
                }}
                data = self._post("/api/run", payload, timeout=180)
                audit = data.get("audit", {{}}).get("overall", "unknown")
                report = data.get("report_markdown") or json.dumps(data, ensure_ascii=False, indent=2)
                return f"Audit: {{audit}}\\nRun ID: {{data.get('run_id', '')}}\\n\\n{{report}}"

            def research_search(
                self,
                topic: str,
                mode: str = "daily",
                no_network: bool = True,
            ) -> str:
                """Run local search-only planning, retrieval, evidence extraction, and report persistence."""
                payload = {{
                    "topic": topic,
                    "mode": mode,
                    "no_network": no_network,
                }}
                data = self._post("/api/search", payload, timeout=120)
                report = data.get("report_markdown") or json.dumps(data, ensure_ascii=False, indent=2)
                return f"Kind: search\\nRun ID: {{data.get('run_id', '')}}\\n\\n{{report}}"

            def research_add_evidence(self, text: str, url: str = "") -> str:
                """Add one source-backed evidence note to the local evidence store."""
                data = self._post("/api/evidence", {{"text": text, "url": url}}, timeout=60)
                return json.dumps(data, ensure_ascii=False, indent=2)

            def research_selftest(self) -> str:
                """Run the local acceptance selftest for pipeline, audit, history, MCP, and Obsidian export."""
                data = self._post("/api/selftest", {{}}, timeout=180)
                return json.dumps(data, ensure_ascii=False, indent=2)

            def _post(self, path: str, payload: dict, timeout: int = 120) -> dict:
                body = json.dumps(payload).encode("utf-8")
                request = urllib.request.Request(
                    self.valves.base_url.rstrip("/") + path,
                    data=body,
                    headers={{"content-type": "application/json"}},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
        '''
    ).lstrip()


def render_python_client(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    return dedent(
        f'''
        """Small stdlib client for the local AI Research Workflow API."""

        from __future__ import annotations

        import json
        import time
        import urllib.error
        import urllib.parse
        import urllib.request
        from dataclasses import dataclass
        from typing import Any

        EXPECTED_CONTRACT_VERSION = "{CONTRACT_VERSION}"
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
            base_url: str = "{base_url.rstrip("/")}"
            timeout: int = 180

            def health(self) -> dict[str, Any]:
                return self._get("/v1/health")

            def capabilities(self) -> dict[str, Any]:
                return self._get("/v1/capabilities")

            def route(self, query: str, **kwargs: Any) -> dict[str, Any]:
                """Plan provider stages without executing search or changing service state."""
                return self._post("/v1/route", {{"topic": query, **kwargs}})

            def contract_info(self) -> dict[str, Any]:
                health = self.health()
                return {{
                    "contract_version": health.get("contract_version", ""),
                    "config_hash": health.get("config_hash", ""),
                    "base_url": health.get("base_url", self.base_url.rstrip("/")),
                    "status": health.get("status", ""),
                }}

            def assert_contract(self, expected_contract_version: str = EXPECTED_CONTRACT_VERSION) -> dict[str, Any]:
                info = self.contract_info()
                if info.get("contract_version") != expected_contract_version:
                    raise ResearchWorkflowError(
                        "contract_mismatch",
                        f"Expected contract {{expected_contract_version}}, got {{info.get('contract_version')}}",
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
                    {{
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
                    }},
                )

            def agent_verify(self, text: str = "", *, topic: str = "Research Report", run_id: str = "", client_request_id: str = "") -> dict[str, Any]:
                return self._post("/v1/agent/verify", {{"topic": topic, "text": text, "run_id": run_id, "client_request_id": client_request_id}})

            def agent_fetch(self, urls: list[str] | str, *, fetch: bool = True, client_request_id: str = "") -> dict[str, Any]:
                return self._post("/v1/agent/fetch", {{"urls": urls, "fetch": fetch, "client_request_id": client_request_id}})

            def agent_job(self, job_id: str) -> dict[str, Any]:
                return self._get(f"/v1/agent/jobs/{{urllib.parse.quote(job_id)}}")

            def agent_events(self, job_id: str) -> dict[str, Any]:
                return self._get(f"/v1/agent/jobs/{{urllib.parse.quote(job_id)}}/events")

            def agent_retry(self, job_id: str, *, payload: dict[str, Any] | None = None, background: bool = True) -> dict[str, Any]:
                return self._post(
                    f"/v1/agent/jobs/{{urllib.parse.quote(job_id)}}/retry",
                    {{"payload": payload or {{}}, "background": background}},
                )

            def wait_for_agent_job(self, job_id: str, *, interval: float = 1.5, timeout_seconds: float = 300) -> dict[str, Any]:
                deadline = time.monotonic() + timeout_seconds
                while True:
                    job = self.agent_job(job_id)
                    if job.get("status") == "succeeded":
                        return job
                    if job.get("status") == "failed":
                        raise ResearchWorkflowError("job_failed", f"Research Workflow job {{job_id}} failed.", details=self.failure_details(job_id, job))
                    if time.monotonic() >= deadline:
                        raise ResearchWorkflowError("timeout", f"Research Workflow job {{job_id}} did not finish.", details=self.failure_details(job_id, job))
                    time.sleep(interval)

            def failure_details(self, job_id: str, job: dict[str, Any] | None = None) -> dict[str, Any]:
                current = job or self.agent_job(job_id)
                events = self.agent_events(job_id)
                return {{
                    "job": current,
                    "recent_events": (events.get("events") or [])[-5:],
                    "error": current.get("error", ""),
                }}

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
                    {{
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
                    }},
                )

            def deepen(self, topic: str, **kwargs: Any) -> dict[str, Any]:
                payload = {{"topic": topic, **kwargs}}
                return self._post("/v1/deepen", payload)

            def verify(self, text: str, *, topic: str = "Research Report") -> dict[str, Any]:
                return self._post("/v1/verify", {{"topic": topic, "text": text}})

            def ingest(self, urls: list[str] | str, *, fetch: bool = False) -> dict[str, Any]:
                return self._post("/v1/ingest", {{"urls": urls, "fetch": fetch}})

            def run(self, run_id: str) -> dict[str, Any]:
                return self._get(f"/v1/runs/{{urllib.parse.quote(run_id)}}")

            def report_markdown(self, run_id: str) -> str:
                return self._request_text("GET", f"/v1/runs/{{urllib.parse.quote(run_id)}}/report.md")

            def report_json(self, run_id: str) -> dict[str, Any]:
                return self._get(f"/v1/runs/{{urllib.parse.quote(run_id)}}/report.json")

            def events(self, run_id: str) -> dict[str, Any]:
                return self._get(f"/v1/runs/{{urllib.parse.quote(run_id)}}/events")

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
                    raise ResearchWorkflowError("invalid_response", f"Invalid JSON response for {{path}}.", details=text[:500]) from exc
                if not isinstance(data, dict):
                    raise ResearchWorkflowError("invalid_response", f"Expected JSON object for {{path}}.", details=data)
                return data

            def _request_text(self, method: str, path: str, payload: dict[str, Any] | None = None) -> str:
                url = self.base_url.rstrip("/") + path
                data = None
                headers = {{"accept": "application/json"}}
                if payload is not None:
                    data = json.dumps(payload).encode("utf-8")
                    headers["content-type"] = "application/json"
                request = urllib.request.Request(url, data=data, headers=headers, method=method)
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        return response.read().decode("utf-8")
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise ResearchWorkflowError("http_error", f"Research Workflow HTTP {{exc.code}} for {{path}}.", details=detail) from exc
                except urllib.error.URLError as exc:
                    raise ResearchWorkflowError("service_unavailable", f"Research Workflow service is unavailable for {{path}}.", details=str(exc)) from exc


        if __name__ == "__main__":
            client = ResearchWorkflowClient()
            print(json.dumps(client.health(), ensure_ascii=False, indent=2))
        '''
    ).lstrip()


def render_rest_client(base_url: str | None = None) -> str:
    base_url = _resolve_base_url(base_url)
    return dedent(
        f"""
        const DEFAULT_BASE_URL = "{base_url.rstrip("/")}";
        export const EXPECTED_CONTRACT_VERSION = "{CONTRACT_VERSION}";
        export const SDK_ERROR_CATEGORIES = [
          "service_unavailable",
          "contract_mismatch",
          "job_failed",
          "timeout",
          "http_error",
          "invalid_response",
        ];

        export class ResearchWorkflowError extends Error {{
          constructor(category, message, details = null) {{
            super(message);
            this.name = "ResearchWorkflowError";
            this.category = category;
            this.details = details;
          }}
        }}

        export async function health(baseUrl = DEFAULT_BASE_URL) {{
          return requestJSON(`${{baseUrl.replace(/\\/$/, "")}}/v1/health`);
        }}

        export async function contractInfo(baseUrl = DEFAULT_BASE_URL) {{
          const data = await health(baseUrl);
          return {{
            contract_version: data.contract_version || "",
            config_hash: data.config_hash || "",
            base_url: data.base_url || baseUrl.replace(/\\/$/, ""),
            status: data.status || "",
          }};
        }}

        export async function assertContract(expectedContractVersion = EXPECTED_CONTRACT_VERSION, baseUrl = DEFAULT_BASE_URL) {{
          const info = await contractInfo(baseUrl);
          if (info.contract_version !== expectedContractVersion) {{
            throw new ResearchWorkflowError("contract_mismatch", `Expected contract ${{expectedContractVersion}}, got ${{info.contract_version}}`, info);
          }}
          return info;
        }}

        export async function route(query, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/v1/route", {{ topic: query, ...options }});
        }}

        export async function agentResearch(query, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/v1/agent/research", {{
            query,
            mode: "auto",
            max_results: 10,
            no_network: false,
            providers: [],
            urls: [],
            fetch: false,
            auto_verify: true,
            client_request_id: "",
            profile: "default",
            routing_strategy: "adaptive_staged",
            external_payloads: [],
            ...options,
          }});
        }}

        export async function agentVerify(text, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/v1/agent/verify", {{ text, topic: "Research Report", ...options }});
        }}

        export async function agentFetch(urls, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/v1/agent/fetch", {{ urls, fetch: true, ...options }});
        }}

        export async function getAgentJob(jobId, baseUrl = DEFAULT_BASE_URL) {{
          return requestJSON(`${{baseUrl.replace(/\\/$/, "")}}/v1/agent/jobs/${{encodeURIComponent(jobId)}}`);
        }}

        export async function getAgentJobEvents(jobId, baseUrl = DEFAULT_BASE_URL) {{
          return requestJSON(`${{baseUrl.replace(/\\/$/, "")}}/v1/agent/jobs/${{encodeURIComponent(jobId)}}/events`);
        }}

        export async function retryAgentJob(jobId, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, `/v1/agent/jobs/${{encodeURIComponent(jobId)}}/retry`, {{
            payload: {{}},
            background: true,
            ...options,
          }});
        }}

        export async function waitForAgentJob(jobId, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          const intervalMs = options.intervalMs ?? 1500;
          const timeoutMs = options.timeoutMs ?? 300000;
          const startedAt = Date.now();
          while (true) {{
            const job = await getAgentJob(jobId, baseUrl);
            if (job.status === "succeeded") return job;
            if (job.status === "failed") {{
              throw new ResearchWorkflowError("job_failed", `Research Workflow job ${{jobId}} failed.`, await failureDetails(jobId, job, baseUrl));
            }}
            if (Date.now() - startedAt >= timeoutMs) {{
              throw new ResearchWorkflowError("timeout", `Research Workflow job ${{jobId}} timed out.`, await failureDetails(jobId, job, baseUrl));
            }}
            await new Promise(resolve => setTimeout(resolve, intervalMs));
          }}
        }}

        export async function failureDetails(jobId, job = null, baseUrl = DEFAULT_BASE_URL) {{
          const current = job || await getAgentJob(jobId, baseUrl);
          const events = await getAgentJobEvents(jobId, baseUrl);
          return {{
            job: current,
            recent_events: (events.events || []).slice(-5),
            error: current.error || "",
          }};
        }}

        export async function agentResearchAndWait(query, options = {{}}, baseUrl = DEFAULT_BASE_URL) {{
          const accepted = await agentResearch(query, options, baseUrl);
          return waitForAgentJob(accepted.job_id, options, baseUrl);
        }}

        export async function runResearchWorkflow(options, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/api/run", {{
            mode: "daily",
            no_network: true,
            max_results: 10,
            urls: [],
            manual_evidence: [],
            ...options,
          }});
        }}

        export async function runResearchSearch(options, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/api/search", {{
            mode: "daily",
            no_network: true,
            max_results: 10,
            urls: [],
            ...options,
          }});
        }}

        export async function addResearchEvidence(evidence, baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/api/evidence", evidence);
        }}

        export async function runResearchSelftest(baseUrl = DEFAULT_BASE_URL) {{
          return postJSON(baseUrl, "/api/selftest", {{}});
        }}

        export async function listResearchRuns(limit = 20, baseUrl = DEFAULT_BASE_URL) {{
          return requestJSON(`${{baseUrl.replace(/\\/$/, "")}}/api/runs?limit=${{encodeURIComponent(limit)}}`);
        }}

        export async function auditResearchRun(runId, baseUrl = DEFAULT_BASE_URL) {{
          return requestJSON(`${{baseUrl.replace(/\\/$/, "")}}/api/audit?id=${{encodeURIComponent(runId)}}`);
        }}

        async function postJSON(baseUrl, path, payload) {{
          return requestJSON(baseUrl.replace(/\\/$/, "") + path, {{
            method: "POST",
            headers: {{ "content-type": "application/json" }},
            body: JSON.stringify(payload),
          }});
        }}

        async function requestJSON(url, options = {{}}) {{
          let response;
          try {{
            response = await fetch(url, options);
          }} catch (error) {{
            throw new ResearchWorkflowError("service_unavailable", `Research Workflow service is unavailable: ${{error.message}}`, {{ url }});
          }}
          return parseResponse(response);
        }}

        async function parseResponse(response) {{
          let data;
          try {{
            data = await response.json();
          }} catch (error) {{
            throw new ResearchWorkflowError("invalid_response", `Invalid JSON response: ${{error.message}}`, {{ status: response.status }});
          }}
          if (!response.ok) {{
            throw new ResearchWorkflowError("http_error", data.error || `Research Workflow request failed: ${{response.status}}`, {{ status: response.status, data }});
          }}
          return data;
        }}
        """
    ).lstrip()


def _sdk_contract() -> dict:
    return {
        "expected_contract_version": CONTRACT_VERSION,
        "recommended_timeout_seconds": 300,
        "recommended_poll_interval_seconds": 1.5,
        "entrypoints": [
            "health",
            "contract_info",
            "assert_contract",
            "route",
            "agent_research",
            "agent_verify",
            "agent_fetch",
            "agent_job",
            "agent_events",
            "agent_retry",
            "wait_for_agent_job",
            "failure_details",
        ],
        "error_categories": [
            "service_unavailable",
            "contract_mismatch",
            "job_failed",
            "timeout",
            "http_error",
            "invalid_response",
        ],
    }


def _write_file(path: Path, content: str, description: str) -> IntegrationFile:
    path.write_text(content, encoding="utf-8")
    return IntegrationFile(path.stem, path, description)
