from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent_diagnose import agent_diagnose_to_json, agent_diagnose_to_markdown, run_agent_diagnose
from .academic_reference_check import check_academic_references, references_check_to_markdown
from .agent_smoke import agent_smoke_to_markdown, run_agent_smoke
from .answer_benchmark import answer_result_to_markdown, evaluate_answer_case, explain_answer_case, load_answer_cases, run_answer_benchmark
from .audit import audit_run, audit_to_markdown
from .bootstrap import bootstrap_to_json, bootstrap_to_markdown, run_bootstrap
from .config import ResearchConfig, config_fingerprint, load_config, web_base_url, write_default_config
from .contract_test import contract_test_to_json, contract_test_to_markdown, run_contract_test
from .diagnostics import doctor_to_markdown, run_doctor
from .evidence import extract_evidence_from_sources, source_from_url
from .evals import evals_to_json, evals_to_markdown, run_evals
from .evidence_bundle_reference import (
    CANONICAL_SCHEMA_SHA256,
    RECEIPT_SCHEMA as EVIDENCE_BUNDLE_RECEIPT_SCHEMA,
    verify_evidence_bundle_reference,
)
from .external_adapters import load_external_payloads
from .factcheck import extract_atomic_claims, verify_claims
from .insights import claim_citations_from_claims
from .integrations import write_integrations
from .launcher import launcher_to_json, launcher_to_markdown, start_web, stop_web, web_status
from .metadata import version_info, version_info_to_markdown
from .model_gateway import RECEIPT_SCHEMA, run_model_assist
from .models import Claim, Evidence, Report, Source
from .obsidian_export import export_run_to_obsidian
from .pipeline import dedupe_rows, get_run, list_runs, run_research_pipeline
from .planner import plan_query
from .quality import deepen_claim_suggestions_for_run, deepen_suggestions_to_markdown, quality_to_markdown, run_quality
from .readiness import readiness_to_json, readiness_to_markdown, run_readiness
from .release_check import release_check_to_json, release_check_to_markdown, run_release_check
from .routing import PROFILES, ROUTING_STRATEGIES, build_routing_plan, provider_catalog, routing_plan_to_markdown
from .report import draft_report, report_to_json, report_to_markdown
from .revisions import suggest_revisions
from .search import SearchError, multi_source_search
from .search_pipeline import run_search_pipeline
from .selftest import run_selftest, selftest_to_markdown
from .service_registry import update_service_smoke
from .service_status import build_service_recovery_proposal, run_service_status, service_recovery_proposal_to_markdown, service_status_to_markdown
from .sqlite_store import sync_jsonl_to_sqlite_quietly
from .store import DEFAULT_DATA_DIR, append_jsonl, ensure_data_dir, read_jsonl, write_text
from .watch_folder import process_watch_folder, watch_folder_to_markdown


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    config_path = _preparse_config_path(argv)
    config = load_config(config_path)
    parser = build_parser(config)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    data_dir_arg = getattr(args, "command_data_dir", None) or args.data_dir
    data_dir = Path(data_dir_arg or config.data_dir or DEFAULT_DATA_DIR)
    if args.command not in {"route", "answer-evaluate", "answer-explain", "answer-benchmark", "evidence-bundle-verify", "model-assist"}:
        data_dir = ensure_data_dir(data_dir)

    if args.command == "config":
        return command_config(args, config)
    if args.command == "route":
        return command_route(args, config)
    if args.command == "version":
        return command_version(args, config)
    if args.command == "doctor":
        return command_doctor(args, data_dir, config)
    if args.command == "search":
        return command_search(args, data_dir, config)
    if args.command == "query":
        return command_query(args, data_dir, config)
    if args.command == "verify":
        return command_verify(args, data_dir)
    if args.command == "ingest":
        return command_ingest(args, data_dir, config)
    if args.command == "export":
        return command_export(args, data_dir)
    if args.command == "run":
        return command_run(args, data_dir, config)
    if args.command == "runs":
        return command_runs(args, data_dir)
    if args.command == "show":
        return command_show(args, data_dir)
    if args.command == "obsidian":
        return command_obsidian(args, data_dir, config)
    if args.command == "audit":
        return command_audit(args, data_dir)
    if args.command == "quality":
        return command_quality(args, data_dir)
    if args.command == "deepen-claims":
        return command_deepen_claims(args, data_dir)
    if args.command == "references":
        return command_references(args, data_dir)
    if args.command == "answer-evaluate":
        return command_answer_evaluate(args)
    if args.command == "answer-explain":
        return command_answer_explain(args)
    if args.command == "answer-benchmark":
        return command_answer_benchmark(args)
    if args.command == "selftest":
        return command_selftest(args, config)
    if args.command == "integrations":
        return command_integrations(args, config)
    if args.command == "bootstrap":
        return command_bootstrap(args, data_dir, config)
    if args.command == "web":
        return command_web(args, data_dir, config)
    if args.command == "service":
        return command_service(args, data_dir, config)
    if args.command == "contract-test":
        return command_contract_test(args, data_dir, config)
    if args.command == "evals":
        return command_evals(args, config)
    if args.command == "readiness":
        return command_readiness(args, config)
    if args.command == "jobs":
        return command_jobs(args, data_dir, config)
    if args.command == "agent-smoke":
        return command_agent_smoke(args, data_dir, config)
    if args.command == "agent-diagnose":
        return command_agent_diagnose(args, data_dir, config)
    if args.command == "release-check":
        return command_release_check(args, config)
    if args.command == "evidence-bundle-verify":
        return command_evidence_bundle_verify(args)
    if args.command == "model-assist":
        return command_model_assist(args)
    parser.print_help()
    return 2


def build_parser(config: ResearchConfig | None = None) -> argparse.ArgumentParser:
    config = config or ResearchConfig()
    parser = argparse.ArgumentParser(prog="research", description="Local-first research and atomic fact checking")
    parser.add_argument("--config", help="Path to research.config.json")
    parser.add_argument("--data-dir", default=str(config.data_dir or DEFAULT_DATA_DIR), help="Data directory for JSONL stores")
    sub = parser.add_subparsers(dest="command")

    search = sub.add_parser("search", help="Run the search-only chain: plan, retrieve, extract evidence, and persist a search report")
    _add_search_arguments(search, config)

    query = sub.add_parser("query", help="Alias for search-only retrieval and draft report generation")
    _add_search_arguments(query, config)

    route = sub.add_parser("route", help="Plan search routing without calling providers or changing state")
    route.add_argument("topic")
    route.add_argument("--mode", choices=["daily", "academic", "tool_selection", "auto"], default="auto")
    route.add_argument("--profile", choices=PROFILES, default=config.search_profile)
    route.add_argument("--routing-strategy", choices=ROUTING_STRATEGIES, default=config.routing_strategy)
    route.add_argument("--provider", action="append", choices=sorted(provider_catalog()), help="Exact manual provider override; can be repeated")
    route.add_argument("--deep", action="store_true")
    route.add_argument("--format", choices=["md", "json"], default="md")

    verify = sub.add_parser("verify", help="Extract and verify atomic claims in a Markdown/text file")
    _add_data_dir_argument(verify)
    verify.add_argument("path")
    verify.add_argument("--no-revisions", action="store_true", help="Do not generate revision suggestions")

    ingest = sub.add_parser("ingest", help="Ingest manually selected URLs from a text file")
    _add_data_dir_argument(ingest)
    ingest.add_argument("path")
    ingest.add_argument("--fetch", action="store_true", default=config.fetch_urls, help="Fetch page text when available")

    export = sub.add_parser("export", help="Export stored research data")
    _add_data_dir_argument(export)
    export.add_argument("--format", choices=["md", "json"], default="md")

    run = sub.add_parser("run", help="Run query, optional ingest/evidence, verification, and report generation")
    _add_data_dir_argument(run)
    run.add_argument("topic")
    run.add_argument("--mode", choices=["daily", "academic", "tool_selection", "auto"], default="auto")
    run.add_argument("--max-results", type=int, default=config.max_results)
    run.add_argument("--no-network", action="store_true")
    run.add_argument("--provider", action="append", choices=sorted(provider_catalog()), help="Exact search provider override; can be repeated")
    run.add_argument("--profile", choices=PROFILES, default=config.search_profile)
    run.add_argument("--routing-strategy", choices=ROUTING_STRATEGIES, default=config.routing_strategy)
    run.add_argument("--url", action="append", default=[], help="Manual source URL; can be repeated")
    run.add_argument("--urls-file", help="Text file with one source URL per line")
    run.add_argument("--evidence", action="append", default=[], help="Manual evidence note; can be repeated")
    run.add_argument("--evidence-url", action="append", default=[], help="Source URL for the matching --evidence item; can be repeated")
    run.add_argument("--verify-text", help="Text to verify instead of the generated draft")
    run.add_argument("--verify-file", help="File containing text to verify")
    run.add_argument("--external-payload", action="append", default=[], help="JSON payload from a reused external research/fact-check tool; can be repeated")
    run.add_argument("--fetch", action="store_true", default=config.fetch_urls, help="Fetch page text for URL evidence")
    run.add_argument("--format", choices=["md", "json"], default="md")

    config_cmd = sub.add_parser("config", help="Show or initialize local configuration")
    config_cmd.add_argument("--init", action="store_true", help="Write a default research.config.json")
    config_cmd.add_argument("--path", default="research.config.json", help="Config path for --init")
    config_cmd.add_argument("--overwrite", action="store_true", help="Overwrite an existing config file")

    version_cmd = sub.add_parser("version", help="Show package, API, contract, config, and git metadata")
    version_cmd.add_argument("--format", choices=["md", "json"], default="md")

    doctor = sub.add_parser("doctor", help="Check config, storage, search, MCP, Web assets, and local pipeline")
    _add_data_dir_argument(doctor)
    doctor.add_argument("--format", choices=["md", "json"], default="md")
    doctor.add_argument("--skip-pipeline", action="store_true", help="Skip the local sample pipeline check")

    runs = sub.add_parser("runs", help="List persisted research runs")
    _add_data_dir_argument(runs)
    runs.add_argument("--limit", type=int, default=20)
    runs.add_argument("--format", choices=["md", "json"], default="md")

    show = sub.add_parser("show", help="Show a persisted run report by run ID")
    _add_data_dir_argument(show)
    show.add_argument("run_id")
    show.add_argument("--format", choices=["md", "json"], default="md")
    show.add_argument("--output", help="Optional file path to write the selected run")

    obsidian = sub.add_parser("obsidian", help="Export a persisted run as an Obsidian note")
    _add_data_dir_argument(obsidian)
    obsidian.add_argument("run_id")
    obsidian.add_argument("--vault", default=config.obsidian_vault, help="Obsidian vault root path")
    obsidian.add_argument("--folder", default=config.obsidian_folder, help="Folder inside the vault")
    obsidian.add_argument("--overwrite", action="store_true", help="Overwrite an existing note")

    audit = sub.add_parser("audit", help="Audit a persisted run for evidence and report integrity")
    _add_data_dir_argument(audit)
    audit.add_argument("run_id")
    audit.add_argument("--format", choices=["md", "json"], default="md")

    quality = sub.add_parser("quality", help="Summarize citation coverage, source diversity, and evidence strength for a run")
    _add_data_dir_argument(quality)
    quality.add_argument("run_id")
    quality.add_argument("--format", choices=["md", "json"], default="md")

    deepen_claims = sub.add_parser("deepen-claims", help="Generate next search queries for partial, not_found, or high-risk weak claims")
    _add_data_dir_argument(deepen_claims)
    deepen_claims.add_argument("run_id")
    deepen_claims.add_argument("--max-queries", type=int, default=5)
    deepen_claims.add_argument("--format", choices=["md", "json"], default="md")

    references = sub.add_parser("references", help="Check DOI/arXiv references in a file or persisted run")
    _add_data_dir_argument(references)
    references.add_argument("path_or_run_id")
    references.add_argument("--format", choices=["md", "json"], default="md")

    answer_evaluate = sub.add_parser("answer-evaluate", help="Read-only offline evaluation of whether local evidence supports an answer")
    answer_evaluate.add_argument("path", help="JSON fixture path")
    answer_evaluate.add_argument("--case-id", default="", help="Case id when the input contains multiple cases")
    answer_evaluate.add_argument("--format", choices=["md", "json"], default="md")

    answer_explain = sub.add_parser("answer-explain", help="Explain offline answer readiness gates and evidence gaps")
    answer_explain.add_argument("path", help="JSON fixture path")
    answer_explain.add_argument("--case-id", default="", help="Case id when the input contains multiple cases")
    answer_explain.add_argument("--format", choices=["md", "json"], default="md")

    answer_benchmark = sub.add_parser("answer-benchmark", help="Run all offline answer-readiness fixtures and compare expected states")
    answer_benchmark.add_argument("path", help="JSON fixture suite path")
    answer_benchmark.add_argument("--format", choices=["md", "json"], default="md")

    selftest = sub.add_parser("selftest", help="Run local end-to-end smoke tests for pipeline, audit, MCP, and Obsidian export")
    selftest.add_argument("--format", choices=["md", "json"], default="md")
    selftest.add_argument("--work-dir", help="Optional directory to keep selftest artifacts")

    integrations = sub.add_parser("integrations", help="Write adapter files for Open WebUI and other open-source search UIs")
    integrations.add_argument("--output", default="integrations", help="Output directory for generated integration files")
    integrations.add_argument("--base-url", default=web_base_url(config), help="Research Workflow Web UI base URL")
    integrations.add_argument("--format", choices=["md", "json"], default="md")

    bootstrap = sub.add_parser("bootstrap", help="Initialize config, write integrations, run doctor, and run selftest")
    _add_data_dir_argument(bootstrap)
    bootstrap.add_argument("--config-path", default="research.config.json", help="Config file to create if missing")
    bootstrap.add_argument("--integrations-dir", default="integrations", help="Output directory for integration files")
    bootstrap.add_argument("--base-url", default=web_base_url(config), help="Research Workflow Web UI base URL")
    bootstrap.add_argument("--skip-selftest", action="store_true", help="Skip full local selftest")
    bootstrap.add_argument("--format", choices=["md", "json"], default="md")

    web = sub.add_parser("web", help="Manage the local Web UI service")
    _add_data_dir_argument(web)
    web_sub = web.add_subparsers(dest="web_command", required=True)
    web_start = web_sub.add_parser("start", help="Start the Web UI in the background")
    web_start.add_argument("--host", default=config.web_host)
    web_start.add_argument("--port", type=int, default=config.web_port)
    web_start.add_argument("--open", action="store_true", help="Open the Web UI in the default browser")
    web_start.add_argument("--dry-run", action="store_true", help="Print the command without starting the server")
    web_start.add_argument("--format", choices=["md", "json"], default="md")
    web_status_cmd = web_sub.add_parser("status", help="Show Web UI service status")
    web_status_cmd.add_argument("--host", default=config.web_host)
    web_status_cmd.add_argument("--port", type=int, default=config.web_port)
    web_status_cmd.add_argument("--format", choices=["md", "json"], default="md")
    web_stop = web_sub.add_parser("stop", help="Stop the Web UI process recorded in the pid file")
    web_stop.add_argument("--host", default=config.web_host)
    web_stop.add_argument("--port", type=int, default=config.web_port)
    web_stop.add_argument("--format", choices=["md", "json"], default="md")

    service = sub.add_parser("service", help="Inspect stable service status for external agents")
    _add_data_dir_argument(service)
    service_sub = service.add_subparsers(dest="service_command", required=True)
    service_status = service_sub.add_parser("status", help="Check config URL, service record, live listener, and /v1/health")
    service_status.add_argument("--timeout", type=float, default=2, help="HTTP/socket timeout in seconds")
    service_status.add_argument("--format", choices=["md", "json"], default="md")

    contract = sub.add_parser("contract-test", help="Validate the stable external integration contract")
    service_proposal = service_sub.add_parser("proposal", help="Print a read-only stale-record recovery proposal")
    service_proposal.add_argument("--timeout", type=float, default=2, help="HTTP/socket timeout in seconds")
    service_proposal.add_argument("--format", choices=["md", "json"], default="md")
    _add_data_dir_argument(contract)
    contract.add_argument("--live", action="store_true", help="Also call the running HTTP service with agent-smoke")
    contract.add_argument("--base-url", default=web_base_url(config), help="Research Workflow Web UI base URL for live contract checks")
    contract.add_argument("--timeout", type=float, default=60, help="Live contract polling timeout in seconds")
    contract.add_argument("--interval", type=float, default=1, help="Live contract polling interval in seconds")
    contract.add_argument("--format", choices=["md", "json"], default="md")

    evals = sub.add_parser("evals", help="Run the three local acceptance scenarios from the original test plan")
    evals.add_argument("--format", choices=["md", "json"], default="md")
    evals.add_argument("--work-dir", help="Optional directory to keep eval artifacts")

    readiness = sub.add_parser("readiness", help="Run a complete local readiness report across doctor, selftest, evals, integrations, offline agent contract checks, and Web launcher")
    readiness.add_argument("--format", choices=["md", "json"], default="md")
    readiness.add_argument("--work-dir", help="Optional directory to keep readiness artifacts")
    readiness.add_argument("--skip-slow", action="store_true", help="Skip selftest and evals")

    jobs = sub.add_parser("jobs", help="Process watch-folder JSON jobs for agent fallback integration")
    _add_data_dir_argument(jobs)
    jobs.add_argument("--jobs-dir", default=".research-jobs", help="Watch-folder root with inbox/outbox/failed directories")
    jobs.add_argument("--limit", type=int, default=20, help="Maximum inbox jobs to process; 0 means all")
    jobs.add_argument("--format", choices=["md", "json"], default="md")

    agent_smoke = sub.add_parser("agent-smoke", help="Smoke-test the stable async HTTP contract used by external agents")
    agent_smoke.add_argument("--base-url", default=web_base_url(config), help="Research Workflow Web UI base URL")
    agent_smoke.add_argument("--query", default="agent contract smoke", help="No-network smoke-test query")
    agent_smoke.add_argument("--timeout", type=float, default=60, help="Total polling timeout in seconds")
    agent_smoke.add_argument("--interval", type=float, default=1, help="Polling interval in seconds")
    agent_smoke.add_argument("--format", choices=["md", "json"], default="md")

    agent_diagnose = sub.add_parser("agent-diagnose", help="Collect service, health, smoke, and recent-job diagnostics for external Agent callers")
    _add_data_dir_argument(agent_diagnose)
    agent_diagnose.add_argument("--base-url", default=web_base_url(config), help="Research Workflow Web UI base URL")
    agent_diagnose.add_argument("--skip-smoke", action="store_true", help="Do not submit a live agent smoke job")
    agent_diagnose.add_argument("--job-limit", type=int, default=10, help="Number of recent jobs to include")
    agent_diagnose.add_argument("--format", choices=["md", "json"], default="md")

    release_check = sub.add_parser("release-check", help="Run local release readiness checks without changing integrations")
    release_check.add_argument("--format", choices=["md", "json"], default="md")
    release_check.add_argument("--work-dir", help="Optional directory to keep release-check artifacts")
    release_check.add_argument("--run-tests", action="store_true", help="Also run full pytest")

    model_assist = sub.add_parser(
        "model-assist",
        help="Run one candidate-only text task through the canonical model-provider gateway",
    )
    model_assist.add_argument("--profile", required=True, help="provider-profile.v1 JSON")
    model_assist.add_argument("--route", required=True, help="route_plan.v1 JSON")
    model_assist.add_argument("--consent", required=True, help="consent_manifest.v2 JSON")
    model_assist.add_argument("--ledger", required=True, help="Gateway execution ledger path")
    model_assist.add_argument("--receipt", required=True, help="ARW model-assist receipt path")
    model_assist.add_argument("--request-id", required=True)
    model_assist.add_argument(
        "--task",
        required=True,
        choices=["research_query", "claim_assist", "controlled_summary", "visual_claim_assist"],
    )
    model_assist.add_argument("--max-cost-usd", required=True, type=float)
    model_assist.add_argument(
        "--loopback-stub-url",
        default="",
        help="Loopback-only OpenAI-compatible stub URL for synthetic verification",
    )
    evidence_bundle = sub.add_parser(
        "evidence-bundle-verify",
        help="Validate a QRP research-output.v1 reference without importing ARW state",
    )
    evidence_bundle.add_argument("bundle", help="QRP research-output.v1 JSON artifact")
    evidence_bundle.add_argument("--schema", required=True, help="Authoritative QRP research-output.v1 JSON Schema")
    evidence_bundle.add_argument("--receipt", required=True, help="Deterministic validation receipt path")
    evidence_bundle.add_argument("--expected-bundle-sha256", help="Optional expected artifact SHA-256")
    evidence_bundle.add_argument(
        "--expected-schema-sha256",
        default=CANONICAL_SCHEMA_SHA256,
        help="Expected authoritative schema SHA-256 from the shared contract catalog",
    )
    return parser


def _add_data_dir_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", dest="command_data_dir", help=argparse.SUPPRESS)


def _add_search_arguments(parser: argparse.ArgumentParser, config: ResearchConfig) -> None:
    _add_data_dir_argument(parser)
    parser.add_argument("topic")
    parser.add_argument("--mode", choices=["daily", "academic", "tool_selection", "auto"], default="auto")
    parser.add_argument("--max-results", type=int, default=config.max_results)
    parser.add_argument("--no-network", action="store_true", help="Skip live provider calls")
    parser.add_argument("--provider", action="append", choices=sorted(provider_catalog()), help="Exact search provider override; can be repeated")
    parser.add_argument("--profile", choices=PROFILES, default=config.search_profile)
    parser.add_argument("--routing-strategy", choices=ROUTING_STRATEGIES, default=config.routing_strategy)
    parser.add_argument("--url", action="append", default=[], help="Manual source URL; can be repeated")
    parser.add_argument("--urls-file", help="Text file with one source URL per line")
    parser.add_argument("--fetch", action="store_true", default=config.fetch_urls, help="Fetch page text for URL evidence")
    parser.add_argument("--format", choices=["md", "json"], default="md")


def command_model_assist(args: argparse.Namespace) -> int:
    try:
        profile = _load_json_object(args.profile)
        route = _load_json_object(args.route)
        consent = _load_json_object(args.consent)
        transport = None
        secret_resolver = None
        if args.loopback_stub_url:
            from model_provider_gateway.execution import LiteLLMTransport

            transport = LiteLLMTransport.loopback_stub(args.loopback_stub_url)

            def secret_resolver(_ref: str) -> str:
                return "synthetic-loopback-only"
        receipt = run_model_assist(
            profile=profile,
            route=route,
            consent=consent,
            task=args.task,
            request_id=args.request_id,
            max_estimated_cost_usd=args.max_cost_usd,
            ledger_path=args.ledger,
            receipt_path=args.receipt,
            transport=transport,
            secret_resolver=secret_resolver,
        )
    except (OSError, ValueError, json.JSONDecodeError, ImportError):
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "ok": False,
            "status": "blocked",
            "error_class": "input_or_gateway_unavailable",
            "error_message": "model-assist input or loopback gateway could not be loaded",
            "research_truth_effect": "none",
            "evidence_status_change": "none",
            "human_review_required": True,
        }
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt.get("ok") else 1


def command_evidence_bundle_verify(args: argparse.Namespace) -> int:
    try:
        receipt = verify_evidence_bundle_reference(
            bundle_path=args.bundle,
            schema_path=args.schema,
            receipt_path=args.receipt,
            expected_bundle_sha256=args.expected_bundle_sha256,
            expected_schema_sha256=args.expected_schema_sha256,
        )
    except (OSError, TimeoutError, ValueError):
        receipt = {
            "schema": EVIDENCE_BUNDLE_RECEIPT_SCHEMA,
            "ok": False,
            "status": "blocked",
            "blockers": ["receipt_preflight_failed"],
            "consumer_effect": "reference_validation_only",
            "imported_into_arw_state": False,
            "research_truth_effect": "none",
            "evidence_status_change": "none",
            "candidate_promoted": False,
            "human_review_required": True,
        }
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt.get("ok") else 1


def _load_json_object(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("model-assist input must be a JSON object")
    return value


def command_config(args: argparse.Namespace, config: ResearchConfig) -> int:
    if args.init:
        path = write_default_config(args.path, overwrite=args.overwrite)
        print(f"Wrote config: {path}")
        return 0
    print(json.dumps(config.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_version(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = version_info(config)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(version_info_to_markdown(result))
    return 0


def command_doctor(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = run_doctor(config, data_dir, include_pipeline=not args.skip_pipeline)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(doctor_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_query(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    return command_search(args, data_dir, config)


def command_route(args: argparse.Namespace, config: ResearchConfig) -> int:
    plan = build_routing_plan(
        args.topic,
        mode=args.mode,
        profile=args.profile,
        routing_strategy=args.routing_strategy,
        provider_pool=config.search_providers,
        explicit_providers=args.provider,
        deep=args.deep,
    )
    if args.format == "json":
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(routing_plan_to_markdown(plan))
    return 0


def command_search(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    urls = _url_args(args)
    result = run_search_pipeline(
        topic=args.topic,
        data_dir=data_dir,
        mode=args.mode,
        max_results=args.max_results,
        no_network=args.no_network,
        urls=urls,
        fetch_urls=args.fetch,
        searxng_url=config.searxng_url,
        search_providers=args.provider or config.search_providers,
        profile=args.profile,
        routing_strategy="manual" if args.provider else args.routing_strategy,
        providers_explicit=bool(args.provider),
    )
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(result.report_markdown)
    return 0


def _url_args(args: argparse.Namespace) -> list[str]:
    urls = list(getattr(args, "url", []) or [])
    urls_file = getattr(args, "urls_file", "") or ""
    if urls_file:
        urls.extend(
            line.strip()
            for line in Path(urls_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return urls


def command_verify(args: argparse.Namespace, data_dir: Path) -> int:
    path = Path(args.path)
    text = path.read_text(encoding="utf-8")
    claims = extract_atomic_claims(text)
    evidence = [_evidence_from_row(row) for row in dedupe_rows(read_jsonl(data_dir / "evidence.jsonl"))]
    verified = verify_claims(claims, evidence)
    revision_suggestions = [] if args.no_revisions else suggest_revisions(verified, evidence)
    append_jsonl(data_dir / "claims.jsonl", verified)
    sync_jsonl_to_sqlite_quietly(data_dir)

    sources = [_source_from_row(row) for row in dedupe_rows(read_jsonl(data_dir / "sources.jsonl"))]
    claim_citations = claim_citations_from_claims(verified, evidence, sources)
    report = Report(
        topic=path.stem,
        summary=_summary_from_claims(verified),
        findings=[claim.text for claim in verified if str(claim.verdict) == "supported"],
        claim_table=verified,
        gaps=[claim.text for claim in verified if str(claim.verdict) == "not_found"],
        sources=sources,
        evidence=evidence,
        claim_citations=claim_citations,
        revision_suggestions=revision_suggestions,
    )
    out = data_dir / "reports" / f"{path.stem}.verified.md"
    write_text(out, report_to_markdown(report))
    print(f"Extracted {len(claims)} claims; wrote verified report: {out}")
    return 0


def command_ingest(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    urls = [
        line.strip()
        for line in Path(args.path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    sources = [
        source_from_url(
            url,
            search_queries=[str(args.path)],
            search_buckets=["manual URLs"],
            search_purposes=["cli ingest source"],
            search_stages=["cli_ingest"],
        )
        for url in urls
    ]
    evidence = extract_evidence_from_sources(sources, fetch=args.fetch)
    append_jsonl(data_dir / "sources.jsonl", sources)
    append_jsonl(data_dir / "evidence.jsonl", evidence)
    sync_jsonl_to_sqlite_quietly(data_dir)
    print(f"Ingested {len(sources)} sources and {len(evidence)} evidence records")
    return 0


def command_export(args: argparse.Namespace, data_dir: Path) -> int:
    sources = dedupe_rows(read_jsonl(data_dir / "sources.jsonl"))
    evidence = dedupe_rows(read_jsonl(data_dir / "evidence.jsonl"))
    claims = dedupe_rows(read_jsonl(data_dir / "claims.jsonl"))
    runs = dedupe_rows(read_jsonl(data_dir / "runs.jsonl"))
    payload = {"sources": sources, "evidence": evidence, "claims": claims, "runs": runs}
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("# Research Store Export\n")
    print("## Sources\n")
    for source in sources:
        print(f"- [{source.get('title') or source.get('url')}]({source.get('url')}) - {source.get('source_type')}")
    print("\n## Evidence\n")
    for item in evidence:
        print(f"- `{item.get('id')}` from `{item.get('source_id')}`: {item.get('quote_or_paraphrase')}")
    print("\n## Claims\n")
    for claim in claims:
        print(f"- {claim.get('verdict')} ({claim.get('confidence')}): {claim.get('text')}")
    print("\n## Runs\n")
    for run in sorted(runs, key=lambda item: str(item.get("created_at", "")), reverse=True):
        print(f"- `{run.get('id')}` {run.get('topic')} - {run.get('claim_count')} claims; {run.get('report_path')}")
    return 0


def command_runs(args: argparse.Namespace, data_dir: Path) -> int:
    runs = list_runs(data_dir, limit=args.limit)
    if args.format == "json":
        print(json.dumps({"runs": runs}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print("# Research Runs\n")
    if not runs:
        print("- No runs recorded.")
        return 0
    for run in runs:
        print(
            f"- `{run.get('id')}` {run.get('topic')} - "
            f"{run.get('supported_count', 0)}/{run.get('claim_count', 0)} supported; "
            f"{run.get('report_path')}"
        )
    return 0


def command_show(args: argparse.Namespace, data_dir: Path) -> int:
    run = get_run(data_dir, args.run_id)
    if not run:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1
    content = (
        json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True)
        if args.format == "json"
        else str(run.get("report_markdown") or "")
    )
    if args.output:
        write_text(Path(args.output), content if content.endswith("\n") else content + "\n")
        print(f"Wrote run {args.run_id}: {args.output}")
    else:
        print(content)
    return 0


def command_obsidian(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    vault = args.vault or config.obsidian_vault
    result = export_run_to_obsidian(
        data_dir=data_dir,
        run_id=args.run_id,
        vault=vault,
        folder=args.folder or config.obsidian_folder,
        overwrite=args.overwrite,
    )
    print(f"Wrote Obsidian note: {result['path']}")
    return 0


def command_audit(args: argparse.Namespace, data_dir: Path) -> int:
    result = audit_run(data_dir, args.run_id)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(audit_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_quality(args: argparse.Namespace, data_dir: Path) -> int:
    result = run_quality(data_dir, args.run_id)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(quality_to_markdown(result))
    return 0 if result.get("overall") in {"ok", "warn"} else 1


def command_deepen_claims(args: argparse.Namespace, data_dir: Path) -> int:
    result = deepen_claim_suggestions_for_run(data_dir, args.run_id, max_queries=args.max_queries)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(deepen_suggestions_to_markdown(result))
    return 0 if result.get("overall") in {"ok", "warn"} else 1


def command_references(args: argparse.Namespace, data_dir: Path) -> int:
    text = _text_from_path_or_run(data_dir, args.path_or_run_id)
    result = check_academic_references(text).to_dict()
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(references_check_to_markdown(result))
    return 0


def command_answer_evaluate(args: argparse.Namespace) -> int:
    case = _answer_case(args.path, args.case_id)
    result = evaluate_answer_case(case)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(answer_result_to_markdown(result))
    return 0


def command_answer_explain(args: argparse.Namespace) -> int:
    case = _answer_case(args.path, args.case_id)
    result = explain_answer_case(case)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(answer_result_to_markdown(result, explain=True))
    return 0


def command_answer_benchmark(args: argparse.Namespace) -> int:
    result = run_answer_benchmark(args.path)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(answer_result_to_markdown(result))
    return 0 if result["overall"] == "ok" else 1


def _answer_case(path: str, case_id: str) -> dict:
    cases = load_answer_cases(path)
    if case_id:
        for case in cases:
            if str(case.get("id") or "") == case_id:
                return case
        raise ValueError(f"Answer benchmark case not found: {case_id}")
    if len(cases) != 1:
        raise ValueError("Input contains multiple cases; pass --case-id or use answer-benchmark.")
    return cases[0]

def command_selftest(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = run_selftest(config, Path(args.work_dir) if args.work_dir else None)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(selftest_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_integrations(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = write_integrations(Path(args.output), args.base_url, config_hash=config_fingerprint(config))
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print("# Research Workflow Integrations\n")
    print(f"Base URL: `{result['base_url']}`\n")
    for file in result["files"]:
        print(f"- `{file['path']}` - {file['description']}")
    return 0


def command_bootstrap(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = run_bootstrap(
        config=config,
        data_dir=data_dir,
        config_path=Path(args.config_path),
        integrations_dir=Path(args.integrations_dir),
        base_url=args.base_url,
        skip_selftest=args.skip_selftest,
    )
    if args.format == "json":
        print(bootstrap_to_json(result))
    else:
        print(bootstrap_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_web(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    if args.web_command == "start":
        result = start_web(data_dir, config, host=args.host, port=args.port, open_browser=args.open, dry_run=args.dry_run)
    elif args.web_command == "stop":
        result = stop_web(data_dir, config, host=args.host, port=args.port)
    else:
        result = web_status(data_dir, config, host=args.host, port=args.port)
    if args.format == "json":
        print(launcher_to_json(result))
    else:
        print(launcher_to_markdown(result))
    return 0


def command_service(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    status = run_service_status(data_dir=data_dir, config=config, timeout_seconds=args.timeout)
    result = build_service_recovery_proposal(status, data_dir, config) if args.service_command == "proposal" else status
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(service_recovery_proposal_to_markdown(result) if args.service_command == "proposal" else service_status_to_markdown(result))
    return 0 if result["status"] in {"running", "stopped", "stale_record", "stale_smoke", "dependency_degraded", "config_mismatch"} else 1


def command_contract_test(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = run_contract_test(
        config,
        data_dir,
        live=args.live,
        base_url=args.base_url,
        timeout_seconds=args.timeout,
        interval_seconds=args.interval,
    )
    if args.format == "json":
        print(contract_test_to_json(result))
    else:
        print(contract_test_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_evals(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = run_evals(config, Path(args.work_dir) if args.work_dir else None)
    if args.format == "json":
        print(evals_to_json(result))
    else:
        print(evals_to_markdown(result))
    return 0 if result["overall"] == "ok" else 1


def command_readiness(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = run_readiness(config, Path(args.work_dir) if args.work_dir else None, skip_slow=args.skip_slow)
    if args.format == "json":
        print(readiness_to_json(result))
    else:
        print(readiness_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_jobs(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = process_watch_folder(root=Path(args.jobs_dir), data_dir=data_dir, config=config, limit=args.limit)
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(watch_folder_to_markdown(result))
    return 0 if not result.failed else 1


def command_agent_smoke(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = run_agent_smoke(
        args.base_url,
        query=args.query,
        timeout_seconds=args.timeout,
        interval_seconds=args.interval,
    )
    update_service_smoke(data_dir, base_url=args.base_url, smoke=result)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(agent_smoke_to_markdown(result))
    return 0 if result["overall"] == "ok" else 1


def command_agent_diagnose(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    result = run_agent_diagnose(
        data_dir,
        config,
        base_url=args.base_url,
        include_smoke=not args.skip_smoke,
        job_limit=args.job_limit,
    )
    if args.format == "json":
        print(agent_diagnose_to_json(result))
    else:
        print(agent_diagnose_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_release_check(args: argparse.Namespace, config: ResearchConfig) -> int:
    result = run_release_check(config, Path(args.work_dir) if args.work_dir else None, run_tests=args.run_tests)
    if args.format == "json":
        print(release_check_to_json(result))
    else:
        print(release_check_to_markdown(result))
    return 0 if result["overall"] in {"ok", "warn"} else 1


def command_run(args: argparse.Namespace, data_dir: Path, config: ResearchConfig) -> int:
    urls = list(args.url or [])
    if args.urls_file:
        urls.extend(
            line.strip()
            for line in Path(args.urls_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    verify_text = args.verify_text or ""
    if args.verify_file:
        verify_text = Path(args.verify_file).read_text(encoding="utf-8")

    result = run_research_pipeline(
        topic=args.topic,
        data_dir=data_dir,
        mode=args.mode,
        max_results=args.max_results,
        no_network=args.no_network,
        urls=urls,
        manual_evidence=_manual_evidence_args(args.evidence, args.evidence_url),
        text_to_verify=verify_text,
        fetch_urls=args.fetch,
        searxng_url=config.searxng_url,
        search_providers=args.provider or config.search_providers,
        profile=args.profile,
        routing_strategy="manual" if args.provider else args.routing_strategy,
        providers_explicit=bool(args.provider),
        external_payloads=load_external_payloads(args.external_payload),
    )
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(result.report_markdown)
    return 0


def _safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in text.strip())
    return cleaned.strip("-")[:80] or "research-report"


def _manual_evidence_args(texts: list[str], urls: list[str]) -> list[dict]:
    items: list[dict] = []
    for index, text in enumerate(texts or []):
        item = {"text": text}
        if index < len(urls or []) and urls[index]:
            item["url"] = urls[index]
        items.append(item)
    return items


def _text_from_path_or_run(data_dir: Path, value: str) -> str:
    path = Path(value)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    record = get_run(data_dir, value)
    if record:
        return str(record.get("report_markdown") or record.get("report_json") or "")
    raise FileNotFoundError(f"Not a file or run_id: {value}")


def _summary_from_claims(claims: list[Claim]) -> str:
    supported = sum(1 for claim in claims if str(claim.verdict) == "supported")
    partial = sum(1 for claim in claims if str(claim.verdict) == "partial")
    contradicted = sum(1 for claim in claims if str(claim.verdict) == "contradicted")
    not_found = sum(1 for claim in claims if str(claim.verdict) == "not_found")
    return f"Claim check complete: {supported} supported, {partial} partial, {contradicted} contradicted, {not_found} not found."


def _source_from_row(row: dict) -> Source:
    return Source(**row)


def _evidence_from_row(row: dict) -> Evidence:
    return Evidence(**row)


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _preparse_config_path(argv: list[str] | None) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    args, _ = parser.parse_known_args(argv)
    return args.config


if __name__ == "__main__":
    raise SystemExit(main())
