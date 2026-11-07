from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from research_workflow.audit import audit_run
from research_workflow.bootstrap import bootstrap_to_markdown, run_bootstrap
from research_workflow.config import ResearchConfig, config_fingerprint, load_config, web_base_url, write_default_config
from research_workflow.contract import CONTRACT_VERSION
from research_workflow.contract_test import contract_test_to_markdown, run_contract_test
from research_workflow.cli import main as cli_main
from research_workflow.diagnostics import run_doctor
from research_workflow.evidence import extract_evidence_from_sources, fetch_page, fetch_via_personal_intelligence_desk, fetch_via_web_content_fetcher, parse_page, source_from_url
from research_workflow.evals import evals_to_markdown, run_evals
from research_workflow.factcheck import extract_atomic_claims, verify_claims
from research_workflow.academic_reference_check import check_academic_references, extract_academic_references
from research_workflow.external_adapters import normalize_external_payload
from research_workflow.external_workers import ExternalWorkerSpec, run_gpt_researcher_worker, run_loki_worker
from research_workflow.integrations import integration_manifest, render_rest_client, write_integrations
from research_workflow.jobs import mark_stale_jobs
from research_workflow.launcher import launcher_to_markdown, start_web, stop_web, web_command, web_status
from research_workflow.mcp_server import handle_request
from research_workflow.models import Evidence, Source, SourceType, Verdict, now_iso
from research_workflow.obsidian_export import export_run_to_obsidian, obsidian_note_from_run
from research_workflow.openfactcheck_backend import normalize_openfactcheck_result, run_openfactcheck_optional
from research_workflow.longtracer_backend import verify_with_longtracer
from research_workflow.metadata import package_version
from research_workflow.pipeline import get_run, list_runs, run_research_pipeline
from research_workflow.planner import plan_query
from research_workflow.prompt_registry import get_claim_verification_prompt, load_claim_verification_prompts
from research_workflow.query_expansion import expand_claim_queries
from research_workflow.quality import deepen_claim_suggestions_for_run, run_quality
from research_workflow.readiness import readiness_to_markdown, run_readiness
from research_workflow.reader_health import reader_health
from research_workflow.revisions import suggest_revisions
from research_workflow import search as search_module
from research_workflow.agent_diagnose import agent_diagnose_to_markdown, run_agent_diagnose
from research_workflow.agent_smoke import agent_smoke_to_markdown, run_agent_smoke
from research_workflow.claim_deepen import deepen_uncertain_claims
from research_workflow.search import ProviderSearchResult, SearchError, check_searxng, discover_public_searxng_urls, infer_source_type, load_provider_stats, multi_source_search_with_status, normalize_providers, resolve_searxng_urls, search_provider, search_searxng
from research_workflow.search_pipeline import _select_sources, build_tool_matrix, canonical_groups_from_sources, list_search_runs, planned_search_queries, planned_search_query_specs, provider_health_from_queries, run_search_pipeline, search_report_to_markdown, source_quality_summary
from research_workflow.service_registry import read_service_record, update_service_smoke, write_service_record
from research_workflow.service_status import build_service_recovery_proposal, run_service_status, service_status_to_markdown
from research_workflow.sqlite_store import evidence_for_claim, find_evidence_by_hash, find_source_by_url, sqlite_summary, stale_sources, sync_jsonl_to_sqlite
from research_workflow.sparse_retrieval import retrieve_evidence_bm25
from research_workflow.synthesis import synthesize_claim_text
from research_workflow.web import ResearchWebApp, make_handler
from research_workflow.watch_folder import process_watch_folder


def test_web_content_fetcher_reader_health_checks_contract(tmp_path) -> None:
    package_dir = tmp_path / "web_content_fetcher"
    package_dir.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "web_content_fetcher_contract.py"
    (package_dir / "__init__.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "9.8.7"\n', encoding="utf-8")
    config = ResearchConfig(reader_provider="web_content_fetcher", web_content_fetcher_path=str(tmp_path))

    health = reader_health(config)

    assert health["status"] == "ok"
    assert health["available"] is True
    assert health["version"] == "9.8.7"
    assert health["module_path"].endswith("__init__.py")


def test_doctor_errors_when_selected_reader_contract_is_missing(tmp_path) -> None:
    config = ResearchConfig(reader_provider="web_content_fetcher", web_content_fetcher_path=str(tmp_path / "missing"))

    result = run_doctor(config, tmp_path / "data", include_pipeline=False)

    reader_check = next(check for check in result["checks"] if check["name"] == "reader")
    assert result["overall"] == "error"
    assert reader_check["status"] == "error"


def test_web_status_exposes_reader_health(tmp_path) -> None:
    package_dir = tmp_path / "web_content_fetcher"
    package_dir.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "web_content_fetcher_contract.py"
    (package_dir / "__init__.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    app = ResearchWebApp(
        tmp_path,
        config=ResearchConfig(
            reader_provider="web_content_fetcher",
            web_content_fetcher_path=str(tmp_path),
        ),
    )

    status = app.status()

    assert status["reader_provider"] == "web_content_fetcher"
    assert status["reader_path"] == str(tmp_path)
    assert status["reader"]["provider"] == "web_content_fetcher"
    assert status["reader"]["status"] == "ok"


def test_web_capabilities_does_not_probe_live_searxng(tmp_path, monkeypatch) -> None:
    def fail_if_called(*args, **kwargs):
        raise AssertionError("capabilities must not perform live SearXNG checks")

    monkeypatch.setattr("research_workflow.search.check_searxng", fail_if_called)
    app = ResearchWebApp(tmp_path, config=ResearchConfig(searxng_url="http://127.0.0.1:8928"))

    capabilities = app.capabilities()

    assert "POST /v1/agent/research" in capabilities["stable_endpoints"]
    assert "searxng" in capabilities["providers"]


def test_readiness_checks_selected_web_content_fetcher_import(tmp_path) -> None:
    package_dir = tmp_path / "web_content_fetcher"
    package_dir.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "web_content_fetcher_contract.py"
    (package_dir / "__init__.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    config = ResearchConfig(reader_provider="web_content_fetcher", web_content_fetcher_path=str(tmp_path))

    result = run_readiness(config, tmp_path / "readiness", skip_slow=True)

    reader_check = next(check for check in result["checks"] if check["name"] == "reader_import")
    assert reader_check["status"] == "ok"
    assert reader_check["details"]["reader"]["version"] == "1.2.3"


def test_web_search_records_job_events(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig())

    result = app.search({"topic": "SearXNG", "no_network": True, "auto_verify": True})

    assert result["job_id"].startswith("job_")
    assert result["job_status"] == "succeeded"
    events = app.job_events(result["job_id"])["events"]
    stages = {event["stage"] for event in events}
    assert {"planning", "provider_search", "fetch_evidence", "verify_claims", "write_report"}.issubset(stages)


def test_v1_start_job_runs_search_without_nested_job(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig())

    result = app.start_job({"kind": "search", "payload": {"topic": "FActScore", "no_network": True}, "background": False})

    assert result["job_status"] == "succeeded"
    assert len(app.jobs()["jobs"]) == 1


def test_background_job_returns_before_completion_and_can_be_polled(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig())

    accepted = app.start_job(
        {
            "kind": "search",
            "payload": {"topic": "FActScore", "no_network": True, "urls": ["https://github.com/shmsw25/factscore"]},
            "background": True,
        }
    )

    assert accepted["job_id"].startswith("job_")
    final = _wait_for_job(app, accepted["job_id"])
    assert final["status"] == "succeeded"
    assert final["result"]["run_id"]
    assert app.run_record(final["result"]["run_id"])["id"] == final["result"]["run_id"]
    events = app.job_events(accepted["job_id"])["events"]
    assert any(event["type"] == "url" for event in events)


def test_terminal_job_can_be_retried_from_persisted_payload(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig())
    original = app.start_job({"kind": "search", "payload": {"topic": "FActScore", "no_network": True}, "background": False})

    retried = app.retry_job(original["job_id"], {"background": False})

    assert retried["job_id"] != original["job_id"]
    assert retried["retry_of"] == original["job_id"]
    assert retried["job_status"] == "succeeded"
    retry_record = app.job(retried["job_id"])
    assert retry_record["payload"]["_retry_of"] == original["job_id"]
    events = app.job_events(retried["job_id"])["events"]
    assert any(event["stage"] == "retry" and event.get("details", {}).get("retry_of") == original["job_id"] for event in events)


def test_agent_retry_returns_stable_job_shape(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8785))
    original = app.start_job({"kind": "search", "payload": {"topic": "FActScore", "no_network": True}, "background": False})

    accepted = app.agent_retry(original["job_id"], {"background": False})

    assert set(accepted) == {"job_id", "status", "kind", "stage", "events", "links"}
    assert accepted["job_id"] != original["job_id"]
    assert accepted["status"] == "succeeded"
    assert accepted["links"]["events"].endswith(f"/v1/agent/jobs/{accepted['job_id']}/events")
    assert accepted["links"]["retry"].endswith(f"/v1/agent/jobs/{accepted['job_id']}/retry")


def test_stale_running_job_is_marked_failed(tmp_path) -> None:
    from research_workflow.store import append_jsonl

    job = {
        "id": "job_stale_demo",
        "kind": "search",
        "status": "running",
        "stage": "provider_search",
        "payload": {"topic": "old"},
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    }
    append_jsonl(tmp_path / "jobs.jsonl", [job])

    stale = mark_stale_jobs(tmp_path, max_age_seconds=10, now_epoch=1767225611)

    assert stale[0]["id"] == "job_stale_demo"
    assert stale[0]["status"] == "failed"
    assert stale[0]["stage"] == "stale"
    app = ResearchWebApp(tmp_path, config=ResearchConfig())
    record = app.job("job_stale_demo")
    assert record["status"] == "failed"
    events = app.job_events("job_stale_demo")["events"]
    assert any(event["stage"] == "stale" and event["status"] == "failed" for event in events)


def test_agent_contract_research_returns_stable_job_shape(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8767))

    accepted = app.agent_research({"query": "SearXNG", "no_network": True})

    assert set(accepted) == {"job_id", "status", "kind", "stage", "events", "links"}
    assert accepted["links"]["job"].endswith(f"/v1/agent/jobs/{accepted['job_id']}")
    assert accepted["links"]["events"].endswith(f"/v1/agent/jobs/{accepted['job_id']}/events")
    assert accepted["links"]["retry"].endswith(f"/v1/agent/jobs/{accepted['job_id']}/retry")
    final = _wait_for_job(app, accepted["job_id"])
    agent_job = app.agent_job(accepted["job_id"])
    agent_events = app.agent_job_events(accepted["job_id"])
    assert final["status"] == "succeeded"
    assert agent_job["job_id"] == accepted["job_id"]
    assert "result" in agent_job
    assert agent_events["job_id"] == accepted["job_id"]
    assert agent_events["links"]["events"].endswith(f"/v1/agent/jobs/{accepted['job_id']}/events")
    assert any(event["status"] == "succeeded" for event in agent_events["events"])


def test_agent_research_client_request_id_is_idempotent(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8767))

    first = app.agent_research({"query": "SearXNG", "no_network": True, "client_request_id": "req-001"})
    second = app.agent_research({"query": "SearXNG", "no_network": True, "client_request_id": "req-001"})

    assert second["job_id"] == first["job_id"]
    assert second["links"]["job"].endswith(f"/v1/agent/jobs/{first['job_id']}")
    assert len([job for job in app.jobs(limit=20)["jobs"] if job["kind"] == "search"]) == 1


def test_agent_client_request_id_is_scoped_by_kind(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8767))

    search = app.agent_research({"query": "SearXNG", "no_network": True, "client_request_id": "same-req"})
    verify = app.agent_verify({"text": "SearXNG is a metasearch engine.", "client_request_id": "same-req"})

    assert verify["job_id"] != search["job_id"]


def test_agent_contract_verify_returns_stable_job_shape(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8767))

    accepted = app.agent_verify({"text": "SearXNG is a metasearch engine."})

    assert set(accepted) == {"job_id", "status", "kind", "stage", "events", "links"}
    assert accepted["kind"] == "verify"
    final = _wait_for_job(app, accepted["job_id"])
    agent_job = app.agent_job(accepted["job_id"])
    assert final["status"] == "succeeded"
    assert agent_job["status"] == "succeeded"
    assert "result" in agent_job


def test_agent_contract_fetch_returns_stable_job_shape(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8767))

    accepted = app.agent_fetch({"urls": ["https://docs.searxng.org/"], "fetch": False})

    assert set(accepted) == {"job_id", "status", "kind", "stage", "events", "links"}
    assert accepted["kind"] == "fetch"
    final = _wait_for_job(app, accepted["job_id"])
    agent_job = app.agent_job(accepted["job_id"])
    assert final["status"] == "succeeded"
    assert agent_job["status"] == "succeeded"
    assert "result" in agent_job


def test_agent_smoke_validates_stable_http_contract() -> None:
    calls = []

    def fake_request(method, url, payload, timeout):
        calls.append((method, url, payload, timeout))
        if url.endswith("/v1/health"):
            return {"status": "ok", "api_version": "v1"}
        if url.endswith("/v1/capabilities"):
            return {
                "stable_endpoints": [
                    "POST /v1/agent/research",
                    "GET /v1/agent/jobs/{job_id}",
                    "GET /v1/agent/jobs/{job_id}/events",
                    "POST /v1/agent/jobs/{job_id}/retry",
                ]
            }
        if url.endswith("/v1/discovery"):
            return {"preferred_order": [{"interface": "mcp"}, {"interface": "http", "entrypoint": "POST /v1/agent/research"}]}
        if url.endswith("/v1/agent/research"):
            return {
                "job_id": "job_smoke",
                "status": "pending",
                "links": {
                    "job": "http://127.0.0.1:8785/v1/agent/jobs/job_smoke",
                    "events": "http://127.0.0.1:8785/v1/agent/jobs/job_smoke/events",
                    "retry": "http://127.0.0.1:8785/v1/agent/jobs/job_smoke/retry",
                },
            }
        if url.endswith("/v1/agent/jobs/job_smoke/events"):
            return {"job_id": "job_smoke", "events": [{"status": "succeeded"}]}
        if url.endswith("/v1/agent/jobs/job_smoke/retry"):
            return {
                "job_id": "job_retry",
                "status": "succeeded",
                "links": {
                    "job": "http://127.0.0.1:8785/v1/agent/jobs/job_retry",
                    "events": "http://127.0.0.1:8785/v1/agent/jobs/job_retry/events",
                    "retry": "http://127.0.0.1:8785/v1/agent/jobs/job_retry/retry",
                },
            }
        if url.endswith("/v1/agent/jobs/job_retry"):
            return {"job_id": "job_retry", "status": "succeeded", "result": {"run_id": "search_retry"}}
        if url.endswith("/v1/agent/jobs/job_smoke"):
            return {"job_id": "job_smoke", "status": "succeeded", "result": {"run_id": "search_smoke"}}
        raise AssertionError(url)

    result = run_agent_smoke("http://127.0.0.1:8785", requester=fake_request, interval_seconds=0)

    assert result["overall"] == "ok"
    assert [step["name"] for step in result["steps"]] == ["health", "capabilities", "discovery", "submit", "poll", "retry"]
    assert calls[3][2]["no_network"] is True
    assert "Agent Contract Smoke" in agent_smoke_to_markdown(result)

def test_live_contract_passes_against_short_lived_server(tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = int(server.server_address[1])
    config = ResearchConfig(
        web_host="127.0.0.1",
        web_port=port,
        search_providers=[],
        searxng_public_discovery=False,
    )
    app = ResearchWebApp(tmp_path, config=config)
    server.RequestHandlerClass = make_handler(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_contract_test(
            config,
            tmp_path,
            live=True,
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=15,
            interval_seconds=0.05,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    live_check = next(check for check in result["checks"] if check["name"] == "live_agent_smoke")
    assert result["overall"] == "ok"
    assert live_check["status"] == "ok"
    assert [step["name"] for step in live_check["details"]["steps"]][-1] == "retry"
    assert thread.is_alive() is False

def test_cli_live_contract_uses_short_lived_server_and_temp_record(tmp_path, capsys) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = int(server.server_address[1])
    config = ResearchConfig(
        data_dir=str(tmp_path),
        web_host="127.0.0.1",
        web_port=port,
        search_providers=[],
        searxng_public_discovery=False,
    )
    config_path = tmp_path / "ephemeral.config.json"
    config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    app = ResearchWebApp(tmp_path, config=config)
    server.RequestHandlerClass = make_handler(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        exit_code = cli_main(
            [
                "--config",
                str(config_path),
                "contract-test",
                "--data-dir",
                str(tmp_path),
                "--live",
                "--base-url",
                f"http://127.0.0.1:{port}",
                "--timeout",
                "15",
                "--interval",
                "0.05",
                "--format",
                "json",
            ]
        )
        output = capsys.readouterr().out
        json_start = output.find("{")
        payload = json.loads(output[json_start:])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    live_check = next(check for check in payload["checks"] if check["name"] == "live_agent_smoke")
    assert exit_code == 0, payload
    assert payload["overall"] == "ok"
    assert live_check["status"] == "ok"
    assert [step["name"] for step in live_check["details"]["steps"]][-1] == "retry"
    assert read_service_record(tmp_path)["agent_smoke"]["overall"] == "ok"
    assert thread.is_alive() is False


def test_service_registry_records_base_url_pid_and_smoke(tmp_path) -> None:
    record = write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=1234)

    assert record["service"] == "ai-research-workflow"
    assert record["base_url"] == "http://127.0.0.1:8785"
    assert record["pid"] == 1234
    assert read_service_record(tmp_path)["status"] == "running"

    updated = update_service_smoke(tmp_path, base_url="http://127.0.0.1:8785", smoke={"overall": "ok", "steps": [{"name": "health", "status": "ok"}]})

    assert updated["agent_smoke"]["overall"] == "ok"
    assert updated["agent_smoke"]["steps"] == [{"name": "health", "status": "ok"}]
    assert updated["pid"] == 1234


def test_service_registry_refreshes_started_at_for_new_process(tmp_path) -> None:
    first = write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=1234)
    original_started_at = first["started_at"]

    same_process = write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=1234)

    assert same_process["started_at"] == original_started_at

    next_process = write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=5678)

    assert next_process["pid"] == 5678
    assert next_process["started_at"] == next_process["updated_at"]


def test_service_status_reports_stopped_when_no_service_record(tmp_path) -> None:
    config = ResearchConfig(web_port=9876)
    result = run_service_status(config, tmp_path, timeout_seconds=0.1)

    assert result["status"] == "stopped"
    assert result["config"]["base_url"] == "http://127.0.0.1:9876"
    assert result["config"]["contract_version"] == CONTRACT_VERSION
    assert result["config"]["config_hash"] == config_fingerprint(config)
    assert result["running"] is False
    assert result["smoke_status"]["status"] == "no_service_record"
    assert result["dependencies"]["searxng"]["status"] in {"error", "skipped"}
    assert "Start the Web UI" in result["recommended_action"]
    assert "Research Workflow Service Status" in service_status_to_markdown(result)


def test_service_status_marks_stale_service_record(tmp_path) -> None:
    write_service_record(tmp_path, base_url="http://127.0.0.1:9877", pid=999999, extra={"contract_version": "old", "config_hash": "old"})

    result = run_service_status(ResearchConfig(web_port=9876), tmp_path, timeout_seconds=0.1)

    assert result["status"] == "config_mismatch"
    assert "service_record_base_url_mismatch" in result["stale_reasons"]
    assert "service_record_config_hash_mismatch" in result["stale_reasons"]
    assert "service_record_contract_version_mismatch" in result["stale_reasons"]
    assert result["service_record_matches_config"] is False


def test_service_status_marks_dead_pid_and_empty_port_as_stale_record(tmp_path, monkeypatch) -> None:
    import research_workflow.service_status as service_status_module

    config = ResearchConfig(web_port=9876, search_providers=[])
    write_service_record(
        tmp_path,
        base_url="http://127.0.0.1:9876",
        pid=999999,
        extra={
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(config),
        },
    )
    monkeypatch.setattr(service_status_module, "_process_exists", lambda pid: False)
    monkeypatch.setattr(service_status_module, "_can_connect", lambda host, port, timeout_seconds: False)
    monkeypatch.setattr(service_status_module, "_fetch_json", lambda url, timeout_seconds: {"ok": False, "error": "timed out"})

    result = run_service_status(config, tmp_path, timeout_seconds=0.1)

    assert result["status"] == "stale_record"
    assert "pid_not_alive" in result["stale_reasons"]
    assert "no_live_listener" in result["stale_reasons"]
    assert "health_failed" in result["stale_reasons"]


def test_service_recovery_proposal_is_read_only(tmp_path) -> None:
    pid_file = tmp_path / "web.pid"
    pid_file.write_text("999999", encoding="utf-8")
    status = {
        "status": "stale_record",
        "stale_reasons": ["pid_not_alive", "no_live_listener", "health_failed"],
        "live_listener": False,
        "service_record_matches_config": True,
        "config": {"base_url": "http://127.0.0.1:9876"},
    }

    result = build_service_recovery_proposal(status, tmp_path, ResearchConfig(web_port=9876))

    actions = [item["action"] for item in result["actions"]]
    assert result["proposal_only"] is True
    assert result["applied"] is False
    assert result["safety"] == {
        "deletes_files": False,
        "stops_processes": False,
        "starts_service": False,
        "changes_config": False,
    }
    assert "clear_stale_pid_file" in actions
    assert "mark_service_stopped" in actions
    assert "start_from_project_config" in actions
    assert pid_file.read_text(encoding="utf-8") == "999999"

def test_service_status_marks_stale_smoke_after_process_restart(tmp_path, monkeypatch) -> None:
    import research_workflow.service_status as service_status_module

    write_service_record(
        tmp_path,
        base_url="http://127.0.0.1:8785",
        pid=1234,
        extra={
            "started_at": "2026-06-22 10:00:00",
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(ResearchConfig(web_port=8785)),
            "agent_smoke": {"overall": "ok", "checked_at": "2026-06-22 09:00:00", "steps": [{"name": "health", "status": "ok"}]},
        },
    )
    monkeypatch.setattr(service_status_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(service_status_module, "_can_connect", lambda host, port, timeout_seconds: True)
    monkeypatch.setattr(service_status_module, "_fetch_json", lambda url, timeout_seconds: {"ok": True, "status": "ok"})
    monkeypatch.setattr(
        service_status_module,
        "_fetch_searxng_health",
        lambda base_url, enabled, timeout_seconds: {"name": "searxng", "enabled": enabled, "ok": True, "status": "ok", "result_count": 1},
    )

    result = run_service_status(ResearchConfig(web_port=8785), tmp_path, timeout_seconds=0.1)

    assert result["running"] is True
    assert result["status"] == "stale_smoke"
    assert result["smoke_status"]["status"] == "stale"
    assert "agent_smoke_stale" in result["stale_reasons"]


def test_service_status_marks_dependency_degraded(tmp_path, monkeypatch) -> None:
    import research_workflow.service_status as service_status_module

    config = ResearchConfig(web_port=8785, searxng_url="http://127.0.0.1:8928")
    write_service_record(
        tmp_path,
        base_url="http://127.0.0.1:8785",
        pid=1234,
        extra={
            "started_at": "2026-06-22 10:00:00",
            "contract_version": CONTRACT_VERSION,
            "config_hash": config_fingerprint(config),
            "agent_smoke": {"overall": "ok", "checked_at": "2026-06-22 10:01:00", "steps": [{"name": "health", "status": "ok"}]},
        },
    )
    monkeypatch.setattr(service_status_module, "_process_exists", lambda pid: True)
    monkeypatch.setattr(service_status_module, "_can_connect", lambda host, port, timeout_seconds: True)
    monkeypatch.setattr(service_status_module, "_fetch_json", lambda url, timeout_seconds: {"ok": True, "status": "ok"})
    monkeypatch.setattr(
        service_status_module,
        "_fetch_searxng_health",
        lambda base_url, enabled, timeout_seconds: {
            "name": "searxng",
            "enabled": enabled,
            "ok": False,
            "status": "error",
            "searxng_url": base_url,
            "error": "connection refused",
        },
    )

    result = run_service_status(config, tmp_path, timeout_seconds=0.1)

    assert result["running"] is True
    assert result["status"] == "dependency_degraded"
    assert result["dependencies"]["searxng"]["error"] == "connection refused"
    assert result["degraded_reasons"] == ["dependency_degraded:searxng"]


def test_contract_test_validates_offline_agent_contract(tmp_path) -> None:
    result = run_contract_test(load_config(None), tmp_path, live=False)

    assert result["overall"] == "ok"
    assert result["base_url"] == web_base_url(load_config(None))
    assert any(check["name"] == "integration_generation" and check["status"] == "ok" for check in result["checks"])
    assert any(check["name"] == "sdk_generation_contract" and check["status"] == "ok" for check in result["checks"])
    assert any(check["name"] == "generated_artifacts_contract" and check["status"] == "ok" for check in result["checks"])
    assert any(check["name"] == "openapi_schema_contract" and check["status"] == "ok" for check in result["checks"])
    assert any(check["name"] == "live_agent_smoke" and check["status"] == "skipped" for check in result["checks"])
    assert result["stable_contract"]["contract_version"] == CONTRACT_VERSION
    assert "research_search" in result["stable_contract"]["mcp_tools"]
    assert "Research Workflow Contract Test" in contract_test_to_markdown(result)


def test_live_contract_test_refreshes_service_smoke(tmp_path, monkeypatch) -> None:
    import research_workflow.contract_test as contract_test_module

    monkeypatch.setattr(
        contract_test_module,
        "run_agent_smoke",
        lambda *args, **kwargs: {"overall": "ok", "steps": [{"name": "health", "status": "ok"}, {"name": "poll", "status": "ok"}]},
    )

    result = run_contract_test(ResearchConfig(web_port=8785), tmp_path, live=True, base_url="http://127.0.0.1:8785")
    service_record = read_service_record(tmp_path)

    assert result["overall"] == "ok"
    assert service_record["status"] == "running"
    assert service_record["agent_smoke"]["overall"] == "ok"
    assert service_record["agent_smoke"]["steps"] == [{"name": "health", "status": "ok"}, {"name": "poll", "status": "ok"}]


def test_cli_service_status_and_contract_test_json(tmp_path, capsys) -> None:
    assert cli_main(["--data-dir", str(tmp_path), "service", "status", "--timeout", "0.1", "--format", "json"]) == 0
    service_payload = json.loads(capsys.readouterr().out)
    assert service_payload["service"] == "ai-research-workflow"
    assert "config" in service_payload
    assert "dependencies" in service_payload

    assert cli_main(["--data-dir", str(tmp_path), "contract-test", "--format", "json"]) == 0
    contract_payload = json.loads(capsys.readouterr().out)
    assert contract_payload["overall"] == "ok"
    assert contract_payload["stable_contract"]["http_endpoints"]


def test_cli_version_outputs_release_metadata(capsys) -> None:
    assert cli_main(["version", "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == "ai-research-workflow"
    assert payload["package_version"] == package_version()
    assert payload["contract_version"] == CONTRACT_VERSION
    assert payload["config_hash"]
    assert "git_commit" in payload
    assert "git_remote" in payload


def test_agent_diagnose_collects_machine_readable_context(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=8785))
    accepted = app.agent_research({"query": "SearXNG", "no_network": True, "client_request_id": "diag-req"})
    _wait_for_job(app, accepted["job_id"])
    write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=1234)

    result = run_agent_diagnose(tmp_path, ResearchConfig(web_port=8785), base_url="http://127.0.0.1:8785", include_smoke=False)

    assert result["overall"] in {"ok", "warn"}
    assert result["agent_smoke"]["overall"] == "skipped"
    assert result["service_record"]["base_url"] == "http://127.0.0.1:8785"
    assert "integration_status" in result
    assert result["package_version"] == package_version()
    assert "git_commit" in result
    assert "git_remote" in result
    assert "recommended_next_action" in result
    assert result["recent_jobs"][0]["client_request_id"] == "diag-req"
    assert "Agent Diagnose" in agent_diagnose_to_markdown(result)


def test_cli_agent_diagnose_outputs_json_without_live_smoke(tmp_path, capsys) -> None:
    write_service_record(tmp_path, base_url="http://127.0.0.1:8785", pid=1234)

    assert cli_main(["--data-dir", str(tmp_path), "agent-diagnose", "--base-url", "http://127.0.0.1:8785", "--skip-smoke", "--format", "json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_smoke"]["overall"] == "skipped"
    assert payload["base_url"] == "http://127.0.0.1:8785"
    assert "integration_status" in payload
    assert payload["recommended_next_action"]


def test_web_main_uses_runtime_port_for_agent_links(monkeypatch, tmp_path) -> None:
    from research_workflow import web as web_module

    captured = {}

    class FakeServer:
        def __init__(self, address, handler):
            captured["address"] = address

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr(web_module, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(web_module, "make_handler", lambda app: captured.setdefault("base_url", web_module._base_url_from_config(app.config)) or object)

    result = web_module.main(["--host", "127.0.0.1", "--port", "8785", "--data-dir", str(tmp_path)])

    assert result == 0
    assert captured["address"] == ("127.0.0.1", 8785)
    assert captured["base_url"] == "http://127.0.0.1:8785"
    service = read_service_record(tmp_path)
    assert service["base_url"] == "http://127.0.0.1:8785"
    assert service["status"] == "running"
    assert service["contract_version"] == CONTRACT_VERSION
    assert service["config_hash"]


def test_sqlite_store_sync_supports_dedupe_and_reverse_lookup(tmp_path) -> None:
    source = Source(
        id="src_demo",
        url="https://example.com/demo",
        title="Demo",
        retrieved_at="2025-01-01T00:00:00+00:00",
    )
    evidence = Evidence(
        id="ev_demo",
        source_id="src_demo",
        quote_or_paraphrase="SearXNG is a metasearch engine.",
        support_direction=Verdict.SUPPORTED,
        confidence=0.9,
    )
    claim = {
        "id": "claim_demo",
        "text": "SearXNG is a metasearch engine.",
        "claim_type": "factual",
        "risk_level": "normal",
        "verdict": "supported",
        "confidence": 0.9,
        "evidence_ids": ["ev_demo"],
    }
    from research_workflow.store import append_jsonl

    append_jsonl(tmp_path / "sources.jsonl", [source, {**source.to_dict(), "id": "src_duplicate"}])
    append_jsonl(tmp_path / "evidence.jsonl", [evidence, {**evidence.to_dict(), "id": "ev_duplicate"}])
    append_jsonl(tmp_path / "claims.jsonl", [claim])

    summary = sync_jsonl_to_sqlite(tmp_path)

    assert summary["counts"]["sources"] == 1
    assert summary["counts"]["evidence"] == 1
    assert find_source_by_url(tmp_path, "https://example.com/demo")["title"] == "Demo"
    assert find_evidence_by_hash(tmp_path, "SearXNG is a metasearch engine.")["source_id"] == "src_demo"
    assert evidence_for_claim(tmp_path, "claim_demo")[0]["quote_or_paraphrase"] == "SearXNG is a metasearch engine."
    assert stale_sources(tmp_path, days=30)


def test_search_pipeline_updates_sqlite_store_by_default(tmp_path) -> None:
    result = run_search_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        urls=["https://docs.searxng.org/"],
        auto_verify=True,
    )

    summary = sqlite_summary(tmp_path)

    assert summary["counts"]["sources"] == len(result.sources)
    assert summary["counts"]["evidence"] == len(result.evidence)
    assert summary["counts"]["runs"] >= 1


def test_web_ingest_updates_sqlite_store_by_default(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig())

    app.ingest({"urls": ["https://docs.searxng.org/"], "fetch": False})

    summary = sqlite_summary(tmp_path)
    assert summary["counts"]["sources"] == 1


def _wait_for_job(app: ResearchWebApp, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = app.job(job_id)
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")


def test_plan_query_academic_mode_adds_academic_targets() -> None:
    plan = plan_query("FActScore RARR FacTool 方法差异", "academic")
    assert plan.mode == "academic"
    assert any("Scholar" in target for target in plan.source_targets)
    assert plan.en_keywords


def test_plan_query_auto_detects_tool_selection_without_factcheck_pollution() -> None:
    plan = plan_query("AI agent inbox API webhook 自定义字段 依赖关系 看板 表格", "auto")
    assert plan.mode == "tool_selection"
    assert any("Webhook" in target or "API" in target for target in plan.source_targets)
    keyword_text = " ".join([*plan.zh_keywords, *plan.en_keywords]).lower()
    assert "fact checking" not in keyword_text
    assert "事实核查" not in keyword_text


def test_tool_selection_queries_do_not_inject_deep_research_factcheck_seeds() -> None:
    plan = plan_query("open source task management API webhook custom fields dependencies kanban table", "auto")
    specs = planned_search_query_specs(plan)
    queries = [spec.query for spec in specs]
    joined = " ".join(queries).lower()
    assert plan.mode == "tool_selection"
    assert any("vikunja" in query.lower() for query in queries)
    assert any("openproject" in query.lower() for query in queries)
    assert "factscore" not in joined
    assert "facTool".lower() not in joined
    assert "browsecomp-plus" not in joined


def test_domain_safety_queries_do_not_inject_project_inventory() -> None:
    plan = plan_query("老人家会干出哪些意想不到的傻事，怎么预防", "daily")
    specs = planned_search_query_specs(plan)
    joined = " ".join([*plan.zh_keywords, *plan.en_keywords, *(spec.query for spec in specs)]).lower()

    assert plan.mode == "daily"
    assert any("public-health" in target or "medical" in target for target in plan.source_targets)
    assert "github" not in joined
    assert "open source" not in joined
    assert "开源" not in joined
    assert "factscore" not in joined
    assert "openfactverification" not in joined


def test_domain_safety_search_pipeline_skips_factcheck_followups(tmp_path) -> None:
    result = run_search_pipeline(
        topic="老年人 意外行为 风险 预防 照护 诈骗 跌倒 走失 用药 火灾",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
    )
    queries = " ".join(item["query"] for item in result.search_queries).lower()

    assert "github" not in queries
    assert "factscore" not in queries
    assert "openfactverification" not in queries
    assert "official guidance" in queries


def test_tool_selection_matrix_recognizes_task_api_docs() -> None:
    source = Source(
        url="https://vikunja.io/help/webhooks",
        title="Webhooks | Vikunja",
        source_type=SourceType.OFFICIAL,
        publisher="vikunja.io",
        snippet="Vikunja supports webhooks, task relations, priorities, kanban, and API automation.",
    )
    rows = build_tool_matrix([source])
    assert rows
    assert rows[0]["type"] == "API and automation docs"
    assert rows[0]["recommendation"] == "candidate to integrate"


def test_tool_selection_filters_unrelated_github_noise(tmp_path) -> None:
    result = run_search_pipeline(
        topic="AI agent inbox API webhook 自定义字段 依赖关系 看板 表格",
        data_dir=tmp_path,
        mode="auto",
        no_network=True,
        urls=[
            "https://github.com/githubdaily/githubdaily",
            "https://vikunja.io/help/webhooks",
            "https://www.openproject.org/docs/user-guide/work-packages/work-package-relations-hierarchies/",
        ],
    )
    urls = [source.url for source in result.sources]
    assert "https://github.com/githubdaily/githubdaily" not in urls
    assert any("vikunja.io" in url for url in urls)
    assert any("openproject.org" in url for url in urls)


def test_config_file_and_environment_overrides(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "research.config.json"
    config_path.write_text(
        json.dumps({"data_dir": "from-config", "max_results": 7, "fetch_urls": True, "web_port": 9000}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_MAX_RESULTS", "3")
    monkeypatch.setenv("RESEARCH_SEARCH_PROVIDERS", "searxng,brave")
    config = load_config(config_path)
    assert config.data_dir == "from-config"
    assert config.max_results == 3
    assert config.fetch_urls is True
    assert config.web_port == 9000
    assert config.search_providers == ["searxng", "brave"]


def test_web_base_url_uses_configured_host_and_port() -> None:
    config = ResearchConfig(web_host="127.0.0.1", web_port=9777)
    assert web_base_url(config) == "http://127.0.0.1:9777"




def test_write_default_config_refuses_overwrite(tmp_path) -> None:
    config_path = write_default_config(tmp_path / "research.config.json")
    assert config_path.exists()
    try:
        write_default_config(config_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected existing config to be preserved")


def test_cli_config_controls_default_data_dir(tmp_path) -> None:
    config_path = tmp_path / "research.config.json"
    data_dir = tmp_path / "configured-data"
    config_path.write_text(json.dumps({"data_dir": str(data_dir)}), encoding="utf-8")
    exit_code = cli_main(
        [
            "--config",
            str(config_path),
            "run",
            "SearXNG",
            "--no-network",
            "--evidence",
            "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    assert (data_dir / "runs.jsonl").exists()


def test_cli_global_data_dir_is_not_shadowed_by_subcommand(tmp_path) -> None:
    data_dir = tmp_path / "global-data-dir"
    exit_code = cli_main(
        [
            "--data-dir",
            str(data_dir),
            "run",
            "SearXNG",
            "--no-network",
            "--evidence",
            "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    assert (data_dir / "runs.jsonl").exists()


def test_cli_search_runs_search_only_pipeline(tmp_path, capsys) -> None:
    data_dir = tmp_path / "search-data"
    assert (
        cli_main(
            [
                "--data-dir",
                str(data_dir),
                "search",
                "SearXNG",
                "--no-network",
                "--url",
                "https://docs.searxng.org/",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["kind"] == "search"
    assert output["run_id"].startswith("search_")
    assert output["claims"] == []
    assert output["sources"]
    assert (data_dir / "search_runs.jsonl").exists()


def test_cli_ingest_records_source_provenance(tmp_path, capsys) -> None:
    data_dir = tmp_path / "ingest-data"
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("https://docs.searxng.org/\n", encoding="utf-8")
    assert cli_main(["--data-dir", str(data_dir), "ingest", str(urls_file)]) == 0
    capsys.readouterr()
    sources = [json.loads(line) for line in (data_dir / "sources.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert sources[0]["search_buckets"] == ["manual URLs"]
    assert sources[0]["search_stages"] == ["cli_ingest"]


def test_cli_pairs_evidence_urls_with_manual_evidence(tmp_path) -> None:
    data_dir = tmp_path / "risk-data"
    exit_code = cli_main(
        [
            "--data-dir",
            str(data_dir),
            "run",
            "SearXNG risk",
            "--no-network",
            "--evidence",
            "SearXNG released version 2024.5 in 2024.",
            "--evidence-url",
            "https://example.com/source-a",
            "--evidence",
            "SearXNG released version 2024.5 in 2024.",
            "--evidence-url",
            "https://example.com/source-b",
            "--verify-text",
            "SearXNG released version 2024.5 in 2024.",
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    run = list_runs(data_dir)[0]
    assert run["supported_count"] == 1


def test_cli_lists_and_shows_runs(tmp_path, capsys) -> None:
    data_dir = tmp_path / "run-history"
    assert (
        cli_main(
            [
                "--data-dir",
                str(data_dir),
                "run",
                "SearXNG",
                "--no-network",
                "--evidence",
                "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                "--format",
                "json",
            ]
        )
        == 0
    )
    run_id = list_runs(data_dir)[0]["id"]
    assert cli_main(["--data-dir", str(data_dir), "runs", "--format", "json"]) == 0
    runs_output = capsys.readouterr().out
    assert run_id in runs_output
    output_path = tmp_path / "shown.md"
    assert cli_main(["--data-dir", str(data_dir), "show", run_id, "--output", str(output_path)]) == 0
    assert output_path.exists()
    assert "Claim check complete" in output_path.read_text(encoding="utf-8")


def test_cli_exports_run_to_obsidian_vault(tmp_path, capsys) -> None:
    data_dir = tmp_path / "run-history"
    vault = tmp_path / "vault"
    assert (
        cli_main(
            [
                "--data-dir",
                str(data_dir),
                "run",
                "SearXNG",
                "--no-network",
                "--evidence",
                "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            ]
        )
        == 0
    )
    run_id = list_runs(data_dir)[0]["id"]
    assert cli_main(["--data-dir", str(data_dir), "obsidian", run_id, "--vault", str(vault), "--folder", "AI Research"]) == 0
    output = capsys.readouterr().out
    assert "Wrote Obsidian note" in output
    notes = list((vault / "AI Research").glob("*.md"))
    assert len(notes) == 1
    text = notes[0].read_text(encoding="utf-8")
    assert "run_id:" in text
    assert "tags:" in text
    assert "# SearXNG" in text
    assert "## Research Report" in text
    assert "## Source Table" in text
    assert "## Unverified And Disputed Claims" in text
    assert "evidence_quality:" in text
    assert "weak_claim_count:" in text
    assert "reference_check_overall:" in text
    assert "## Evidence Quality Review" in text
    assert "## Claim Deepen Suggestions" in text
    assert "## References Check" in text
    assert "## Next Search Suggestions" in text


def test_cli_audits_run(tmp_path, capsys) -> None:
    data_dir = tmp_path / "run-audit"
    assert (
        cli_main(
            [
                "--data-dir",
                str(data_dir),
                "run",
                "SearXNG",
                "--no-network",
                "--evidence",
                "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            ]
        )
        == 0
    )
    run_id = list_runs(data_dir)[0]["id"]
    assert cli_main(["--data-dir", str(data_dir), "audit", run_id, "--format", "json"]) == 0
    output = capsys.readouterr().out
    assert '"overall": "ok"' in output
    assert '"high_risk_policy"' in output


def test_cli_quality_deepen_claims_and_references(tmp_path, capsys) -> None:
    result = run_research_pipeline(
        topic="FActScore",
        data_dir=tmp_path,
        mode="academic",
        no_network=True,
        text_to_verify="FActScore was introduced by a 2099 Nature paper.",
    )

    assert cli_main(["--data-dir", str(tmp_path), "quality", result.run_id, "--format", "json"]) == 0
    quality = json.loads(capsys.readouterr().out)
    assert "quality_summary" in quality

    assert cli_main(["--data-dir", str(tmp_path), "deepen-claims", result.run_id, "--max-queries", "2", "--format", "json"]) == 0
    deepen = json.loads(capsys.readouterr().out)
    assert deepen["deepen_suggestions"]

    refs_path = tmp_path / "refs.md"
    refs_path.write_text("See doi:10.1145/3366423.3380124 and arXiv:9913.00001.", encoding="utf-8")
    assert cli_main(["--data-dir", str(tmp_path), "references", str(refs_path), "--format", "json"]) == 0
    refs = json.loads(capsys.readouterr().out)
    assert refs["validation_summary"]["counts"]["not_found"] >= 1


def test_cli_run_json_returns_full_result_with_audit(tmp_path, capsys) -> None:
    data_dir = tmp_path / "run-json"
    assert (
        cli_main(
            [
                "--data-dir",
                str(data_dir),
                "run",
                "SearXNG",
                "--no-network",
                "--evidence",
                "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"]
    assert output["audit"]["overall"] == "ok"
    assert output["report_json"]


def test_cli_selftest_runs_end_to_end(tmp_path, capsys) -> None:
    assert cli_main(["selftest", "--format", "json", "--work-dir", str(tmp_path / "selftest")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["overall"] == "ok"
    names = {check["name"] for check in output["checks"]}
    assert {"pipeline", "audit", "obsidian_export", "mcp_tools", "mcp_run"}.issubset(names)


def test_evals_cover_acceptance_scenarios(tmp_path) -> None:
    result = run_evals(load_config(None), tmp_path / "evals")
    assert result["overall"] == "ok"
    assert result["case_count"] >= 20
    cases = {case["name"]: case for case in result["cases"]}
    assert cases["daily_tool_research"]["claim_coverage"] >= 0.8
    assert cases["academic_factcheck_methods"]["claim_coverage"] >= 0.8
    hallucination = cases["hallucination_trigger"]
    verdicts = {claim["text"]: claim["verdict"] for claim in hallucination["claims"]}
    assert verdicts["Gigablast is still one of the best independent search engines in 2026."] == "contradicted"
    assert verdicts["Mojeek does not accept advertising and is purely public-interest operated."] == "not_found"
    revisions = {item["claim_text"]: item["action"] for item in cases["revision_suggestions"]["revision_suggestions"]}
    assert revisions["Gigablast is still active in 2026."] == "replace"
    assert revisions["UnknownSearch has 99% market share in 2026."] == "needs_more_sources"
    assert "Research Workflow Evals" in evals_to_markdown(result)


def test_cli_evals_runs_acceptance_suite(tmp_path, capsys) -> None:
    assert cli_main(["evals", "--format", "json", "--work-dir", str(tmp_path / "evals")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["overall"] == "ok"
    assert output["case_count"] >= 20


def test_readiness_runs_complete_local_report(tmp_path) -> None:
    result = run_readiness(load_config(None), tmp_path / "readiness")
    assert result["overall"] == "ok"
    assert result["ready"] is True
    assert result["package_version"] == package_version()
    assert "git_commit" in result
    assert "git_remote" in result
    assert result["contract_version"] == CONTRACT_VERSION
    assert result["config_hash"]
    assert result["recommended_next_action"]
    names = {check["name"] for check in result["checks"]}
    assert {"doctor", "selftest", "evals", "integrations", "agent_contract", "web_launcher"}.issubset(names)
    agent_contract = next(check for check in result["checks"] if check["name"] == "agent_contract")
    assert agent_contract["status"] == "ok"
    assert agent_contract["details"]["contract_version"] == CONTRACT_VERSION
    assert "Research Workflow Readiness" in readiness_to_markdown(result)


def test_cli_readiness_runs(tmp_path, capsys) -> None:
    assert cli_main(["readiness", "--format", "json", "--work-dir", str(tmp_path / "readiness")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["overall"] == "ok"
    assert output["ready"] is True
    assert output["contract_version"] == CONTRACT_VERSION
    assert output["recommended_next_action"]


def test_cli_readiness_skip_slow_warns(tmp_path, capsys) -> None:
    assert cli_main(["readiness", "--format", "json", "--work-dir", str(tmp_path / "readiness"), "--skip-slow"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["overall"] == "warn"
    assert output["ready"] is False
    assert output["contract_version"] == CONTRACT_VERSION
    assert output["recommended_next_action"]
    doctor = next(check for check in output["checks"] if check["name"] == "doctor")
    assert doctor["status"] == "ok"
    assert "optional provider" in doctor["message"]
    agent_contract = next(check for check in output["checks"] if check["name"] == "agent_contract")
    assert agent_contract["status"] == "ok"


def test_cli_writes_integration_templates(tmp_path, capsys) -> None:
    output_dir = tmp_path / "integrations"
    assert cli_main(["integrations", "--output", str(output_dir), "--base-url", "http://127.0.0.1:8774", "--format", "json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["base_url"] == "http://127.0.0.1:8774"
    assert (output_dir / "openwebui_research_workflow_tool.py").exists()
    assert (output_dir / "research-workflow-client.mjs").exists()
    assert (output_dir / "research_workflow_client.py").exists()
    assert (output_dir / "research-workflow.integration.json").exists()
    assert (output_dir / "openclaw-research-workflow.prompt.md").exists()
    assert (output_dir / "hermes-research-workflow.prompt.md").exists()
    assert (output_dir / "research-workflow-bookmarklet.md").exists()
    assert (output_dir / "n8n-research-workflow.json").exists()
    assert (output_dir / "node-red-research-workflow.json").exists()
    assert (output_dir / "dify-openapi-tool.md").exists()
    assert (output_dir / "AGENT_DISCOVERY.md").exists()
    assert (output_dir / "agent-discovery.json").exists()
    assert "/api/selftest" in (output_dir / "openwebui_research_workflow_tool.py").read_text(encoding="utf-8")
    manifest = json.loads((output_dir / "research-workflow.integration.json").read_text(encoding="utf-8"))
    assert manifest["preferred_entrypoint"] == "POST /v1/agent/research"
    assert manifest["endpoints"]["discovery"]["path"] == "/v1/discovery"
    assert "research_search" in manifest["recommended_mcp_tools"]
    assert "search_queries/search_buckets" in manifest["response_contract"]["sources"]
    assert manifest["clients"]["python"] == "research_workflow_client.py"
    assert manifest["config_hash"]
    assert manifest["sdk_contract"]["expected_contract_version"] == CONTRACT_VERSION
    assert "contract_mismatch" in manifest["sdk_contract"]["error_categories"]
    assert manifest["automation_adapters"]["n8n"] == "n8n-research-workflow.json"
    assert manifest["agent_discovery"]["endpoint"] == "GET /v1/discovery"
    assert manifest["agent_discovery"]["json"] == "agent-discovery.json"
    python_client = (output_dir / "research_workflow_client.py").read_text(encoding="utf-8")
    assert "class ResearchWorkflowClient" in python_client
    assert "/v1/search" in python_client
    assert "def agent_research" in python_client
    assert "/v1/agent/research" in python_client
    assert "def wait_for_agent_job" in python_client
    assert "def assert_contract" in python_client
    assert "def failure_details" in python_client
    assert "ResearchWorkflowError" in python_client
    assert "contract_mismatch" in python_client
    assert "job_failed" in python_client
    assert "client_request_id" in python_client
    assert "/v1/agent/jobs/" in python_client
    js_client = (output_dir / "research-workflow-client.mjs").read_text(encoding="utf-8")
    assert "agentResearch" in js_client
    assert "waitForAgentJob" in js_client
    assert "assertContract" in js_client
    assert "failureDetails" in js_client
    assert "ResearchWorkflowError" in js_client
    assert "contract_mismatch" in js_client
    assert "job_failed" in js_client
    assert "client_request_id" in js_client
    assert "/v1/agent/research" in js_client
    n8n_workflow = json.loads((output_dir / "n8n-research-workflow.json").read_text(encoding="utf-8"))
    assert n8n_workflow["name"] == "AI Research Workflow Search"
    assert any(node["type"] == "n8n-nodes-base.httpRequest" for node in n8n_workflow["nodes"])
    node_red_flow = json.loads((output_dir / "node-red-research-workflow.json").read_text(encoding="utf-8"))
    assert any(node.get("type") == "http request" for node in node_red_flow)
    dify_doc = (output_dir / "dify-openapi-tool.md").read_text(encoding="utf-8")
    assert "/v1/openapi.json" in dify_doc
    assert "researchSearch" in dify_doc
    discovery = json.loads((output_dir / "agent-discovery.json").read_text(encoding="utf-8"))
    assert discovery["config_hash"]
    assert discovery["sdk_contract"]["expected_contract_version"] == CONTRACT_VERSION
    assert discovery["preferred_order"][0]["interface"] == "mcp"
    assert "research_search" in discovery["preferred_order"][0]["tools"]
    assert any("search_queries" in item for item in discovery["evidence_policy"])
    assert discovery["runtime_contract"]["online_status"] == "unknown"
    assert discovery["runtime_contract"]["configured_endpoint_only"] is True
    assert discovery["result_contract"]["schema"] == "research.search-result.v1"
    assert set(discovery["result_contract"]["shared_by"]) == {"cli", "http", "mcp", "watch_folder"}
    assert discovery["evidence_bundle_reference"]["facade"] == "evidence-bundle.v1"
    assert discovery["evidence_bundle_reference"]["candidate_promoted"] is False
    assert discovery["evidence_bundle_reference"]["service_required"] is False
    discovery_doc = (output_dir / "AGENT_DISCOVERY.md").read_text(encoding="utf-8")
    assert "Interface Priority" in discovery_doc
    assert "Evidence Policy" in discovery_doc
    assert "search_buckets" in discovery_doc
    assert "/v1/discovery" in discovery_doc
    assert "configured endpoint, not proof" in discovery_doc
    assert "research.search-result.v1" in discovery_doc
    assert "QRP Evidence-Bundle Reference Boundary" in discovery_doc
    openclaw_prompt = (output_dir / "openclaw-research-workflow.prompt.md").read_text(encoding="utf-8")
    assert "research_deepen" in openclaw_prompt
    assert "/v1/openapi.json" in openclaw_prompt
    assert ".research-jobs/inbox" in openclaw_prompt
    bookmarklet_doc = (output_dir / "research-workflow-bookmarklet.md").read_text(encoding="utf-8")
    assert "javascript:" in bookmarklet_doc
    assert "/v1/ingest" in bookmarklet_doc
    assert "Research Ingest" in bookmarklet_doc






def test_bootstrap_initializes_and_preserves_existing_files(tmp_path) -> None:
    config = load_config(None)
    result = run_bootstrap(
        config=config,
        data_dir=tmp_path / "data",
        config_path=tmp_path / "research.config.json",
        integrations_dir=tmp_path / "integrations",
        base_url="http://127.0.0.1:8774",
    )
    assert result["overall"] == "ok"
    assert result["config"]["created"] is True
    assert (tmp_path / "integrations" / "openwebui_research_workflow_tool.py").exists()
    assert result["selftest"]["overall"] == "ok"
    markdown = bootstrap_to_markdown(result)
    assert "Research Workflow Bootstrap" in markdown

    preserved = run_bootstrap(
        config=config,
        data_dir=tmp_path / "data-2",
        config_path=tmp_path / "research.config.json",
        integrations_dir=tmp_path / "integrations-2",
        base_url="http://127.0.0.1:8774",
        skip_selftest=True,
    )
    assert preserved["config"]["preserved"] is True
    assert preserved["selftest"] is None


def test_cli_bootstrap_runs(tmp_path, capsys) -> None:
    assert (
        cli_main(
            [
                "--data-dir",
                str(tmp_path / "data"),
                "bootstrap",
                "--config-path",
                str(tmp_path / "research.config.json"),
                "--integrations-dir",
                str(tmp_path / "integrations"),
                "--base-url",
                "http://127.0.0.1:8774",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["overall"] == "ok"
    assert output["selftest"]["overall"] == "ok"
    assert (tmp_path / "integrations" / "research-workflow-client.mjs").exists()


def test_web_launcher_builds_and_reports_dry_run(tmp_path) -> None:
    config = load_config(None)
    command = web_command(config, tmp_path, host="127.0.0.1", port=8774)
    assert command[:3] == [command[0], "-m", "research_workflow.web"]
    result = start_web(tmp_path, config, host="127.0.0.1", port=8774, dry_run=True)
    assert result["dry_run"] is True
    assert result["url"] == "http://127.0.0.1:8774"
    assert "--data-dir" in result["command"]
    markdown = launcher_to_markdown(result)
    assert "Research Workflow Web Launcher" in markdown


def test_web_launcher_status_and_stop_use_pid_file(tmp_path, monkeypatch) -> None:
    config = load_config(None)
    paths = tmp_path
    (paths / "web.pid").write_text("12345", encoding="utf-8")
    monkeypatch.setattr("research_workflow.launcher._process_exists", lambda pid: pid == 12345)
    monkeypatch.setattr("research_workflow.launcher._url_listens", lambda url: True)
    monkeypatch.setattr("research_workflow.launcher._health_ok", lambda url: True)
    killed: list[int] = []
    monkeypatch.setattr("research_workflow.launcher.os.kill", lambda pid, sig: killed.append(pid))
    status = web_status(tmp_path, config)
    assert status["running"] is True
    assert status["status"] == "running"
    stopped = stop_web(tmp_path, config)
    assert stopped["stopped"] is True
    assert stopped["status"] == "stopped"
    assert killed == [12345]
    assert read_service_record(tmp_path)["status"] == "stopped"


def test_service_status_treats_stopped_service_record_as_stopped(tmp_path) -> None:
    write_service_record(tmp_path, base_url="http://127.0.0.1:9876", pid=12345, status="stopped")

    result = run_service_status(ResearchConfig(web_port=9876), tmp_path, timeout_seconds=0.1)

    assert result["status"] == "stopped"
    assert result["stale_reasons"] == []


def test_web_launcher_marks_pid_only_status_as_stale(tmp_path, monkeypatch) -> None:
    config = ResearchConfig(web_port=9876)
    (tmp_path / "web.pid").write_text("12345", encoding="utf-8")
    monkeypatch.setattr("research_workflow.launcher._process_exists", lambda pid: pid == 12345)
    monkeypatch.setattr("research_workflow.launcher._url_listens", lambda url: False)
    monkeypatch.setattr("research_workflow.launcher._health_ok", lambda url: False)

    status = web_status(tmp_path, config)

    assert status["running"] is False
    assert status["status"] == "stale"
    assert "no_live_listener" in status["stale_reasons"]
    assert "health_failed" in status["stale_reasons"]


def test_cli_web_start_dry_run(tmp_path, capsys) -> None:
    assert (
        cli_main(
            [
                "--data-dir",
                str(tmp_path),
                "web",
                "start",
                "--host",
                "127.0.0.1",
                "--port",
                "8774",
                "--dry-run",
                "--format",
                "json",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["dry_run"] is True
    assert output["url"] == "http://127.0.0.1:8774"


def test_integration_manifest_and_rest_client() -> None:
    manifest = integration_manifest("http://127.0.0.1:8774")
    assert manifest["endpoints"]["run"]["path"] == "/api/run"
    assert manifest["endpoints"]["selftest"]["path"] == "/api/selftest"
    assert "avg_seconds" in manifest["response_contract"]["provider_health"]
    client = render_rest_client("http://127.0.0.1:8774")
    assert "runResearchWorkflow" in client
    assert "/api/run" in client


def test_doctor_reports_local_pipeline_and_mcp(tmp_path) -> None:
    config = load_config(None)
    result = run_doctor(config, tmp_path)
    checks = {check["name"]: check for check in result["checks"]}
    assert result["overall"] in {"ok", "warn"}
    assert checks["data_dir"]["status"] == "ok"
    assert checks["mcp_tools"]["status"] == "ok"
    assert checks["pipeline"]["status"] == "ok"
    provider_details = checks["search_providers"]["details"]
    assert "effective" in provider_details
    assert "disabled_unconfigured" in provider_details


def test_cli_doctor_uses_requested_data_dir(tmp_path) -> None:
    data_dir = tmp_path / "doctor-data"
    exit_code = cli_main(["--data-dir", str(data_dir), "doctor", "--format", "json", "--skip-pipeline"])
    assert exit_code == 0
    assert data_dir.exists()


def test_source_type_inference() -> None:
    assert str(infer_source_type("https://github.com/shmsw25/factscore")) == "project"
    assert str(infer_source_type("https://arxiv.org/abs/2305.14251")) == "academic"
    assert source_from_url("https://docs.searxng.org/").publisher == "docs.searxng.org"


def test_parse_page_extracts_metadata_and_clean_text() -> None:
    page = parse_page(
        """
        <html>
          <head>
            <title>Example Research Page</title>
            <meta name="description" content="SearXNG is a metasearch engine for auditable search." />
            <meta property="article:published_time" content="2024-05-01" />
            <script>ignore me</script>
          </head>
          <body>
            <p>SearXNG is a free internet metasearch engine which aggregates results from many search services.</p>
            <p>Users are neither tracked nor profiled.</p>
          </body>
        </html>
        """
    )
    assert page.title == "Example Research Page"
    assert page.description == "SearXNG is a metasearch engine for auditable search."
    assert page.published_at == "2024-05-01"
    assert "ignore me" not in page.text
    assert "aggregates results" in page.text


def test_parse_page_filters_navigation_and_cookie_noise() -> None:
    page = parse_page(
        """
        <html>
          <body>
            <nav>Home Pricing Login</nav>
            <aside class="sidebar">Subscribe to updates</aside>
            <div class="content">
              <p>SearXNG is a free internet metasearch engine which aggregates results from many search services.</p>
              <p>The project provides a privacy-respecting search interface and can be self-hosted locally.</p>
            </div>
            <footer>Cookie settings and site map</footer>
          </body>
        </html>
        """
    )
    assert "Home Pricing Login" not in page.text
    assert "Cookie settings" not in page.text
    assert "privacy-respecting search interface" in page.text


def test_fetch_style_extraction_enriches_source_and_splits_evidence() -> None:
    source = source_from_url("https://docs.searxng.org/")
    page = parse_page(
        """
        <html>
          <head>
            <title>SearXNG Documentation</title>
            <meta name="description" content="SearXNG is a free internet metasearch engine." />
          </head>
          <body>
            <p>SearXNG is a free internet metasearch engine which aggregates results from many search services.</p>
            <p>SearXNG provides a privacy-respecting search interface for users.</p>
          </body>
        </html>
        """
    )
    from research_workflow.evidence import enrich_source_from_page, evidence_snippets

    enrich_source_from_page(source, page)
    snippets = evidence_snippets(page)
    assert source.title == "SearXNG Documentation"
    assert source.snippet == "SearXNG is a free internet metasearch engine."
    assert len(snippets) >= 2
    assert any("aggregates results" in snippet for snippet in snippets)


def test_extract_evidence_keeps_manual_snippet_path() -> None:
    source = source_from_url("https://docs.searxng.org/")
    source.snippet = "SearXNG is a free internet metasearch engine which aggregates results from many search services."
    evidence = extract_evidence_from_sources([source], fetch=False)
    assert len(evidence) == 1
    assert evidence[0].source_id == source.id
    assert "aggregates results" in evidence[0].quote_or_paraphrase
    assert evidence[0].extraction_method == "search_snippet"
    assert evidence[0].source_quote_type == "snippet"
    assert evidence[0].text_length > 40


def test_personal_intelligence_desk_reader_adapter_reuses_monitor_sites(tmp_path, monkeypatch) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "monitor_sites.py").write_text(
        """
def fetch_page_for_monitor(url, monitor, errors, timeout=15):
    assert monitor["web_access"]["enabled"] is True
    return b"<html><main>PID extracted article text for auditable evidence.</main></html>", "text/html", url + "?final", "direct"

def decode(data):
    return data.decode("utf-8")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PERSONAL_INTELLIGENCE_DESK_PATH", str(tmp_path))
    text, content_type, final_url, method = fetch_via_personal_intelligence_desk("https://example.com/a")
    assert "PID extracted article text" in text
    assert content_type == "text/html"
    assert final_url.endswith("?final")
    assert method == "personal_intelligence_desk:direct"
    sys.modules.pop("monitor_sites", None)


def test_web_content_fetcher_reader_adapter_reuses_shared_package(tmp_path, monkeypatch) -> None:
    package = tmp_path / "web_content_fetcher"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class FetchRequest:
    url: str
    timeout_seconds: int = 20
    use_web_access: bool = False
    web_access_base_url: str = ""
    prefer_browser: bool = False
    archive_dir: str = ""

class FetchResult:
    status = "ok"
    transport = "direct"
    content_type = "text/html"
    final_url = "https://example.com/final"
    extracted_text = "Shared web-content-fetcher extracted text for evidence."
    raw_snapshot = ""
    errors = []

def fetch_content(request):
    assert request.url == "https://example.com/a"
    return FetchResult()
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WEB_CONTENT_FETCHER_PATH", str(tmp_path))
    text, content_type, final_url, method = fetch_via_web_content_fetcher("https://example.com/a")
    assert "Shared web-content-fetcher extracted text" in text
    assert content_type == "text/html"
    assert final_url == "https://example.com/final"
    assert method == "web_content_fetcher:direct"


def test_web_content_fetcher_reader_prefers_installed_package(tmp_path, monkeypatch) -> None:
    package = tmp_path / "web_content_fetcher"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from dataclasses import dataclass

@dataclass
class FetchRequest:
    url: str
    timeout_seconds: int = 20
    use_web_access: bool = False
    web_access_base_url: str = ""
    prefer_browser: bool = False
    archive_dir: str = ""

class FetchResult:
    status = "ok"
    transport = "package"
    content_type = "text/plain"
    final_url = "https://example.com/pkg"
    extracted_text = "Installed package reader text."
    raw_snapshot = ""
    errors = []

def fetch_content(request):
    return FetchResult()
""",
        encoding="utf-8",
    )
    for name in list(sys.modules):
        if name == "web_content_fetcher" or name.startswith("web_content_fetcher."):
            sys.modules.pop(name, None)
    monkeypatch.delenv("WEB_CONTENT_FETCHER_PATH", raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))

    text, content_type, final_url, method = fetch_via_web_content_fetcher("https://example.com/a")

    assert text == "Installed package reader text."
    assert content_type == "text/plain"
    assert final_url == "https://example.com/pkg"
    assert method == "web_content_fetcher:package"


def test_web_content_fetcher_reader_is_strict_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_READER_PROVIDER", "web_content_fetcher")
    monkeypatch.setenv("WEB_CONTENT_FETCHER_PATH", str(tmp_path / "missing-wcf"))
    called_fallback = False

    def fake_fetch_page_bytes(*args, **kwargs):
        nonlocal called_fallback
        called_fallback = True
        return "<html>fallback</html>", "text/html", "https://example.com", "direct"

    monkeypatch.setattr("research_workflow.evidence.fetch_page_bytes", fake_fetch_page_bytes)
    try:
        fetch_page("https://example.com/a")
    except RuntimeError as exc:
        assert "strict_reader_failed" in str(exc)
        assert "web_content_fetcher_not_found" in str(exc)
    else:
        raise AssertionError("strict web-content-fetcher reader should fail instead of falling back")
    assert called_fallback is False


def test_personal_intelligence_desk_reader_is_strict_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_READER_PROVIDER", "personal_intelligence_desk")
    monkeypatch.setenv("PERSONAL_INTELLIGENCE_DESK_PATH", str(tmp_path / "missing-pid"))
    called_fallback = False

    def fake_fetch_page_bytes(*args, **kwargs):
        nonlocal called_fallback
        called_fallback = True
        return "<html>fallback</html>", "text/html", "https://example.com", "direct"

    monkeypatch.setattr("research_workflow.evidence.fetch_page_bytes", fake_fetch_page_bytes)
    try:
        fetch_page("https://example.com/a")
    except RuntimeError as exc:
        assert "strict_reader_failed" in str(exc)
        assert "personal_intelligence_desk_not_found" in str(exc)
    else:
        raise AssertionError("strict PID reader should fail instead of falling back")
    assert called_fallback is False


def test_feishu_doc_fetch_falls_back_to_jina_reader(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_READER_PROVIDER", raising=False)

    def fake_fetch_page_bytes(*args, **kwargs):
        raise RuntimeError("dynamic page blocked")

    def fake_fetch_via_web_access(*args, **kwargs):
        raise RuntimeError("web access empty")

    def fake_fetch_via_jina_reader(url):
        return (
            "Title: Cordys CRM 快速上手指南\n\nMarkdown Content:\n\nCordys CRM 官方在线文档已经上线，请移步 https://cordys.cn/docs/ 。",
            "text/markdown",
            "https://r.jina.ai/http://jlx18gc3up.feishu.cn/docx/VMJzdCipnoj5fYxcbakcWQSAnCh",
            "jina_reader",
        )

    monkeypatch.setattr("research_workflow.evidence.fetch_page_bytes", fake_fetch_page_bytes)
    monkeypatch.setattr("research_workflow.evidence.fetch_via_web_access", fake_fetch_via_web_access)
    monkeypatch.setattr("research_workflow.evidence.fetch_via_jina_reader", fake_fetch_via_jina_reader)

    page = fetch_page("https://jlx18gc3up.feishu.cn/docx/VMJzdCipnoj5fYxcbakcWQSAnCh")

    assert page is not None
    assert page.fetch_method == "jina_reader:feishu_fallback"
    assert "Cordys CRM 官方在线文档" in page.text
    assert "feishu_jina_fallback: ok" in page.fetch_errors


def test_feishu_doc_sparse_direct_text_falls_back_to_jina_reader(monkeypatch) -> None:
    monkeypatch.delenv("RESEARCH_READER_PROVIDER", raising=False)

    def fake_fetch_page_bytes(*args, **kwargs):
        return "<html><title>Feishu Docs</title><body>Loading...</body></html>", "text/html", "https://feishu.cn/docx/x", "direct"

    def fake_fetch_via_jina_reader(url):
        return "Title: Feishu Docs\n\nMarkdown Content:\n\n完整飞书正文。", "text/markdown", "https://r.jina.ai/http://feishu.cn/docx/x", "jina_reader"

    monkeypatch.setattr("research_workflow.evidence.fetch_page_bytes", fake_fetch_page_bytes)
    monkeypatch.setattr("research_workflow.evidence.fetch_via_jina_reader", fake_fetch_via_jina_reader)

    page = fetch_page("https://feishu.cn/docx/x")

    assert page is not None
    assert page.fetch_method == "jina_reader:feishu_fallback"
    assert "完整飞书正文" in page.text


def test_check_searxng_rejects_unavailable_endpoint() -> None:
    assert check_searxng("http://127.0.0.1:1") is False


def test_search_searxng_maps_infoboxes_when_results_are_empty(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {
                    "results": [],
                    "infoboxes": [
                        {
                            "infobox": "SearXNG",
                            "id": "https://en.wikipedia.org/wiki/SearXNG",
                            "content": "SearXNG is a metasearch engine forked from searX.",
                            "engine": "wikipedia",
                            "urls": [{"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/SearXNG"}],
                        }
                    ],
                }
            ).encode("utf-8")

    monkeypatch.setattr(search_module, "urlopen", lambda *args, **kwargs: FakeResponse())
    sources = search_searxng("http://127.0.0.1:8898", "SearXNG")
    assert sources[0].title == "SearXNG"
    assert sources[0].publisher == "en.wikipedia.org"
    assert "metasearch engine" in sources[0].snippet


def test_resolve_searxng_urls_supports_multiple_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SEARXNG_PUBLIC_DISCOVERY", "0")
    monkeypatch.setenv("RESEARCH_SEARXNG_URL", "http://127.0.0.1:8898")
    monkeypatch.setenv("RESEARCH_SEARXNG_URLS", "https://search.example.org; https://search.example.org/, https://second.example.org")
    urls = resolve_searxng_urls()

    assert urls[:3] == [
        "http://127.0.0.1:8898",
        "https://search.example.org",
        "https://second.example.org",
    ]


def test_discover_public_searxng_urls_filters_instances(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RESEARCH_SEARXNG_PUBLIC_DISCOVERY", "1")

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {
                    "instances": {
                        "https://good.example.org/": {"http": {"status_code": 200, "error": None}, "network_type": "normal", "comments": []},
                        "https://down.example.org": {"http": {"status_code": 503, "error": None}, "network_type": "normal", "comments": []},
                        "https://commented.example.org": {"http": {"status_code": 200, "error": None}, "network_type": "normal", "comments": ["maintenance"]},
                        "https://onion.example.org": {"http": {"status_code": 200, "error": None}, "network_type": "tor", "comments": []},
                        "https://second.example.org": {"http": {"status_code": 200, "error": None}, "network_type": "clearnet", "comments": []},
                    }
                }
            ).encode("utf-8")

    monkeypatch.setattr(search_module, "urlopen", lambda *args, **kwargs: FakeResponse())

    assert discover_public_searxng_urls(limit=5) == ["https://good.example.org", "https://second.example.org"]


def test_search_provider_searxng_falls_back_after_failed_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SEARXNG_PUBLIC_DISCOVERY", "0")
    monkeypatch.setenv("RESEARCH_SEARXNG_URL", "http://local-searxng")
    monkeypatch.setenv("RESEARCH_SEARXNG_URLS", "https://fallback-searxng")
    calls: list[str] = []

    def fake_search(base_url: str, query: str, max_results: int = 10):
        calls.append(base_url)
        if base_url == "http://local-searxng":
            raise SearchError("local down")
        source = source_from_url("https://example.com/result")
        source.search_engine = f"searxng:{base_url}"
        return [source]

    monkeypatch.setattr(search_module, "search_searxng", fake_search)

    sources = search_provider("searxng", "test")

    assert calls == ["http://local-searxng", "https://fallback-searxng"]
    assert sources[0].url == "https://example.com/result"

def test_searxng_failure_matrix_uses_only_registered_provider_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SEARXNG_PUBLIC_DISCOVERY", "0")
    monkeypatch.setenv("RESEARCH_SEARXNG_URL", "http://fixture-searxng")
    monkeypatch.setenv("RESEARCH_SEARXNG_URLS", "")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fixture-only")

    states = {
        "normal": ("ok", ""),
        "empty": ("empty", ""),
        "timeout": ("network_error", "timed out"),
        "unreachable": ("network_error", "connection refused"),
    }
    for state, (expected_status, error_text) in states.items():
        def fake_searxng(base_url: str, query: str, max_results: int = 10, *, _state: str = state):
            if _state == "normal":
                source = source_from_url("https://local.example.test/primary-evidence")
                source.search_engine = "searxng:fixture"
                return [source]
            if _state == "empty":
                return []
            raise SearchError(error_text)

        def fake_brave(query: str, max_results: int = 10):
            source = source_from_url(f"https://registered.example.test/{state}-fallback-evidence")
            source.search_engine = "brave:fixture"
            return [source]

        monkeypatch.setattr(search_module, "search_searxng", fake_searxng)
        monkeypatch.setattr(search_module, "search_brave", fake_brave)
        result = multi_source_search_with_status(
            "fixture claim",
            providers=["searxng", "brave"],
            max_results=4,
            searxng_url="http://fixture-searxng",
        )

        statuses = {item["provider"]: item["status"] for item in result.provider_statuses}
        assert statuses["searxng"] == expected_status
        assert statuses["brave"] == "ok"
        assert any(source.search_engine == "brave:fixture" for source in result.sources)
        assert all(source.url.startswith("https://") for source in result.sources)
        assert all("model-memory" not in source.url for source in result.sources)
        assert bool(result.warnings) is bool(error_text)

def test_searxng_without_registered_fallback_is_explicitly_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_SEARXNG_PUBLIC_DISCOVERY", "0")
    monkeypatch.setenv("RESEARCH_SEARXNG_URL", "http://fixture-searxng")
    monkeypatch.setenv("RESEARCH_SEARXNG_URLS", "")

    def unavailable_searxng(base_url: str, query: str, max_results: int = 10):
        raise SearchError("connection refused")

    monkeypatch.setattr(search_module, "search_searxng", unavailable_searxng)
    try:
        multi_source_search_with_status(
            "fixture claim",
            providers=["searxng"],
            max_results=1,
            searxng_url="http://fixture-searxng",
        )
    except SearchError as exc:
        assert "connection refused" in str(exc)
        assert "model" not in str(exc).lower()
    else:
        raise AssertionError("Unavailable SearXNG must not synthesize model-memory evidence.")


def test_search_provider_registry_normalizes_and_keeps_commercial_disabled(monkeypatch) -> None:
    assert normalize_providers("searxng, brave, searxng") == ["searxng", "brave"]
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert search_provider("brave", "SearXNG") == []


def test_search_provider_maps_brave_payload(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")

    def fake_json_request(url, **kwargs):
        assert "api.search.brave.com" in url
        assert kwargs["headers"]["X-Subscription-Token"] == "token"
        return {
            "web": {
                "results": [
                    {
                        "title": "SearXNG docs",
                        "url": "https://docs.searxng.org/",
                        "description": "SearXNG documentation.",
                    }
                ]
            }
        }

    monkeypatch.setattr(search_module, "_json_request", fake_json_request)
    sources = search_provider("brave", "SearXNG")
    assert sources[0].search_engine == "brave"
    assert sources[0].publisher == "docs.searxng.org"


def test_search_provider_maps_tavily_kagi_serpapi_exa_jina_you_perplexity_and_metaso_payloads(monkeypatch) -> None:
    payloads = {
        "tavily": {"results": [{"title": "Tavily result", "url": "https://example.com/tavily", "content": "Tavily snippet"}]},
        "kagi": {"data": [{"title": "Kagi result", "url": "https://example.com/kagi", "snippet": "Kagi snippet"}]},
        "serpapi": {"organic_results": [{"title": "SerpAPI result", "link": "https://example.com/serp", "snippet": "Serp snippet"}]},
        "exa": {"results": [{"title": "Exa result", "url": "https://example.com/exa", "publishedDate": "2026-05-30T00:00:00Z", "highlights": ["Exa snippet"]}]},
        "jina": {"data": [{"title": "Jina result", "url": "https://example.com/jina", "description": "Jina snippet"}]},
        "you": {"results": {"web": [{"title": "You result", "url": "https://example.com/you", "snippets": ["You snippet"]}]}},
        "perplexity": {"results": [{"title": "Perplexity result", "url": "https://example.com/perplexity", "snippet": "Perplexity snippet", "date": "2026-05-30"}]},
        "metaso": {"webpages": [{"title": "Metaso result", "link": "https://example.com/metaso", "snippet": "Metaso snippet", "date": "2026年05月30日"}]},
    }
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    monkeypatch.setenv("KAGI_API_KEY", "token")
    monkeypatch.setenv("SERPAPI_API_KEY", "token")
    monkeypatch.setenv("EXA_API_KEY", "token")
    monkeypatch.setenv("JINA_API_KEY", "token")
    monkeypatch.setenv("YOU_API_KEY", "token")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "token")
    monkeypatch.setenv("METASO_API_KEY", "token")

    def fake_json_request(url, **kwargs):
        if "tavily" in url:
            assert kwargs["method"] == "POST"
            return payloads["tavily"]
        if "api.exa.ai" in url:
            assert kwargs["headers"]["x-api-key"] == "token"
            assert kwargs["body"]["numResults"] == 10
            return payloads["exa"]
        if "s.jina.ai" in url:
            assert kwargs["headers"]["Authorization"].startswith("Bearer ")
            return payloads["jina"]
        if "kagi" in url:
            assert kwargs["headers"]["Authorization"].startswith("Bot ")
            return payloads["kagi"]
        if "metaso" in url:
            assert kwargs["headers"]["Authorization"].startswith("Bearer ")
            assert kwargs["body"]["scope"] == "webpage"
            assert kwargs["body"]["size"] == "10"
            return payloads["metaso"]
        if "perplexity" in url:
            assert kwargs["headers"]["Authorization"].startswith("Bearer ")
            assert kwargs["body"]["snippet_mode"] == "medium"
            return payloads["perplexity"]
        if "serpapi" in url:
            return payloads["serpapi"]
        if "ydc-index.io" in url:
            assert kwargs["headers"]["X-API-Key"] == "token"
            return payloads["you"]
        raise AssertionError(url)

    monkeypatch.setattr(search_module, "_json_request", fake_json_request)
    assert search_provider("tavily", "SearXNG")[0].search_engine == "tavily"
    exa = search_provider("exa", "SearXNG")[0]
    assert exa.search_engine == "exa"
    assert exa.published_at == "2026-05-30T00:00:00Z"
    assert search_provider("jina", "SearXNG")[0].search_engine == "jina"
    assert search_provider("kagi", "SearXNG")[0].search_engine == "kagi"
    metaso = search_provider("metaso", "SearXNG")[0]
    assert metaso.search_engine == "metaso"
    assert metaso.published_at == "2026年05月30日"
    assert search_provider("perplexity", "SearXNG")[0].search_engine == "perplexity"
    serpapi = search_provider("serpapi", "SearXNG")[0]
    assert serpapi.search_engine == "serpapi"
    assert serpapi.url == "https://example.com/serp"
    you = search_provider("you", "SearXNG")[0]
    assert you.search_engine == "you"
    assert you.snippet == "You snippet"


def test_multi_source_search_marks_limited_provider_and_continues(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")

    def fake_provider(provider, query, **kwargs):
        if provider == "tavily":
            raise SearchError("Search provider request failed for api.tavily.com: HTTP 429 rate limit or quota exceeded")
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["searxng", "tavily", "brave"], max_results=5)
    assert len(result.sources) == 2
    statuses = {item["provider"]: item["status"] for item in result.provider_statuses}
    assert statuses["tavily"] == "limited"
    assert statuses["brave"] == "ok"
    assert result.warnings


def test_multi_source_search_skips_unconfigured_optional_providers(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    calls: list[str] = []

    def fake_provider(provider, query, **kwargs):
        calls.append(provider)
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["brave"], max_results=5)
    assert calls == []
    assert result.sources == []
    assert result.provider_statuses[0]["provider"] == "brave"
    assert result.provider_statuses[0]["status"] == "not_configured"


def test_multi_source_search_runs_active_providers_concurrently(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    monkeypatch.setenv("SERPAPI_API_KEY", "token")
    monkeypatch.setenv("RESEARCH_PROVIDER_ADAPTIVE_RANKING", "0")

    def fake_provider(provider, query, **kwargs):
        time.sleep(0.12)
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    started = time.perf_counter()
    result = multi_source_search_with_status("SearXNG", providers=["brave", "tavily", "serpapi"], max_results=6)
    elapsed = time.perf_counter() - started
    assert [item["provider"] for item in result.provider_statuses] == ["brave", "tavily", "serpapi"]
    assert len(result.sources) == 3
    assert all(item["elapsed_seconds"] > 0 for item in result.provider_statuses)
    assert elapsed < 0.3


def test_multi_source_search_continues_when_provider_crashes(monkeypatch) -> None:
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "token")

    def fake_provider(provider, query, **kwargs):
        if provider == "tavily":
            raise RuntimeError("adapter bug")
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["brave", "tavily"], max_results=4)
    statuses = {item["provider"]: item for item in result.provider_statuses}
    assert len(result.sources) == 1
    assert statuses["brave"]["status"] == "ok"
    assert statuses["tavily"]["status"] == "error"
    assert "adapter bug" in statuses["tavily"]["warning"]


def test_multi_source_search_records_provider_stats_and_rank_notes(tmp_path, monkeypatch) -> None:
    stats_path = tmp_path / "provider_stats.json"
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    monkeypatch.setenv("RESEARCH_PROVIDER_ADAPTIVE_RANKING", "1")

    def fake_provider(provider, query, **kwargs):
        if provider == "tavily":
            raise SearchError("Search provider request failed for api.tavily.com: HTTP 429 quota exceeded")
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["brave", "tavily"], max_results=4, provider_stats_path=str(stats_path))
    stats = load_provider_stats(stats_path)
    statuses = {item["provider"]: item for item in result.provider_statuses}
    assert stats["brave"]["ok_count"] == 1
    assert stats["tavily"]["limited_count"] == 1
    assert stats["tavily"]["consecutive_limited_count"] == 1
    assert statuses["brave"]["recent_status"] == "unknown"
    assert statuses["brave"]["rank_reason"]


def test_multi_source_search_adaptive_ranking_deprioritizes_limited_provider(tmp_path, monkeypatch) -> None:
    stats_path = tmp_path / "provider_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "brave": {"provider": "brave", "ok_count": 3, "source_count": 6, "last_status": "ok"},
                "tavily": {"provider": "tavily", "limited_count": 3, "last_status": "limited", "consecutive_limited_count": 3},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    def fake_provider(provider, query, **kwargs):
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["tavily", "brave"], max_results=4, provider_stats_path=str(stats_path))
    assert [item["provider"] for item in result.provider_statuses] == ["brave", "tavily"]
    statuses = {item["provider"]: item for item in result.provider_statuses}
    assert statuses["tavily"]["recent_status"] == "limited"
    assert "consecutive limited" in statuses["tavily"]["rank_reason"]


def test_multi_source_search_cooldown_skips_recently_limited_provider(tmp_path, monkeypatch) -> None:
    stats_path = tmp_path / "provider_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "brave": {"provider": "brave", "ok_count": 3, "source_count": 6, "last_status": "ok"},
                "tavily": {
                    "provider": "tavily",
                    "limited_count": 3,
                    "last_status": "limited",
                    "consecutive_limited_count": 3,
                    "last_error_at": now_iso(),
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "token")
    monkeypatch.setenv("TAVILY_API_KEY", "token")
    calls: list[str] = []

    def fake_provider(provider, query, **kwargs):
        calls.append(provider)
        return [source_from_url(f"https://example.com/{provider}")]

    monkeypatch.setattr(search_module, "search_provider", fake_provider)
    result = multi_source_search_with_status("SearXNG", providers=["tavily", "brave"], max_results=4, provider_stats_path=str(stats_path))
    statuses = {item["provider"]: item for item in result.provider_statuses}
    assert calls == ["brave"]
    assert statuses["tavily"]["status"] == "cooldown"
    assert "provider cooldown active" in statuses["tavily"]["warning"]


def test_search_pipeline_reports_source_target_gaps_when_no_sources(tmp_path) -> None:
    result = run_search_pipeline(topic="SearXNG", data_dir=tmp_path, mode="daily", no_network=True)
    payload = json.loads(result.report_json)
    assert result.duration_seconds >= 0
    assert payload["duration_seconds"] >= 0
    assert "- Duration:" in result.report_markdown
    assert payload["coverage_gaps"]
    assert all(str(item).startswith("Add sources for:") for item in payload["coverage_gaps"])
    assert "Primary buckets have at least one candidate source" not in result.report_markdown


def test_search_pipeline_reports_not_configured_provider_health(tmp_path, monkeypatch) -> None:
    def fake_search(query, **kwargs):
        return ProviderSearchResult(
            [],
            [{"provider": "brave", "status": "not_configured", "source_count": 0, "warning": "BRAVE_SEARCH_API_KEY is not configured"}],
            [],
        )

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(topic="SearXNG", data_dir=tmp_path, mode="daily", max_results=3, search_providers=["brave"])
    brave = next(item for item in result.provider_health if item["provider"] == "brave")
    assert brave["overall"] == "not_configured"
    assert brave["not_configured_count"] >= 1
    assert "not configured" in result.report_markdown


def test_search_report_includes_provider_warnings() -> None:
    plan = plan_query("SearXNG", "daily")
    markdown = search_report_to_markdown(
        plan,
        [],
        [],
        provider_health=[
            {
                "provider": "tavily",
                "overall": "limited",
                "limited_count": 1,
                "warnings": ["quota exceeded"],
                "cost_tier": "free-tier/API",
                "limit_note": "watch usage",
            }
        ],
    )
    assert "warnings: quota exceeded" in markdown


def test_extract_atomic_claims_marks_dates_high_risk() -> None:
    text = "Gigablast went offline in April 2023. SearXNG is a metasearch engine."
    claims = extract_atomic_claims(text)
    assert len(claims) == 2
    assert claims[0].risk_level == "high"


def test_extract_atomic_claims_ignores_generated_report_tables_and_source_notes() -> None:
    text = """# SearXNG

## Short Answer

SearXNG is a metasearch engine.

## Evidence Table

| Claim | Verdict | Confidence | Evidence |
| --- | --- | --- | --- |
| SearXNG is a metasearch engine. | supported | 0.9 | ev_1 |

## Evidence Notes

- `ev_1` from docs.searxng.org: SearXNG is a metasearch engine.

## Source Notes

- docs.searxng.org - official; retrieved 2026-05-29T00:00:00+00:00
"""
    claims = extract_atomic_claims(text)
    assert [claim.text for claim in claims] == ["SearXNG is a metasearch engine."]


def test_verify_claims_finds_support() -> None:
    claims = extract_atomic_claims("SearXNG is a metasearch engine.")
    evidence = [
        Evidence(
            id="ev_1",
            source_id="src_1",
            quote_or_paraphrase="SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            support_direction=Verdict.SUPPORTED,
            confidence=0.9,
        )
    ]
    verified = verify_claims(claims, evidence)
    assert verified[0].verdict in {Verdict.SUPPORTED, Verdict.PARTIAL}
    assert verified[0].evidence_ids == ["ev_1"]


def test_high_risk_claim_requires_two_independent_sources_for_supported() -> None:
    claims = extract_atomic_claims("SearXNG released version 2024.5 in 2024.")
    evidence = [
        Evidence(
            id="ev_1",
            source_id="src_1",
            quote_or_paraphrase="SearXNG released version 2024.5 in 2024.",
            confidence=0.9,
        )
    ]
    verified = verify_claims(claims, evidence)
    assert verified[0].risk_level == "high"
    assert verified[0].verdict == Verdict.PARTIAL
    assert verified[0].confidence <= 0.54


def test_high_risk_claim_can_be_supported_by_two_sources() -> None:
    claims = extract_atomic_claims("SearXNG released version 2024.5 in 2024.")
    evidence = [
        Evidence(
            id="ev_1",
            source_id="src_1",
            quote_or_paraphrase="SearXNG released version 2024.5 in 2024.",
            confidence=0.9,
        ),
        Evidence(
            id="ev_2",
            source_id="src_2",
            quote_or_paraphrase="SearXNG released version 2024.5 in 2024.",
            confidence=0.9,
        ),
    ]
    verified = verify_claims(claims, evidence)
    assert verified[0].risk_level == "high"
    assert verified[0].verdict == Verdict.SUPPORTED


def test_revision_suggestions_cover_unsupported_claims() -> None:
    claims = extract_atomic_claims("Gigablast is still active in 2026.")
    evidence = [
        Evidence(
            id="ev_1",
            source_id="src_1",
            quote_or_paraphrase="Gigablast stopped operating in April 2023.",
        )
    ]
    verified = verify_claims(claims, evidence)
    suggestions = suggest_revisions(verified, evidence)
    assert suggestions
    assert suggestions[0].action == "replace"
    assert suggestions[0].evidence_ids == ["ev_1"]


def test_web_status_reports_store_counts(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    status = app.status()
    assert status["service"] == "ai-research-workflow"
    assert isinstance(status["pid"], int)
    assert status["source_count"] == 0
    assert status["evidence_count"] == 0
    assert status["claim_count"] == 0
    assert "searxng_configured" in status


def test_web_v1_health_capabilities_and_openapi(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=ResearchConfig(web_port=9777))
    health = app.health()
    capabilities = app.capabilities()
    discovery = app.discovery()
    schema = app.openapi_schema()
    assert health["status"] == "ok"
    assert health["api_version"] == "v1"
    assert health["contract_version"] == CONTRACT_VERSION
    assert health["config_hash"] == config_fingerprint(app.config)
    assert capabilities["contract_version"] == CONTRACT_VERSION
    assert capabilities["config_hash"] == config_fingerprint(app.config)
    assert "tool_selection" in capabilities["modes"]
    assert "POST /v1/search" in capabilities["stable_endpoints"]
    assert "POST /v1/jobs" in capabilities["stable_endpoints"]
    assert "GET /v1/discovery" in capabilities["stable_endpoints"]
    assert capabilities["features"]["sse_events"] is True
    assert capabilities["features"]["watch_folder"] is True
    assert capabilities["features"]["reader_health"] is True
    assert discovery["preferred_order"][0]["interface"] == "mcp"
    assert discovery["contract_version"] == CONTRACT_VERSION
    assert "research_search" in discovery["preferred_order"][0]["tools"]
    assert schema["openapi"].startswith("3.")
    assert schema["servers"] == [{"url": "http://127.0.0.1:9777"}]
    assert "/v1/search" in schema["paths"]
    assert "/v1/discovery" in schema["paths"]
    assert "/v1/jobs" in schema["paths"]
    assert "/v1/jobs/{job_id}/events" in schema["paths"]
    assert "/v1/agent/research" in schema["paths"]
    assert "/v1/agent/jobs/{job_id}" in schema["paths"]
    assert "text/event-stream" in schema["paths"]["/v1/runs/{run_id}/events"]["get"]["responses"]["200"]["content"]
    assert schema["components"]["schemas"]["SearchRequest"]["properties"]["mode"]["enum"] == ["auto", "daily", "academic", "tool_selection"]
    assert schema["paths"]["/v1/search"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/SearchResponse"
    assert schema["paths"]["/v1/ingest"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/IngestResponse"
    assert schema["paths"]["/v1/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/HealthResponse"
    agent_status_fields = schema["components"]["schemas"]["AgentJobStatus"]["properties"]
    for field in ["job_id", "status", "kind", "stage", "result", "error", "events", "links"]:
        assert field in agent_status_fields
        assert field in schema["components"]["schemas"]["AgentJobAccepted"]["properties"]
        assert field in schema["components"]["schemas"]["AgentJobEvents"]["properties"]
    assert "search_queries" in schema["components"]["schemas"]["Source"]["properties"]
    assert "search_buckets" in schema["components"]["schemas"]["Source"]["properties"]
    provider_health = schema["components"]["schemas"]["ProviderHealth"]["properties"]
    assert "avg_seconds" in provider_health
    assert "recent_status" in provider_health
    assert "rank_reason" in provider_health
    assert "warnings" in provider_health
    assert schema["components"]["schemas"]["SearchResponse"]["properties"]["provider_health"]["items"]["$ref"] == "#/components/schemas/ProviderHealth"
    assert "RevisionSuggestion" in schema["components"]["schemas"]
    assert schema["components"]["schemas"]["SearchResponse"]["properties"]["revision_suggestions"]["items"]["$ref"] == "#/components/schemas/RevisionSuggestion"
    assert "AgentJobAccepted" in schema["components"]["schemas"]
    assert "AgentJobStatus" in schema["components"]["schemas"]


def test_web_ui_claim_table_exposes_revision_column() -> None:
    html = Path("web/index.html").read_text(encoding="utf-8")
    app_js = Path("web/app.js").read_text(encoding="utf-8")
    assert "<th>Revision</th>" in html
    assert "Job Progress" in html
    assert "state.revisionSuggestions" in app_js
    assert "renderJobProgress" in app_js
    assert "revision-cell" in app_js
    assert "evidenceTypeHTML" in app_js
    assert "source_quote_type" in app_js


def test_web_query_without_network_returns_plan(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.query({"topic": "本地 AI 搜索工具对比", "mode": "daily", "no_network": True})
    assert result["plan"]["topic"] == "本地 AI 搜索工具对比"
    assert result["sources"] == []
    assert "report_markdown" in result


def test_web_search_persists_search_only_run(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.search(
        {
            "topic": "SearXNG",
            "mode": "daily",
            "no_network": True,
            "urls": ["https://docs.searxng.org/"],
        }
    )
    assert result["kind"] == "search"
    assert result["claims"] == []
    assert result["sources"]
    assert result["sources"][0]["search_buckets"] == ["manual URLs"]
    assert result["sources"][0]["search_stages"] == ["manual_ingest"]
    assert "Retrieved by: manual URLs" in result["report_markdown"]
    exported = app.export()
    assert exported["data"]["runs"][0]["id"] == result["run_id"]


def test_web_v1_deepen_and_run_reports(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.deepen({"topic": "SearXNG", "mode": "daily", "no_network": True, "urls": ["https://docs.searxng.org/"]})
    run_id = result["run_id"]
    record = app.run_record(run_id)
    markdown = app.run_report(run_id, "md")
    report_json = app.run_report(run_id, "json")
    events = app.run_events(run_id)
    sse = app.run_events_sse(run_id)
    assert record["id"] == run_id
    assert markdown["format"] == "md"
    assert "SearXNG" in markdown["content"]
    assert report_json["format"] == "json"
    assert events["run_id"] == run_id
    assert any(event["type"] == "search_query" for event in events["events"])
    assert "event: search_query" in sse
    assert "event: done" in sse
    assert run_id in sse


def test_web_manual_evidence_can_support_verification(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    app.add_evidence(
        {
            "url": "https://docs.searxng.org/",
            "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
        }
    )
    result = app.verify(
        {
            "topic": "SearXNG",
            "text": "SearXNG is a metasearch engine that aggregates results from many search services.",
        }
    )
    assert result["claims"][0]["verdict"] in {"supported", "partial"}
    assert result["claims"][0]["evidence_ids"]
    assert "revision_suggestions" in result


def test_web_export_dedupes_append_only_rows(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    payload = {
        "url": "https://docs.searxng.org/",
        "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
    }
    app.add_evidence(payload)
    app.add_evidence(payload)
    exported = app.export()
    assert len(exported["data"]["sources"]) == 1
    assert len(exported["data"]["evidence"]) == 1


def test_pipeline_runs_full_local_chain_with_manual_evidence(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        urls=["https://docs.searxng.org/"],
        manual_evidence=[
            {
                "url": "https://docs.searxng.org/",
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
        text_to_verify="SearXNG is a metasearch engine that aggregates results from many search services.",
    )
    assert result.plan.topic == "SearXNG"
    assert result.sources
    assert any("manual URLs" in source.search_buckets for source in result.sources)
    assert any("manual evidence" in source.search_buckets for source in result.sources)
    assert result.evidence
    assert result.claims[0].verdict in {Verdict.SUPPORTED, Verdict.PARTIAL}
    assert result.claims[0].evidence_ids
    assert result.run_id
    assert result.report_path
    assert result.audit
    assert result.audit["overall"] == "ok"
    assert '"evidence"' in result.report_json
    assert "## Evidence Notes" in result.report_markdown
    assert "Retrieved by: manual URLs, manual evidence" in result.report_markdown
    assert (tmp_path / "runs.jsonl").exists()


def test_search_pipeline_runs_without_factcheck(tmp_path) -> None:
    result = run_search_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        urls=["https://docs.searxng.org/"],
    )
    assert result.run_id.startswith("search_")
    assert result.plan.topic == "SearXNG"
    assert result.sources
    assert result.evidence
    assert "## Evidence Snippets" in result.report_markdown
    payload = json.loads(result.report_json)
    assert payload["kind"] == "search"
    assert payload["claim_table"] == []
    assert payload["search_queries"]
    assert list_search_runs(tmp_path)[0]["id"] == result.run_id


def test_search_pipeline_marks_provider_queries_skipped_when_no_network(tmp_path) -> None:
    result = run_search_pipeline(
        topic="深度搜索、事实核查相关的，agent skill、最佳实践、开源项目有哪些？",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
    )
    assert result.warning == "live provider search disabled"
    assert result.search_queries
    assert {item["status"] for item in result.search_queries} == {"skipped"}
    assert "live provider search disabled" in result.report_markdown


def test_search_pipeline_marks_empty_provider_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", lambda *args, **kwargs: ProviderSearchResult([], [], []))
    result = run_search_pipeline(topic="SearXNG", data_dir=tmp_path, mode="daily")
    assert result.search_queries
    assert {item["status"] for item in result.search_queries} == {"empty"}
    assert result.sources == []


def test_inventory_search_falls_back_to_curated_seed_sources_when_provider_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", lambda *args, **kwargs: ProviderSearchResult([], [], []))
    result = run_search_pipeline(
        topic="深度搜索、事实核查相关的，agent skill、最佳实践、开源项目有哪些？",
        data_dir=tmp_path,
        mode="daily",
        max_results=12,
    )
    urls = {source.url for source in result.sources}
    assert "https://github.com/Libr-AI/OpenFactVerification" in urls
    assert "https://github.com/GAIR-NLP/factool" in urls
    assert "https://arxiv.org/abs/2502.05803" in urls
    assert {source.search_engine for source in result.sources} == {"curated_seed"}


def test_search_pipeline_uses_planned_multi_query_retrieval(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(query, **kwargs):
        calls.append(query)
        return ProviderSearchResult([source_from_url(f"https://example.com/{len(calls)}")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        max_results=4,
    )
    assert len(calls) > 1
    assert calls == [item["query"] for item in result.search_queries]
    assert len(result.sources) == 4
    assert all(item["status"] == "ok" for item in result.search_queries)
    assert all("bucket" in item for item in result.search_queries)
    assert "## Queries Run" in result.report_markdown


def test_search_pipeline_runs_gap_followup_when_buckets_are_missing(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_search(query, **kwargs):
        calls.append(query)
        if "FActScore" in query or "OpenFactVerification" in query:
            return ProviderSearchResult([source_from_url("https://github.com/shmsw25/factscore")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])
        return ProviderSearchResult([source_from_url("https://github.com/bytedance/deer-flow")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(
        topic="有哪些适合AI agent 的深度搜索、事实核查相关工具？",
        data_dir=tmp_path,
        mode="daily",
        max_results=6,
    )
    stages = {item["stage"] for item in result.search_queries}
    assert "stage_1_recall" in stages
    assert "stage_2_gap_followup" in stages
    assert any("FActScore RARR FacTool" in query for query in calls)
    assert "Stage 2 Gap Follow-up" in result.report_markdown


def test_source_ranking_prefers_canonical_repositories_over_commentary() -> None:
    sources = [
        source_from_url("https://zhuanlan.zhihu.com/p/1926055354426458199"),
        source_from_url("https://github.com/rpuls/deer-flow-quickstart"),
        source_from_url("https://github.com/bytedance/deer-flow"),
        source_from_url("https://www.cnblogs.com/itech/p/19918043"),
    ]
    selected = _select_sources(sources, max_results=3)
    assert selected[0].url == "https://github.com/bytedance/deer-flow"
    assert "cnblogs.com" not in {source.publisher for source in selected[:2]}
    assert selected[0].quality_score > 0
    assert selected[0].quality_reasons


def test_source_quality_summary_flags_primary_and_evidence_coverage() -> None:
    source = source_from_url("https://github.com/bytedance/deer-flow")
    selected = _select_sources([source], max_results=1)
    summary = source_quality_summary(
        selected,
        [
            Evidence(
                id="ev_1",
                source_id=selected[0].id,
                quote_or_paraphrase="DeerFlow is an open source deep research framework.",
            )
        ],
    )
    assert summary["overall"] in {"strong", "usable"}
    assert summary["primary_source_count"] == 1
    assert summary["sources_with_evidence"] == 1


def test_canonical_groups_merge_equivalent_project_urls(tmp_path, monkeypatch) -> None:
    def fake_search(query, **kwargs):
        first = source_from_url("https://github.com/bytedance/deer-flow")
        second = source_from_url("https://github.com/bytedance/deer-flow/")
        second.search_engine = "brave"
        return ProviderSearchResult([first, second], [{"provider": "searxng", "status": "ok", "source_count": 2}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(topic="DeerFlow deep research GitHub", data_dir=tmp_path, mode="daily", max_results=5)
    assert len(result.sources) == 1
    assert result.sources[0].duplicate_count >= 2
    assert result.sources[0].search_queries
    assert result.sources[0].search_buckets
    assert "Retrieved by:" in result.report_markdown
    assert result.canonical_groups
    assert result.canonical_groups[0]["canonical_url"] == "https://github.com/bytedance/deer-flow"


def test_provider_health_counts_limited_sources_and_cost_notes() -> None:
    health = provider_health_from_queries(
        [
            {
                "provider_statuses": [
                    {"provider": "tavily", "status": "ok", "source_count": 2},
                    {"provider": "tavily", "status": "limited", "source_count": 0, "warning": "quota exceeded", "recent_status": "limited", "rank_reason": "deprioritized after recent limited", "rank_score": -4.2},
                    {"provider": "tavily", "status": "cooldown", "source_count": 0, "warning": "provider cooldown active", "recent_status": "cooldown", "rank_reason": "provider cooldown active", "rank_score": -99.0},
                    {"provider": "searxng", "status": "empty", "source_count": 0},
                ]
            }
        ]
    )
    tavily = next(item for item in health if item["provider"] == "tavily")
    assert tavily["overall"] == "partial"
    assert tavily["limited_count"] == 1
    assert tavily["cooldown_count"] == 1
    assert "avg_seconds" in tavily
    assert "max_seconds" in tavily
    assert tavily["recent_status"] == "cooldown"
    assert "cooldown" in tavily["rank_reason"]
    assert tavily["warnings"] == ["quota exceeded", "provider cooldown active"]
    assert "free" in tavily["cost_tier"]


def test_metaso_business_error_is_classified_as_auth_error(monkeypatch) -> None:
    def fake_request(*args, **kwargs):
        return {"errCode": 2005, "errMsg": "API密钥无效"}

    monkeypatch.setenv("METASO_API_KEY", "present")
    monkeypatch.setattr("research_workflow.search._json_request", fake_request)
    try:
        search_module.search_metaso("AI 搜索", max_results=1)
    except SearchError as exc:
        assert search_module._provider_error_status(str(exc)) == "auth_error"
    else:
        raise AssertionError("expected Metaso business error to raise SearchError")


def test_provider_error_status_classifies_chinese_balance_as_limited() -> None:
    assert search_module._provider_error_status("Metaso API error: 余额不足") == "limited"


def test_tool_matrix_infers_github_project_rows(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_GITHUB_STATUS", "0")
    source = source_from_url("https://github.com/assafelovic/gpt-researcher")
    source.snippet = "GPT Researcher is an open source deep research agent."
    rows = build_tool_matrix([source])
    assert rows[0]["name"] == "assafelovic/gpt-researcher"
    assert rows[0]["open_source"] == "yes"
    assert rows[0]["maintenance_status"] == "unknown (disabled)"
    assert rows[0]["recommendation"] == "candidate to integrate"


def test_search_pipeline_can_auto_verify_claims_from_evidence(tmp_path, monkeypatch) -> None:
    def fake_search(query, **kwargs):
        source = source_from_url("https://docs.searxng.org/")
        source.snippet = "SearXNG is a free internet metasearch engine which aggregates results from many search services."
        return ProviderSearchResult([source], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(topic="SearXNG", data_dir=tmp_path, mode="daily", max_results=3, auto_verify=True)
    assert result.claims
    assert result.claims[0].verdict in {Verdict.SUPPORTED, Verdict.PARTIAL}
    assert "Auto Fact Check" in result.report_markdown


def test_search_pipeline_returns_tool_matrix(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_GITHUB_STATUS", "0")

    def fake_search(query, **kwargs):
        return ProviderSearchResult([source_from_url("https://github.com/stanford-oval/storm")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(topic="STORM deep research GitHub", data_dir=tmp_path, mode="daily", max_results=3)
    assert result.tool_matrix
    assert result.source_quality["source_count"] == len(result.sources)
    assert "Tool Matrix" in result.report_markdown
    assert "Evidence Quality" in result.report_markdown
    payload = json.loads(result.report_json)
    assert payload["tool_matrix"][0]["name"] == "stanford-oval/storm"
    assert payload["source_quality"]["source_count"] == len(result.sources)


def test_planned_search_queries_include_academic_expansions() -> None:
    plan = plan_query("FActScore benchmark", "academic")
    queries = planned_search_queries(plan)
    assert plan.topic in queries
    assert any("paper benchmark dataset" in query for query in queries)


def test_inventory_search_queries_use_clean_bucketed_expansions() -> None:
    plan = plan_query("深度搜索、事实核查相关的，skill、最佳实践、开源项目有哪些", "daily")
    specs = planned_search_query_specs(plan)
    queries = [spec.query for spec in specs]
    buckets = {spec.bucket for spec in specs}
    assert "deep research open source github" in queries
    assert "LLM fact checking open source FActScore RARR FacTool SAFE" in queries
    assert "OpenFactVerification Loki GitHub" in queries
    assert "DeepTRACE deep research audit fact support matrix arxiv" in queries
    assert "Sift Source Inspection Fact-checking Tool ashg2099 GitHub" in queries
    assert "open-source projects" in buckets
    assert "fact-checking methods" in buckets
    assert "search and extraction infrastructure" in buckets
    assert "unverified external candidates" in buckets


def test_search_pipeline_inventory_report_groups_sources_and_gaps(tmp_path, monkeypatch) -> None:
    def fake_search(query, **kwargs):
        if "FActScore" in query:
            return ProviderSearchResult([source_from_url("https://github.com/shmsw25/factscore")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])
        if "SearXNG" in query:
            return ProviderSearchResult([source_from_url("https://docs.searxng.org/")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])
        return ProviderSearchResult([source_from_url("https://example.com/blog")], [{"provider": "searxng", "status": "ok", "source_count": 1}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(
        topic="深度搜索、事实核查相关的，skill、最佳实践、开源项目有哪些",
        data_dir=tmp_path,
        mode="daily",
        max_results=6,
    )
    payload = json.loads(result.report_json)
    assert "## Research Buckets" in result.report_markdown
    assert "## Coverage Gaps" in result.report_markdown
    assert "## External AI Seeds To Verify" in result.report_markdown
    assert "Fact-checking methods" in payload["research_buckets"]
    assert payload["seed_queries"]
    assert payload["unverified_seed_queries"]
    assert payload["coverage_gaps"]


def test_pipeline_persists_restorable_run_record(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    runs = list_runs(tmp_path)
    restored = get_run(tmp_path, result.run_id)
    assert runs[0]["id"] == result.run_id
    assert restored is not None
    assert restored["plan"]["topic"] == "SearXNG"
    assert restored["evidence"]
    assert restored["claim_count"] == len(result.claims)
    assert result.claim_citations
    assert restored["claim_citations"]
    assert json.loads(restored["report_json"])["claim_citations"]
    assert "## Claim Citations" in restored["report_markdown"]
    assert restored["report_markdown"] == result.report_markdown
    assert restored["audit"]["overall"] == "ok"


def test_obsidian_export_refuses_unsafe_folder(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    try:
        export_run_to_obsidian(data_dir=tmp_path, run_id=result.run_id, vault=tmp_path / "vault", folder="../escape")
    except ValueError as exc:
        assert "relative" in str(exc)
    else:
        raise AssertionError("expected unsafe folder to be rejected")


def test_obsidian_export_handles_legacy_run_without_quality_fields() -> None:
    note = obsidian_note_from_run(
        {
            "id": "run_legacy",
            "topic": "Legacy Run",
            "created_at": "2026-06-13T00:00:00Z",
            "mode": "daily",
            "report_markdown": "# Legacy Run\n\nOld report.",
            "sources": [],
            "claims": [],
        }
    )

    assert "evidence_quality: \"unknown\"" in note
    assert "## Evidence Quality Review" in note
    assert "No evidence quality summary recorded." in note
    assert "## References Check" in note


def test_audit_detects_high_risk_policy_violation(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "url": "https://example.com/source-a",
                "text": "SearXNG released version 2024.5 in 2024.",
            }
        ],
        text_to_verify="SearXNG released version 2024.5 in 2024.",
    )
    record = get_run(tmp_path, result.run_id)
    assert record is not None
    report = json.loads(record["report_json"])
    report["claim_table"][0]["verdict"] = "supported"
    record["report_json"] = json.dumps(report)
    record["supported_count"] = 1
    record["partial_count"] = 0
    (tmp_path / "runs.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    audited = audit_run(tmp_path, result.run_id)
    checks = {check["name"]: check for check in audited["checks"]}
    assert audited["overall"] == "error"
    assert checks["high_risk_policy"]["status"] == "error"


def test_quality_summary_flags_uncited_and_weak_evidence(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "url": "https://example.com/short",
                "text": "SearXNG aggregates search results.",
            }
        ],
        text_to_verify="SearXNG aggregates search results.",
    )

    quality = run_quality(tmp_path, result.run_id)

    assert quality["overall"] == "warn"
    assert quality["quality_summary"]["weak_evidence"]
    assert "Replace short or URL-only evidence rows" in quality["quality_summary"]["recommendations"][0]


def test_deepen_claims_generates_queries_for_not_found_claim(tmp_path) -> None:
    result = run_research_pipeline(
        topic="FActScore",
        data_dir=tmp_path,
        mode="academic",
        no_network=True,
        text_to_verify="FActScore was introduced by a 2099 Nature paper.",
    )

    suggestions = deepen_claim_suggestions_for_run(tmp_path, result.run_id, max_queries=3)

    assert suggestions["overall"] == "ok"
    assert suggestions["deepen_suggestions"]
    assert suggestions["deepen_suggestions"][0]["next_search_queries"]
    assert "missing_evidence_reason" in suggestions["deepen_suggestions"][0]


def test_synthesis_promotes_evidence_to_candidate_claims() -> None:
    text = synthesize_claim_text(
        "SearXNG",
        [
            Evidence(
                id="ev_1",
                source_id="src_1",
                quote_or_paraphrase="SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            )
        ],
    )
    assert "SearXNG is a free internet metasearch engine" in text


def test_pipeline_topic_only_uses_evidence_synthesis(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    assert result.claims
    assert result.claims[0].verdict in {Verdict.SUPPORTED, Verdict.PARTIAL}


def test_external_adapter_normalizes_reused_project_payload() -> None:
    records = normalize_external_payload(
        {
            "adapter": "gpt-researcher",
            "sources": [
                {
                    "id": "src_ext_1",
                    "url": "https://example.com/researcher",
                    "title": "Researcher project",
                    "source_type": "project",
                }
            ],
            "evidence": [
                {
                    "source_id": "src_ext_1",
                    "text": "Reusable deep-research tools should expose sources and evidence separately from generated reports.",
                    "confidence": 0.88,
                }
            ],
            "claims": [{"text": "Deep-research tools can expose evidence separately from generated reports."}],
        },
        topic="external reuse",
    )

    assert records.sources[0].search_engine == "gpt-researcher"
    assert records.evidence[0].source_id == "src_ext_1"
    assert records.claims[0].text.startswith("Deep-research tools")
    assert "Deep-research tools" in records.text_to_verify


def test_openfactcheck_backend_normalizes_result_payload() -> None:
    records = normalize_openfactcheck_result(
        {
            "claims": [{"text": "SearXNG aggregates results from search services.", "verdict": "supported"}],
            "sources": [{"id": "src_ofc_1", "url": "https://docs.searxng.org/", "title": "SearXNG docs"}],
            "evidence": [
                {
                    "source_id": "src_ofc_1",
                    "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                    "confidence": 0.9,
                }
            ],
        },
        topic="SearXNG",
    )

    assert records.sources[0].search_engine == "openfactcheck"
    assert records.evidence[0].source_id == "src_ofc_1"
    assert records.text_to_verify


def test_pipeline_accepts_optional_openfactcheck_backend(tmp_path) -> None:
    def backend(text: str, topic: str) -> dict:
        return {
            "claims": [{"text": f"{topic} is a free internet metasearch engine."}],
            "sources": [{"id": "src_ofc_1", "url": "https://docs.searxng.org/", "title": "SearXNG docs"}],
            "evidence": [
                {
                    "source_id": "src_ofc_1",
                    "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                    "confidence": 0.9,
                }
            ],
            "text_to_verify": "SearXNG is a free internet metasearch engine.",
        }

    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        no_network=True,
        openfactcheck_backend=backend,
    )

    assert result.sources[0].search_engine == "openfactcheck"
    assert result.claims
    assert result.claim_citations[0].citations
    assert result.audit and result.audit["overall"] == "ok"


def test_fire_style_claim_deepen_loop_adds_evidence(monkeypatch) -> None:
    claim = extract_atomic_claims("SearXNG is a free internet metasearch engine.")[0]
    verified = verify_claims([claim], [])
    source = source_from_url("https://docs.searxng.org/")
    evidence = Evidence(
        id="ev_deepen_1",
        source_id=source.id,
        quote_or_paraphrase="SearXNG is a free internet metasearch engine.",
    )

    monkeypatch.setattr("research_workflow.claim_deepen.multi_source_search", lambda *args, **kwargs: [source])
    monkeypatch.setattr("research_workflow.claim_deepen.extract_evidence_from_sources", lambda *args, **kwargs: [evidence])

    result = deepen_uncertain_claims(claims=verified, sources=[], evidence=[], max_rounds=2)

    assert result.claims[0].verdict == Verdict.SUPPORTED
    assert result.claims[0].evidence_ids == ["ev_deepen_1"]
    assert result.attempts[0].before_verdict == Verdict.NOT_FOUND
    assert result.attempts[0].after_verdict == Verdict.SUPPORTED


def test_bm25_sparse_retrieval_ranks_local_evidence() -> None:
    relevant = Evidence(
        id="ev_sparse_relevant",
        source_id="src_a",
        quote_or_paraphrase="SearXNG is a free internet metasearch engine which aggregates results from search services.",
    )
    unrelated = Evidence(
        id="ev_sparse_unrelated",
        source_id="src_b",
        quote_or_paraphrase="A calendar tool stores recurring reminders and local scheduling metadata.",
    )

    matches = retrieve_evidence_bm25("SearXNG metasearch engine", [unrelated, relevant])

    assert [match.evidence.id for match in matches][:1] == ["ev_sparse_relevant"]
    assert matches[0].score > 0


def test_claim_deepen_uses_local_sparse_retrieval_before_external_search(monkeypatch) -> None:
    claim = extract_atomic_claims("SearXNG is a free internet metasearch engine.")[0]
    verified = verify_claims([claim], [])
    evidence = [
        Evidence(
            id="ev_sparse_deepen",
            source_id="src_sparse",
            quote_or_paraphrase="SearXNG is a free internet metasearch engine.",
        )
    ]

    def fail_search(*args, **kwargs):
        raise AssertionError("external search should not run when local sparse evidence is sufficient")

    monkeypatch.setattr("research_workflow.claim_deepen.multi_source_search", fail_search)

    result = deepen_uncertain_claims(claims=verified, sources=[], evidence=evidence, max_rounds=1)

    assert result.claims[0].verdict == Verdict.SUPPORTED
    assert result.claims[0].evidence_ids == ["ev_sparse_deepen"]
    assert result.attempts[0].warning == "local_sparse_retrieval"


def test_openfactcheck_optional_backend_failure_is_warning() -> None:
    def backend(text: str, topic: str) -> dict:
        raise RuntimeError("quota exhausted")

    result = run_openfactcheck_optional(text="claim", topic="topic", backend=backend)

    assert result.records.warnings
    assert "quota exhausted" in result.records.warnings[0]


def test_longtracer_backend_maps_claim_level_verification() -> None:
    source = source_from_url("https://example.com/source")
    evidence = Evidence(
        id="ev_lt_1",
        source_id=source.id,
        quote_or_paraphrase="The Eiffel Tower is in Paris and is 330 meters tall.",
    )

    def checker(response: str, sources: list[str], **kwargs):
        assert "Eiffel" in response
        assert sources == [evidence.quote_or_paraphrase]
        return {
            "trust_score": 0.5,
            "hallucination_count": 1,
            "all_supported": False,
            "verdict": "FAIL",
            "claims": [
                {"claim": "The Eiffel Tower is 330 meters tall.", "supported": True, "score": 0.9},
                {"claim": "The Eiffel Tower is in Berlin.", "supported": False, "score": 0.1, "is_hallucination": True},
            ],
            "flagged_claims": [{"claim": "The Eiffel Tower is in Berlin."}],
            "hallucinations": [{"claim": "The Eiffel Tower is in Berlin."}],
        }

    result = verify_with_longtracer(
        response="The Eiffel Tower is 330 meters tall and in Berlin.",
        sources=[source],
        evidence=[evidence],
        checker=checker,
    )

    assert result.trust_score == 0.5
    assert result.hallucination_count == 1
    assert result.claims[0].verdict == Verdict.SUPPORTED
    assert result.claims[1].verdict == Verdict.CONTRADICTED


def test_academic_reference_check_extracts_doi_and_arxiv() -> None:
    result = check_academic_references("See doi:10.48550/arXiv.2411.00784 and arXiv:2411.00784v1.")

    assert len(result.references) == 2
    assert result.references[0].doi == "10.48550/arXiv.2411.00784"
    assert result.references[1].arxiv_id == "2411.00784v1"
    assert result.validation_summary["counts"]["valid"] >= 1
    assert result.sources[0].source_type == SourceType.ACADEMIC
    assert "doi.org" in result.references[0].urls[0]


def test_academic_reference_extractor_dedupes_identifiers() -> None:
    refs = extract_academic_references("10.1145/123.456 10.1145/123.456 arXiv:2501.00001 arXiv:2501.00001")

    assert [ref.doi for ref in refs if ref.doi] == ["10.1145/123.456"]
    assert [ref.arxiv_id for ref in refs if ref.arxiv_id] == ["2501.00001"]


def test_academic_reference_worker_hook_does_not_block_local_plugin() -> None:
    spec = ExternalWorkerSpec(name="refchecker", command=[])
    result = check_academic_references("See doi:10.1145/3366423.3380124.", worker_spec=spec)

    assert result.references[0].doi == "10.1145/3366423.3380124"
    assert result.sources
    assert result.worker is not None
    assert result.worker.status == "skipped"
    assert result.warnings


def test_academic_reference_check_detects_mismatch_and_unidentified_references() -> None:
    result = check_academic_references(
        "\n".join(
            [
                "[1] Wrong Title mismatch doi:10.1145/3366423.3380124",
                "[2] Imaginary Paper (2026). Journal of Unsupported Claims.",
                "arXiv:9913.00001",
            ]
        )
    )

    statuses = {item.status for item in result.references}
    assert "metadata_mismatch" in statuses
    assert "not_found" in statuses
    assert result.validation_summary["counts"]["not_found"] >= 1


def test_fathom_style_query_expansion_for_high_risk_claim() -> None:
    claim = extract_atomic_claims("OpenAI released model GPT-5 on June 4 2026.")[0]
    claim.risk_level = "high"
    expanded = expand_claim_queries(claim, max_queries=5, include_academic=True)

    assert len(expanded.queries) == 5
    assert expanded.queries[0].startswith("OpenAI released")
    assert any("official" in query for query in expanded.queries)
    assert any("contradiction" in query for query in expanded.queries)


def test_claim_deepen_uses_fathom_style_expanded_queries(monkeypatch) -> None:
    claim = extract_atomic_claims("OpenAI released model GPT-5 on June 4 2026.")[0]
    claim.risk_level = "high"
    verified = verify_claims([claim], [])
    captured_queries: list[str] = []

    def fake_search(query: str, **kwargs):
        captured_queries.append(query)
        raise SearchError("stop after query capture")

    monkeypatch.setattr("research_workflow.claim_deepen.multi_source_search", fake_search)

    result = deepen_uncertain_claims(claims=verified, sources=[], evidence=[], max_rounds=2)

    assert captured_queries[0] == "OpenAI released model GPT-5 on June 4 2026"
    assert result.attempts[0].query == captured_queries[0]


def test_claim_verification_prompt_registry_loads_templates() -> None:
    prompts = load_claim_verification_prompts()

    assert "query_generation_initial" in prompts
    assert "search_decision" in prompts
    assert "evidence_evaluation" in prompts
    assert "model prior knowledge" in get_claim_verification_prompt("evidence_evaluation").lower()


def test_external_worker_wrapper_normalizes_stdout_json() -> None:
    script = (
        "import json; "
        "print(json.dumps({'sources':[{'url':'https://example.com','title':'Example'}],"
        "'evidence':[{'url':'https://example.com','text':'GPT Researcher can emit source-grounded evidence.'}],"
        "'answer':'GPT Researcher can emit source-grounded evidence.'}))"
    )
    result = run_gpt_researcher_worker(
        ExternalWorkerSpec(name="gpt-researcher", command=[sys.executable, "-c", script], timeout_seconds=10),
        topic="worker reuse",
    )

    assert result.status == "ok"
    assert result.records.sources[0].search_engine == "gpt-researcher-worker"
    assert result.records.evidence[0].quote_or_paraphrase.startswith("GPT Researcher")


def test_external_worker_wrapper_failure_is_nonblocking() -> None:
    result = run_loki_worker(
        ExternalWorkerSpec(name="loki", command=[sys.executable, "-c", "import sys; sys.exit(7)"], timeout_seconds=10),
        topic="worker reuse",
    )

    assert result.status == "error"
    assert result.returncode == 7
    assert "exited with code 7" in result.warning


def test_pipeline_reuses_external_payload_as_evidence_and_verifies_report(tmp_path) -> None:
    result = run_research_pipeline(
        topic="external deep research reuse",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        external_payloads=[
            {
                "adapter": "storm-worker",
                "sources": [
                    {
                        "url": "https://example.com/storm-evidence",
                        "title": "STORM evidence export",
                        "source_type": "project",
                    }
                ],
                "evidence": [
                    {
                        "url": "https://example.com/storm-evidence",
                        "text": "Reusable deep-research workers can provide source-grounded evidence for downstream atomic fact checking.",
                    }
                ],
                "report_markdown": "Reusable deep-research workers can provide source-grounded evidence for downstream atomic fact checking.",
            }
        ],
    )

    assert result.sources[0].search_engine == "storm-worker"
    assert result.evidence[0].extraction_method == "external:storm-worker"
    assert result.claims
    assert result.claims[0].verdict in {Verdict.SUPPORTED, Verdict.PARTIAL}
    assert result.claims[0].evidence_ids


def test_cli_run_accepts_external_payload_file(tmp_path, capsys) -> None:
    payload_path = tmp_path / "external.json"
    payload_path.write_text(
        json.dumps(
            {
                "adapter": "factscore-worker",
                "evidence": [
                        {
                            "url": "https://example.com/factscore-evidence",
                            "text": "Atomic fact-checking pipelines can verify generated claims against collected evidence.",
                        }
                    ],
                    "answer": "Atomic fact-checking pipelines can verify generated claims against collected evidence.",
                }
            ),
        encoding="utf-8",
    )

    exit_code = cli_main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "run",
            "external fact check reuse",
            "--no-network",
            "--external-payload",
            str(payload_path),
            "--format",
            "json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sources"][0]["search_engine"] == "factscore-worker"
    assert payload["claims"][0]["evidence_ids"]


def test_pipeline_prefers_current_run_evidence_over_old_store(tmp_path) -> None:
    first = run_research_pipeline(
        topic="Old",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[{"text": "OldTool is an unrelated product with unrelated claims."}],
    )
    assert first.evidence
    second = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    assert all("OldTool" not in item.quote_or_paraphrase for item in second.evidence)


def test_web_run_uses_full_pipeline(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.run(
        {
            "topic": "SearXNG",
            "mode": "daily",
            "no_network": True,
            "urls": ["https://docs.searxng.org/"],
            "manual_evidence": [
                {
                    "url": "https://docs.searxng.org/",
                    "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                }
            ],
            "text_to_verify": "SearXNG is a metasearch engine that aggregates results from many search services.",
        }
    )
    assert result["claims"][0]["evidence_ids"]
    assert result["run_id"]
    assert result["audit"]["overall"] == "ok"
    assert "Evidence Table" in result["report_markdown"]
    assert "Claim Citations" in result["report_markdown"]
    assert result["claim_citations"][0]["citations"]
    exported = app.export()
    assert exported["data"]["runs"][0]["id"] == result["run_id"]
    assert exported["data"]["runs"][0]["evidence"]


def test_web_run_accepts_external_payloads(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)

    result = app.run(
        {
            "topic": "external worker",
            "no_network": True,
            "external_payloads": {
                "adapter": "openfactcheck-worker",
                "evidence": [
                    {
                        "url": "https://example.com/openfactcheck",
                        "text": "External fact-check workers can pass evidence into the local research workflow.",
                    }
                ],
                "answer": "External fact-check workers can pass evidence into the local research workflow.",
            },
        }
    )

    assert result["sources"][0]["search_engine"] == "openfactcheck-worker"
    assert result["claims"][0]["evidence_ids"]
    assert result["audit"]["overall"] == "ok"


def test_web_exports_run_to_obsidian(tmp_path) -> None:
    app = ResearchWebApp(tmp_path, config=load_config(None))
    result = app.run(
        {
            "topic": "SearXNG",
            "no_network": True,
            "manual_evidence": [
                {
                    "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                }
            ],
        }
    )
    vault = tmp_path / "vault"
    exported = app.obsidian({"run_id": result["run_id"], "vault": str(vault), "folder": "Research Notes"})
    assert exported["path"].endswith(".md")
    note = Path(exported["path"]).read_text(encoding="utf-8")
    assert "run_id:" in note
    assert "workflow_kind:" in note
    assert "audit_overall:" in note
    assert "source_quality:" in note
    assert "evidence_quality:" in note
    assert "weak_claim_count:" in note
    assert "reference_check_overall:" in note
    assert "## Evidence Quality Review" in note
    assert "## Claim Citation Table" in note
    assert "## Claim Deepen Suggestions" in note
    assert "## References Check" in note
    assert "## Run Metadata" in note


def test_web_audits_run(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.run(
        {
            "topic": "SearXNG",
            "no_network": True,
            "manual_evidence": [
                {
                    "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                }
            ],
        }
    )
    audited = app.audit(result["run_id"])
    assert audited["overall"] == "ok"


def test_web_quality_deepen_claims_and_reference_checks(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.run(
        {
            "topic": "FActScore",
            "mode": "academic",
            "no_network": True,
            "text_to_verify": "FActScore was introduced by a 2099 Nature paper.",
        }
    )

    quality = app.quality(result["run_id"])
    deepen = app.deepen_claims(result["run_id"], {"max_queries": 2})
    references = app.check_references({"text": "See doi:10.1145/3366423.3380124 and arXiv:9913.00001."})

    assert "quality_summary" in quality
    assert deepen["deepen_suggestions"]
    assert references["validation_summary"]["counts"]["not_found"] >= 1


def test_web_ui_exposes_quality_review_actions() -> None:
    index = Path("web/index.html").read_text(encoding="utf-8")
    app_js = Path("web/app.js").read_text(encoding="utf-8")

    assert "qualityReviewBtn" in index
    assert "claimSuggestBtn" in index
    assert "referenceCheckBtn" in index
    assert "/v1/runs/${encodeURIComponent(runId)}/quality" in app_js
    assert "/v1/runs/${encodeURIComponent(runId)}/deepen-claims" in app_js
    assert "/v1/references/check" in app_js
    assert "citation coverage" in app_js
    assert "source diversity" in app_js


def test_web_runs_selftest(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.selftest({})
    assert result["overall"] == "ok"
    assert any(check["name"] == "mcp_run" for check in result["checks"])
    assert (tmp_path / "selftest" / "data" / "runs.jsonl").exists()


def test_web_runs_evals(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.evals({})
    assert result["overall"] == "ok"
    assert result["case_count"] >= 20
    assert (tmp_path / "evals" / "hallucination_trigger" / "runs.jsonl").exists()


def test_web_runs_readiness_quick_report(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    result = app.readiness({})
    assert result["overall"] == "warn"
    assert result["ready"] is False
    names = {check["name"] for check in result["checks"]}
    assert {"doctor", "selftest", "evals", "integrations", "web_launcher"}.issubset(names)


def test_mcp_server_lists_and_calls_full_chain(tmp_path) -> None:
    initialized = handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    assert initialized is not None
    assert initialized["result"]["serverInfo"]["version"] == package_version()

    listed = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "run_research" in names
    assert "run_search" in names
    assert "doctor" in names
    assert "list_runs" in names
    assert "get_run" in names
    assert "export_obsidian" in names
    assert "audit" in names
    assert "selftest" in names
    assert "evals" in names
    assert "bootstrap" in names
    assert "readiness" in names
    assert "research_search" in names
    assert "research_deepen" in names
    assert "research_verify" in names
    assert "research_ingest" in names
    assert "research_get_report" in names
    assert "research_status" in names
    assert "research_quality" in names
    assert "research_deepen_claims" in names
    assert "research_check_references" in names
    assert "verify_with_longtracer" in names
    assert "check_academic_references" in names
    assert "expand_claim_search_queries" in names
    assert "list_claim_verification_prompts" in names
    assert "get_claim_verification_prompt" in names
    tools_by_name = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert "provider_health" in tools_by_name["research_search"]["description"]
    assert "provenance" in tools_by_name["research_search"]["description"]
    assert "manual provenance" in tools_by_name["research_ingest"]["description"]
    assert "LongTracer" in tools_by_name["verify_with_longtracer"]["description"]

    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "run_research",
                "arguments": {
                    "topic": "SearXNG",
                    "no_network": True,
                    "data_dir": str(tmp_path),
                    "manual_evidence": [
                        {
                            "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                        }
                    ],
                },
            },
        }
    )
    assert called is not None
    text = called["result"]["content"][0]["text"]
    assert '"run_id"' in text
    assert '"audit"' in text
    assert '"evidence"' in text
    assert '"verdict": "supported"' in text


def test_mcp_exposes_embedded_reference_modules() -> None:
    prompt_result = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tools/call",
            "params": {"name": "get_claim_verification_prompt", "arguments": {"name": "evidence_evaluation"}},
        }
    )
    assert prompt_result is not None
    prompt_payload = json.loads(prompt_result["result"]["content"][0]["text"])
    assert "provided evidence" in prompt_payload["text"]

    reference_result = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tools/call",
            "params": {
                "name": "check_academic_references",
                "arguments": {"text": "See doi:10.1145/3366423.3380124 and arXiv:2401.12345."},
            },
        }
    )
    assert reference_result is not None
    reference_payload = json.loads(reference_result["result"]["content"][0]["text"])
    assert len(reference_payload["references"]) == 2
    assert reference_payload["sources"]

    query_result = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {
                "name": "expand_claim_search_queries",
                "arguments": {
                    "claim": {
                        "text": "SearXNG released version 2024.5 in 2024.",
                        "claim_type": "date",
                        "risk_level": "high",
                    }
                },
            },
        }
    )
    assert query_result is not None
    query_payload = json.loads(query_result["result"]["content"][0]["text"])
    assert query_payload["queries"]
    assert query_payload["strategy"] == "fathom_hyde_light"


def test_mcp_run_search_tool(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "run_search",
                "arguments": {
                    "topic": "SearXNG",
                    "no_network": True,
                    "data_dir": str(tmp_path),
                    "urls": ["https://docs.searxng.org/"],
                },
            },
        }
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["kind"] == "search"
    assert payload["claims"] == []
    assert payload["sources"]


def test_mcp_recommended_research_tools(tmp_path) -> None:
    search = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {
                "name": "research_search",
                "arguments": {
                    "query": "SearXNG",
                    "mode": "daily",
                    "no_network": True,
                    "data_dir": str(tmp_path),
                    "urls": ["https://docs.searxng.org/"],
                },
            },
        }
    )
    assert search is not None
    search_payload = json.loads(search["result"]["content"][0]["text"])
    run_id = search_payload["run_id"]
    assert search_payload["kind"] == "search"

    status = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {"name": "research_status", "arguments": {"data_dir": str(tmp_path), "run_id": run_id}},
        }
    )
    assert status is not None
    status_payload = json.loads(status["result"]["content"][0]["text"])
    assert status_payload["run_id"] == run_id
    assert status_payload["source_count"] >= 1

    report = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {"name": "research_get_report", "arguments": {"data_dir": str(tmp_path), "run_id": run_id, "format": "md"}},
        }
    )
    assert report is not None
    report_payload = json.loads(report["result"]["content"][0]["text"])
    assert report_payload["format"] == "md"
    assert "SearXNG" in report_payload["content"]


def test_mcp_recommended_ingest_and_verify(tmp_path) -> None:
    ingested = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 33,
            "method": "tools/call",
            "params": {"name": "research_ingest", "arguments": {"data_dir": str(tmp_path), "urls": ["https://docs.searxng.org/"]}},
        }
    )
    assert ingested is not None
    ingest_payload = json.loads(ingested["result"]["content"][0]["text"])
    assert ingest_payload["sources"]
    assert ingest_payload["sources"][0]["search_buckets"] == ["manual URLs"]
    assert ingest_payload["sources"][0]["search_stages"] == ["mcp_ingest"]

    verified = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 34,
            "method": "tools/call",
            "params": {
                "name": "research_verify",
                "arguments": {
                    "data_dir": str(tmp_path),
                    "text_or_run_id": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
                },
            },
        }
    )
    assert verified is not None
    verify_payload = json.loads(verified["result"]["content"][0]["text"])
    assert verify_payload["claims"]
    assert "revision_suggestions" in verify_payload


def test_watch_folder_processes_search_job(tmp_path) -> None:
    jobs_root = tmp_path / "jobs"
    inbox = jobs_root / "inbox"
    inbox.mkdir(parents=True)
    config = load_config(None)
    (inbox / "job_001.json").write_text(
        json.dumps(
            {
                "id": "job_001",
                "type": "search",
                "topic": "SearXNG",
                "mode": "daily",
                "no_network": True,
                "urls": ["https://docs.searxng.org/"],
            }
        ),
        encoding="utf-8",
    )
    result = process_watch_folder(root=jobs_root, data_dir=tmp_path / "data", config=config)
    payload = result.to_dict()
    assert payload["processed_count"] == 1
    assert payload["failed_count"] == 0
    assert (jobs_root / "outbox" / "job_001.report.md").exists()
    assert (jobs_root / "outbox" / "job_001.report.json").exists()
    assert (jobs_root / "outbox" / "job_001.status.json").exists()
    status = json.loads((jobs_root / "outbox" / "job_001.status.json").read_text(encoding="utf-8"))
    assert status["service"] == "ai-research-workflow"
    assert status["contract_version"] == CONTRACT_VERSION
    assert status["config_hash"] == config_fingerprint(config)
    assert status["created_outputs"]
    assert status["report_md"] in status["created_outputs"]
    assert status["report_json"] in status["created_outputs"]
    assert status["source_provenance"][0]["search_buckets"] == ["manual URLs"]
    assert status["source_provenance"][0]["search_stages"] == ["manual_ingest"]
    assert not (jobs_root / "inbox" / "job_001.json").exists()


def test_watch_folder_processes_fetch_job(tmp_path) -> None:
    jobs_root = tmp_path / "jobs"
    inbox = jobs_root / "inbox"
    inbox.mkdir(parents=True)
    config = load_config(None)
    (inbox / "job_fetch.json").write_text(
        json.dumps({"id": "job_fetch", "type": "fetch", "urls": ["https://docs.searxng.org/"], "fetch": False}),
        encoding="utf-8",
    )

    result = process_watch_folder(root=jobs_root, data_dir=tmp_path / "data", config=config)

    assert result.to_dict()["processed_count"] == 1
    status = json.loads((jobs_root / "outbox" / "job_fetch.status.json").read_text(encoding="utf-8"))
    report = json.loads((jobs_root / "outbox" / "job_fetch.report.json").read_text(encoding="utf-8"))
    assert status["type"] == "fetch"
    assert status["status"] == "ok"
    assert status["contract_version"] == CONTRACT_VERSION
    assert status["config_hash"] == config_fingerprint(config)
    assert status["source_count"] == 1
    assert status["evidence_count"] == 0
    assert status["report_json"] in status["created_outputs"]
    assert report["sources"][0]["search_stages"] == ["watch_folder_ingest"]
    assert report["evidence"] == []


def test_watch_folder_failed_job_includes_contract_metadata(tmp_path) -> None:
    jobs_root = tmp_path / "jobs"
    inbox = jobs_root / "inbox"
    inbox.mkdir(parents=True)
    config = load_config(None)
    (inbox / "job_bad.json").write_text(json.dumps({"id": "job_bad", "type": "verify"}), encoding="utf-8")

    result = process_watch_folder(root=jobs_root, data_dir=tmp_path / "data", config=config)

    payload = result.to_dict()
    assert payload["processed_count"] == 0
    assert payload["failed_count"] == 1
    status = json.loads((jobs_root / "failed" / "job_bad.status.json").read_text(encoding="utf-8"))
    assert status["service"] == "ai-research-workflow"
    assert status["type"] == "verify"
    assert status["status"] == "failed"
    assert status["contract_version"] == CONTRACT_VERSION
    assert status["config_hash"] == config_fingerprint(config)
    assert status["created_outputs"] == []
    assert "verify job requires text" in status["error"]


def test_cli_jobs_processes_watch_folder(tmp_path, capsys) -> None:
    jobs_root = tmp_path / "jobs"
    inbox = jobs_root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "job_002.json").write_text(
        json.dumps({"id": "job_002", "type": "search", "topic": "SearXNG", "no_network": True, "urls": ["https://docs.searxng.org/"]}),
        encoding="utf-8",
    )
    exit_code = cli_main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "jobs",
            "--jobs-dir",
            str(jobs_root),
            "--format",
            "json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["processed_count"] == 1
    assert (jobs_root / "outbox" / "job_002.status.json").exists()

def test_cli_http_mcp_and_watch_folder_share_search_interface_contract(tmp_path, capsys) -> None:
    topic = "fixture interface contract"
    url = "https://docs.example.test/fixture"
    expected = {
        "schema": "research.search-result.v1",
        "task_status": "succeeded",
        "evidence_status": "collected",
        "source_trail_status": "complete",
        "source_count": 1,
        "evidence_count": 1,
        "claim_count": 0,
    }

    cli_data = tmp_path / "cli"
    assert cli_main(
        [
            "--data-dir",
            str(cli_data),
            "search",
            topic,
            "--no-network",
            "--url",
            url,
            "--format",
            "json",
        ]
    ) == 0
    cli_payload = json.loads(capsys.readouterr().out)

    http_app = ResearchWebApp(tmp_path / "http", config=ResearchConfig(search_providers=[]))
    http_payload = http_app.search({"topic": topic, "no_network": True, "urls": [url]})

    mcp_result = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 401,
            "method": "tools/call",
            "params": {
                "name": "research_search",
                "arguments": {
                    "query": topic,
                    "no_network": True,
                    "urls": [url],
                    "data_dir": str(tmp_path / "mcp"),
                },
            },
        }
    )
    assert mcp_result is not None
    mcp_payload = json.loads(mcp_result["result"]["content"][0]["text"])

    jobs_root = tmp_path / "watch"
    inbox = jobs_root / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "contract.json").write_text(
        json.dumps({"id": "contract", "type": "search", "topic": topic, "no_network": True, "urls": [url]}),
        encoding="utf-8",
    )
    watch_result = process_watch_folder(
        root=jobs_root,
        data_dir=tmp_path / "watch-data",
        config=ResearchConfig(search_providers=[]),
    )
    assert watch_result.failed == []
    watch_payload = json.loads((jobs_root / "outbox" / "contract.status.json").read_text(encoding="utf-8"))

    contracts = [
        cli_payload["interface_contract"],
        http_payload["interface_contract"],
        mcp_payload["interface_contract"],
        watch_payload["interface_contract"],
    ]
    assert contracts == [expected, expected, expected, expected]


def test_mcp_doctor_tool(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "doctor",
                "arguments": {"data_dir": str(tmp_path), "include_pipeline": False},
            },
        }
    )
    assert called is not None
    text = called["result"]["content"][0]["text"]
    assert '"overall"' in text
    assert '"mcp_tools"' in text


def test_mcp_lists_and_gets_runs(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    listed = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "list_runs",
                "arguments": {"data_dir": str(tmp_path)},
            },
        }
    )
    assert listed is not None
    assert result.run_id in listed["result"]["content"][0]["text"]
    fetched = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_run",
                "arguments": {"data_dir": str(tmp_path), "run_id": result.run_id},
            },
        }
    )
    assert fetched is not None
    text = fetched["result"]["content"][0]["text"]
    assert result.run_id in text
    assert "report_markdown" in text


def test_mcp_run_research_accepts_external_payloads(tmp_path) -> None:
    from research_workflow.mcp_tools import run_research as mcp_run_research

    result = mcp_run_research(
        topic="external mcp worker",
        data_dir=str(tmp_path),
        no_network=True,
        external_payloads=[
            {
                "adapter": "rarr-worker",
                "evidence": [
                    {
                        "url": "https://example.com/rarr",
                        "text": "External revision workers can hand evidence to a downstream verifier.",
                    }
                ],
                "answer": "External revision workers can hand evidence to a downstream verifier.",
            }
        ],
    )

    assert result["sources"][0]["search_engine"] == "rarr-worker"
    assert result["claims"][0]["evidence_ids"]


def test_mcp_exports_obsidian(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    vault = tmp_path / "vault"
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "export_obsidian",
                "arguments": {"data_dir": str(tmp_path), "run_id": result.run_id, "vault": str(vault)},
            },
        }
    )
    assert called is not None
    text = called["result"]["content"][0]["text"]
    assert result.run_id in text
    assert list((vault / "AI Research").glob("*.md"))


def test_mcp_audits_run(tmp_path) -> None:
    result = run_research_pipeline(
        topic="SearXNG",
        data_dir=tmp_path,
        mode="daily",
        no_network=True,
        manual_evidence=[
            {
                "text": "SearXNG is a free internet metasearch engine which aggregates results from many search services.",
            }
        ],
    )
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "audit",
                "arguments": {"data_dir": str(tmp_path), "run_id": result.run_id},
            },
        }
    )
    assert called is not None
    text = called["result"]["content"][0]["text"]
    assert '"overall": "ok"' in text
    assert '"claim_evidence_links"' in text


def test_mcp_quality_deepen_claims_and_reference_checks(tmp_path) -> None:
    result = run_research_pipeline(
        topic="FActScore",
        data_dir=tmp_path,
        mode="academic",
        no_network=True,
        text_to_verify="FActScore was introduced by a 2099 Nature paper.",
    )
    quality = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 701,
            "method": "tools/call",
            "params": {"name": "research_quality", "arguments": {"data_dir": str(tmp_path), "run_id": result.run_id}},
        }
    )
    deepen = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 702,
            "method": "tools/call",
            "params": {"name": "research_deepen_claims", "arguments": {"data_dir": str(tmp_path), "run_id": result.run_id, "max_queries": 2}},
        }
    )
    references = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 703,
            "method": "tools/call",
            "params": {"name": "research_check_references", "arguments": {"text": "arXiv:9913.00001"}},
        }
    )

    assert quality is not None
    assert '"quality_summary"' in quality["result"]["content"][0]["text"]
    assert deepen is not None
    assert '"deepen_suggestions"' in deepen["result"]["content"][0]["text"]
    assert references is not None
    assert '"not_found"' in references["result"]["content"][0]["text"]


def test_mcp_selftest(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "selftest",
                "arguments": {"work_dir": str(tmp_path / "selftest")},
            },
        }
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["overall"] == "ok"
    assert any(check["name"] == "mcp_run" for check in payload["checks"])


def test_mcp_evals(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "evals",
                "arguments": {"work_dir": str(tmp_path / "evals")},
            },
        }
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["overall"] == "ok"
    assert payload["case_count"] >= 20


def test_mcp_bootstrap(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "bootstrap",
                "arguments": {
                    "data_dir": str(tmp_path / "data"),
                    "config_path": str(tmp_path / "research.config.json"),
                    "integrations_dir": str(tmp_path / "integrations"),
                    "base_url": "http://127.0.0.1:8774",
                    "skip_selftest": True,
                },
            },
        }
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["overall"] == "ok"
    assert payload["config"]["created"] is True
    assert payload["selftest"] is None
    assert (tmp_path / "integrations" / "openwebui_research_workflow_tool.py").exists()


def test_mcp_readiness(tmp_path) -> None:
    called = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "readiness",
                "arguments": {"work_dir": str(tmp_path / "readiness")},
            },
        }
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["overall"] == "ok"
    assert payload["ready"] is True
