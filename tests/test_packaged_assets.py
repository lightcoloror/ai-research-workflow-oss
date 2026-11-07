from __future__ import annotations

import hashlib
from pathlib import Path

from research_workflow.assets import asset_dir, asset_file


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_packaged_web_assets_match_canonical_source() -> None:
    packaged = ROOT / "src" / "research_workflow" / "assets" / "web"
    for name in ("index.html", "app.js", "styles.css"):
        assert _sha256(packaged / name) == _sha256(ROOT / "web" / name)
        assert (asset_dir("web") / name).exists()


def test_packaged_prompt_matches_canonical_source() -> None:
    canonical = ROOT / "docs" / "claim_verification_prompts.md"
    packaged = (
        ROOT
        / "src"
        / "research_workflow"
        / "assets"
        / "docs"
        / "claim_verification_prompts.md"
    )
    assert _sha256(packaged) == _sha256(canonical)
    assert asset_file("docs/claim_verification_prompts.md").exists()


def test_detached_install_does_not_probe_parent_git(tmp_path, monkeypatch) -> None:
    from research_workflow import metadata

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("An installed package must not inspect a parent checkout")

    monkeypatch.setattr(metadata.subprocess, "run", unexpected_probe)
    assert metadata._git_output(tmp_path, ["rev-parse", "HEAD"]) == ""
