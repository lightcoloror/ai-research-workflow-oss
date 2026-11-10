# HTTP outcome and automatic CLI fallback

The existing research wrapper still selects its original same-project CLI before submitting a POST when health is unavailable or Markdown format is requested. After a POST attempt, failures now propagate with a bare `throw`; they never automatically start a second CLI execution. This includes a response-decoration failure after the POST returned. The CLI reason no longer claims that no process starts.

Parameters, profiles, payloads, health check, CLI arguments and native exit check, HTTP timeouts and pre-POST branch are preserved. This is a narrow client-side change; a timeout does not prove whether the server executed a request. A passing health GET does not authenticate a service. No server-side deduplication, transaction, retry, rollback or research-fact guarantee is added.

## Isolated regression

From this source checkout, with existing PowerShell 7:

    pwsh -NoProfile -File tests/test_post_outcome.ps1 -Candidate scripts/research-agent.ps1 -Original tests/fixtures/research-agent-before-post-fix.txt -Case All

The `.txt` fixture is the earlier generic first-party script, supplied as AST comparison data; it contains no research records and is not an execution entry. The driver parses source without executing or dot-sourcing the complete wrapper. It extracts the final POST try/catch, supplies synthetic values, and uses verified in-memory Function stubs. Four behavior controls (success, timeout, received-then-error, decoration error), three pure health/format conditions, and a full remaining-source comparison passed on PowerShell 7.5.2. Error-marker propagation was checked; real network exception classification was not.

The same `-Case PostTimeout` checker passed against the candidate and exited 1 against a separate bad copy restoring only the old catch. Its failing assertion observed one mock CLI call where zero was required. That bad copy and machine logs are not distributed.

These are extracted-source/function-substitution controls, not the complete script, actual native CLI, real HTTP/service/provider, account, metadata end-to-end, Windows PowerShell 5.1, research evidence or publication validation. The wrapper can perform real research when intentionally invoked; the synthetic test does not authorize it. No new dependencies or runtime settings are introduced.
