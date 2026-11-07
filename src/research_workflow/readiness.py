from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .config import ResearchConfig, config_fingerprint, web_base_url
from .contract import CONTRACT_VERSION
from .diagnostics import run_doctor
from .evals import run_evals
from .integrations import write_integrations
from .launcher import start_web
from .metadata import version_info
from .reader_health import reader_health
from .selftest import run_selftest


@dataclass(slots=True)
class ReadinessCheck:
    name: str
    status: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.details is None:
            data.pop("details")
        return data


def run_readiness(config: ResearchConfig, work_dir: Path | None = None, *, skip_slow: bool = False) -> dict[str, Any]:
    with TemporaryDirectory(prefix="research-readiness-") as temp_root:
        root = work_dir or Path(temp_root)
        root.mkdir(parents=True, exist_ok=True)
        checks: list[ReadinessCheck] = []

        checks.append(_doctor_check(config, root / "doctor", include_pipeline=not skip_slow))
        checks.append(_reader_import_check(config))
        if skip_slow:
            checks.append(ReadinessCheck("selftest", "warn", "Skipped by --skip-slow."))
            checks.append(ReadinessCheck("evals", "warn", "Skipped by --skip-slow."))
        else:
            checks.append(_selftest_check(config, root / "selftest"))
            checks.append(_evals_check(config, root / "evals"))
        checks.append(_integrations_check(root / "integrations", web_base_url(config), config_hash=config_fingerprint(config)))
        checks.append(_agent_contract_check(config, root / "contract"))
        checks.append(_web_launcher_check(config, root / "web"))
        overall = _overall_status(checks)
        ready = all(check.status == "ok" for check in checks)
        metadata = version_info(config)

        return {
            "overall": overall,
            "version": metadata,
            "package_version": metadata.get("package_version", ""),
            "git_commit": metadata.get("git_commit", ""),
            "git_remote": metadata.get("git_remote", ""),
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(config),
            "work_dir": str(root),
            "checks": [check.to_dict() for check in checks],
            "ready": ready,
            "recommended_next_action": _recommended_next_action(overall, ready, checks),
        }


def readiness_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Readiness", "", f"Overall: `{result.get('overall', 'unknown')}`", f"Ready: `{str(result.get('ready')).lower()}`", ""]
    for check in result.get("checks", []):
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**: {check.get('message')}")
    return "\n".join(lines) + "\n"


def readiness_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def _doctor_check(config: ResearchConfig, data_dir: Path, *, include_pipeline: bool) -> ReadinessCheck:
    try:
        result = run_doctor(config, data_dir, include_pipeline=include_pipeline)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("doctor", "error", f"Doctor failed: {exc}")
    status = result.get("overall", "error")
    if status == "warn" and _only_optional_search_warnings(result):
        provider_check = _check_by_name(result, "search_providers")
        effective = provider_check.get("details", {}).get("effective", []) if provider_check else []
        missing = provider_check.get("details", {}).get("disabled_unconfigured", []) if provider_check else []
        suffix = ""
        if missing:
            suffix = f" Effective providers: {', '.join(effective) or 'none'}; optional missing keys: {', '.join(missing)}."
        return ReadinessCheck("doctor", "ok", f"Doctor returned ok with optional provider fallbacks.{suffix}", {"result": result})
    if status == "ok":
        return ReadinessCheck("doctor", "ok", "Doctor returned ok. optional provider fallbacks remain available.", {"result": result})
    return ReadinessCheck("doctor", status, f"Doctor returned {status}.", {"result": result})


def _reader_import_check(config: ResearchConfig) -> ReadinessCheck:
    try:
        health = reader_health(config)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("reader_import", "error", f"Reader health check failed: {exc}")
    provider = health.get("provider", config.reader_provider)
    if health.get("status") == "ok" and health.get("available", False):
        return ReadinessCheck("reader_import", "ok", f"Reader provider {provider} is importable.", {"reader": health})
    if str(provider) in {"web_content_fetcher", "wcf", "web-content-fetcher"}:
        return ReadinessCheck("reader_import", "error", "web-content-fetcher reader is selected but not importable.", {"reader": health})
    return ReadinessCheck("reader_import", "warn", f"Reader provider {provider} is not fully available.", {"reader": health})


def _selftest_check(config: ResearchConfig, data_dir: Path) -> ReadinessCheck:
    try:
        result = run_selftest(config, data_dir)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("selftest", "error", f"Selftest failed: {exc}")
    status = result.get("overall", "error")
    return ReadinessCheck("selftest", status, f"Selftest returned {status}.", {"run_id": result.get("run_id"), "result": result})


def _evals_check(config: ResearchConfig, data_dir: Path) -> ReadinessCheck:
    try:
        result = run_evals(config, data_dir)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("evals", "error", f"Evals failed: {exc}")
    status = result.get("overall", "error")
    coverage = {case.get("name"): case.get("claim_coverage") for case in result.get("cases", [])}
    return ReadinessCheck("evals", status, f"Evals returned {status}.", {"case_count": result.get("case_count"), "coverage": coverage, "result": result})


def _integrations_check(output_dir: Path, base_url: str, *, config_hash: str = "") -> ReadinessCheck:
    try:
        result = write_integrations(output_dir, base_url, config_hash=config_hash)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("integrations", "error", f"Integration generation failed: {exc}")
    paths = [Path(file["path"]) for file in result.get("files", [])]
    missing = [str(path) for path in paths if not path.exists()]
    if missing or len(paths) < 3:
        return ReadinessCheck("integrations", "error", "Generated integration files are incomplete.", {"missing": missing, "files": result.get("files", [])})
    return ReadinessCheck("integrations", "ok", "Generated Open WebUI tool, JS client, and endpoint manifest.", {"files": result.get("files", [])})


def _agent_contract_check(config: ResearchConfig, data_dir: Path) -> ReadinessCheck:
    try:
        from .contract_test import run_contract_test

        result = run_contract_test(config, data_dir, live=False)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("agent_contract", "error", f"Contract test failed: {exc}")
    status = result.get("overall", "error")
    return ReadinessCheck(
        "agent_contract",
        status,
        f"Contract test returned {status}.",
        {
            "contract_version": result.get("stable_contract", {}).get("contract_version", ""),
            "config_hash": result.get("stable_contract", {}).get("config_hash", ""),
            "checks": [{"name": check.get("name"), "status": check.get("status")} for check in result.get("checks", [])],
        },
    )


def _web_launcher_check(config: ResearchConfig, data_dir: Path) -> ReadinessCheck:
    try:
        result = start_web(data_dir, config, dry_run=True)
    except Exception as exc:  # noqa: BLE001
        return ReadinessCheck("web_launcher", "error", f"Web launcher dry-run failed: {exc}")
    command = result.get("command", [])
    if "research_workflow.web" not in command:
        return ReadinessCheck("web_launcher", "error", "Launcher command does not target research_workflow.web.", {"command": command})
    return ReadinessCheck("web_launcher", "ok", "Web launcher command is constructible.", {"command": command, "url": result.get("url")})


def _overall_status(checks: list[ReadinessCheck]) -> str:
    if any(check.status == "error" for check in checks):
        return "error"
    if any(check.status == "warn" for check in checks):
        return "warn"
    return "ok"


def _recommended_next_action(overall: str, ready: bool, checks: list[ReadinessCheck]) -> str:
    if ready:
        return "Readiness checks passed; run agent-smoke --format json when a live HTTP service is running."
    failing = [check for check in checks if check.status == "error"]
    if failing:
        names = ", ".join(check.name for check in failing)
        return f"Fix failing readiness checks: {names}."
    if overall == "warn":
        warnings = ", ".join(check.name for check in checks if check.status == "warn")
        return f"Warnings remain: {warnings}. Run readiness without --skip-slow for the full gate when needed."
    return "Inspect readiness checks for details."


def _only_optional_search_warnings(result: dict[str, Any]) -> bool:
    checks = result.get("checks", [])
    warnings = [check for check in checks if check.get("status") == "warn"]
    return bool(warnings) and all(check.get("name") in {"searxng", "search_providers"} for check in warnings)


def _check_by_name(result: dict[str, Any], name: str) -> dict[str, Any] | None:
    for check in result.get("checks", []):
        if check.get("name") == name:
            return check
    return None
