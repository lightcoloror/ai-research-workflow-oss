from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ResearchConfig, config_fingerprint, web_base_url, write_default_config
from .diagnostics import run_doctor
from .integrations import write_integrations
from .selftest import run_selftest


def run_bootstrap(
    config: ResearchConfig,
    data_dir: Path,
    config_path: Path = Path("research.config.json"),
    integrations_dir: Path = Path("integrations"),
    base_url: str = "",
    skip_selftest: bool = False,
) -> dict[str, Any]:
    config_created = False
    config_preserved = False
    try:
        written_config = write_default_config(config_path)
        config_created = True
    except FileExistsError:
        written_config = config_path
        config_preserved = True

    resolved_base_url = (base_url or web_base_url(config)).rstrip("/")
    integrations = write_integrations(integrations_dir, resolved_base_url, config_hash=config_fingerprint(config))
    doctor = run_doctor(config, data_dir, include_pipeline=not skip_selftest)
    selftest = None if skip_selftest else run_selftest(config, data_dir / "selftest")
    doctor_status = "ok" if _only_optional_search_warnings(doctor) else doctor.get("overall")
    overall = _overall_status([doctor_status, selftest.get("overall") if selftest else None])

    return {
        "overall": overall,
        "config": {
            "path": str(written_config),
            "created": config_created,
            "preserved": config_preserved,
        },
        "data_dir": str(data_dir),
        "web_url": resolved_base_url,
        "integrations": integrations,
        "doctor": doctor,
        "selftest": selftest,
        "next_steps": [
            f"Start the Web UI: python -m research_workflow.web --host {config.web_host} --port {config.web_port}",
            f"Open {resolved_base_url}",
            "Use the Selftest button before treating the local stack as ready.",
            "Import integrations/openwebui_research_workflow_tool.py into Open WebUI or use integrations/research-workflow-client.mjs from another frontend.",
        ],
    }


def bootstrap_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Bootstrap", "", f"Overall: `{result.get('overall', 'unknown')}`", ""]
    config = result.get("config", {})
    config_state = "created" if config.get("created") else "preserved"
    lines.append(f"- Config: `{config.get('path')}` ({config_state})")
    lines.append(f"- Data dir: `{result.get('data_dir')}`")
    lines.append(f"- Web URL: `{result.get('web_url')}`")
    lines.append("")
    lines.append("## Integrations")
    for file in result.get("integrations", {}).get("files", []):
        lines.append(f"- `{file.get('path')}` - {file.get('description')}")
    lines.append("")
    lines.append("## Checks")
    doctor = result.get("doctor") or {}
    lines.append(f"- Doctor: `{doctor.get('overall', 'skipped')}`")
    selftest = result.get("selftest")
    lines.append(f"- Selftest: `{selftest.get('overall') if selftest else 'skipped'}`")
    lines.append("")
    lines.append("## Next Steps")
    for step in result.get("next_steps", []):
        lines.append(f"- {step}")
    return "\n".join(lines) + "\n"


def bootstrap_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def _overall_status(statuses: list[str | None]) -> str:
    effective = [status for status in statuses if status]
    if any(status == "error" for status in effective):
        return "error"
    if any(status == "warn" for status in effective):
        return "warn"
    return "ok"


def _only_optional_search_warnings(result: dict[str, Any]) -> bool:
    if result.get("overall") != "warn":
        return False
    checks = result.get("checks", [])
    warnings = [check for check in checks if check.get("status") == "warn"]
    return bool(warnings) and all(check.get("name") in {"searxng", "search_providers"} for check in warnings)
