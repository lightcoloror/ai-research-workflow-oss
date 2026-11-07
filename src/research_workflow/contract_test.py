from __future__ import annotations

import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .agent_smoke import run_agent_smoke
from .assets import asset_file
from .config import ResearchConfig, config_fingerprint, web_base_url
from .contract import CONTRACT_VERSION
from .integrations import write_integrations
from .metadata import package_version
from .service_registry import update_service_smoke
from .watch_folder import process_watch_folder
from .web import ResearchWebApp


REQUIRED_AGENT_ENDPOINTS = {
    "GET /v1/health",
    "GET /v1/capabilities",
    "GET /v1/discovery",
    "GET /v1/openapi.json",
    "POST /v1/agent/research",
    "GET /v1/agent/jobs/{job_id}",
    "GET /v1/agent/jobs/{job_id}/events",
    "POST /v1/agent/jobs/{job_id}/retry",
}

REQUIRED_MCP_TOOLS = {
    "research_search",
    "research_deepen",
    "research_verify",
    "research_ingest",
    "research_get_report",
    "research_status",
}


def run_contract_test(
    config: ResearchConfig,
    data_dir: Path,
    *,
    live: bool = False,
    base_url: str = "",
    timeout_seconds: float = 60,
    interval_seconds: float = 1,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    app = ResearchWebApp(data_dir, config=config)
    resolved_base_url = (base_url or web_base_url(config)).rstrip("/")

    _record(checks, "health_shape", _check_health(app.health()))
    _record(checks, "capabilities_contract", _check_capabilities(app.capabilities()))
    _record(checks, "discovery_contract", _check_discovery(app.discovery(), resolved_base_url))
    _record(checks, "openapi_contract", _check_openapi(app.openapi_schema(), resolved_base_url))
    _record(checks, "openapi_schema_contract", _check_openapi_schema_contract(app.openapi_schema()))
    _record(checks, "agent_job_shape", _check_agent_job_shape(app))
    _record(checks, "watch_folder_contract", _check_watch_folder_contract(config))
    _record(checks, "sdk_generation_contract", _check_sdk_generation(config))
    _record(checks, "integration_generation", _check_integrations(config))
    _record(checks, "generated_artifacts_contract", _check_generated_artifacts(config))
    if live:
        smoke = run_agent_smoke(resolved_base_url, timeout_seconds=timeout_seconds, interval_seconds=interval_seconds)
        update_service_smoke(data_dir, base_url=resolved_base_url, smoke=smoke)
        _record(checks, "live_agent_smoke", {"status": "ok" if smoke.get("overall") == "ok" else "error", "details": smoke})
    else:
        _record(checks, "live_agent_smoke", {"status": "skipped", "message": "Use --live to test the running HTTP service."})

    overall = "error" if any(check["status"] == "error" for check in checks) else "warn" if any(check["status"] == "warn" for check in checks) else "ok"
    return {
        "overall": overall,
        "service": "ai-research-workflow",
        "base_url": resolved_base_url,
        "live": live,
        "checks": checks,
        "stable_contract": {
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(config),
            "http_endpoints": sorted(REQUIRED_AGENT_ENDPOINTS),
            "mcp_tools": sorted(REQUIRED_MCP_TOOLS),
            "watch_folder_job_types": ["search", "deepen", "verify", "fetch", "ingest"],
        },
    }


def contract_test_to_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Research Workflow Contract Test",
        "",
        f"- Overall: `{result.get('overall')}`",
        f"- Base URL: `{result.get('base_url')}`",
        f"- Live: `{str(result.get('live')).lower()}`",
        "",
        "## Checks",
    ]
    for check in result.get("checks", []):
        message = check.get("message") or ""
        suffix = f": {message}" if message else ""
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**{suffix}")
    return "\n".join(lines) + "\n"


def contract_test_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def _check_health(health: dict[str, Any]) -> dict[str, Any]:
    required = {"status", "service", "api_version", "contract_version", "config_hash", "base_url"}
    missing = sorted(required - set(health))
    if missing:
        return {"status": "error", "message": "Health response is missing required fields.", "missing": missing, "health": health}
    if health.get("status") != "ok" or health.get("service") != "ai-research-workflow":
        return {"status": "error", "message": "Health response identifies the wrong service.", "health": health}
    if health.get("contract_version") != CONTRACT_VERSION:
        return {"status": "error", "message": "Health response has an unexpected contract version.", "expected": CONTRACT_VERSION, "health": health}
    return {"status": "ok", "message": "Health response has the stable v1 shape.", "health": health}


def _check_capabilities(capabilities: dict[str, Any]) -> dict[str, Any]:
    stable = set(capabilities.get("stable_endpoints") or [])
    missing = sorted(REQUIRED_AGENT_ENDPOINTS - stable)
    if missing:
        return {"status": "error", "message": "Capabilities are missing stable endpoints.", "missing": missing}
    if capabilities.get("contract_version") != CONTRACT_VERSION:
        return {"status": "error", "message": "Capabilities have an unexpected contract version.", "expected": CONTRACT_VERSION, "actual": capabilities.get("contract_version")}
    return {"status": "ok", "message": "Capabilities include required stable endpoints."}


def _check_discovery(discovery: dict[str, Any], base_url: str) -> dict[str, Any]:
    preferred = discovery.get("preferred_order") or []
    interfaces = [item.get("interface") for item in preferred]
    tools = set()
    for item in preferred:
        tools.update(item.get("tools") or [])
    missing_tools = sorted(REQUIRED_MCP_TOOLS - tools)
    if "mcp" not in interfaces or "http" not in interfaces:
        return {"status": "error", "message": "Discovery must expose MCP and HTTP interface order.", "interfaces": interfaces}
    if missing_tools:
        return {"status": "error", "message": "Discovery is missing recommended MCP tools.", "missing_tools": missing_tools}
    if str(discovery.get("base_url", "")).rstrip("/") != base_url:
        return {"status": "error", "message": "Discovery base_url does not match current config.", "expected": base_url, "actual": discovery.get("base_url")}
    if discovery.get("contract_version") != CONTRACT_VERSION:
        return {"status": "error", "message": "Discovery has an unexpected contract version.", "expected": CONTRACT_VERSION, "actual": discovery.get("contract_version")}
    return {"status": "ok", "message": "Discovery matches stable agent interface policy."}


def _check_openapi(schema: dict[str, Any], base_url: str) -> dict[str, Any]:
    paths = set(schema.get("paths") or {})
    required_paths = {endpoint.split(" ", 1)[1] for endpoint in REQUIRED_AGENT_ENDPOINTS}
    missing = sorted(required_paths - paths)
    servers = schema.get("servers") or []
    if missing:
        return {"status": "error", "message": "OpenAPI is missing required paths.", "missing": missing}
    if not servers or str(servers[0].get("url", "")).rstrip("/") != base_url:
        return {"status": "error", "message": "OpenAPI server URL does not match current config.", "servers": servers, "expected": base_url}
    return {"status": "ok", "message": "OpenAPI exposes required agent paths and configured server URL."}


def _check_openapi_schema_contract(schema: dict[str, Any]) -> dict[str, Any]:
    schemas = ((schema.get("components") or {}).get("schemas") or {})
    required_schema_fields = {
        "AgentJobAccepted": {"job_id", "status", "kind", "stage", "events", "links", "result", "error"},
        "AgentJobStatus": {"job_id", "status", "kind", "stage", "events", "links", "result", "error"},
        "AgentJobEvents": {"job_id", "status", "kind", "stage", "events", "links", "result", "error"},
    }
    missing: dict[str, list[str]] = {}
    for name, fields in required_schema_fields.items():
        properties = set((schemas.get(name) or {}).get("properties") or {})
        gap = sorted(fields - properties)
        if gap:
            missing[name] = gap
    if missing:
        return {"status": "error", "message": "OpenAPI stable Agent schemas are missing required fields.", "missing": missing}
    return {"status": "ok", "message": "OpenAPI stable Agent schemas expose job status, result, error, events, and links fields."}


def _check_agent_job_shape(app: ResearchWebApp) -> dict[str, Any]:
    accepted = app.agent_research({"query": "contract smoke", "no_network": True, "client_request_id": "contract-test"})
    required = {"job_id", "status", "kind", "stage", "events", "links"}
    missing = sorted(required - set(accepted))
    if missing:
        return {"status": "error", "message": "Agent research response is missing required fields.", "missing": missing, "accepted": accepted}
    links = accepted.get("links") or {}
    if not {"job", "events", "retry"}.issubset(links):
        return {"status": "error", "message": "Agent research response is missing stable links.", "links": links}
    final: dict[str, Any] = {}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        final = app.agent_job(accepted["job_id"])
        if final.get("status") in {"succeeded", "failed"}:
            break
        time.sleep(0.1)
    if final.get("status") != "succeeded":
        return {"status": "error", "message": "No-network agent job did not succeed.", "job": final}
    return {"status": "ok", "message": "No-network agent research job returns stable async shape.", "job_id": accepted["job_id"]}


def _check_watch_folder_contract(config: ResearchConfig) -> dict[str, Any]:
    with TemporaryDirectory(prefix="research-contract-watch-folder-") as temp_root:
        root = Path(temp_root) / "jobs"
        inbox = root / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "contract_fetch.json").write_text(
            json.dumps(
                {
                    "id": "contract_fetch",
                    "type": "fetch",
                    "urls": ["https://docs.searxng.org/"],
                    "fetch": False,
                }
            ),
            encoding="utf-8",
        )
        result = process_watch_folder(root=root, data_dir=Path(temp_root) / "data", config=config)
        if result.failed:
            return {"status": "error", "message": "Watch-folder contract fetch job failed.", "failed": result.failed}
        status_path = root / "outbox" / "contract_fetch.status.json"
        report_path = root / "outbox" / "contract_fetch.report.json"
        if not status_path.exists() or not report_path.exists():
            return {"status": "error", "message": "Watch-folder contract did not create expected output files."}
        status = json.loads(status_path.read_text(encoding="utf-8"))
        required = {"service", "api_version", "contract_version", "config_hash", "job_id", "type", "status", "created_outputs", "report_json"}
        missing = sorted(required - set(status))
        if missing:
            return {"status": "error", "message": "Watch-folder status is missing required fields.", "missing": missing, "status_payload": status}
        if status.get("contract_version") != CONTRACT_VERSION or status.get("config_hash") != config_fingerprint(config):
            return {"status": "error", "message": "Watch-folder status has stale contract/config metadata.", "status_payload": status}
        if status.get("type") != "fetch" or status.get("status") != "ok":
            return {"status": "error", "message": "Watch-folder fetch job returned unexpected status.", "status_payload": status}
    return {"status": "ok", "message": "Watch-folder fallback accepts URL capture jobs and writes stable status envelope."}


def _check_integrations(config: ResearchConfig) -> dict[str, Any]:
    with TemporaryDirectory(prefix="research-contract-integrations-") as temp_root:
        output_dir = Path(temp_root)
        result = write_integrations(output_dir, web_base_url(config), config_hash=config_fingerprint(config))
        discovery_path = output_dir / "agent-discovery.json"
        manifest_path = output_dir / "research-workflow.integration.json"
        if not discovery_path.exists() or not manifest_path.exists():
            return {"status": "error", "message": "Generated integrations are missing discovery or manifest files.", "files": result.get("files", [])}
        discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if discovery.get("base_url") != web_base_url(config) or manifest.get("base_url") != web_base_url(config):
            return {"status": "error", "message": "Generated integrations do not use configured base URL.", "discovery": discovery.get("base_url"), "manifest": manifest.get("base_url")}
        if discovery.get("contract_version") != CONTRACT_VERSION or manifest.get("contract_version") != CONTRACT_VERSION:
            return {"status": "error", "message": "Generated integrations do not use current contract version.", "discovery": discovery.get("contract_version"), "manifest": manifest.get("contract_version")}
        if discovery.get("package_version") != package_version() or manifest.get("package_version") != package_version():
            return {"status": "error", "message": "Generated integrations do not use current package version.", "discovery": discovery.get("package_version"), "manifest": manifest.get("package_version")}
        if discovery.get("config_hash") != config_fingerprint(config) or manifest.get("config_hash") != config_fingerprint(config):
            return {"status": "error", "message": "Generated integrations do not use current config hash.", "discovery": discovery.get("config_hash"), "manifest": manifest.get("config_hash")}
    return {"status": "ok", "message": "Generated integration discovery and manifest use current config."}


def _check_sdk_generation(config: ResearchConfig) -> dict[str, Any]:
    with TemporaryDirectory(prefix="research-contract-sdk-") as temp_root:
        output_dir = Path(temp_root)
        write_integrations(output_dir, web_base_url(config), config_hash=config_fingerprint(config))
        python_client = (output_dir / "research_workflow_client.py").read_text(encoding="utf-8")
        js_client = (output_dir / "research-workflow-client.mjs").read_text(encoding="utf-8")
        manifest = json.loads((output_dir / "research-workflow.integration.json").read_text(encoding="utf-8"))
        discovery = json.loads((output_dir / "agent-discovery.json").read_text(encoding="utf-8"))
    required_methods = [
        "agent_research",
        "agent_verify",
        "agent_fetch",
        "agent_job",
        "agent_events",
        "agent_retry",
        "wait_for_agent_job",
        "failure_details",
        "assert_contract",
        "contract_info",
    ]
    missing_python = [method for method in required_methods if f"def {method}" not in python_client]
    required_js = [
        "agentResearch",
        "agentVerify",
        "agentFetch",
        "getAgentJob",
        "getAgentJobEvents",
        "retryAgentJob",
        "waitForAgentJob",
        "failureDetails",
        "assertContract",
        "contractInfo",
    ]
    missing_js = [method for method in required_js if method not in js_client]
    error_categories = {"service_unavailable", "contract_mismatch", "job_failed", "timeout", "http_error", "invalid_response"}
    missing_errors = sorted(category for category in error_categories if category not in python_client or category not in js_client)
    sdk_contract = manifest.get("sdk_contract") or {}
    discovery_sdk = discovery.get("sdk_contract") or {}
    if missing_python or missing_js or missing_errors:
        return {"status": "error", "message": "Generated SDK clients are missing stable helpers.", "missing_python": missing_python, "missing_js": missing_js, "missing_errors": missing_errors}
    if sdk_contract.get("expected_contract_version") != CONTRACT_VERSION or discovery_sdk.get("expected_contract_version") != CONTRACT_VERSION:
        return {"status": "error", "message": "Generated SDK contract metadata is stale.", "manifest": sdk_contract, "discovery": discovery_sdk}
    return {"status": "ok", "message": "Generated Python and JavaScript SDKs expose stable helpers and error categories."}


def _check_generated_artifacts(config: ResearchConfig) -> dict[str, Any]:
    manifest = _read_json(asset_file("integrations/research-workflow.integration.json"))
    discovery = _read_json(asset_file("integrations/agent-discovery.json"))
    python_client = _read_text(asset_file("integrations/research_workflow_client.py"))
    js_client = _read_text(asset_file("integrations/research-workflow-client.mjs"))
    readme = _read_text(asset_file("README.md"))
    skill = _read_text(asset_file("docs/openclaw-hermes-research-agent.skill.md"))
    issues: list[str] = []
    warnings: list[str] = []
    expected_hash = config_fingerprint(config)
    for name, payload in {"manifest": manifest, "discovery": discovery}.items():
        if not payload:
            issues.append(f"{name}_missing")
            continue
        if payload.get("contract_version") != CONTRACT_VERSION:
            issues.append(f"{name}_contract_version_mismatch")
        if payload.get("package_version") != package_version():
            issues.append(f"{name}_package_version_mismatch")
        if payload.get("config_hash") != expected_hash:
            warnings.append(f"{name}_config_hash_mismatch")
    required_sdk_terms = ["contract_mismatch", "job_failed", "timeout", "http_error", "invalid_response", "assert_contract", "contract_info"]
    if any(term not in python_client for term in required_sdk_terms):
        issues.append("python_sdk_stale")
    required_js_terms = ["contract_mismatch", "job_failed", "timeout", "http_error", "invalid_response", "assertContract", "contractInfo"]
    if any(term not in js_client for term in required_js_terms):
        issues.append("javascript_sdk_stale")
    required_docs = ["agent-diagnose --skip-smoke --format json", "research readiness", "research contract-test"]
    if any(term not in readme for term in required_docs):
        issues.append("readme_entrypoint_missing")
    if "research_search" not in skill or "research_verify" not in skill:
        issues.append("agent_skill_entrypoint_missing")
    if issues:
        return {
            "status": "error",
            "message": "Generated artifacts are stale or incomplete.",
            "issues": sorted(issues),
            "warnings": sorted(warnings),
            "recommended_next_action": "run research integrations --output integrations",
        }
    if warnings:
        return {
            "status": "ok",
            "message": "Checked-in generated artifacts match the current release contract; config-sensitive hashes differ for this environment.",
            "warnings": sorted(warnings),
            "recommended_next_action": "run research integrations --output integrations when updating checked-in artifacts for this local config",
        }
    return {"status": "ok", "message": "Checked-in generated artifacts match the current release contract."}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _record(checks: list[dict[str, Any]], name: str, result: dict[str, Any]) -> None:
    checks.append({"name": name, **result})
