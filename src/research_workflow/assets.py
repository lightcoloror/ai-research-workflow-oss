from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parents[1]


def asset_dir(name: str) -> Path:
    source = SOURCE_ROOT / name
    if source.is_dir():
        return source
    return PACKAGE_ROOT / "assets" / name


def asset_file(relative: str | Path) -> Path:
    relative_path = Path(relative)
    source = SOURCE_ROOT / relative_path
    if source.is_file():
        return source
    return PACKAGE_ROOT / "assets" / relative_path
