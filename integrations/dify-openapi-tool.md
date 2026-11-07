# Dify Tool Setup

Use Dify's OpenAPI tool import when the local Research Workflow service is reachable from the Dify runtime.

## Import

OpenAPI URL:

```text
http://127.0.0.1:9082/v1/openapi.json
```

Recommended operation:

```text
researchSearch
```

Recommended payload:

```json
{
  "topic": "{{ user_query }}",
  "mode": "auto",
  "max_results": 10,
  "no_network": false,
  "providers": []
}
```

## Agent Policy

- Use `researchSearch` for ordinary deep search.
- Use `researchDeepen` when `coverage_gaps` or weak source quality remain.
- Use `researchVerify` before presenting factual conclusions that were written outside the report.
- Treat `not_found` claims as unresolved, not false and not confirmed.
- Return `run_id`, source URLs, provider failures, and unverified gaps to the user.

## Network Note

If Dify runs in Docker, `127.0.0.1` points at the Dify container, not the Windows host. Use the host gateway address or expose the Research Workflow service on an explicit local network interface only when you accept that trust boundary.
