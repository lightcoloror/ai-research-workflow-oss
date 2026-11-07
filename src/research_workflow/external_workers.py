from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .external_adapters import ExternalAdapterRecords, normalize_external_payload


SUPPORTED_WORKERS = {"loki", "gpt-researcher", "storm"}


@dataclass(slots=True)
class ExternalWorkerSpec:
    name: str
    command: list[str]
    cwd: str = ""
    timeout_seconds: int = 120
    output_path: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExternalWorkerRun:
    worker: str
    status: str
    records: ExternalAdapterRecords = field(default_factory=ExternalAdapterRecords)
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker": self.worker,
            "status": self.status,
            "sources": [source.to_dict() for source in self.records.sources],
            "evidence": [item.to_dict() for item in self.records.evidence],
            "claims": [claim.to_dict() for claim in self.records.claims],
            "text_to_verify": self.records.text_to_verify,
            "warnings": [*self.records.warnings, *([self.warning] if self.warning else [])],
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def worker_spec_from_dict(payload: dict[str, Any]) -> ExternalWorkerSpec:
    command = payload.get("command") or []
    if isinstance(command, str):
        command = [command]
    return ExternalWorkerSpec(
        name=str(payload.get("name") or payload.get("worker") or "").strip(),
        command=[str(item) for item in command],
        cwd=str(payload.get("cwd") or ""),
        timeout_seconds=int(payload.get("timeout_seconds") or payload.get("timeout") or 120),
        output_path=str(payload.get("output_path") or ""),
        env={str(key): str(value) for key, value in (payload.get("env") or {}).items()},
    )


def run_worker(
    spec: ExternalWorkerSpec,
    *,
    topic: str,
    text: str = "",
) -> ExternalWorkerRun:
    worker_name = _worker_name(spec.name)
    if not spec.command:
        return ExternalWorkerRun(worker=worker_name, status="skipped", warning=f"{worker_name} worker command is not configured")
    command = [_format_arg(arg, topic=topic, text=text) for arg in spec.command]
    try:
        completed = subprocess.run(
            command,
            cwd=spec.cwd or None,
            env=None if not spec.env else {**os.environ, **spec.env},
            capture_output=True,
            text=True,
            timeout=spec.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ExternalWorkerRun(worker=worker_name, status="timeout", stdout=exc.stdout or "", stderr=exc.stderr or "", warning=f"{worker_name} worker timed out")
    except Exception as exc:  # noqa: BLE001
        return ExternalWorkerRun(worker=worker_name, status="error", warning=f"{worker_name} worker failed to start: {exc}")
    payload_text = _read_payload_text(spec.output_path, completed.stdout)
    if completed.returncode != 0:
        return ExternalWorkerRun(
            worker=worker_name,
            status="error",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            warning=f"{worker_name} worker exited with code {completed.returncode}",
        )
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        return ExternalWorkerRun(
            worker=worker_name,
            status="error",
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            warning=f"{worker_name} worker did not emit valid JSON: {exc}",
        )
    if not isinstance(payload, dict):
        return ExternalWorkerRun(worker=worker_name, status="error", stdout=completed.stdout, stderr=completed.stderr, returncode=completed.returncode, warning=f"{worker_name} worker JSON must be an object")
    payload.setdefault("adapter", f"{worker_name}-worker")
    records = normalize_external_payload(payload, topic=topic)
    return ExternalWorkerRun(worker=worker_name, status="ok", records=records, stdout=completed.stdout, stderr=completed.stderr, returncode=completed.returncode)


def run_loki_worker(spec: ExternalWorkerSpec, *, topic: str, text: str = "") -> ExternalWorkerRun:
    spec.name = "loki"
    return run_worker(spec, topic=topic, text=text)


def run_gpt_researcher_worker(spec: ExternalWorkerSpec, *, topic: str, text: str = "") -> ExternalWorkerRun:
    spec.name = "gpt-researcher"
    return run_worker(spec, topic=topic, text=text)


def run_storm_worker(spec: ExternalWorkerSpec, *, topic: str, text: str = "") -> ExternalWorkerRun:
    spec.name = "storm"
    return run_worker(spec, topic=topic, text=text)


def _worker_name(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in SUPPORTED_WORKERS else cleaned or "external"


def _format_arg(value: str, *, topic: str, text: str) -> str:
    return value.replace("{topic}", topic).replace("{text}", text)


def _read_payload_text(output_path: str, stdout: str) -> str:
    if output_path:
        path = Path(output_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return stdout
