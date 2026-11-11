from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path


FORBIDDEN_PARTS = {
    ".serena",
    "browser-profile",
    "browser-profiles",
    "cookies",
    "downloads",
    "obsidian-export",
    "provider-config",
    "provider-state",
}
FORBIDDEN_NAMES = {"research.config.json", ".research-web.pid"}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".har",
    ".log",
    ".mhtml",
    ".pid",
    ".sqlite",
    ".sqlite3",
    ".wacz",
    ".warc",
}
TEXT_SUFFIXES = {
    "",
    ".cmd",
    ".css",
    ".env",
    ".html",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}
PORTABLE_SURFACE = {
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "README.md",
    "integrations",
    "pyproject.toml",
    "research.config.example.json",
    "scripts",
    "src",
    "tests",
}
GENERIC_PERMISSION_URL_PATTERN = re.compile(
    r"(?i)https?://[^\s\]\)]+(?:[?&](?:access_token|auth|key|sig|signature|token)=)"
)
REQUIRED_POLICY_KEYS = {
    "windows_user_home",
    "syncthing_vault",
    "personal_documents",
    "hardcoded_workspace_path",
}


def _checked_mode(path: Path) -> int:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Linked path")
    return metadata.st_mode


def parse_policy_dict(
    data: object,
) -> tuple[dict[str, re.Pattern[str]], re.Pattern[str]]:
    if not isinstance(data, dict):
        raise ValueError("Policy JSON must be an object")
    if set(data.keys()) != REQUIRED_POLICY_KEYS:
        raise ValueError("Policy keys mismatch")
    compiled: dict[str, re.Pattern[str]] = {}
    for key in REQUIRED_POLICY_KEYS:
        val = data[key]
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Policy value for '{key}' must be a non-empty string")
        try:
            compiled[key] = re.compile(val)
        except (re.error, OverflowError, RecursionError):
            raise ValueError(f"Policy value for '{key}' is invalid regex") from None
    content_rules = {
        "windows_user_home": compiled["windows_user_home"],
        "syncthing_vault": compiled["syncthing_vault"],
        "personal_documents": compiled["personal_documents"],
        "permission_url": GENERIC_PERMISSION_URL_PATTERN,
    }
    workspace_rule = compiled["hardcoded_workspace_path"]
    return content_rules, workspace_rule


def load_policy(
    policy_path: Path, root: Path
) -> tuple[dict[str, re.Pattern[str]], re.Pattern[str]]:
    policy_resolved = policy_path.resolve()
    root_resolved = root.resolve()
    if not policy_resolved.is_file():
        raise ValueError("Policy path must be an existing file")
    try:
        policy_resolved.relative_to(root_resolved)
        inside_root = True
    except ValueError:
        inside_root = False
    if inside_root:
        raise ValueError("Policy file must be outside scan root")
    try:
        raw_text = policy_resolved.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Failed to read or parse policy JSON") from None
    return parse_policy_dict(data)


def audit(
    root: Path, local_policy: Path | dict[str, str] | None = None
) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    files_scanned = 0

    try:
        if not stat.S_ISDIR(_checked_mode(root)):
            raise ValueError("Root must be a directory")
        root_resolved = root.resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return {
            "schema": "ai-research-workflow-release-audit.v1",
            "root": str(root),
            "files_scanned": 0,
            "status": "blocked",
            "findings": [{"code": "forbidden_release_path", "path": "."}],
        }

    if local_policy is None:
        return {
            "schema": "ai-research-workflow-release-audit.v1",
            "root": str(root),
            "files_scanned": 0,
            "status": "blocked",
            "findings": [{"code": "invalid_policy", "path": "."}],
        }

    try:
        if isinstance(local_policy, dict):
            content_rules, workspace_rule = parse_policy_dict(local_policy)
        else:
            content_rules, workspace_rule = load_policy(Path(local_policy), root)
    except (OSError, ValueError, RuntimeError):
        return {
            "schema": "ai-research-workflow-release-audit.v1",
            "root": str(root),
            "files_scanned": 0,
            "status": "blocked",
            "findings": [{"code": "invalid_policy", "path": "."}],
        }

    walk_error = False

    def on_walk_error(err: OSError) -> None:
        nonlocal walk_error
        walk_error = True

    for dirpath, dirnames, filenames in os.walk(
        root_resolved, followlinks=False, onerror=on_walk_error
    ):
        allowed_dirs = []
        for dirname in sorted(dirnames):
            if dirname == ".git":
                continue
            directory = Path(dirpath) / dirname
            relative_dir = directory.relative_to(root_resolved).as_posix()
            try:
                lowered = dirname.lower()
                if (lowered in FORBIDDEN_PARTS or lowered.startswith(".research")
                        or not stat.S_ISDIR(_checked_mode(directory))):
                    raise ValueError("Excluded directory")
            except (OSError, ValueError):
                findings.append({"code": "forbidden_release_path", "path": relative_dir})
            else:
                allowed_dirs.append(dirname)
        dirnames[:] = allowed_dirs
        for filename in sorted(filenames):
            if filename == ".git":
                continue
            filepath = Path(dirpath) / filename
            try:
                relative = filepath.relative_to(root_resolved)
            except ValueError:
                findings.append({"code": "forbidden_release_path", "path": filename})
                continue
            relative_text = relative.as_posix()
            files_scanned += 1

            lowered_parts = {part.lower() for part in relative.parts}
            has_runtime_root = any(
                part.lower().startswith(".research") for part in relative.parts
            )
            if (
                lowered_parts & FORBIDDEN_PARTS
                or filepath.name.lower() in FORBIDDEN_NAMES
                or filepath.suffix.lower() in FORBIDDEN_SUFFIXES
                or has_runtime_root
            ):
                findings.append(
                    {"code": "forbidden_release_path", "path": relative_text}
                )
                continue

            try:
                if not stat.S_ISREG(_checked_mode(filepath)):
                    raise ValueError("Expected a regular file")
            except (OSError, ValueError):
                findings.append({"code": "forbidden_release_path", "path": relative_text})
                continue
            if filepath.suffix.lower() not in TEXT_SUFFIXES:
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                findings.append(
                    {"code": "unreadable_text_file", "path": relative_text}
                )
                continue

            for code, pattern in content_rules.items():
                if pattern.search(text):
                    findings.append({"code": code, "path": relative_text})
            top = relative.parts[0] if relative.parts else ""
            if top in PORTABLE_SURFACE and workspace_rule.search(text):
                findings.append(
                    {"code": "hardcoded_workspace_path", "path": relative_text}
                )

    if walk_error:
        findings.append({"code": "forbidden_release_path", "path": "."})

    return {
        "schema": "ai-research-workflow-release-audit.v1",
        "root": str(root),
        "files_scanned": files_scanned,
        "status": "pass" if not findings else "blocked",
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed privacy and path audit for an ARW release surface."
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--local-policy", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = audit(args.root, args.local_policy)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        try:
            if args.output.resolve() == args.local_policy.resolve():
                raise ValueError("Output cannot replace policy")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        except (OSError, ValueError, RuntimeError):
            print("Could not write the release audit report.", file=sys.stderr)
            return 2
    print(rendered)
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
