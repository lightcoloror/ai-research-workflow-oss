from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from .config import ResearchConfig, web_base_url
from .service_registry import read_service_record, write_service_record
from .store import ensure_data_dir


def web_url(config: ResearchConfig, host: str | None = None, port: int | None = None) -> str:
    if host or port:
        actual_host = host or config.web_host
        actual_port = int(port or config.web_port)
        return f"http://{actual_host}:{actual_port}"
    return web_base_url(config)


def web_command(config: ResearchConfig, data_dir: Path, host: str | None = None, port: int | None = None) -> list[str]:
    return [
        sys.executable,
        "-m",
        "research_workflow.web",
        "--host",
        host or config.web_host,
        "--port",
        str(int(port or config.web_port)),
        "--data-dir",
        str(data_dir),
    ]


def web_paths(data_dir: Path) -> dict[str, Path]:
    root = ensure_data_dir(data_dir)
    return {
        "pid": root / "web.pid",
        "stdout": root / "web.out.log",
        "stderr": root / "web.err.log",
        "service": root / "service.json",
    }


def web_status(data_dir: Path, config: ResearchConfig, host: str | None = None, port: int | None = None) -> dict[str, Any]:
    paths = web_paths(data_dir)
    pid = _read_pid(paths["pid"])
    url = web_url(config, host, port)
    process_alive = bool(pid and _process_exists(pid))
    service_record = read_service_record(data_dir)
    record_is_stopped = str(service_record.get("status") or "") == "stopped"
    service_url = str(service_record.get("base_url") or "").rstrip("/")
    service_record_matches_url = not service_url or service_url == url
    live_listener = _url_listens(url)
    health = _health_ok(url)
    running = bool(not record_is_stopped and process_alive and service_record_matches_url and live_listener and health)
    stale_reasons = _stale_reasons(
        has_record=bool(pid or (service_record and not record_is_stopped)),
        process_alive=process_alive,
        service_record_matches_url=service_record_matches_url,
        live_listener=live_listener,
        health=health,
    )
    return {
        "running": running,
        "status": "running" if running else "stale" if stale_reasons else "stopped",
        "pid": pid,
        "url": url,
        "pid_file": str(paths["pid"]),
        "stdout_log": str(paths["stdout"]),
        "stderr_log": str(paths["stderr"]),
        "process_alive": process_alive,
        "live_listener": live_listener,
        "health_ok": health,
        "service_record_matches_url": service_record_matches_url,
        "stale_reasons": stale_reasons,
        "service_record": service_record,
        "service_record_path": str(paths["service"]),
    }


def start_web(
    data_dir: Path,
    config: ResearchConfig,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    paths = web_paths(data_dir)
    existing = web_status(data_dir, config, host, port)
    command = web_command(config, data_dir, host, port)
    url = web_url(config, host, port)
    if dry_run:
        return {**existing, "dry_run": True, "command": command, "url": url}
    if existing["running"]:
        return {**existing, "started": False, "command": command}
    if existing.get("status") == "stale":
        _clear_pid_file(paths["pid"])

    stdout = paths["stdout"].open("ab")
    stderr = paths["stderr"].open("ab")
    popen_kwargs: dict[str, Any] = {"stdout": stdout, "stderr": stderr, "cwd": Path.cwd()}
    if os.name == "nt":
        create_breakaway_from_job = 0x01000000
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | create_breakaway_from_job
    try:
        process = subprocess.Popen(command, **popen_kwargs)  # noqa: S603
    except PermissionError:
        if os.name != "nt":
            raise
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        process = subprocess.Popen(command, **popen_kwargs)  # noqa: S603
    finally:
        stdout.close()
        stderr.close()
    paths["pid"].write_text(str(process.pid), encoding="utf-8")
    started_status = _wait_for_healthy(url, process.pid, timeout_seconds=10)
    if open_browser:
        webbrowser.open(url)
    return {
        "running": bool(started_status["running"]),
        "status": started_status["status"],
        "started": True,
        "pid": process.pid,
        "url": url,
        "command": command,
        "pid_file": str(paths["pid"]),
        "stdout_log": str(paths["stdout"]),
        "stderr_log": str(paths["stderr"]),
        "live_listener": started_status["live_listener"],
        "health_ok": started_status["health_ok"],
        "stale_reasons": started_status["stale_reasons"],
    }


def stop_web(data_dir: Path, config: ResearchConfig, host: str | None = None, port: int | None = None) -> dict[str, Any]:
    paths = web_paths(data_dir)
    status = web_status(data_dir, config, host, port)
    pid = status.get("pid")
    if not pid or not status.get("process_alive"):
        _clear_pid_file(paths["pid"])
        write_service_record(data_dir, base_url=web_url(config, host, port), pid=pid, status="stopped")
        return {**status, "stopped": False}
    os.kill(int(pid), signal.SIGTERM)
    _clear_pid_file(paths["pid"])
    write_service_record(data_dir, base_url=web_url(config, host, port), pid=int(pid), status="stopped")
    return {**web_status(data_dir, config, host, port), "stopped": True, "pid": pid}


def launcher_to_markdown(result: dict[str, Any]) -> str:
    lines = ["# Research Workflow Web Launcher", ""]
    lines.append(f"- Running: `{str(result.get('running')).lower()}`")
    if result.get("started") is not None:
        lines.append(f"- Started: `{str(result.get('started')).lower()}`")
    if result.get("stopped") is not None:
        lines.append(f"- Stopped: `{str(result.get('stopped')).lower()}`")
    if result.get("pid"):
        lines.append(f"- PID: `{result.get('pid')}`")
    lines.append(f"- URL: `{result.get('url')}`")
    if result.get("command"):
        lines.append(f"- Command: `{' '.join(str(part) for part in result.get('command', []))}`")
    if result.get("stdout_log"):
        lines.append(f"- Stdout log: `{result.get('stdout_log')}`")
    if result.get("stderr_log"):
        lines.append(f"- Stderr log: `{result.get('stderr_log')}`")
    return "\n".join(lines) + "\n"


def launcher_to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def _read_pid(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return int(value) if value.isdigit() else None


def _clear_pid_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(  # noqa: S603, S607
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        if str(pid) in result.stdout:
            return True
        if "Access denied" in result.stderr or "拒绝" in result.stderr:
            fallback = subprocess.run(  # noqa: S603, S607
                ["powershell", "-NoProfile", "-Command", f"Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Id"],
                capture_output=True,
                text=True,
                check=False,
            )
            return str(pid) in fallback.stdout
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _url_listens(url: str, *, timeout_seconds: float = 0.5) -> bool:
    host, port = _host_port(url)
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _health_ok(url: str, *, timeout_seconds: float = 1.0) -> bool:
    request = urllib.request.Request(f"{url.rstrip('/')}/v1/health", headers={"accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _host_port(url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname or "", int(parsed.port or (443 if parsed.scheme == "https" else 80))


def _stale_reasons(
    *,
    has_record: bool,
    process_alive: bool,
    service_record_matches_url: bool,
    live_listener: bool,
    health: bool,
) -> list[str]:
    if not has_record:
        return []
    reasons: list[str] = []
    if not process_alive:
        reasons.append("pid_not_alive")
    if not service_record_matches_url:
        reasons.append("service_record_base_url_mismatch")
    if not live_listener:
        reasons.append("no_live_listener")
    if not health:
        reasons.append("health_failed")
    return reasons


def _wait_for_healthy(url: str, pid: int, *, timeout_seconds: float) -> dict[str, Any]:
    import time

    deadline = time.monotonic() + timeout_seconds
    live_listener = False
    health = False
    while time.monotonic() < deadline:
        process_alive = _process_exists(pid)
        live_listener = _url_listens(url)
        health = _health_ok(url)
        if process_alive and live_listener and health:
            return {"running": True, "status": "running", "live_listener": True, "health_ok": True, "stale_reasons": []}
        if not process_alive:
            break
        time.sleep(0.2)
    reasons = _stale_reasons(
        has_record=True,
        process_alive=_process_exists(pid),
        service_record_matches_url=True,
        live_listener=live_listener,
        health=health,
    )
    return {"running": False, "status": "stale", "live_listener": live_listener, "health_ok": health, "stale_reasons": reasons}
