from __future__ import annotations

import json
import os
from hashlib import sha256
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


CONFIG_ENV = "RESEARCH_CONFIG"
DEFAULT_CONFIG_FILES = ("research.config.json", ".research/config.json")


@dataclass(slots=True)
class ResearchConfig:
    data_dir: str = ".research"
    searxng_url: str = ""
    searxng_urls: list[str] = field(default_factory=list)
    searxng_public_discovery: bool = True
    searxng_public_limit: int = 3
    searxng_public_instances_url: str = "https://searx.space/data/instances.json"
    search_providers: list[str] = field(default_factory=lambda: ["searxng"])
    routing_strategy: str = "legacy_fanout"
    search_profile: str = "default"
    max_results: int = 10
    fetch_urls: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = 9082
    obsidian_vault: str = ""
    obsidian_folder: str = "AI Research"
    reader_provider: str = "local"
    web_content_fetcher_path: str = ""
    personal_intelligence_desk_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def web_base_url(config: ResearchConfig) -> str:
    return f"http://{config.web_host}:{int(config.web_port)}"


def config_fingerprint(config: ResearchConfig) -> str:
    payload = json.dumps(config.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_config(path: str | Path | None = None) -> ResearchConfig:
    data = _read_config_data(path)
    config = ResearchConfig(**{key: value for key, value in data.items() if key in ResearchConfig.__dataclass_fields__})
    _apply_env(config)
    return config


def find_config_path(path: str | Path | None = None) -> Path | None:
    if path:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    configured = os.environ.get(CONFIG_ENV, "").strip()
    if configured:
        candidate = Path(configured)
        return candidate if candidate.exists() else None
    for name in DEFAULT_CONFIG_FILES:
        candidate = Path(name)
        if candidate.exists():
            return candidate
    return None


def write_default_config(path: str | Path = "research.config.json", *, overwrite: bool = False) -> Path:
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ResearchConfig().to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _read_config_data(path: str | Path | None) -> dict[str, Any]:
    config_path = find_config_path(path)
    if not config_path:
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON config: {config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Config must be a JSON object: {config_path}")
    return payload


def _apply_env(config: ResearchConfig) -> None:
    env_map = {
        "RESEARCH_DATA_DIR": ("data_dir", str),
        "RESEARCH_SEARXNG_URL": ("searxng_url", str),
        "RESEARCH_SEARXNG_URLS": ("searxng_urls", _env_list),
        "RESEARCH_SEARXNG_PUBLIC_DISCOVERY": ("searxng_public_discovery", _env_bool),
        "RESEARCH_SEARXNG_PUBLIC_LIMIT": ("searxng_public_limit", int),
        "RESEARCH_SEARXNG_PUBLIC_INSTANCES_URL": ("searxng_public_instances_url", str),
        "RESEARCH_SEARCH_PROVIDERS": ("search_providers", _env_list),
        "RESEARCH_ROUTING_STRATEGY": ("routing_strategy", str),
        "RESEARCH_SEARCH_PROFILE": ("search_profile", str),
        "RESEARCH_MAX_RESULTS": ("max_results", int),
        "RESEARCH_FETCH_URLS": ("fetch_urls", _env_bool),
        "RESEARCH_WEB_HOST": ("web_host", str),
        "RESEARCH_WEB_PORT": ("web_port", int),
        "RESEARCH_OBSIDIAN_VAULT": ("obsidian_vault", str),
        "RESEARCH_OBSIDIAN_FOLDER": ("obsidian_folder", str),
        "RESEARCH_READER_PROVIDER": ("reader_provider", str),
        "WEB_CONTENT_FETCHER_PATH": ("web_content_fetcher_path", str),
        "PERSONAL_INTELLIGENCE_DESK_PATH": ("personal_intelligence_desk_path", str),
    }
    for env_name, (field, caster) in env_map.items():
        value = os.environ.get(env_name)
        if value is None or value == "":
            continue
        setattr(config, field, caster(value))


def _env_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
