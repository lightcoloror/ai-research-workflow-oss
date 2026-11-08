from __future__ import annotations

import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .config import ResearchConfig, config_fingerprint, web_base_url
from .contract import API_VERSION, CONTRACT_VERSION


PACKAGE_NAME = "ai-research-workflow"
PACKAGE_VERSION = "0.1.1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def package_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return PACKAGE_VERSION


def version_info(config: ResearchConfig, *, root: Path | None = None) -> dict[str, Any]:
    root = root or PROJECT_ROOT
    return {
        "service": "ai-research-workflow",
        "package_name": PACKAGE_NAME,
        "package_version": package_version(),
        "api_version": API_VERSION,
        "contract_version": CONTRACT_VERSION,
        "config_hash": config_fingerprint(config),
        "base_url": web_base_url(config),
        "git_commit": _git_output(root, ["rev-parse", "--short", "HEAD"]),
        "git_remote": _git_output(root, ["remote", "get-url", "origin"]),
    }


def version_info_to_markdown(info: dict[str, Any]) -> str:
    lines = ["# Research Workflow Version", ""]
    for key in ["package_version", "api_version", "contract_version", "config_hash", "base_url", "git_commit", "git_remote"]:
        lines.append(f"- {key}: `{info.get(key, '')}`")
    return "\n".join(lines) + "\n"


def _git_output(root: Path, args: list[str]) -> str:
    # An installed source edition has no checkout metadata to inspect.
    if not (root / ".git").exists():
        return ""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()
