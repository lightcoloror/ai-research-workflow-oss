from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from . import mcp_tools
from .metadata import package_version


JSONDict = dict[str, Any]
ToolHandler = Callable[[JSONDict], Any]


TOOL_SCHEMAS: dict[str, JSONDict] = {
    "research_route": {
        "description": "Plan a local, auditable search route without calling providers or changing research state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "auto"},
                "profile": {"type": "string", "enum": ["default", "anti_seo", "technical_primary", "medical_high_risk", "policy_primary", "product_feedback"], "default": "default"},
                "routing_strategy": {"type": "string", "enum": ["adaptive_staged", "legacy_fanout", "manual"], "default": "adaptive_staged"},
                "search_providers": {"type": "array", "items": {"type": "string"}, "default": []},
                "config_path": {"type": "string", "default": ""},
                "deep": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    "research_search": {
        "description": "Recommended stable agent tool: run search-only research and return run_id, report_markdown, sources with provenance fields, evidence, coverage_gaps, and provider_health with quota/error/timing details.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "auto"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "no_network": {"type": "boolean", "default": False},
                "urls": {"type": "array", "items": {"type": "string"}, "default": []},
                "fetch_urls": {"type": "boolean", "default": False},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "search_providers": {"type": "array", "items": {"type": "string"}, "default": []},
                "auto_verify": {"type": "boolean", "default": False},
                "profile": {"type": "string", "default": ""},
                "routing_strategy": {"type": "string", "default": ""},
                "external_payloads": {"type": "array", "items": {"type": "object"}, "default": []},
            },
            "required": ["query"],
        },
    },
    "research_deepen": {
        "description": "Recommended stable agent tool: rerun a topic with deeper query budgets; returns the same auditable shape as research_search including source provenance and provider_health timing/error fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "auto"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "no_network": {"type": "boolean", "default": False},
                "urls": {"type": "array", "items": {"type": "string"}, "default": []},
                "fetch_urls": {"type": "boolean", "default": False},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "search_providers": {"type": "array", "items": {"type": "string"}, "default": []},
                "profile": {"type": "string", "default": ""},
                "routing_strategy": {"type": "string", "default": ""},
                "external_payloads": {"type": "array", "items": {"type": "object"}, "default": []},
                "auto_verify": {"type": "boolean", "default": False},
            },
            "required": ["query"],
        },
    },
    "research_verify": {
        "description": "Recommended stable agent tool: verify prose or an existing run_id against local evidence; returns claims and revision_suggestions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text_or_run_id": {"type": "string"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["text_or_run_id"],
        },
    },
    "research_ingest": {
        "description": "Recommended stable agent tool: ingest manually selected URLs into the local evidence store; returned sources include manual provenance fields for auditability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
                "fetch_urls": {"type": "boolean", "default": False},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["urls"],
        },
    },
    "research_get_report": {
        "description": "Recommended stable agent tool: return a persisted run report as Markdown or JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "format": {"type": "string", "enum": ["md", "json"], "default": "md"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["run_id"],
        },
    },
    "research_status": {
        "description": "Recommended stable agent tool: return service status or one persisted run status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "default": ""},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
        },
    },
    "research_quality": {
        "description": "Summarize citation coverage, source diversity, and evidence strength for a persisted run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["run_id"],
        },
    },
    "research_deepen_claims": {
        "description": "Generate next-search query suggestions for partial, not_found, or high-risk weak claims in a persisted run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "max_queries": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["run_id"],
        },
    },
    "research_check_references": {
        "description": "Validate DOI/arXiv references from text or a persisted run, returning valid/partial/not_found/metadata_mismatch summaries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "default": ""},
                "run_id": {"type": "string", "default": ""},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
        },
    },
    "multi_source_search": {
        "description": "Run configured multi-source search providers and return Source records. Prefer research_search when you need persisted reports, provider_health, coverage_gaps, and full provenance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "daily"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "providers": {"type": "array", "items": {"type": "string"}, "default": []},
            },
            "required": ["query"],
        },
    },
    "extract_evidence": {
        "description": "Create Evidence records from manually selected URLs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["urls"],
        },
    },
    "extract_atomic_claims": {
        "description": "Split prose or Markdown into atomic factual claims.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    "verify_claims": {
        "description": "Verify claims only against supplied evidence; unsupported claims remain not_found.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claims": {"type": "array", "items": {"type": "object"}},
                "evidence_policy": {"type": "object", "default": {}},
            },
            "required": ["claims"],
        },
    },
    "verify_with_longtracer": {
        "description": "Optionally verify a response against sources with LongTracer-style STS/NLI citation checking. Returns warnings instead of blocking when LongTracer is unavailable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "object"}},
                "evidence": {"type": "array", "items": {"type": "object"}, "default": []},
                "threshold": {"type": "number", "default": 0.5},
            },
            "required": ["response", "sources"],
        },
    },
    "check_academic_references": {
        "description": "Extract DOI/arXiv academic references and create verification-ready academic source records, with optional worker support outside the core.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
    },
    "expand_claim_search_queries": {
        "description": "Generate Fathom/HyDE-style sparse retrieval query variants for one high-risk or uncertain atomic claim.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {"type": "object"},
                "max_queries": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "include_academic": {"type": "boolean", "default": False},
            },
            "required": ["claim"],
        },
    },
    "list_claim_verification_prompts": {
        "description": "List local ClaimeAI-inspired prompt templates used for query generation, search decisions, and evidence evaluation.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    "get_claim_verification_prompt": {
        "description": "Return one local claim verification prompt template by name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    "write_research_report": {
        "description": "Render a Markdown research report from findings and verified claims.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}, "default": []},
                "claims": {"type": "array", "items": {"type": "object"}, "default": []},
            },
            "required": ["topic"],
        },
    },
    "run_research": {
        "description": "Run the complete local-first research chain and persist a restorable run record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "auto"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "no_network": {"type": "boolean", "default": False},
                "urls": {"type": "array", "items": {"type": "string"}, "default": []},
                "manual_evidence": {"type": "array", "items": {"type": "object"}, "default": []},
                "text_to_verify": {"type": "string", "default": ""},
                "fetch_urls": {"type": "boolean", "default": False},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "search_providers": {"type": "array", "items": {"type": "string"}, "default": []},
                "profile": {"type": "string", "default": ""},
                "routing_strategy": {"type": "string", "default": ""},
                "external_payloads": {"type": "array", "items": {"type": "object"}, "default": []},
            },
            "required": ["topic"],
        },
    },
    "run_search": {
        "description": "Run the search-only chain: plan, retrieve, extract evidence, persist a search report, and skip fact checking. Returns sources with provenance and provider_health diagnostics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "mode": {"type": "string", "enum": ["daily", "academic", "tool_selection", "auto"], "default": "auto"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "no_network": {"type": "boolean", "default": False},
                "urls": {"type": "array", "items": {"type": "string"}, "default": []},
                "fetch_urls": {"type": "boolean", "default": False},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "search_providers": {"type": "array", "items": {"type": "string"}, "default": []},
                "profile": {"type": "string", "default": ""},
                "routing_strategy": {"type": "string", "default": ""},
                "external_payloads": {"type": "array", "items": {"type": "object"}, "default": []},
            },
            "required": ["topic"],
        },
    },
    "doctor": {
        "description": "Run local diagnostics for config, storage, search, MCP, Web assets, and pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "include_pipeline": {"type": "boolean", "default": True},
            },
        },
    },
    "list_runs": {
        "description": "List persisted research runs from the local run history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    "get_run": {
        "description": "Return one persisted run record, including report Markdown and JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["run_id"],
        },
    },
    "export_obsidian": {
        "description": "Export a persisted run as a Markdown note in an Obsidian vault folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
                "vault": {"type": "string", "default": ""},
                "folder": {"type": "string", "default": ""},
                "overwrite": {"type": "boolean", "default": False},
            },
            "required": ["run_id"],
        },
    },
    "audit": {
        "description": "Audit one persisted run for evidence links, high-risk policy, counters, and report integrity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": ""},
            },
            "required": ["run_id"],
        },
    },
    "selftest": {
        "description": "Run local end-to-end smoke tests for pipeline, automatic audit, MCP, and Obsidian export.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "default": ""},
                "work_dir": {"type": "string", "default": ""},
            },
        },
    },
    "evals": {
        "description": "Run the three local acceptance scenarios: daily tools, academic methods, and hallucination handling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "default": ""},
                "work_dir": {"type": "string", "default": ""},
            },
        },
    },
    "bootstrap": {
        "description": "Initialize local config if missing, write integration files, run doctor, and optionally run selftest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data_dir": {"type": "string", "default": ""},
                "config_path": {"type": "string", "default": "research.config.json"},
                "integrations_dir": {"type": "string", "default": "integrations"},
                "base_url": {"type": "string", "default": ""},
                "skip_selftest": {"type": "boolean", "default": False},
            },
        },
    },
    "readiness": {
        "description": "Run a complete local readiness report across doctor, selftest, evals, integrations, offline agent contract checks, and Web launcher.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string", "default": ""},
                "work_dir": {"type": "string", "default": ""},
                "skip_slow": {"type": "boolean", "default": False},
            },
        },
    },
}


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "research_route": lambda args: mcp_tools.research_route(
        query=str(args.get("query", "")),
        mode=str(args.get("mode", "auto")),
        profile=str(args.get("profile", "")),
        routing_strategy=str(args.get("routing_strategy", "")),
        search_providers=list(args.get("search_providers", [])) if args.get("search_providers") else None,
        config_path=str(args.get("config_path", "")),
        deep=bool(args.get("deep", False)),
    ),
    "research_search": lambda args: mcp_tools.research_search(
        query=str(args.get("query", "")),
        mode=str(args.get("mode", "auto")),
        max_results=int(args.get("max_results", 10)),
        no_network=bool(args.get("no_network", False)),
        urls=list(args.get("urls", [])),
        fetch_urls=bool(args.get("fetch_urls", False)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        search_providers=list(args.get("search_providers", [])) if args.get("search_providers") else None,
        profile=str(args.get("profile", "")),
        routing_strategy=str(args.get("routing_strategy", "")),
        external_payloads=list(args.get("external_payloads", [])),
        auto_verify=bool(args.get("auto_verify", False)),
    ),
    "research_deepen": lambda args: mcp_tools.research_deepen(
        query=str(args.get("query", "")),
        mode=str(args.get("mode", "auto")),
        max_results=int(args.get("max_results", 10)),
        no_network=bool(args.get("no_network", False)),
        urls=list(args.get("urls", [])),
        fetch_urls=bool(args.get("fetch_urls", False)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        search_providers=list(args.get("search_providers", [])) if args.get("search_providers") else None,
        auto_verify=bool(args.get("auto_verify", False)),
        profile=str(args.get("profile", "")),
        routing_strategy=str(args.get("routing_strategy", "")),
        external_payloads=list(args.get("external_payloads", [])),
    ),
    "research_verify": lambda args: mcp_tools.research_verify(
        text_or_run_id=str(args.get("text_or_run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_ingest": lambda args: mcp_tools.research_ingest(
        urls=list(args.get("urls", [])),
        fetch_urls=bool(args.get("fetch_urls", False)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_get_report": lambda args: mcp_tools.research_get_report(
        run_id=str(args.get("run_id", "")),
        format=str(args.get("format", "md")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_status": lambda args: mcp_tools.research_status(
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_quality": lambda args: mcp_tools.research_quality(
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_deepen_claims": lambda args: mcp_tools.research_deepen_claims(
        run_id=str(args.get("run_id", "")),
        max_queries=int(args.get("max_queries", 5)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "research_check_references": lambda args: mcp_tools.research_check_references(
        text=str(args.get("text", "")),
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "multi_source_search": lambda args: mcp_tools.multi_source_search(
        query=str(args.get("query", "")),
        mode=str(args.get("mode", "daily")),
        max_results=int(args.get("max_results", 10)),
        providers=list(args.get("providers", [])) if args.get("providers") else None,
    ),
    "extract_evidence": lambda args: mcp_tools.extract_evidence(list(args.get("urls", []))),
    "extract_atomic_claims": lambda args: mcp_tools.extract_atomic_claims(str(args.get("text", ""))),
    "verify_claims": lambda args: mcp_tools.verify_claims(
        claims=list(args.get("claims", [])),
        evidence_policy=dict(args.get("evidence_policy", {}) or {}),
    ),
    "verify_with_longtracer": lambda args: mcp_tools.verify_with_longtracer(
        response=str(args.get("response", "")),
        sources=list(args.get("sources", [])),
        evidence=list(args.get("evidence", [])),
        threshold=float(args.get("threshold", 0.5)),
    ),
    "check_academic_references": lambda args: mcp_tools.check_academic_references(
        text=str(args.get("text", "")),
    ),
    "expand_claim_search_queries": lambda args: mcp_tools.expand_claim_search_queries(
        claim=dict(args.get("claim", {}) or {}),
        max_queries=int(args.get("max_queries", 5)),
        include_academic=bool(args.get("include_academic", False)),
    ),
    "list_claim_verification_prompts": lambda args: mcp_tools.list_claim_verification_prompts(),
    "get_claim_verification_prompt": lambda args: mcp_tools.get_claim_verification_prompt(
        name=str(args.get("name", "")),
    ),
    "write_research_report": lambda args: mcp_tools.write_research_report(
        topic=str(args.get("topic", "")),
        findings=list(args.get("findings", [])),
        claims=list(args.get("claims", [])),
    ),
    "run_research": lambda args: mcp_tools.run_research(
        topic=str(args.get("topic", "")),
        mode=str(args.get("mode", "auto")),
        max_results=int(args.get("max_results", 10)),
        no_network=bool(args.get("no_network", False)),
        urls=list(args.get("urls", [])),
        manual_evidence=list(args.get("manual_evidence", [])),
        text_to_verify=str(args.get("text_to_verify", "")),
        fetch_urls=bool(args.get("fetch_urls", False)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        search_providers=list(args.get("search_providers", [])) if args.get("search_providers") else None,
        profile=str(args.get("profile", "")),
        routing_strategy=str(args.get("routing_strategy", "")),
        external_payloads=list(args.get("external_payloads", [])),
    ),
    "run_search": lambda args: mcp_tools.run_search(
        topic=str(args.get("topic", "")),
        mode=str(args.get("mode", "auto")),
        max_results=int(args.get("max_results", 10)),
        no_network=bool(args.get("no_network", False)),
        urls=list(args.get("urls", [])),
        fetch_urls=bool(args.get("fetch_urls", False)),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        search_providers=list(args.get("search_providers", [])) if args.get("search_providers") else None,
        profile=str(args.get("profile", "")),
        routing_strategy=str(args.get("routing_strategy", "")),
        external_payloads=list(args.get("external_payloads", [])),
    ),
    "doctor": lambda args: mcp_tools.doctor(
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        include_pipeline=bool(args.get("include_pipeline", True)),
    ),
    "list_runs": lambda args: mcp_tools.list_runs(
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        limit=int(args.get("limit", 20)),
    ),
    "get_run": lambda args: mcp_tools.get_run(
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "export_obsidian": lambda args: mcp_tools.export_obsidian(
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
        vault=str(args.get("vault", "")),
        folder=str(args.get("folder", "")),
        overwrite=bool(args.get("overwrite", False)),
    ),
    "audit": lambda args: mcp_tools.audit(
        run_id=str(args.get("run_id", "")),
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "")),
    ),
    "selftest": lambda args: mcp_tools.selftest(
        config_path=str(args.get("config_path", "")),
        work_dir=str(args.get("work_dir", "")),
    ),
    "evals": lambda args: mcp_tools.evals(
        config_path=str(args.get("config_path", "")),
        work_dir=str(args.get("work_dir", "")),
    ),
    "bootstrap": lambda args: mcp_tools.bootstrap(
        data_dir=str(args.get("data_dir", "")),
        config_path=str(args.get("config_path", "research.config.json")),
        integrations_dir=str(args.get("integrations_dir", "integrations")),
        base_url=str(args.get("base_url", "")),
        skip_selftest=bool(args.get("skip_selftest", False)),
    ),
    "readiness": lambda args: mcp_tools.readiness(
        config_path=str(args.get("config_path", "")),
        work_dir=str(args.get("work_dir", "")),
        skip_slow=bool(args.get("skip_slow", False)),
    ),
}


def handle_request(request: JSONDict) -> JSONDict | None:
    method = str(request.get("method", ""))
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-research-workflow", "version": package_version()},
            }
        elif method == "tools/list":
            result = {"tools": [_tool_descriptor(name, schema) for name, schema in TOOL_SCHEMAS.items()]}
        elif method == "tools/call":
            params = dict(request.get("params", {}) or {})
            result = call_tool(str(params.get("name", "")), dict(params.get("arguments", {}) or {}))
        else:
            return _error(request_id, -32601, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:  # noqa: BLE001
        return _error(request_id, -32000, str(exc))


def call_tool(name: str, arguments: JSONDict) -> JSONDict:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        raise ValueError(f"Unknown tool: {name}")
    payload = handler(arguments)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            }
        ],
        "isError": False,
    }


def serve(input_stream: Any = sys.stdin, output_stream: Any = sys.stdout) -> None:
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        response = handle_request(json.loads(line))
        if response is None:
            continue
        output_stream.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        output_stream.write("\n")
        output_stream.flush()


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = argparse.ArgumentParser(description="Run the AI Research Workflow MCP stdio server")
    parser.parse_args(argv)
    serve()
    return 0


def _tool_descriptor(name: str, schema: JSONDict) -> JSONDict:
    return {
        "name": name,
        "description": str(schema["description"]),
        "inputSchema": schema["inputSchema"],
    }


def _error(request_id: Any, code: int, message: str) -> JSONDict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _configure_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    raise SystemExit(main())
