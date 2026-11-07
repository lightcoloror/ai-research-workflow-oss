from __future__ import annotations

import hashlib
import json
from dataclasses import is_dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DATA_DIR = Path(".research")


def stable_id(*parts: str, prefix: str = "") -> str:
    raw = "\n".join(part or "" for part in parts).encode("utf-8", errors="ignore")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def ensure_data_dir(data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(exist_ok=True)
    return data_dir


def _to_jsonable(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if is_dataclass(item):
        return item.__dict__
    if isinstance(item, dict):
        return item
    raise TypeError(f"Cannot serialize {type(item)!r}")


def append_jsonl(path: Path, items: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(_to_jsonable(item), ensure_ascii=False, sort_keys=True))
            fh.write("\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

