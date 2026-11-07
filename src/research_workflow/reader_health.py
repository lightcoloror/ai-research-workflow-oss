from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None  # type: ignore[assignment]

from .config import ResearchConfig


def reader_health(config: ResearchConfig) -> dict[str, Any]:
    provider = str(config.reader_provider or "local").strip().lower() or "local"
    if provider in {"", "local", "default"}:
        return {
            "provider": "local",
            "configured": True,
            "available": True,
            "status": "ok",
            "message": "Using built-in lightweight URL fetcher.",
        }
    if provider == "web_content_fetcher":
        return _web_content_fetcher_health(config)
    if provider in {"personal_intelligence_desk", "pid"}:
        return _personal_intelligence_desk_health(config, provider)
    if provider == "jina_reader":
        return {
            "provider": provider,
            "configured": True,
            "available": True,
            "status": "ok",
            "message": "Using Jina Reader HTTP adapter when fetching URLs.",
        }
    if provider == "firecrawl":
        return {
            "provider": provider,
            "configured": True,
            "available": True,
            "status": "ok",
            "message": "Using Firecrawl HTTP adapter when FIRECRAWL_API_KEY is configured.",
        }
    return {
        "provider": provider,
        "configured": False,
        "available": False,
        "status": "error",
        "message": f"Unknown reader provider: {provider}",
    }


def _web_content_fetcher_health(config: ResearchConfig) -> dict[str, Any]:
    configured_path = str(config.web_content_fetcher_path or "").strip()
    if not configured_path:
        return _installed_web_content_fetcher_health()

    root = Path(configured_path).expanduser()
    details: dict[str, Any] = {
        "provider": "web_content_fetcher",
        "configured": True,
        "path": str(root),
        "version": _package_version(root),
    }
    package_dir = root / "web_content_fetcher"
    init_file = package_dir / "__init__.py"
    if not root.exists() or not init_file.exists():
        return {
            **details,
            "available": False,
            "status": "error",
            "message": "web-content-fetcher package path is missing or incomplete.",
        }
    original_sys_path = list(sys.path)
    removed_modules = _remove_modules("web_content_fetcher")
    try:
        sys.path.insert(0, str(root))
        module = importlib.import_module("web_content_fetcher")
        module_path = Path(getattr(module, "__file__", "")).resolve()
        module_path.relative_to(root.resolve())
        result = _validate_web_content_fetcher_module(module, module_path)
        return {**details, **result}
    except Exception as exc:  # noqa: BLE001
        return {
            **details,
            "available": False,
            "status": "error",
            "message": f"web-content-fetcher contract check failed: {exc}",
        }
    finally:
        sys.path[:] = original_sys_path
        _remove_modules("web_content_fetcher")
        sys.modules.update(removed_modules)


def _installed_web_content_fetcher_health() -> dict[str, Any]:
    details: dict[str, Any] = {
        "provider": "web_content_fetcher",
        "configured": False,
        "path": "",
        "version": "",
    }
    try:
        module = importlib.import_module("web_content_fetcher")
    except ModuleNotFoundError:
        return {
            **details,
            "available": False,
            "status": "error",
            "message": "web-content-fetcher is not installed; install the reader extra or configure WEB_CONTENT_FETCHER_PATH.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            **details,
            "available": False,
            "status": "error",
            "message": f"web-content-fetcher import failed: {exc}",
        }

    module_path = Path(getattr(module, "__file__", "")).resolve()
    result = _validate_web_content_fetcher_module(module, module_path)
    return {
        **details,
        "configured": bool(result.get("available")),
        **result,
    }


def _validate_web_content_fetcher_module(module: Any, module_path: Path) -> dict[str, Any]:
    missing = [name for name in ("FetchRequest", "FetchResult", "fetch_content") if not hasattr(module, name)]
    if missing:
        return {
            "available": False,
            "status": "error",
            "module_path": str(module_path),
            "message": f"web-content-fetcher contract missing: {', '.join(missing)}.",
        }
    request = module.FetchRequest(url="https://example.com")
    if getattr(request, "url", "") != "https://example.com":
        return {
            "available": False,
            "status": "error",
            "module_path": str(module_path),
            "message": "FetchRequest contract did not preserve the URL field.",
        }
    return {
        "available": True,
        "status": "ok",
        "module_path": str(module_path),
        "message": "web-content-fetcher import and public contract are valid.",
    }

def _personal_intelligence_desk_health(config: ResearchConfig, provider: str) -> dict[str, Any]:
    root = Path(config.personal_intelligence_desk_path).expanduser()
    adapter = root / "scripts" / "monitor_sites.py"
    if adapter.exists():
        return {
            "provider": provider,
            "configured": True,
            "available": True,
            "path": str(root),
            "status": "ok",
            "message": "Legacy personal-intelligence-desk reader adapter is present.",
        }
    return {
        "provider": provider,
        "configured": bool(str(config.personal_intelligence_desk_path).strip()),
        "available": False,
        "path": str(root),
        "status": "error",
        "message": "Legacy personal-intelligence-desk reader adapter is missing.",
    }


def _remove_modules(prefix: str) -> dict[str, Any]:
    removed = {}
    for name in list(sys.modules):
        if name == prefix or name.startswith(f"{prefix}."):
            removed[name] = sys.modules.pop(name)
    return removed


def _package_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists() or tomllib is None:
        return ""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    project = data.get("project") if isinstance(data, dict) else {}
    return str(project.get("version") or "") if isinstance(project, dict) else ""
