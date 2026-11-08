# Medical Evidence And Health Support Methods

Added: 2026-10-31 | Codex (gpt-6.1-sol).

## Existing Entry Points

This is a method-layer addition to the existing research and scheduling tools,
not a new health platform, memory store, provider, sandbox or clinical engine.

1. Research public medical questions with the existing `medical_high_risk`
   profile. The route lists two on-demand `review_prompts`.
2. Load `medical_evidence_review`, then `medical_report_handoff` through the
   existing prompt registry / MCP get/list tools. Use existing source, evidence,
   claim and report IDs; do not create a competing evidence database.
3. Only when ordinary health-support planning is requested, separately load
   `health_support_review`. Do not infer a request for action from a paper query.
4. Pass only the user's confirmed, non-clinical action to schedule-planner's
   existing normalized-input/review flow. Its health-support and
   recovery-evidence documentation is a separate, unbundled project asset and is
   not included in this public candidate.

Python access without a running server (from this project's environment):

```python
from research_workflow.prompt_registry import get_claim_verification_prompt
instructions = get_claim_verification_prompt("medical_evidence_review")
```

## What Was Absorbed

OPL-Cancer: claim-to-source-to-review traceability and two audience views of one
evidence base. Keep professional and plain-language reports aligned on IDs,
uncertainty, conflicting evidence and applicability; plain language must not
become a stronger claim. Preserve the original author's meaning separately from
the assistant's interpretation and the clinician's assessment.

PHA: separate data quality/trend analysis, medical evidence/applicability review,
and goal/constraint-aware coaching. These are review responsibilities, not a
requirement to spawn three agents. First specify the analysis question, units,
time window and missingness; inspect actual results before interpreting trends.
Choose a small user-confirmed action, then distinguish actual feedback from the
proposal before revising it. Reuse existing records, not a new patient profile.

## What Was Not Absorbed

- OPL's G2 OR-style PMID/lexical test: identifier validity and quote existence
  must be checked separately; neither establishes that the quote supports a claim.
- G36 entity overlap as proof of clinical applicability; G13 model-name strings
  as proof of independent review; a claim hash/graph edge as proof of truth.
- Static guideline excerpts as current complete guidelines, or regex PII
  removal as a guarantee of anonymization.
- PHA's simplified Python sandbox or query/result logging as safe isolation for
  private health data. No SDK, API key, wearable link or runtime is installed.
- Automated diagnosis, treatment selection, prognosis or drug design.

The existing `verify_claims`/coverage checks remain retrieval and heuristic
signals. A source count or automatic `supported` verdict is NOT human semantic
review, clinical suitability or permission to act. Keep manual source review
pending when the original text, applicability or independence is unresolved.

## Reuse Provenance And Limits

Architecture-pattern adaptation only; no upstream implementation was copied.

- OPL-Cancer `ec709c01da7ba0f9e0885c122c3da9b3f9b05fcd`:
  `src/opl_cancer/delivery/claim_graph.py`, provenance hasher and gates G2/G36.
  The upstream source snapshot and relative negative G2 fixture are not bundled
  in this public candidate; the referenced local review archive is not
  distributed here.
- Google Health consumer-health-research
  `3e9d1e4877a54cf81ce8ef34085006952dd19601`:
  `personal_health_agent/pha/agents/orchestrator.py`,
  `health_coach_agent.py`, and `pha/tools/python_sandbox.py` were inspected in
  the preceding review. No full upstream checkout or PHA runtime test was made.
  Fixed upstream root:
  <https://github.com/Google-Health/consumer-health-research/tree/3e9d1e4877a54cf81ce8ef34085006952dd19601/personal_health_agent>.

The upstream license status, source-snapshot provenance and clinical validity of
both references remain unverified or unknown; this candidate does not assert
otherwise.

The focused offline tests included in this repository establish template
availability, explicit routing and boundary wording only; they do not establish
model compliance, patient outcomes or clinical validation. The guidance and
templates describe human review responsibilities; no program or model in this
repository performs those reviews automatically or grants clinical clearance.

## Change Rationale And Exit

Intent: strengthen existing evidence and feedback handoffs without another stack.
Decision/reason: reuse the installed prompt registry and scheduler's existing
review/completion gates; suspicious upstream validators are not transplanted.
Evidence: pinned sources above, existing routing/prompt code, focused offline tests.
Scope: explicit medical profile metadata and optional Agent instructions only;
search budgets, fact-check algorithm and scheduling engine are unchanged.
Rollback: remove only the added prompt sections, review_prompts field/assignment
and Markdown rendering, new tests/docs and discovery paragraphs. Never overwrite
the existing discovery file wholesale when rolling back; remove only the appended
paragraphs.
