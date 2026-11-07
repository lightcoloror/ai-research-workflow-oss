from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ResearchConfig, config_fingerprint
from .contract import API_VERSION, CONTRACT_VERSION
from .evidence import extract_evidence_from_sources, source_from_url
from .factcheck import extract_atomic_claims, verify_claims
from .models import Evidence
from .search_pipeline import run_search_pipeline
from .sqlite_store import sync_jsonl_to_sqlite_quietly
from .store import append_jsonl, ensure_data_dir, read_jsonl, write_text


@dataclass(slots=True)
class WatchFolderResult:
    root: Path
    processed: list[dict]
    failed: list[dict]

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "processed_count": len(self.processed),
            "failed_count": len(self.failed),
            "processed": self.processed,
            "failed": self.failed,
        }


def process_watch_folder(
    *,
    root: Path,
    data_dir: Path,
    config: ResearchConfig,
    limit: int = 20,
) -> WatchFolderResult:
    root = Path(root)
    data_dir = ensure_data_dir(data_dir)
    inbox = root / "inbox"
    processing = root / "processing"
    outbox = root / "outbox"
    failed = root / "failed"
    for directory in (inbox, processing, outbox, failed):
        directory.mkdir(parents=True, exist_ok=True)

    processed_rows: list[dict] = []
    failed_rows: list[dict] = []
    jobs = sorted(inbox.glob("*.json"))
    if limit > 0:
        jobs = jobs[:limit]

    for job_path in jobs:
        processing_path = processing / job_path.name
        try:
            job_path.replace(processing_path)
            job = json.loads(processing_path.read_text(encoding="utf-8"))
            row = _process_job(job, data_dir=data_dir, config=config, outbox=outbox)
            shutil.move(str(processing_path), str(outbox / f"{row['job_id']}.request.json"))
            processed_rows.append(row)
        except Exception as exc:  # noqa: BLE001
            job_id = _job_id_from_path(processing_path)
            job_type = _job_type_from_path_or_payload(processing_path)
            request_path = failed / f"{job_id}.request.json"
            status = _status_envelope(
                {
                    "job_id": job_id,
                    "type": job_type,
                    "status": "failed",
                    "error": str(exc),
                    "request_path": str(request_path),
                    "created_outputs": [],
                },
                config=config,
            )
            if processing_path.exists():
                shutil.move(str(processing_path), str(request_path))
            _write_status(failed, status, config=config)
            failed_rows.append(status)

    return WatchFolderResult(root=root, processed=processed_rows, failed=failed_rows)


def watch_folder_to_markdown(result: WatchFolderResult) -> str:
    payload = result.to_dict()
    lines = [
        "# Watch Folder Processing",
        "",
        f"- Root: `{payload['root']}`",
        f"- Processed: `{payload['processed_count']}`",
        f"- Failed: `{payload['failed_count']}`",
        "",
        "## Processed",
    ]
    if result.processed:
        for row in result.processed:
            lines.append(f"- `{row['job_id']}` {row['type']} -> `{row['status_path']}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Failed"])
    if result.failed:
        for row in result.failed:
            lines.append(f"- `{row['job_id']}` {row.get('error', '')}")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _process_job(job: dict[str, Any], *, data_dir: Path, config: ResearchConfig, outbox: Path) -> dict:
    job_id = str(job.get("id") or _safe_job_id(str(job.get("topic") or job.get("query") or job.get("type") or "job")))
    job_type = str(job.get("type") or "search")
    if job_type in {"search", "deepen"}:
        result = run_search_pipeline(
            topic=str(job.get("topic") or job.get("query") or ""),
            data_dir=data_dir,
            mode=str(job.get("mode") or "auto"),
            max_results=int(job.get("max_results") or config.max_results),
            no_network=bool(job.get("no_network", False)),
            urls=_as_string_list(job.get("urls")),
            fetch_urls=bool(job.get("fetch", job.get("fetch_urls", config.fetch_urls))),
            searxng_url=config.searxng_url,
            search_providers=_as_string_list(job.get("providers") or job.get("search_providers")) or config.search_providers,
            deep=job_type == "deepen" or bool(job.get("deep", False)),
            auto_verify=bool(job.get("auto_verify", False)),
        )
        report_md = outbox / f"{job_id}.report.md"
        report_json = outbox / f"{job_id}.report.json"
        write_text(report_md, result.report_markdown)
        write_text(report_json, result.report_json)
        status = {
            "job_id": job_id,
            "type": job_type,
            "status": "ok",
            "run_id": result.run_id,
            "report_md": str(report_md),
            "report_json": str(report_json),
            "created_outputs": [str(report_md), str(report_json)],
            "source_count": len(result.sources),
            "evidence_count": len(result.evidence),
            "claim_count": len(result.claims),
            "source_provenance": _source_provenance_summary(result.sources),
            "interface_contract": result.interface_contract(),
        }
        return _write_status(outbox, status, config=config)
    if job_type == "verify":
        text = str(job.get("text") or "")
        if not text:
            raise ValueError("verify job requires text")
        evidence = [Evidence(**item) for item in read_jsonl(data_dir / "evidence.jsonl")]
        claims = verify_claims(extract_atomic_claims(text), evidence)
        report_json = outbox / f"{job_id}.report.json"
        write_text(report_json, json.dumps({"claims": [claim.to_dict() for claim in claims]}, ensure_ascii=False, indent=2, sort_keys=True))
        status = {"job_id": job_id, "type": job_type, "status": "ok", "report_json": str(report_json), "created_outputs": [str(report_json)], "claim_count": len(claims)}
        return _write_status(outbox, status, config=config)
    if job_type in {"fetch", "ingest"}:
        urls = _as_string_list(job.get("urls") or job.get("url"))
        if not urls:
            raise ValueError(f"{job_type} job requires url or urls")
        sources = [
            source_from_url(
                url,
                search_queries=["watch-folder ingest"],
                search_buckets=["manual URLs"],
                search_purposes=["agent supplied source"],
                search_stages=["watch_folder_ingest"],
            )
            for url in urls
        ]
        should_fetch = bool(job.get("fetch", job.get("fetch_urls", config.fetch_urls)))
        evidence = extract_evidence_from_sources(sources, fetch=True) if should_fetch else []
        if sources:
            append_jsonl(data_dir / "sources.jsonl", sources)
        if evidence:
            append_jsonl(data_dir / "evidence.jsonl", evidence)
        sync_jsonl_to_sqlite_quietly(data_dir)
        report_json = outbox / f"{job_id}.report.json"
        payload = {
            "sources": [source.to_dict() for source in sources],
            "evidence": [item.to_dict() for item in evidence],
        }
        write_text(report_json, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        status = {
            "job_id": job_id,
            "type": job_type,
            "status": "ok",
            "report_json": str(report_json),
            "created_outputs": [str(report_json)],
            "source_count": len(sources),
            "evidence_count": len(evidence),
            "claim_count": 0,
            "source_provenance": _source_provenance_summary(sources),
        }
        return _write_status(outbox, status, config=config)
    raise ValueError(f"unsupported job type: {job_type}")


def _write_status(outbox: Path, status: dict, *, config: ResearchConfig) -> dict:
    status = _status_envelope(status, config=config)
    status_path = outbox / f"{status['job_id']}.status.json"
    status["status_path"] = str(status_path)
    if str(status.get("status") or "") == "ok" and str(status_path) not in status["created_outputs"]:
        status["created_outputs"].append(str(status_path))
    write_text(status_path, json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
    return status


def _status_envelope(status: dict, *, config: ResearchConfig) -> dict:
    status.setdefault("service", "ai-research-workflow")
    status.setdefault("api_version", API_VERSION)
    status.setdefault("contract_version", CONTRACT_VERSION)
    status.setdefault("config_hash", config_fingerprint(config))
    status.setdefault("created_outputs", [])
    return status


def _source_provenance_summary(sources: list) -> list[dict]:
    return [
        {
            "id": source.id,
            "url": source.url,
            "search_buckets": source.search_buckets,
            "search_stages": source.search_stages,
        }
        for source in sources[:10]
    ]


def _job_id_from_path(path: Path) -> str:
    return _safe_job_id(path.stem or "job")


def _job_type_from_path_or_payload(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "unknown"
    return str(payload.get("type") or "search")


def _safe_job_id(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "job"


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(item).strip() for item in value if str(item).strip()]
