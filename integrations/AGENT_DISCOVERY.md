# Agent Discovery: AI Research Workflow

Use this local service for evidence-first deep search, source capture, and atomic fact checking.

Base URL:

```text
http://127.0.0.1:9082
```

## Interface Priority

1. MCP tools when available:

This is the configured endpoint, not proof that a process is currently listening. Run research service status --format json before HTTP use.


- `research_route`
- `research_search`
- `research_deepen`
- `research_verify`
- `research_ingest`
- `research_get_report`
- `research_status`
- `research_quality`
- `research_deepen_claims`
- `research_check_references`

2. HTTP API when the agent can call localhost:

```text
GET  http://127.0.0.1:9082/v1/health
GET  http://127.0.0.1:9082/v1/capabilities
GET  http://127.0.0.1:9082/v1/discovery
GET  http://127.0.0.1:9082/v1/openapi.json
POST http://127.0.0.1:9082/v1/agent/research
POST http://127.0.0.1:9082/v1/agent/verify
POST http://127.0.0.1:9082/v1/agent/fetch
GET  http://127.0.0.1:9082/v1/agent/jobs/{job_id}
GET  http://127.0.0.1:9082/v1/agent/jobs/{job_id}/events
POST http://127.0.0.1:9082/v1/agent/jobs/{job_id}/retry
```

Before HTTP integration, check the local service state:

```powershell
python -m research_workflow.cli service status --format json
```

Before relying on this service from another project or agent, run the stable contract test:

```powershell
python -m research_workflow.cli contract-test --format json
```

Use `--live` only when the Web UI service is already running and you want to test real HTTP calls.

3. Watch-folder fallback when the agent can write local files:

```text
.research-jobs/inbox
.research-jobs/outbox
.research-jobs/failed
```

Supported watch-folder job types: `search`, `deepen`, `verify`, `fetch`, and `ingest`.
Use `fetch` or `ingest` when another agent only needs to hand off URLs:

```json
{"id":"job_url_001","type":"fetch","urls":["https://example.com"],"fetch":false}
```

Status files include `contract_version` and `config_hash` so callers can detect stale generated manifests or mismatched config.

4. Bookmarklet/browser ingest when a human or browser agent selected a useful current page:

```text
research-workflow-bookmarklet.md
```

## Mode Selection

- `tool_selection`: choosing tools, APIs, services, local infrastructure, or Inbox systems.
- `academic`: papers, benchmarks, citations, datasets, and methods.
- `daily`: general deep search.
- `auto`: when uncertain.

## Evidence Policy

- Do not use model memory as evidence.
- Do not present not_found claims as confirmed.
- Surface provider failures when they affect coverage.
- Use deepen or manual ingest when coverage gaps remain.
- Use source provenance fields before trusting source selection: search_queries, search_buckets, search_purposes, search_stages.

## Expected Outputs

- `run_id`
- `report_markdown`
- `sources`
  - each source includes `search_queries`, `search_buckets`, `search_purposes`, and `search_stages` for provenance
- `evidence`
- `claims`
- `provider_health`
- `coverage_gaps`

## Generated Companion Files

- `research-workflow.integration.json`
- `agent-discovery.json`
- interface_contract
  - schema: research.search-result.v1
  - evidence status: collected, sources_only, or unavailable
  - source trail status: complete, incomplete, or unavailable
  - the same summary is emitted by CLI, HTTP, MCP, and watch-folder search results
- `openclaw-research-workflow.prompt.md`
- `hermes-research-workflow.prompt.md`
- `research_workflow_client.py`
- `research-workflow-client.mjs`
- `n8n-research-workflow.json`
- `node-red-research-workflow.json`
- `dify-openapi-tool.md`
- `research-workflow-bookmarklet.md`

## QRP Evidence-Bundle Reference Boundary

- Facade: `evidence-bundle.v1`; native payload: QRP `research-output.v1` 1.0.0.
- Canonical owner: `question-research-poc` at `2d83fc7bb0114417b045726de69f7a3f6b46242b`.
- Entry: `research evidence-bundle-verify`; Python: `research_workflow.evidence_bundle_reference.verify_evidence_bundle_reference`.
- The caller supplies the authoritative QRP schema; its SHA-256 must be `3c2e0e9a05034b1ffa3693735da128c7d3e114ddb155f11668120b974fee1009`.
- Success is a reference-validation receipt, not an ARW evidence import or truth transition.
- Candidate, missing support, conflict, stale, corrupt, link/summary/schema/hash drift, AI-synthesis-as-fact, and model-memory-as-evidence states fail closed.
- Every receipt keeps `research_truth_effect=none`, `evidence_status_change=none`, and `candidate_promoted=false`.

### Medical Evidence And Health Support (On Demand)

Updated: 2026-10-31 | Codex (gpt-6.1-sol).

For medical literature, explicitly select `medical_high_risk`. The route returns
`review_prompts`: `medical_evidence_review` and `medical_report_handoff`.
Retrieve them using the existing `list_claim_verification_prompts` /
`get_claim_verification_prompt` MCP tools (or the Python prompt registry).
They do not run automatically or grant clinical clearance. For user-requested
ordinary health-support planning, separately load `health_support_review`.
See `docs/medical-health-methods.md` before handing any action to schedule-planner.
No diagnostic, treatment, prognosis, drug-design, upload or calendar-write module
is enabled by this guidance.
