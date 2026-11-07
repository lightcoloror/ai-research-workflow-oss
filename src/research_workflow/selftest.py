from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .audit import audit_run
from .config import ResearchConfig
from .obsidian_export import export_run_to_obsidian
from .pipeline import get_run, run_research_pipeline


@dataclass(slots=True)
class SelfTestCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.details is None:
            data.pop("details")
        return data


def run_selftest(config: ResearchConfig, data_dir: Path | None = None) -> dict[str, Any]:
    with TemporaryDirectory(prefix="research-selftest-") as temp_root:
        root = data_dir or Path(temp_root)
        root.mkdir(parents=True, exist_ok=True)
        checks: list[SelfTestCheck] = []
        run_id = ""
        try:
            result = run_research_pipeline(
                topic="SearXNG",
                data_dir=root / "data",
                mode="daily",
                no_network=True,
                manual_evidence=[
                    {
                        "url": "https://docs.searxng.org/",
                        "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                    }
                ],
                text_to_verify="SearXNG is a metasearch engine that aggregates results from many search services.",
                searxng_url=config.searxng_url,
                search_providers=config.search_providers,
            )
            run_id = result.run_id
            if result.audit and result.audit.get("overall") == "ok":
                checks.append(SelfTestCheck("pipeline", "ok", "Pipeline produced a run with automatic audit ok.", {"run_id": run_id}))
            else:
                checks.append(SelfTestCheck("pipeline", "error", "Pipeline did not produce an ok automatic audit.", {"run_id": run_id, "audit": result.audit}))
        except Exception as exc:  # noqa: BLE001
            checks.append(SelfTestCheck("pipeline", "error", f"Pipeline failed: {exc}"))

        if run_id:
            checks.append(_check_restored_run(root / "data", run_id))
            checks.append(_check_manual_audit(root / "data", run_id))
            checks.append(_check_obsidian_export(root / "data", run_id, root / "vault"))
        checks.append(_check_mcp_tools())
        checks.append(_check_mcp_run(root / "mcp-data"))

        return {
            "overall": _overall_status(checks),
            "data_dir": str(root),
            "run_id": run_id,
            "checks": [check.to_dict() for check in checks],
        }


def selftest_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Selftest", "", f"Overall: `{result.get('overall', 'unknown')}`", ""]
    if result.get("run_id"):
        lines.append(f"Run ID: `{result.get('run_id')}`")
        lines.append("")
    for check in result.get("checks", []):
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**: {check.get('message')}")
    return "\n".join(lines) + "\n"


def _check_restored_run(data_dir: Path, run_id: str) -> SelfTestCheck:
    record = get_run(data_dir, run_id)
    if not record:
        return SelfTestCheck("history", "error", "Generated run could not be restored from runs.jsonl.")
    if record.get("audit", {}).get("overall") != "ok":
        return SelfTestCheck("history", "error", "Restored run is missing automatic audit ok.", {"run_id": run_id, "audit": record.get("audit")})
    return SelfTestCheck("history", "ok", "Generated run restored with audit ok.", {"run_id": run_id})


def _check_manual_audit(data_dir: Path, run_id: str) -> SelfTestCheck:
    audited = audit_run(data_dir, run_id)
    if audited.get("overall") != "ok":
        return SelfTestCheck("audit", "error", "Manual audit failed for generated run.", {"audit": audited})
    return SelfTestCheck("audit", "ok", "Manual audit passed for generated run.")


def _check_obsidian_export(data_dir: Path, run_id: str, vault: Path) -> SelfTestCheck:
    try:
        exported = export_run_to_obsidian(data_dir=data_dir, run_id=run_id, vault=vault, folder="AI Research")
        note = Path(str(exported["path"]))
        text = note.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return SelfTestCheck("obsidian_export", "error", f"Obsidian export failed: {exc}")
    if "run_id:" not in text or "# SearXNG" not in text:
        return SelfTestCheck("obsidian_export", "error", "Obsidian note is missing run metadata or report body.", {"path": str(note)})
    return SelfTestCheck("obsidian_export", "ok", "Obsidian export wrote a note with frontmatter and report body.", {"path": str(note)})


def _check_mcp_tools() -> SelfTestCheck:
    from .mcp_server import handle_request

    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) or {}
    tools = {tool.get("name") for tool in listed.get("result", {}).get("tools", [])}
    expected = {"run_research", "audit", "get_run", "list_runs", "export_obsidian", "doctor", "selftest", "evals", "bootstrap", "readiness"}
    missing = sorted(expected - tools)
    if missing:
        return SelfTestCheck("mcp_tools", "error", f"MCP tools missing: {', '.join(missing)}", {"tools": sorted(tools)})
    return SelfTestCheck("mcp_tools", "ok", "MCP tool registry contains end-to-end tools.", {"tools": sorted(tools)})


def _check_mcp_run(data_dir: Path) -> SelfTestCheck:
    from .mcp_server import handle_request

    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_research",
                "arguments": {
                    "topic": "SearXNG",
                    "no_network": True,
                    "data_dir": str(data_dir),
                    "manual_evidence": [
                        {
                            "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                        }
                    ],
                },
            },
        }
    )
    try:
        payload = json.loads(called["result"]["content"][0]["text"]) if called else {}
    except Exception as exc:  # noqa: BLE001
        return SelfTestCheck("mcp_run", "error", f"MCP run response was not parseable: {exc}")
    if payload.get("audit", {}).get("overall") != "ok" or not payload.get("run_id"):
        return SelfTestCheck("mcp_run", "error", "MCP run_research did not return audit ok.", {"payload": payload})
    return SelfTestCheck("mcp_run", "ok", "MCP run_research returned a run with audit ok.", {"run_id": payload.get("run_id")})


def _overall_status(checks: list[SelfTestCheck]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"
