from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .assets import asset_dir
from .audit import audit_run
from .academic_reference_check import check_academic_references
from .config import ResearchConfig, config_fingerprint, load_config, web_base_url
from .contract import API_VERSION, CONTRACT_VERSION
from .diagnostics import run_doctor
from .evidence import extract_evidence_from_sources, source_from_url
from .evals import run_evals
from .factcheck import extract_atomic_claims, verify_claims
from .insights import claim_citations_from_claims
from .integrations import render_agent_discovery_json
from .jobs import add_job_event, get_job, job_events, list_jobs, mark_stale_jobs, run_tracked_job, start_background_job
from .metadata import package_version, version_info
from .models import Evidence, Report, Source
from .obsidian_export import export_run_to_obsidian
from .pipeline import dedupe_rows, get_run, list_runs, manual_evidence_records, run_research_pipeline
from .planner import plan_query
from .quality import deepen_claim_suggestions_for_run, run_quality
from .readiness import run_readiness
from .reader_health import reader_health
from .report import draft_report, report_to_json, report_to_markdown
from .revisions import suggest_revisions
from .search import SearchError, available_providers, multi_source_search, resolve_searxng_url
from .search_pipeline import run_search_pipeline
from .routing import PROFILES, ROUTING_STRATEGIES, build_routing_plan, provider_catalog
from .selftest import run_selftest
from .service_registry import write_service_record
from .sqlite_store import sqlite_summary, sync_jsonl_to_sqlite, sync_jsonl_to_sqlite_quietly
from .store import DEFAULT_DATA_DIR, append_jsonl, ensure_data_dir, read_jsonl


STATIC_ROOT = asset_dir("web")


class ResearchWebApp:
    def __init__(self, data_dir: Path, config: ResearchConfig | None = None) -> None:
        self.config = config or load_config()
        self.data_dir = ensure_data_dir(data_dir)
        mark_stale_jobs(self.data_dir)

    def query(self, payload: dict) -> dict:
        return self.search(payload)

    def health(self) -> dict:
        metadata = version_info(self.config)
        return {
            "status": "ok",
            "service": "ai-research-workflow",
            "package_version": metadata.get("package_version", ""),
            "api_version": API_VERSION,
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(self.config),
            "git_commit": metadata.get("git_commit", ""),
            "git_remote": metadata.get("git_remote", ""),
            "base_url": web_base_url(self.config),
            "data_dir": str(self.data_dir.resolve()),
            "providers": self.config.search_providers,
            "reader_provider": self.config.reader_provider or "local",
            "run_count": len(dedupe_rows(read_jsonl(self.data_dir / "runs.jsonl"))),
        }

    def capabilities(self) -> dict:
        return {
            "service": "ai-research-workflow",
            "package_version": package_version(),
            "api_version": API_VERSION,
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(self.config),
            "modes": ["auto", "daily", "academic", "tool_selection"],
            "providers": list(available_providers(self.config.searxng_url.strip().rstrip("/"))),
            "formats": ["json", "md"],
            "routing": {
                "default_strategy": self.config.routing_strategy,
                "default_profile": self.config.search_profile,
                "strategies": list(ROUTING_STRATEGIES),
                "profiles": list(PROFILES),
                "provider_capabilities": provider_catalog(),
            },
            "features": {
                "search": True,
                "route": True,
                "deepen": True,
                "verify": True,
                "ingest": True,
                "auto_verify": True,
                "provider_health": True,
                "reader_health": True,
                "tool_matrix": True,
                "canonical_merge": True,
                "source_quality": True,
                "sse_events": True,
                "watch_folder": True,
            },
            "stable_endpoints": [
                "GET /v1/health",
                "GET /v1/capabilities",
                "GET /v1/discovery",
                "GET /v1/openapi.json",
                "POST /v1/search",
                "POST /v1/route",
                "POST /v1/deepen",
                "POST /v1/verify",
                "POST /v1/ingest",
                "POST /v1/jobs",
                "GET /v1/jobs",
                "GET /v1/jobs/{job_id}",
                "GET /v1/jobs/{job_id}/events",
                "POST /v1/jobs/{job_id}/retry",
                "POST /v1/agent/research",
                "POST /v1/agent/verify",
                "POST /v1/agent/fetch",
                "GET /v1/agent/jobs/{job_id}",
                "GET /v1/agent/jobs/{job_id}/events",
                "POST /v1/agent/jobs/{job_id}/retry",
                "GET /v1/runs/{run_id}",
                "GET /v1/runs/{run_id}/report.md",
                "GET /v1/runs/{run_id}/report.json",
                "GET /v1/runs/{run_id}/events",
                "POST /v1/runs/{run_id}/quality",
                "POST /v1/runs/{run_id}/deepen-claims",
                "POST /v1/references/check",
            ],
        }

    def discovery(self) -> dict:
        return render_agent_discovery_json(_base_url_from_config(self.config))

    def route(self, payload: dict) -> dict:
        topic = str(payload.get("topic") or payload.get("query") or "").strip()
        if not topic:
            raise ValueError("topic or query is required")
        providers = payload.get("providers") or None
        plan = build_routing_plan(
            topic,
            mode=str(payload.get("mode") or "auto"),
            profile=str(payload.get("profile") or self.config.search_profile),
            routing_strategy="manual" if providers else str(payload.get("routing_strategy") or self.config.routing_strategy),
            provider_pool=self.config.search_providers,
            explicit_providers=providers,
            deep=bool(payload.get("deep", False)),
        )
        return plan.to_dict()

    def search(self, payload: dict) -> dict:
        payload = dict(payload)
        if payload.pop("_skip_job_record", False):
            return self._search_impl(payload)
        return run_tracked_job(self.data_dir, "search", payload, lambda: self._search_impl(payload))

    def _search_impl(self, payload: dict) -> dict:
        topic = str(payload.get("topic", "")).strip()
        urls = payload.get("urls") or []
        if isinstance(urls, str):
            urls = [line.strip() for line in urls.splitlines() if line.strip()]
        previous_run_id = str(payload.get("previous_run_id") or "").strip()
        skip_providers = _successful_providers_from_run(self.data_dir, previous_run_id) if previous_run_id else []
        result = run_search_pipeline(
            topic=topic,
            data_dir=self.data_dir,
            mode=str(payload.get("mode") or "auto"),
            max_results=int(payload.get("max_results") or self.config.max_results),
            no_network=bool(payload.get("no_network", False)),
            urls=urls,
            fetch_urls=bool(payload.get("fetch", self.config.fetch_urls)),
            searxng_url=self.config.searxng_url,
            search_providers=payload.get("providers") or self.config.search_providers,
            profile=str(payload.get("profile") or self.config.search_profile),
            routing_strategy="manual" if payload.get("providers") else str(payload.get("routing_strategy") or self.config.routing_strategy),
            providers_explicit=bool(payload.get("providers")),
            external_payloads=_external_payload_list(payload),
            skip_providers=skip_providers,
            max_adaptive_stages=1 if previous_run_id and bool(payload.get("deep", False)) else None,
            deep=bool(payload.get("deep", False)),
            auto_verify=bool(payload.get("auto_verify", False)),
        )
        return result.to_dict()

    def deepen(self, payload: dict) -> dict:
        payload = dict(payload)
        payload["deep"] = True
        return self.search(payload)

    def run(self, payload: dict) -> dict:
        payload = dict(payload)
        if payload.pop("_skip_job_record", False):
            return self._run_impl(payload)
        return run_tracked_job(self.data_dir, "run", payload, lambda: self._run_impl(payload))

    def _run_impl(self, payload: dict) -> dict:
        topic = str(payload.get("topic", "")).strip()
        urls = payload.get("urls") or []
        if isinstance(urls, str):
            urls = [line.strip() for line in urls.splitlines() if line.strip()]
        manual_evidence = payload.get("manual_evidence") or []
        if isinstance(manual_evidence, dict):
            manual_evidence = [manual_evidence]
        external_payloads = payload.get("external_payloads") or []
        if isinstance(external_payloads, dict):
            external_payloads = [external_payloads]
        result = run_research_pipeline(
            topic=topic,
            data_dir=self.data_dir,
            mode=str(payload.get("mode") or "auto"),
            max_results=int(payload.get("max_results") or self.config.max_results),
            no_network=bool(payload.get("no_network", False)),
            urls=urls,
            manual_evidence=manual_evidence,
            text_to_verify=str(payload.get("text_to_verify") or ""),
            fetch_urls=bool(payload.get("fetch", self.config.fetch_urls)),
            searxng_url=self.config.searxng_url,
            search_providers=payload.get("providers") or self.config.search_providers,
            profile=str(payload.get("profile") or self.config.search_profile),
            routing_strategy="manual" if payload.get("providers") else str(payload.get("routing_strategy") or self.config.routing_strategy),
            providers_explicit=bool(payload.get("providers")),
            external_payloads=external_payloads,
            claim_deepen_rounds=int(payload.get("claim_deepen_rounds") or 0),
        )
        return result.to_dict()

    def start_job(self, payload: dict) -> dict:
        kind = str(payload.get("kind") or "search").strip().lower()
        job_payload = payload.get("payload") or {}
        background = bool(payload.get("background", True))
        if not isinstance(job_payload, dict):
            raise ValueError("payload must be an object")
        routes = {
            "search": self.search,
            "deepen": self.deepen,
            "run": self.run,
            "verify": self.verify,
            "fetch": self.ingest,
            "ingest": self.ingest,
        }
        if kind not in routes:
            raise ValueError("kind must be search, deepen, run, verify, fetch, or ingest")
        job_payload = dict(job_payload)
        job_payload["_skip_job_record"] = True
        runner = lambda: routes[kind](job_payload)
        if background:
            return start_background_job(self.data_dir, kind, job_payload, runner)
        return run_tracked_job(self.data_dir, kind, job_payload, runner)

    def retry_job(self, job_id: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        original = self.job(job_id)
        if original.get("status") in {"pending", "running"}:
            raise ValueError("only terminal jobs can be retried")
        kind = str(payload.get("kind") or original.get("kind") or "").strip().lower()
        job_payload = dict(original.get("payload") or {})
        overrides = payload.get("payload") or {}
        if overrides:
            if not isinstance(overrides, dict):
                raise ValueError("payload must be an object")
            job_payload.update(overrides)
        job_payload.pop("_skip_job_record", None)
        job_payload["_retry_of"] = job_id
        accepted = self.start_job({"kind": kind, "payload": job_payload, "background": bool(payload.get("background", True))})
        retry_of = {"retry_of": job_id}
        accepted["retry_of"] = job_id
        if accepted.get("job"):
            accepted["job"]["retry_of"] = job_id
        add_job_event(self.data_dir, accepted["job_id"], "job", "retry", str(accepted.get("job_status") or "running"), f"Retrying {job_id}.", retry_of)
        return accepted

    def agent_research(self, payload: dict) -> dict:
        job_payload = _agent_research_payload(payload, self.config)
        client_request_id = _client_request_id(payload)
        if client_request_id:
            existing = self._agent_existing_job("search", client_request_id)
            if existing:
                return existing
            job_payload["_client_request_id"] = client_request_id
        accepted = self.start_job({"kind": "search", "payload": job_payload, "background": True})
        return _agent_job_response(accepted, self.config)

    def agent_verify(self, payload: dict) -> dict:
        text = str(payload.get("text") or payload.get("report") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if run_id and not text:
            text = str(self.run_report(run_id, "md").get("content") or "")
        if not text:
            raise ValueError("text or run_id is required")
        job_payload = {"topic": str(payload.get("topic") or "Agent Verification"), "text": text}
        client_request_id = _client_request_id(payload)
        if client_request_id:
            existing = self._agent_existing_job("verify", client_request_id)
            if existing:
                return existing
            job_payload["_client_request_id"] = client_request_id
        accepted = self.start_job({"kind": "verify", "payload": job_payload, "background": True})
        return _agent_job_response(accepted, self.config)

    def agent_fetch(self, payload: dict) -> dict:
        urls = payload.get("urls") or payload.get("url") or []
        if isinstance(urls, str):
            urls = [urls]
        if not urls:
            raise ValueError("url or urls is required")
        job_payload = {"urls": list(urls), "fetch": bool(payload.get("fetch", True))}
        client_request_id = _client_request_id(payload)
        if client_request_id:
            existing = self._agent_existing_job("fetch", client_request_id)
            if existing:
                return existing
            job_payload["_client_request_id"] = client_request_id
        accepted = self.start_job({"kind": "fetch", "payload": job_payload, "background": True})
        return _agent_job_response(accepted, self.config)

    def _agent_existing_job(self, kind: str, client_request_id: str) -> dict | None:
        mark_stale_jobs(self.data_dir)
        for job in list_jobs(self.data_dir, limit=200):
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if str(job.get("kind") or "") == kind and str(payload.get("_client_request_id") or "") == client_request_id:
                return _agent_job_response(
                    {
                        "job_id": job.get("id"),
                        "job_status": job.get("status", "pending"),
                        "job": job,
                        "job_events": job_events(self.data_dir, str(job.get("id") or "")),
                    },
                    self.config,
                )
        return None

    def agent_job(self, job_id: str) -> dict:
        record = self.job(job_id)
        events = job_events(self.data_dir, job_id)
        return {
            "job_id": job_id,
            "status": record.get("status", "unknown"),
            "kind": record.get("kind", ""),
            "stage": record.get("stage", ""),
            "result": record.get("result", {}),
            "error": record.get("error", ""),
            "events": _agent_events(events),
            "links": _agent_links(job_id, self.config),
        }

    def agent_job_events(self, job_id: str) -> dict:
        record = get_job(self.data_dir, job_id)
        if not record:
            raise ValueError("job not found")
        return {
            "job_id": job_id,
            "status": record.get("status", "unknown"),
            "kind": record.get("kind", ""),
            "stage": record.get("stage", ""),
            "result": record.get("result", {}),
            "error": record.get("error", ""),
            "events": _agent_events(job_events(self.data_dir, job_id)),
            "links": _agent_links(job_id, self.config),
        }

    def agent_retry(self, job_id: str, payload: dict | None = None) -> dict:
        accepted = self.retry_job(job_id, payload or {})
        return _agent_job_response(accepted, self.config)

    def jobs(self, limit: int = 20) -> dict:
        stale = mark_stale_jobs(self.data_dir)
        return {"jobs": list_jobs(self.data_dir, limit=limit), "stale_recovered": len(stale)}

    def job(self, job_id: str) -> dict:
        mark_stale_jobs(self.data_dir)
        record = get_job(self.data_dir, job_id)
        if not record:
            raise ValueError("job not found")
        return record

    def job_events(self, job_id: str) -> dict:
        if not get_job(self.data_dir, job_id):
            raise ValueError("job not found")
        return {"job_id": job_id, "events": job_events(self.data_dir, job_id)}

    def ingest(self, payload: dict) -> dict:
        urls = payload.get("urls") or []
        if isinstance(urls, str):
            urls = [line.strip() for line in urls.splitlines() if line.strip()]
        sources = [
            source_from_url(
                str(url).strip(),
                search_queries=["web ingest"],
                search_buckets=["manual URLs"],
                search_purposes=["user supplied source"],
                search_stages=["web_ingest"],
            )
            for url in urls
            if str(url).strip()
        ]
        evidence = extract_evidence_from_sources(sources, fetch=bool(payload.get("fetch", self.config.fetch_urls)))
        append_jsonl(self.data_dir / "sources.jsonl", sources)
        append_jsonl(self.data_dir / "evidence.jsonl", evidence)
        sync_jsonl_to_sqlite_quietly(self.data_dir)
        return {
            "sources": [source.to_dict() for source in sources],
            "evidence": [item.to_dict() for item in evidence],
        }

    def add_evidence(self, payload: dict) -> dict:
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("evidence text is required")
        sources, evidence_items = manual_evidence_records([payload])
        source = sources[0]
        evidence = evidence_items[0]
        append_jsonl(self.data_dir / "sources.jsonl", [source])
        append_jsonl(self.data_dir / "evidence.jsonl", [evidence])
        sync_jsonl_to_sqlite_quietly(self.data_dir)
        return {
            "source": source.to_dict(),
            "evidence": evidence.to_dict(),
        }

    def verify(self, payload: dict) -> dict:
        text = str(payload.get("text", "")).strip()
        topic = str(payload.get("topic", "Research Report")).strip() or "Research Report"
        if not text:
            raise ValueError("text is required")

        claims = extract_atomic_claims(text)
        evidence = [_evidence_from_row(row) for row in dedupe_rows(read_jsonl(self.data_dir / "evidence.jsonl"))]
        sources = [_source_from_row(row) for row in dedupe_rows(read_jsonl(self.data_dir / "sources.jsonl"))]
        verified = verify_claims(claims, evidence)
        claim_citations = claim_citations_from_claims(verified, evidence, sources)
        revision_suggestions = suggest_revisions(verified, evidence) if bool(payload.get("suggest_revisions", True)) else []
        append_jsonl(self.data_dir / "claims.jsonl", verified)
        sync_jsonl_to_sqlite_quietly(self.data_dir)
        report = Report(
            topic=topic,
            summary=_summary_from_claims(verified),
            findings=[claim.text for claim in verified if str(claim.verdict) == "supported"],
            claim_table=verified,
            gaps=[claim.text for claim in verified if str(claim.verdict) == "not_found"],
            sources=sources,
            evidence=evidence,
            claim_citations=claim_citations,
            revision_suggestions=revision_suggestions,
        )
        return {
            "claims": [claim.to_dict() for claim in verified],
            "claim_citations": [item.to_dict() for item in claim_citations],
            "revision_suggestions": [item.to_dict() for item in revision_suggestions],
            "report_markdown": report_to_markdown(report),
            "report_json": report_to_json(report),
        }

    def export(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        output_format = str(payload.get("format") or "json")
        sources = dedupe_rows(read_jsonl(self.data_dir / "sources.jsonl"))
        evidence = dedupe_rows(read_jsonl(self.data_dir / "evidence.jsonl"))
        claims = dedupe_rows(read_jsonl(self.data_dir / "claims.jsonl"))
        runs = dedupe_rows(read_jsonl(self.data_dir / "runs.jsonl"))
        data = {"sources": sources, "evidence": evidence, "claims": claims, "runs": runs}
        if output_format == "md":
            markdown = ["# Research Store Export", "", "## Sources"]
            markdown.extend(f"- [{source.get('title') or source.get('url')}]({source.get('url')}) - {source.get('source_type')}" for source in sources)
            markdown.extend(["", "## Evidence"])
            markdown.extend(f"- `{item.get('id')}`: {item.get('quote_or_paraphrase')}" for item in evidence)
            markdown.extend(["", "## Claims"])
            markdown.extend(f"- {claim.get('verdict')} ({claim.get('confidence')}): {claim.get('text')}" for claim in claims)
            markdown.extend(["", "## Runs"])
            markdown.extend(
                f"- `{run.get('id')}` {run.get('topic')} - {run.get('claim_count')} claims; {run.get('report_path')}"
                for run in sorted(runs, key=lambda item: str(item.get("created_at", "")), reverse=True)
            )
            return {"format": "md", "content": "\n".join(markdown) + "\n", "data": data}
        return {"format": "json", "content": json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), "data": data}

    def status(self) -> dict:
        searxng_url = os.environ.get("RESEARCH_SEARXNG_URL", "").strip() or self.config.searxng_url
        resolved_searxng_url = resolve_searxng_url(self.config.searxng_url)
        available = available_providers(resolved_searxng_url)
        reader = reader_health(self.config)
        return {
            "service": "ai-research-workflow",
            "pid": os.getpid(),
            "searxng_configured": bool(resolved_searxng_url),
            "searxng_url": searxng_url,
            "resolved_searxng_url": resolved_searxng_url,
            "search_providers": self.config.search_providers,
            "available_providers": available,
            "provider_health": _provider_status_catalog(available),
            "data_dir": str(self.data_dir.resolve()),
            "max_results": self.config.max_results,
            "fetch_urls": self.config.fetch_urls,
            "reader_provider": self.config.reader_provider or "local",
            "reader": reader,
            "reader_path": reader.get("path") or self.config.web_content_fetcher_path,
            "sqlite_store": sqlite_summary(self.data_dir),
            "obsidian_configured": bool(str(self.config.obsidian_vault).strip()),
            "obsidian_vault": self.config.obsidian_vault,
            "obsidian_folder": self.config.obsidian_folder,
            "source_count": len(dedupe_rows(read_jsonl(self.data_dir / "sources.jsonl"))),
            "evidence_count": len(dedupe_rows(read_jsonl(self.data_dir / "evidence.jsonl"))),
            "claim_count": len(dedupe_rows(read_jsonl(self.data_dir / "claims.jsonl"))),
            "run_count": len(dedupe_rows(read_jsonl(self.data_dir / "runs.jsonl"))),
        }

    def doctor(self) -> dict:
        return run_doctor(self.config, self.data_dir)

    def sqlite_sync(self, payload: dict | None = None) -> dict:
        return sync_jsonl_to_sqlite(self.data_dir)

    def selftest(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        work_dir = str(payload.get("work_dir") or "").strip()
        data_dir = Path(work_dir) if work_dir else self.data_dir / "selftest"
        return run_selftest(self.config, data_dir)

    def evals(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        work_dir = str(payload.get("work_dir") or "").strip()
        data_dir = Path(work_dir) if work_dir else self.data_dir / "evals"
        return run_evals(self.config, data_dir)

    def readiness(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        work_dir = str(payload.get("work_dir") or "").strip()
        data_dir = Path(work_dir) if work_dir else self.data_dir / "readiness"
        return run_readiness(self.config, data_dir, skip_slow=bool(payload.get("skip_slow", True)))

    def runs(self, limit: int = 20) -> dict:
        return {"runs": list_runs(self.data_dir, limit=limit)}

    def run_record(self, run_id: str) -> dict:
        record = get_run(self.data_dir, run_id)
        if not record:
            raise ValueError("run not found")
        return record

    def obsidian(self, payload: dict) -> dict:
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("run_id is required")
        return export_run_to_obsidian(
            data_dir=self.data_dir,
            run_id=run_id,
            vault=str(payload.get("vault") or self.config.obsidian_vault),
            folder=str(payload.get("folder") or self.config.obsidian_folder),
            overwrite=bool(payload.get("overwrite", False)),
        )

    def audit(self, run_id: str) -> dict:
        if not run_id:
            raise ValueError("run_id is required")
        return audit_run(self.data_dir, run_id)

    def quality(self, run_id: str) -> dict:
        if not run_id:
            raise ValueError("run_id is required")
        return run_quality(self.data_dir, run_id)

    def deepen_claims(self, run_id: str, payload: dict | None = None) -> dict:
        if not run_id:
            raise ValueError("run_id is required")
        payload = payload or {}
        return deepen_claim_suggestions_for_run(self.data_dir, run_id, max_queries=int(payload.get("max_queries") or 5))

    def check_references(self, payload: dict) -> dict:
        text = str(payload.get("text") or "").strip()
        run_id = str(payload.get("run_id") or "").strip()
        if run_id and not text:
            text = str(self.run_report(run_id, "md").get("content") or "")
        if not text:
            raise ValueError("text or run_id is required")
        return check_academic_references(text).to_dict()

    def run_report(self, run_id: str, output_format: str) -> dict:
        record = self.run_record(run_id)
        if output_format == "md":
            return {"format": "md", "content": str(record.get("report_markdown") or "")}
        if output_format == "json":
            report_json = record.get("report_json")
            if isinstance(report_json, str):
                try:
                    return {"format": "json", "content": json.loads(report_json)}
                except json.JSONDecodeError:
                    return {"format": "json", "content": report_json}
            return {"format": "json", "content": report_json or record}
        raise ValueError("format must be md or json")

    def run_events(self, run_id: str) -> dict:
        record = self.run_record(run_id)
        events = []
        for item in record.get("search_queries") or []:
            events.append(
                {
                    "type": "search_query",
                    "stage": item.get("stage"),
                    "status": item.get("status"),
                    "query": item.get("query"),
                    "source_count": item.get("source_count", 0),
                    "provider_statuses": item.get("provider_statuses", []),
                }
            )
        if record.get("audit"):
            events.append({"type": "audit", "status": record.get("audit", {}).get("overall"), "audit": record.get("audit")})
        return {"run_id": run_id, "events": events}

    def run_events_sse(self, run_id: str) -> str:
        payload = self.run_events(run_id)
        lines: list[str] = []
        for index, event in enumerate(payload["events"]):
            event_type = str(event.get("type") or "message")
            event_payload = {"run_id": run_id, "index": index, **event}
            lines.append(f"event: {event_type}")
            lines.append(f"data: {json.dumps(event_payload, ensure_ascii=False, sort_keys=True)}")
            lines.append("")
        lines.append("event: done")
        lines.append(f"data: {json.dumps({'run_id': run_id, 'event_count': len(payload['events'])}, ensure_ascii=False, sort_keys=True)}")
        lines.append("")
        return "\n".join(lines)

    def openapi_schema(self) -> dict:
        return _openapi_schema(web_base_url(self.config))


def make_handler(app: ResearchWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ResearchWorkflowWeb/{package_version()}"

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._send_cors_headers()
            self.send_header("access-control-max-age", "86400")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self._request_path()
            if path == "/":
                path = "/index.html"
            if path == "/v1/health":
                self._json_response(app.health())
                return
            if path == "/v1/capabilities":
                self._json_response(app.capabilities())
                return
            if path == "/v1/discovery":
                self._json_response(app.discovery())
                return
            if path == "/v1/openapi.json":
                self._json_response(app.openapi_schema())
                return
            if path == "/v1/jobs":
                query = parse_qs(urlparse(self.path).query)
                limit = int((query.get("limit") or ["20"])[0])
                self._json_response(app.jobs(limit=limit))
                return
            if path.startswith("/v1/jobs/"):
                self._v1_job_response(path)
                return
            if path.startswith("/v1/agent/jobs/"):
                if path.endswith("/events"):
                    job_id = path.removeprefix("/v1/agent/jobs/").removesuffix("/events").strip("/")
                    try:
                        self._json_response(app.agent_job_events(job_id))
                    except ValueError as exc:
                        self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                    return
                job_id = path.removeprefix("/v1/agent/jobs/").strip("/")
                try:
                    self._json_response(app.agent_job(job_id))
                except ValueError as exc:
                    self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if path.startswith("/v1/runs/"):
                self._v1_run_response(path)
                return
            if path == "/api/export":
                self._json_response(app.export())
                return
            if path == "/api/status":
                self._json_response(app.status())
                return
            if path == "/api/doctor":
                self._json_response(app.doctor())
                return
            if path == "/api/runs":
                query = parse_qs(urlparse(self.path).query)
                limit = int((query.get("limit") or ["20"])[0])
                self._json_response(app.runs(limit=limit))
                return
            if path == "/api/run":
                query = parse_qs(urlparse(self.path).query)
                run_id = (query.get("id") or [""])[0]
                try:
                    self._json_response(app.run_record(run_id))
                except ValueError as exc:
                    self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if path == "/api/audit":
                query = parse_qs(urlparse(self.path).query)
                run_id = (query.get("id") or [""])[0]
                result = app.audit(run_id)
                status = HTTPStatus.OK if result.get("overall") != "error" else HTTPStatus.BAD_REQUEST
                self._json_response(result, status=status)
                return
            self._static_response(path)

        def do_POST(self) -> None:  # noqa: N802
            route = self._request_path()
            if route.startswith("/v1/runs/") and (route.endswith("/quality") or route.endswith("/deepen-claims")):
                suffix = route.removeprefix("/v1/runs/").strip("/")
                parts = suffix.split("/")
                run_id = parts[0] if parts else ""
                action = parts[1] if len(parts) > 1 else ""
                try:
                    if action == "quality":
                        result = app.quality(run_id)
                    elif action == "deepen-claims":
                        result = app.deepen_claims(run_id, self._read_json())
                    else:
                        raise ValueError("unknown run action")
                except ValueError as exc:
                    self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND if "not found" in str(exc) else HTTPStatus.BAD_REQUEST)
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json_response({"error": f"server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json_response(result)
                return
            if route.startswith("/v1/agent/jobs/") and route.endswith("/retry"):
                job_id = route.removeprefix("/v1/agent/jobs/").removesuffix("/retry").strip("/")
                if not job_id:
                    self._json_response({"error": "job_id is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    result = app.agent_retry(job_id, self._read_json())
                except ValueError as exc:
                    self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND if "not found" in str(exc) else HTTPStatus.BAD_REQUEST)
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json_response({"error": f"server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json_response(result)
                return
            if route.startswith("/v1/jobs/") and route.endswith("/retry"):
                job_id = route.removeprefix("/v1/jobs/").removesuffix("/retry").strip("/")
                if not job_id:
                    self._json_response({"error": "job_id is required"}, status=HTTPStatus.BAD_REQUEST)
                    return
                try:
                    result = app.retry_job(job_id, self._read_json())
                except ValueError as exc:
                    self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND if "not found" in str(exc) else HTTPStatus.BAD_REQUEST)
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json_response({"error": f"server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._json_response(result)
                return
            routes = {
                "/api/search": app.search,
                "/api/query": app.query,
                "/api/run": app.run,
                "/api/ingest": app.ingest,
                "/api/evidence": app.add_evidence,
                "/api/verify": app.verify,
                "/api/export": app.export,
                "/api/obsidian": app.obsidian,
                "/api/selftest": app.selftest,
                "/api/evals": app.evals,
                "/api/readiness": app.readiness,
                "/api/sqlite/sync": app.sqlite_sync,
                "/v1/search": app.search,
                "/v1/route": app.route,
                "/v1/deepen": app.deepen,
                "/v1/verify": app.verify,
                "/v1/ingest": app.ingest,
                "/v1/jobs": app.start_job,
                "/v1/agent/research": app.agent_research,
                "/v1/agent/verify": app.agent_verify,
                "/v1/agent/fetch": app.agent_fetch,
                "/v1/references/check": app.check_references,
            }
            if route not in routes:
                self._json_response({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
                return
            try:
                payload = self._read_json()
                result = routes[route](payload)
            except ValueError as exc:
                self._json_response({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:  # noqa: BLE001
                self._json_response({"error": f"server error: {exc}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._json_response(result)

        def _v1_job_response(self, path: str) -> None:
            suffix = path.removeprefix("/v1/jobs/").strip("/")
            if not suffix:
                self._json_response({"error": "job_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            parts = suffix.split("/")
            job_id = parts[0]
            try:
                if len(parts) == 1:
                    self._json_response(app.job(job_id))
                    return
                if parts[1] == "events":
                    self._json_response(app.job_events(job_id))
                    return
            except ValueError as exc:
                self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND if "not found" in str(exc) else HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

        def _v1_run_response(self, path: str) -> None:
            suffix = path.removeprefix("/v1/runs/").strip("/")
            if not suffix:
                self._json_response({"error": "run_id is required"}, status=HTTPStatus.BAD_REQUEST)
                return
            parts = suffix.split("/")
            run_id = parts[0]
            try:
                if len(parts) == 1:
                    self._json_response(app.run_record(run_id))
                    return
                if parts[1] == "report.md":
                    self._text_response(app.run_report(run_id, "md")["content"], content_type="text/markdown; charset=utf-8")
                    return
                if parts[1] == "report.json":
                    self._json_response(app.run_report(run_id, "json")["content"])
                    return
                if parts[1] == "events":
                    if self._wants_sse():
                        self._text_response(app.run_events_sse(run_id), content_type="text/event-stream; charset=utf-8", cache_control="no-cache")
                        return
                    self._json_response(app.run_events(run_id))
                    return
            except ValueError as exc:
                self._json_response({"error": str(exc)}, status=HTTPStatus.NOT_FOUND if "not found" in str(exc) else HTTPStatus.BAD_REQUEST)
                return
            self._json_response({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def _read_json(self) -> dict:
            length = int(self.headers.get("content-length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw)

        def _request_path(self) -> str:
            parsed = urlparse(self.path)
            path = parsed.path if parsed.scheme else self.path.split("?", 1)[0]
            if len(path) > 1:
                path = path.rstrip("/")
            return path

        def _wants_sse(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            requested_format = str((query.get("format") or [""])[0]).lower()
            accept = str(self.headers.get("accept") or "").lower()
            return requested_format == "sse" or "text/event-stream" in accept

        def _json_response(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _text_response(
            self,
            payload: str,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            content_type: str = "text/plain; charset=utf-8",
            cache_control: str = "",
        ) -> None:
            body = str(payload).encode("utf-8")
            self.send_response(status)
            self._send_cors_headers()
            self.send_header("content-type", content_type)
            if cache_control:
                self.send_header("cache-control", cache_control)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return

        def _static_response(self, path: str) -> None:
            requested = unquote(path).lstrip("/")
            file_path = (STATIC_ROOT / requested).resolve()
            try:
                file_path.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not file_path.exists() or not file_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = file_path.read_bytes()
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self._send_cors_headers()
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_cors_headers(self) -> None:
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type, accept")

    return Handler


def main(argv: list[str] | None = None) -> int:
    config_path = _preparse_config_path(argv)
    config = load_config(config_path)
    parser = argparse.ArgumentParser(description="Run the local research workflow Web UI")
    parser.add_argument("--config", help="Path to research.config.json")
    parser.add_argument("--host", default=config.web_host)
    parser.add_argument("--port", type=int, default=config.web_port)
    parser.add_argument("--data-dir", default=str(config.data_dir or DEFAULT_DATA_DIR))
    args = parser.parse_args(argv)
    config = load_config(args.config)
    config.web_host = args.host
    config.web_port = args.port

    app = ResearchWebApp(Path(args.data_dir or config.data_dir or DEFAULT_DATA_DIR), config=config)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    write_service_record(
        app.data_dir,
        base_url=f"http://{args.host}:{args.port}",
        pid=os.getpid(),
        status="running",
        extra={"contract_version": CONTRACT_VERSION, "config_hash": config_fingerprint(config)},
    )
    print(f"Research Workflow Web UI running at http://{args.host}:{args.port}")
    print(f"Data directory: {Path(args.data_dir).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping server")
    finally:
        server.server_close()
    return 0


def _source_from_row(row: dict) -> Source:
    return Source(**row)


def _evidence_from_row(row: dict) -> Evidence:
    return Evidence(**row)


def _provider_status_catalog(available: dict) -> list[dict]:
    cost_notes = {
        "searxng": ("local/free", "No API key; native local SearXNG is preferred. Upstream engines may throttle."),
        "metaso": ("free-tier/API", "Domestic Chinese search API. Continue on quota/rate errors."),
        "tavily": ("free-tier/API", "Good agent search default. Continue on monthly credit exhaustion."),
        "serpapi": ("free-tier/API", "Google-style fallback. Small free quota; continue on 429/quota errors."),
        "brave": ("free-credits/API", "Free credits can be capped; card-backed billing may still show auth/quota failures."),
        "you": ("free-tier/API", "Broad web API. Continue if credits run out."),
        "firecrawl_developer": ("keyless/free-tier", "Developer Index is keyless to start; 2 credits per 10 results when metered. Continue on 429/quota errors."),
        "github_cli": ("local/free", "Read-only public repository search through the local GitHub CLI. Missing auth or command errors do not block fallback providers."),
        "exa": ("optional/API", "Research preset only until quota/key is stable."),
        "jina": ("optional/API", "Reader/Search adapter; useful for extraction-heavy runs."),
        "perplexity": ("paid/API", "Paid optional source; disabled by default."),
        "kagi": ("paid/API", "Paid optional source; disabled by default."),
    }
    rows = []
    for provider, details in available.items():
        cost_tier, limit_note = cost_notes.get(provider, ("unknown", "Check provider console for quota."))
        rows.append(
            {
                "provider": provider,
                "configured": bool(details.get("configured")),
                "enabled_by_default": bool(details.get("enabled_by_default")),
                "detail": str(details.get("detail", "")),
                "cost_tier": cost_tier,
                "limit_note": limit_note,
                "overall": "key_present" if details.get("configured") else "not_configured",
            }
        )
    return rows


def _summary_from_claims(claims: list) -> str:
    supported = sum(1 for claim in claims if str(claim.verdict) == "supported")
    partial = sum(1 for claim in claims if str(claim.verdict) == "partial")
    contradicted = sum(1 for claim in claims if str(claim.verdict) == "contradicted")
    not_found = sum(1 for claim in claims if str(claim.verdict) == "not_found")
    return f"Claim check complete: {supported} supported, {partial} partial, {contradicted} contradicted, {not_found} not found."


def _base_url_from_config(config: ResearchConfig) -> str:
    return web_base_url(config)


def _agent_research_payload(payload: dict, config: ResearchConfig) -> dict:
    topic = str(payload.get("topic") or payload.get("query") or "").strip()
    if not topic:
        raise ValueError("topic or query is required")
    urls = payload.get("urls") or []
    if isinstance(urls, str):
        urls = [line.strip() for line in urls.splitlines() if line.strip()]
    result = {
        "topic": topic,
        "mode": str(payload.get("mode") or "auto"),
        "max_results": int(payload.get("max_results") or config.max_results),
        "no_network": bool(payload.get("no_network", False)),
        "urls": urls,
        "fetch": bool(payload.get("fetch", config.fetch_urls)),
        "profile": str(payload.get("profile") or config.search_profile),
        "routing_strategy": "manual" if payload.get("providers") else str(payload.get("routing_strategy") or config.routing_strategy),
        "external_payloads": _external_payload_list(payload),
        "auto_verify": bool(payload.get("auto_verify", True)),
    }
    if payload.get("providers"):
        result["providers"] = payload["providers"]
    return result


def _external_payload_list(payload: dict) -> list[dict]:
    items = payload.get("external_payloads") or []
    if isinstance(items, dict):
        return [items]
    if not isinstance(items, list):
        raise ValueError("external_payloads must be an object or array")
    return [item for item in items if isinstance(item, dict)]


def _successful_providers_from_run(data_dir: Path, run_id: str) -> list[str]:
    run = get_run(data_dir, run_id)
    if not run:
        return []
    providers: list[str] = []
    for query in run.get("search_queries") or []:
        for status in query.get("provider_statuses") or []:
            if status.get("status") == "ok" and int(status.get("source_count") or 0) > 0:
                provider = str(status.get("provider") or "").strip().lower()
                if provider and provider not in providers:
                    providers.append(provider)
    return providers


def _client_request_id(payload: dict) -> str:
    return str(payload.get("client_request_id") or payload.get("idempotency_key") or "").strip()


def _agent_job_response(accepted: dict, config: ResearchConfig) -> dict:
    job_id = str(accepted.get("job_id") or accepted.get("job", {}).get("id") or "")
    return {
        "job_id": job_id,
        "status": accepted.get("job_status", accepted.get("job", {}).get("status", "pending")),
        "kind": accepted.get("job", {}).get("kind", ""),
        "stage": accepted.get("job", {}).get("stage", "queued"),
        "events": _agent_events(accepted.get("job_events") or []),
        "links": _agent_links(job_id, config),
    }


def _agent_events(events: list[dict]) -> list[dict]:
    stable = []
    for event in events:
        stable.append(
            {
                "id": event.get("id", ""),
                "type": event.get("type", ""),
                "stage": event.get("stage", ""),
                "status": event.get("status", ""),
                "message": event.get("message", ""),
                "created_at": event.get("created_at", ""),
                "details": event.get("details", {}),
            }
        )
    return stable


def _agent_links(job_id: str, config: ResearchConfig) -> dict:
    base_url = _base_url_from_config(config)
    return {
        "job": f"{base_url}/v1/agent/jobs/{job_id}",
        "events": f"{base_url}/v1/agent/jobs/{job_id}/events",
        "retry": f"{base_url}/v1/agent/jobs/{job_id}/retry",
    }


def _openapi_schema(base_url: str) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "AI Research Workflow API",
            "version": package_version(),
            "description": "Local-first multi-source retrieval, evidence collection, and atomic fact checking API.",
        },
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/v1/health": {
                "get": {
                    "summary": "Service health",
                    "operationId": "getHealth",
                    "responses": {"200": {"description": "Health status", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}}}},
                }
            },
            "/v1/capabilities": {
                "get": {
                    "summary": "Service capabilities",
                    "operationId": "getCapabilities",
                    "responses": {"200": {"description": "Capabilities", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CapabilitiesResponse"}}}}},
                }
            },
            "/v1/discovery": {
                "get": {
                    "summary": "Agent discovery descriptor",
                    "operationId": "getAgentDiscovery",
                    "responses": {"200": {"description": "Agent discovery descriptor", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DiscoveryResponse"}}}}},
                }
            },
            "/v1/openapi.json": {
                "get": {
                    "summary": "OpenAPI schema",
                    "operationId": "getOpenApiSchema",
                    "responses": {"200": {"description": "OpenAPI schema", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/search": {
                "post": {
                    "summary": "Run a search-only research workflow",
                    "operationId": "researchSearch",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchRequest"}}}},
                    "responses": {"200": {"description": "Search run", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchResponse"}}}}},
                }
            },
            "/v1/route": {
                "post": {
                    "summary": "Plan provider routing without executing search",
                    "operationId": "researchRoute",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RouteRequest"}}}},
                    "responses": {"200": {"description": "Read-only routing plan", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RouteResponse"}}}}},
                }
            },
            "/v1/deepen": {
                "post": {
                    "summary": "Run a deeper search with expanded query budgets",
                    "operationId": "researchDeepen",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchRequest"}}}},
                    "responses": {"200": {"description": "Deepened search run", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchResponse"}}}}},
                }
            },
            "/v1/verify": {
                "post": {
                    "summary": "Extract and verify atomic claims against local evidence",
                    "operationId": "researchVerify",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/VerifyRequest"}}}},
                    "responses": {"200": {"description": "Verified claims", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/ingest": {
                "post": {
                    "summary": "Ingest manually selected URLs",
                    "operationId": "researchIngest",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/IngestRequest"}}}},
                    "responses": {"200": {"description": "Ingested sources and evidence", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/IngestResponse"}}}}},
                }
            },
            "/v1/jobs": {
                "get": {
                    "summary": "List tracked jobs",
                    "operationId": "listJobs",
                    "responses": {"200": {"description": "Job list", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
                "post": {
                    "summary": "Run a tracked search/deepen/full-chain job",
                    "operationId": "startJob",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobStartRequest"}}}},
                    "responses": {"200": {"description": "Job result", "content": {"application/json": {"schema": {"type": "object"}}}}},
                },
            },
            "/v1/jobs/{job_id}": {
                "get": {
                    "summary": "Get a tracked job",
                    "operationId": "getJob",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Job record", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/jobs/{job_id}/events": {
                "get": {
                    "summary": "Get tracked job stage events",
                    "operationId": "getJobEvents",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Job events", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/jobs/{job_id}/retry": {
                "post": {
                    "summary": "Retry a terminal tracked job with its persisted payload",
                    "operationId": "retryJob",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobRetryRequest"}}}},
                    "responses": {"200": {"description": "Accepted retry job", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/agent/research": {
                "post": {
                    "summary": "Stable agent contract: submit a background research job",
                    "operationId": "agentResearch",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentResearchRequest"}}}},
                    "responses": {"200": {"description": "Accepted agent job", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobAccepted"}}}}},
                }
            },
            "/v1/agent/verify": {
                "post": {
                    "summary": "Stable agent contract: submit a background verification job",
                    "operationId": "agentVerify",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentVerifyRequest"}}}},
                    "responses": {"200": {"description": "Accepted agent job", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobAccepted"}}}}},
                }
            },
            "/v1/agent/fetch": {
                "post": {
                    "summary": "Stable agent contract: fetch and ingest URL evidence in the background",
                    "operationId": "agentFetch",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentFetchRequest"}}}},
                    "responses": {"200": {"description": "Accepted agent job", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobAccepted"}}}}},
                }
            },
            "/v1/agent/jobs/{job_id}": {
                "get": {
                    "summary": "Stable agent contract: poll a background job",
                    "operationId": "agentGetJob",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Stable agent job status", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobStatus"}}}}},
                }
            },
            "/v1/agent/jobs/{job_id}/events": {
                "get": {
                    "summary": "Stable agent contract: poll background job events",
                    "operationId": "agentGetJobEvents",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Stable agent job events", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobEvents"}}}}},
                }
            },
            "/v1/agent/jobs/{job_id}/retry": {
                "post": {
                    "summary": "Stable agent contract: retry a terminal background job",
                    "operationId": "agentRetryJob",
                    "parameters": [{"name": "job_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/JobRetryRequest"}}}},
                    "responses": {"200": {"description": "Accepted agent retry job", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/AgentJobAccepted"}}}}},
                }
            },
            "/v1/runs/{run_id}": {
                "get": {
                    "summary": "Get a persisted run",
                    "operationId": "getRun",
                    "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Run record", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/runs/{run_id}/report.md": {
                "get": {
                    "summary": "Get a persisted run Markdown report",
                    "operationId": "getRunMarkdownReport",
                    "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Markdown report", "content": {"text/markdown": {"schema": {"type": "string"}}}}},
                }
            },
            "/v1/runs/{run_id}/report.json": {
                "get": {
                    "summary": "Get a persisted run JSON report",
                    "operationId": "getRunJsonReport",
                    "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "JSON report", "content": {"application/json": {"schema": {"type": "object"}}}}},
                }
            },
            "/v1/runs/{run_id}/events": {
                "get": {
                    "summary": "Get persisted run events as JSON or SSE",
                    "operationId": "getRunEvents",
                    "parameters": [
                        {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}},
                        {"name": "format", "in": "query", "required": False, "schema": {"type": "string", "enum": ["json", "sse"]}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Run events",
                            "content": {
                                "application/json": {"schema": {"type": "object"}},
                                "text/event-stream": {"schema": {"type": "string"}},
                            },
                        }
                    },
                }
            },
            "/v1/runs/{run_id}/quality": {
                "post": {
                    "summary": "Summarize citation coverage, source diversity, and evidence strength for a persisted run",
                    "operationId": "researchQuality",
                    "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": {"description": "Evidence quality summary", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/QualityResponse"}}}}},
                }
            },
            "/v1/runs/{run_id}/deepen-claims": {
                "post": {
                    "summary": "Generate next-search query suggestions for weak or unresolved claims",
                    "operationId": "researchDeepenClaims",
                    "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": {"type": "object", "properties": {"max_queries": {"type": "integer", "default": 5}}}}}},
                    "responses": {"200": {"description": "Claim deepen suggestions", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DeepenClaimsResponse"}}}}},
                }
            },
            "/v1/references/check": {
                "post": {
                    "summary": "Validate DOI/arXiv bibliography references from text or a persisted run",
                    "operationId": "researchCheckReferences",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {"text": {"type": "string"}, "run_id": {"type": "string"}}}}}},
                    "responses": {"200": {"description": "Reference validation result", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReferenceCheckResponse"}}}}},
                }
            },
        },
        "components": {
            "schemas": {
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "service": {"type": "string"},
                        "api_version": {"type": "string"},
                        "contract_version": {"type": "string"},
                        "config_hash": {"type": "string"},
                        "base_url": {"type": "string"},
                        "data_dir": {"type": "string"},
                        "providers": {"type": "array", "items": {"type": "string"}},
                        "reader_provider": {"type": "string"},
                        "run_count": {"type": "integer"},
                    },
                },
                "CapabilitiesResponse": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "api_version": {"type": "string"},
                        "contract_version": {"type": "string"},
                        "config_hash": {"type": "string"},
                        "modes": {"type": "array", "items": {"type": "string"}},
                        "search_providers": {"type": "array", "items": {"type": "string"}},
                        "stable_endpoints": {"type": "array", "items": {"type": "string"}},
                        "features": {"type": "object"},
                    },
                },
                "DiscoveryResponse": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "base_url": {"type": "string"},
                        "api_version": {"type": "string"},
                        "contract_version": {"type": "string"},
                        "config_hash": {"type": "string"},
                        "preferred_order": {"type": "array", "items": {"type": "object"}},
                        "stable_http": {"type": "object"},
                        "sdk_contract": {"type": "object"},
                    },
                },
                "QualityResponse": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "topic": {"type": "string"},
                        "overall": {"type": "string"},
                        "quality_summary": {"type": "object"},
                        "claim_quality": {"type": "array", "items": {"type": "object"}},
                        "finding_quality": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "DeepenClaimsResponse": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "topic": {"type": "string"},
                        "overall": {"type": "string"},
                        "deepen_suggestions": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "ReferenceCheckResponse": {
                    "type": "object",
                    "properties": {
                        "references": {"type": "array", "items": {"type": "object"}},
                        "sources": {"type": "array", "items": {"type": "object"}},
                        "validation_summary": {"type": "object"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "SearchRequest": {
                    "type": "object",
                    "required": ["topic"],
                    "properties": {
                        "topic": {"type": "string"},
                        "mode": {"type": "string", "enum": ["auto", "daily", "academic", "tool_selection"], "default": "auto"},
                        "max_results": {"type": "integer", "default": 10},
                        "no_network": {"type": "boolean", "default": False},
                        "providers": {"type": "array", "items": {"type": "string"}},
                        "profile": {"type": "string", "enum": list(PROFILES), "default": "default"},
                        "routing_strategy": {"type": "string", "enum": list(ROUTING_STRATEGIES)},
                        "external_payloads": {"oneOf": [{"type": "array", "items": {"type": "object"}}, {"type": "object"}]},
                        "urls": {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]},
                        "fetch": {"type": "boolean", "default": False},
                        "auto_verify": {"type": "boolean", "default": False},
                    },
                },
                "RouteRequest": {
                    "type": "object",
                    "required": ["topic"],
                    "properties": {
                        "topic": {"type": "string"},
                        "query": {"type": "string"},
                        "mode": {"type": "string", "enum": ["auto", "daily", "academic", "tool_selection"], "default": "auto"},
                        "profile": {"type": "string", "enum": list(PROFILES), "default": "default"},
                        "routing_strategy": {"type": "string", "enum": list(ROUTING_STRATEGIES)},
                        "providers": {"type": "array", "items": {"type": "string"}},
                        "deep": {"type": "boolean", "default": False},
                    },
                },
                "RouteResponse": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "mode": {"type": "string"},
                        "profile": {"type": "string"},
                        "route_kind": {"type": "string"},
                        "routing_strategy": {"type": "string"},
                        "stages": {"type": "array", "items": {"type": "object"}},
                        "provider_budget": {"type": "object"},
                        "stop_conditions": {"type": "object"},
                        "external_acquisition": {"type": "array", "items": {"type": "object"}},
                    },
                },
                "VerifyRequest": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {
                        "topic": {"type": "string"},
                        "text": {"type": "string"},
                        "suggest_revisions": {"type": "boolean", "default": True},
                    },
                },
                "IngestRequest": {
                    "type": "object",
                    "required": ["urls"],
                    "properties": {
                        "urls": {"oneOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]},
                        "fetch": {"type": "boolean", "default": False},
                    },
                },
                "JobStartRequest": {
                    "type": "object",
                    "required": ["kind", "payload"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["search", "deepen", "run", "verify", "fetch", "ingest"]},
                        "payload": {"type": "object"},
                        "background": {"type": "boolean", "default": True},
                    },
                },
                "JobRetryRequest": {
                    "type": "object",
                    "properties": {
                        "payload": {"type": "object", "description": "Optional payload overrides merged into the original job payload."},
                        "background": {"type": "boolean", "default": True},
                    },
                },
                "AgentResearchRequest": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "topic": {"type": "string"},
                        "mode": {"type": "string", "enum": ["auto", "daily", "academic", "tool_selection"], "default": "auto"},
                        "max_results": {"type": "integer", "default": 10},
                        "no_network": {"type": "boolean", "default": False},
                        "providers": {"type": "array", "items": {"type": "string"}},
                        "profile": {"type": "string", "enum": list(PROFILES)},
                        "routing_strategy": {"type": "string", "enum": list(ROUTING_STRATEGIES)},
                        "external_payloads": {"oneOf": [{"type": "array", "items": {"type": "object"}}, {"type": "object"}]},
                        "urls": {"type": "array", "items": {"type": "string"}},
                        "fetch": {"type": "boolean", "default": False},
                        "auto_verify": {"type": "boolean", "default": True},
                        "client_request_id": {"type": "string", "description": "Optional idempotency key. Reusing it returns the existing agent job instead of creating a duplicate."},
                        "idempotency_key": {"type": "string", "description": "Alias for client_request_id."},
                    },
                },
                "AgentVerifyRequest": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "text": {"type": "string"},
                        "report": {"type": "string"},
                        "run_id": {"type": "string"},
                        "client_request_id": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                    },
                },
                "AgentFetchRequest": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "urls": {"type": "array", "items": {"type": "string"}},
                        "fetch": {"type": "boolean", "default": True},
                        "client_request_id": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                    },
                },
                "AgentJobAccepted": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "running", "failed", "succeeded"]},
                        "kind": {"type": "string"},
                        "stage": {"type": "string"},
                        "events": {"type": "array", "items": {"type": "object"}},
                        "links": {"type": "object"},
                        "result": {"type": "object"},
                        "error": {"type": "string"},
                    },
                },
                "AgentJobStatus": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "running", "failed", "succeeded"]},
                        "kind": {"type": "string"},
                        "stage": {"type": "string"},
                        "result": {"type": "object"},
                        "error": {"type": "string"},
                        "events": {"type": "array", "items": {"type": "object"}},
                        "links": {"type": "object"},
                    },
                },
                "AgentJobEvents": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "status": {"type": "string"},
                        "kind": {"type": "string"},
                        "stage": {"type": "string"},
                        "result": {"type": "object"},
                        "error": {"type": "string"},
                        "events": {"type": "array", "items": {"type": "object"}},
                        "links": {"type": "object"},
                    },
                },
                "Source": {
                    "type": "object",
                    "description": "Traceable source record. Provenance fields explain which query lane and stage retrieved the source.",
                    "properties": {
                        "id": {"type": "string"},
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "source_type": {"type": "string", "enum": ["official", "academic", "institutional", "project", "community", "unknown"]},
                        "publisher": {"type": "string"},
                        "published_at": {"type": "string"},
                        "retrieved_at": {"type": "string"},
                        "search_engine": {"type": "string"},
                        "snippet": {"type": "string"},
                        "canonical_url": {"type": "string"},
                        "duplicate_count": {"type": "integer"},
                        "merged_urls": {"type": "array", "items": {"type": "string"}},
                        "search_queries": {"type": "array", "items": {"type": "string"}, "description": "Queries or manual labels that produced this source."},
                        "search_buckets": {"type": "array", "items": {"type": "string"}, "description": "Planner source lanes such as official docs, manual URLs, or fact-checking methods."},
                        "search_purposes": {"type": "array", "items": {"type": "string"}, "description": "Why the query/source was requested."},
                        "search_stages": {"type": "array", "items": {"type": "string"}, "description": "Workflow stage such as stage_1_recall, stage_2_gap_followup, manual_ingest, or manual_evidence."},
                        "quality_score": {"type": "integer"},
                        "quality_reasons": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "Evidence": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "source_id": {"type": "string"},
                        "quote_or_paraphrase": {"type": "string"},
                        "claim_relevance": {"type": "string"},
                        "support_direction": {"type": "string"},
                        "confidence": {"type": "number"},
                        "extraction_method": {"type": "string"},
                        "text_length": {"type": "integer"},
                        "source_quote_type": {"type": "string", "enum": ["snippet", "fetched_text", "manual_note", "reader_text", ""]},
                    },
                },
                "SearchResponse": {
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string"},
                        "kind": {"type": "string"},
                        "sources": {"type": "array", "items": {"$ref": "#/components/schemas/Source"}},
                        "evidence": {"type": "array", "items": {"$ref": "#/components/schemas/Evidence"}},
                        "provider_health": {"type": "array", "items": {"$ref": "#/components/schemas/ProviderHealth"}},
                        "revision_suggestions": {"type": "array", "items": {"$ref": "#/components/schemas/RevisionSuggestion"}},
                        "coverage_gaps": {"type": "array", "items": {"type": "string"}},
                        "report_markdown": {"type": "string"},
                        "report_json": {"type": "string"},
                        "duration_seconds": {"type": "number"},
                    },
                },
                "IngestResponse": {
                    "type": "object",
                    "properties": {
                        "sources": {"type": "array", "items": {"$ref": "#/components/schemas/Source"}},
                        "evidence": {"type": "array", "items": {"$ref": "#/components/schemas/Evidence"}},
                    },
                },
                "ProviderHealth": {
                    "type": "object",
                    "description": "Per-provider quota, error, timing, and cost summary for a search run.",
                    "properties": {
                        "provider": {"type": "string"},
                        "overall": {"type": "string", "enum": ["ok", "partial", "empty", "limited", "auth_error", "network_error", "not_configured", "error"]},
                        "ok_count": {"type": "integer"},
                        "empty_count": {"type": "integer"},
                        "limited_count": {"type": "integer"},
                        "auth_error_count": {"type": "integer"},
                        "network_error_count": {"type": "integer"},
                        "not_configured_count": {"type": "integer"},
                        "error_count": {"type": "integer"},
                        "source_count": {"type": "integer"},
                        "total_seconds": {"type": "number"},
                        "avg_seconds": {"type": "number"},
                        "max_seconds": {"type": "number"},
                        "recent_status": {"type": "string"},
                        "rank_score": {"type": "number"},
                        "rank_reason": {"type": "string"},
                        "cost_tier": {"type": "string"},
                        "limit_note": {"type": "string"},
                        "warnings": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "RevisionSuggestion": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "claim_text": {"type": "string"},
                        "verdict": {"type": "string", "enum": ["supported", "partial", "contradicted", "not_found"]},
                        "action": {"type": "string", "enum": ["keep_with_caveat", "replace", "remove", "needs_more_sources"]},
                        "failure_reason": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "suggested_replacement": {"type": "string"},
                    },
                },
            }
        },
    }


def _preparse_config_path(argv: list[str] | None) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    args, _ = parser.parse_known_args(argv)
    return args.config


if __name__ == "__main__":
    raise SystemExit(main())
