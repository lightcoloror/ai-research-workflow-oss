from __future__ import annotations

import json
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
from typing import Any

from .config import ResearchConfig, config_fingerprint
from .contract_test import run_contract_test
from .metadata import PROJECT_ROOT, version_info
from .readiness import run_readiness


def run_release_check(config: ResearchConfig, work_dir: Path | None = None, *, run_tests: bool = False) -> dict[str, Any]:
    if work_dir is None:
        with TemporaryDirectory(prefix="research-release-check-") as temp_root:
            return _run_release_check(config, Path(temp_root), run_tests=run_tests)
    return _run_release_check(config, work_dir, run_tests=run_tests)


def _run_release_check(config: ResearchConfig, work_dir: Path, *, run_tests: bool) -> dict[str, Any]:
    root = PROJECT_ROOT
    work_dir.mkdir(parents=True, exist_ok=True)
    checks = [
        _record("version", _check_version(config)),
        _record("contract_test", run_contract_test(config, work_dir / "contract", live=False)),
        _record("readiness_skip_slow", run_readiness(config, work_dir / "readiness", skip_slow=True)),
    ]
    if run_tests:
        checks.append(_record("pytest", _run_pytest(root)))
    overall = "error" if any(check["status"] == "error" for check in checks) else "warn" if any(check["status"] == "warn" for check in checks) else "ok"
    return {
        "overall": overall,
        "service": "ai-research-workflow",
        "config_hash": config_fingerprint(config),
        "version": version_info(config),
        "checks": checks,
        "recommended_next_action": _recommended_next_action(checks),
    }


def release_check_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def release_check_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Release Check", "", f"- Overall: `{result.get('overall')}`", ""]
    for check in result.get("checks", []):
        lines.append(f"- `{check.get('status')}` **{check.get('name')}**: {check.get('message', '')}")
    lines.extend(["", f"Recommended next action: {result.get('recommended_next_action', '')}"])
    return "\n".join(lines) + "\n"


def _check_version(config: ResearchConfig) -> dict[str, Any]:
    info = version_info(config)
    missing = [key for key in ["package_version", "api_version", "contract_version", "config_hash"] if not info.get(key)]
    if missing:
        return {"overall": "error", "message": "Version metadata is incomplete.", "missing": missing, "version": info}
    return {"overall": "ok", "message": "Version metadata is available.", "version": info}


def _run_pytest(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["python", "-m", "pytest"],
        cwd=str(root),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "overall": "ok" if completed.returncode == 0 else "error",
        "message": "Full pytest completed." if completed.returncode == 0 else "Full pytest failed.",
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }


def _record(name: str, result: dict[str, Any]) -> dict[str, Any]:
    overall = result.get("overall", "error")
    if name == "readiness_skip_slow" and _only_skip_slow_warnings(result):
        overall = "ok"
    status = "ok" if overall == "ok" else "warn" if overall == "warn" else "error"
    return {"name": name, "status": status, "message": result.get("message", f"{name} returned {overall}."), "details": result}


def _recommended_next_action(checks: list[dict[str, Any]]) -> str:
    failing = [check["name"] for check in checks if check.get("status") == "error"]
    if failing:
        return f"Fix release checks: {', '.join(failing)}."
    warnings = [check["name"] for check in checks if check.get("status") == "warn"]
    if warnings:
        return f"Review release warnings: {', '.join(warnings)}."
    return "Release checks passed; commit and push with the documented proxy/OpenSSL git command."


def _only_skip_slow_warnings(result: dict[str, Any]) -> bool:
    if result.get("overall") != "warn":
        return False
    non_ok = [check.get("name") for check in result.get("checks", []) if check.get("status") != "ok"]
    return sorted(non_ok) == ["evals", "selftest"]
