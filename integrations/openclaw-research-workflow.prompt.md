# OpenClaw Research Workflow Calling Policy

You can call the local AI Research Workflow service for deep search, tool selection, evidence collection, and atomic fact checking.

## Preferred Interfaces

1. Use MCP tools first when available:
   - `research_route`
   - `research_search`
   - `research_deepen`
   - `research_verify`
   - `research_ingest`
   - `research_get_report`
   - `research_status`
2. If MCP is unavailable, call the local HTTP API at `http://127.0.0.1:9082`.
3. If both MCP and HTTP are unavailable, write a JSON job to `.research-jobs/inbox` and ask the host to run:

```powershell
python -m research_workflow.cli jobs --jobs-dir .research-jobs --data-dir .research --format json
```
   Supported file job types are `search`, `deepen`, `verify`, `fetch`, and `ingest`.
   For URL handoff, write `{"id":"job_url_001","type":"fetch","urls":["https://example.com"],"fetch":false}`.

## HTTP Calls

- Health: `GET http://127.0.0.1:9082/v1/health`
- Capabilities: `GET http://127.0.0.1:9082/v1/capabilities`
- OpenAPI: `GET http://127.0.0.1:9082/v1/openapi.json`
- Read-only route plan: `POST http://127.0.0.1:9082/v1/route`
- Research job: `POST http://127.0.0.1:9082/v1/agent/research`
- Verify job: `POST http://127.0.0.1:9082/v1/agent/verify`
- Fetch job: `POST http://127.0.0.1:9082/v1/agent/fetch`
- Poll job: `GET http://127.0.0.1:9082/v1/agent/jobs/{job_id}`
- Poll events: `GET http://127.0.0.1:9082/v1/agent/jobs/{job_id}/events`
- Retry job: `POST http://127.0.0.1:9082/v1/agent/jobs/{job_id}/retry`
- Synchronous search fallback: `POST http://127.0.0.1:9082/v1/search`
- Synchronous verify fallback: `POST http://127.0.0.1:9082/v1/verify`
- Report: `GET http://127.0.0.1:9082/v1/runs/{run_id}/report.md`
- Events: `GET http://127.0.0.1:9082/v1/runs/{run_id}/events?format=sse`

## Mode Choice

- Use `tool_selection` for choosing tools, APIs, services, local infrastructure, or Inbox systems.
- Use `academic` for papers, benchmarks, citations, datasets, and methods.
- Use `daily` for general deep search.
- Use `auto` when uncertain.

## Required Behavior

- Use ARW as the research control plane. Omit `providers` for adaptive routing; explicit providers mean exact manual routing.
- Use Agent Reach only for social-platform acquisition and return normalized `external_payloads` to ARW.
- Use `web-content-fetcher` through ARW fetch/ingest for known URLs and canonical page text.
- Do not treat model memory as evidence.
- Do not present `not_found` claims as facts.
- Surface provider failures when they affect coverage.
- Respect `coverage_gate`, `provider_budget`, and `stop_reason`; do not fan out after the gate passes.
- If coverage is weak, call `research_deepen` or ask for manual URLs.
- Before final factual conclusions, call `research_verify` or use verified claims from the run.
- Final answers should separate confirmed findings, weak/unverified points, and next steps.
- For important results, export or ask the host to export the run to Obsidian.
