from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .assets import asset_dir
from .config import ResearchConfig
from .pipeline import run_research_pipeline
from .reader_health import reader_health
from .search import available_providers, check_searxng, resolve_searxng_url
from .sqlite_store import sqlite_summary
from .store import ensure_data_dir, write_text
from .service_status import run_service_status


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.details is None:
            data.pop("details")
        return data


def run_doctor(config: ResearchConfig, data_dir: Path, *, include_pipeline: bool = True) -> dict[str, Any]:
    service_status = run_service_status(config, data_dir, timeout_seconds=0.25)
    checks = [
        _check_config(config),
        _check_service_runtime(service_status),
        _check_data_dir(data_dir),
        _check_static_assets(),
        _check_mcp_tools(),
        _check_search_providers(config),
        _check_searxng(config),
        _check_reader(config),
        _check_sqlite(data_dir),
    ]
    if include_pipeline:
        checks.append(_check_pipeline())
    overall = _overall_status(checks)
    return {
        "overall": overall,
        "observed_service_record": service_status["observed_service_record"],
        "checks": [check.to_dict() for check in checks],
    }


def doctor_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Doctor", "", f"Overall: `{result.get('overall', 'unknown')}`", ""]
    for check in result.get("checks", []):
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**: {check.get('message')}")
    return "\n".join(lines) + "\n"


def _check_service_runtime(status: dict[str, Any]) -> DoctorCheck:
    runtime_state = str(status.get("runtime_state") or "unknown")
    if runtime_state == "online":
        check_status = "ok"
        message = "Configured Web service is listening and healthy."
    elif runtime_state == "stopped_by_design":
        check_status = "ok"
        message = "Web service is stopped by design; configuration does not imply online status."
    elif runtime_state == "needs_manual_start":
        check_status = "warn"
        message = "Web service is not listening and requires an explicit manual start for HTTP use."
    else:
        check_status = "error"
        message = "Web service has a process or listener but its health contract failed."
    return DoctorCheck(
        "service_runtime",
        check_status,
        message,
        status.get("observed_service_record") or {},
    )
def _check_config(config: ResearchConfig) -> DoctorCheck:
    return DoctorCheck(
        name="config",
        status="ok",
        message="Configuration loaded.",
        details=config.to_dict(),
    )


def _check_data_dir(data_dir: Path) -> DoctorCheck:
    try:
        ensured = ensure_data_dir(data_dir)
        probe = ensured / ".doctor-write-test"
        write_text(probe, "ok\n")
        probe.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("data_dir", "error", f"Data directory is not writable: {exc}", {"path": str(data_dir)})
    return DoctorCheck("data_dir", "ok", "Data directory is writable.", {"path": str(ensured.resolve())})


def _check_static_assets() -> DoctorCheck:
    root = asset_dir("web")
    missing = [name for name in ("index.html", "app.js", "styles.css") if not (root / name).exists()]
    if missing:
        return DoctorCheck("web_static", "error", f"Missing static assets: {', '.join(missing)}", {"root": str(root)})
    return DoctorCheck("web_static", "ok", "Web UI static assets are present.", {"root": str(root)})


def _check_mcp_tools() -> DoctorCheck:
    from .mcp_server import TOOL_SCHEMAS, handle_request

    expected = {
        "multi_source_search",
        "extract_evidence",
        "extract_atomic_claims",
        "verify_claims",
        "write_research_report",
        "run_research",
        "doctor",
        "list_runs",
        "get_run",
        "export_obsidian",
        "audit",
        "selftest",
        "evals",
        "bootstrap",
        "readiness",
    }
    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) or {}
    tools = {tool.get("name") for tool in listed.get("result", {}).get("tools", [])}
    missing = sorted(expected - tools)
    if missing:
        return DoctorCheck("mcp_tools", "error", f"MCP tools missing: {', '.join(missing)}", {"tools": sorted(tools)})
    schema_missing = sorted(expected - set(TOOL_SCHEMAS))
    if schema_missing:
        return DoctorCheck("mcp_tools", "error", f"MCP schemas missing: {', '.join(schema_missing)}", {"tools": sorted(tools)})
    return DoctorCheck("mcp_tools", "ok", "MCP tools are registered.", {"tools": sorted(tools)})


def _check_searxng(config: ResearchConfig) -> DoctorCheck:
    resolved = resolve_searxng_url(config.searxng_url)
    if not resolved:
        return DoctorCheck("searxng", "warn", "No SearXNG endpoint configured or auto-detected.")
    if check_searxng(resolved):
        return DoctorCheck("searxng", "ok", "SearXNG JSON endpoint is reachable.", {"url": resolved})
    return DoctorCheck("searxng", "warn", "SearXNG endpoint is configured but not reachable.", {"url": resolved})


def _check_search_providers(config: ResearchConfig) -> DoctorCheck:
    providers = [provider.strip().lower() for provider in config.search_providers if provider.strip()]
    available = available_providers()
    unknown = sorted(provider for provider in providers if provider not in available)
    if unknown:
        return DoctorCheck("search_providers", "error", f"Unknown providers configured: {', '.join(unknown)}", {"providers": providers})
    configured = {name: details for name, details in available.items() if bool(details.get("configured"))}
    effective = [provider for provider in providers if bool(available.get(provider, {}).get("configured"))]
    enabled_unconfigured = [
        provider for provider in providers if provider != "searxng" and not bool(available.get(provider, {}).get("configured"))
    ]
    details = {
        "enabled": providers,
        "effective": effective,
        "configured": sorted(configured),
        "disabled_unconfigured": enabled_unconfigured,
        "available": available,
    }
    if enabled_unconfigured:
        return DoctorCheck(
            "search_providers",
            "warn",
            f"Optional providers enabled without API keys: {', '.join(enabled_unconfigured)}. Effective providers: {', '.join(effective) or 'none'}.",
            details,
        )
    return DoctorCheck(
        "search_providers",
        "ok",
        "Search provider registry is valid.",
        details,
    )


def _check_reader(config: ResearchConfig) -> DoctorCheck:
    health = reader_health(config)
    status = "ok" if health.get("status") == "ok" else "error"
    return DoctorCheck(
        "reader",
        status,
        str(health.get("message") or "Reader health checked."),
        health,
    )


def _check_sqlite(data_dir: Path) -> DoctorCheck:
    try:
        summary = sqlite_summary(data_dir)
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("sqlite_store", "error", f"SQLite store is not usable: {exc}", {"path": str(data_dir)})
    return DoctorCheck("sqlite_store", "ok", "SQLite evidence store is initialized.", summary)


def _check_pipeline() -> DoctorCheck:
    try:
        with TemporaryDirectory(prefix="research-doctor-") as temp_dir:
            result = run_research_pipeline(
                topic="SearXNG",
                data_dir=Path(temp_dir),
                mode="daily",
                no_network=True,
                manual_evidence=[
                    {
                        "url": "https://docs.searxng.org/",
                        "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                    }
                ],
            )
    except Exception as exc:  # noqa: BLE001
        return DoctorCheck("pipeline", "error", f"Local no-network pipeline failed: {exc}")
    supported = sum(1 for claim in result.claims if str(claim.verdict) == "supported")
    if not result.run_id or not result.claims or supported < 1:
        return DoctorCheck(
            "pipeline",
            "error",
            "Pipeline ran but did not produce a supported claim.",
            {"run_id": result.run_id, "claims": len(result.claims), "supported": supported},
        )
    return DoctorCheck(
        "pipeline",
        "ok",
        "Local no-network pipeline produced a persisted supported result.",
        {"run_id": result.run_id, "claims": len(result.claims), "supported": supported},
    )


def _overall_status(checks: list[DoctorCheck]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"
