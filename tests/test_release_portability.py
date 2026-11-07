from __future__ import annotations

import importlib
import sys
from pathlib import Path

from research_workflow.config import ResearchConfig
from research_workflow.reader_health import reader_health


ROOT = Path(__file__).resolve().parents[1]
WCF_COMMIT = "c9432cd98bafa69523722e3ab8e081f373fcb7fb"
QRP_COMMIT = "29f751e610a7a29fcb65b84d0455ccc8eb99b328"
GATEWAY_COMMIT = "6e69689d02dc97bc2cd3e476518ac7571e310461"


def test_release_metadata_pins_public_dependencies() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'license = "Apache-2.0"' in pyproject
    assert f"web-content-fetcher-oss.git@{WCF_COMMIT}" in pyproject
    assert pyproject.count(f"model-provider-gateway-oss.git@{GATEWAY_COMMIT}\"") == 2
    assert f"question-research-poc-oss.git@{QRP_COMMIT}" in pyproject
    assert (ROOT / "LICENSE").exists()
    assert (ROOT / "SECURITY.md").exists()
    assert (ROOT / "THIRD_PARTY_NOTICES.md").exists()


def test_public_defaults_do_not_embed_personal_paths() -> None:
    config = ResearchConfig()
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert config.reader_provider == "local"
    assert "RESEARCH_READER_PROVIDER=local" in env_example
    assert config.web_content_fetcher_path == ""
    assert config.personal_intelligence_desk_path == ""

    inspected = [
        ROOT / "README.md",
        ROOT / "research.config.example.json",
        ROOT / "src" / "research_workflow" / "config.py",
        ROOT / "src" / "research_workflow" / "evidence.py",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in inspected).lower()
    assert ":/users/" not in text
    assert ":\\users\\" not in text
    assert "d:\\used by syncthing" not in text
    assert ":\\used-by-" + "codex" not in text
    assert "d:\\documents\\playground" not in text


def test_core_reader_is_ready_and_explicit_missing_wcf_fails_closed(tmp_path) -> None:
    core = reader_health(ResearchConfig())
    missing = reader_health(
        ResearchConfig(
            reader_provider="web_content_fetcher",
            web_content_fetcher_path=str(tmp_path / "missing-wcf"),
        )
    )

    assert core["provider"] == "local"
    assert core["available"] is True
    assert core["status"] == "ok"
    assert missing["provider"] == "web_content_fetcher"
    assert missing["available"] is False
    assert missing["status"] == "error"


def test_installed_reader_package_satisfies_health_contract(tmp_path, monkeypatch) -> None:
    package_dir = tmp_path / "web_content_fetcher"
    package_dir.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "web_content_fetcher_contract.py"
    (package_dir / "__init__.py").write_text(
        fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name in list(sys.modules):
        if name == "web_content_fetcher" or name.startswith("web_content_fetcher."):
            sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    health = reader_health(
        ResearchConfig(reader_provider="web_content_fetcher", web_content_fetcher_path="")
    )

    assert health["configured"] is True
    assert health["available"] is True
    assert health["status"] == "ok"
    assert health["module_path"].endswith("__init__.py")


def test_release_ignore_rules_cover_private_runtime_state() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (
        ".serena/",
        ".research-*/",
        "research.config.json",
        "*.pid",
        "*.log",
        "*.sqlite3",
        "browser-profile*/",
        "provider-state/",
        "cookies/",
        "obsidian-export*/",
    ):
        assert entry in gitignore
