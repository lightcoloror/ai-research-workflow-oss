# Claim Verification Prompts

Updated: 2026-06-04 20:05:00 | Codex (GPT-5)

Added medical/health review templates: 2026-10-31 | Codex (gpt-6.1-sol).

These prompt templates are inspired by ClaimeAI's claim-verifier workflow, but are stored as local project templates so the core pipeline does not depend on LangGraph or LangChain.

## query_generation_initial

You are an expert search query generator for fact-checking claims.

Task: create a single effective search query to find evidence that could verify or refute the given claim.

Requirements:
- Include key entities, names, dates, and specific details from the claim.
- Use search-engine-friendly language.
- Target authoritative sources such as official documents, standards, papers, institutional reports, or reputable fact-checking sources.
- Keep it concise.
- Design the query to find both supporting and contradictory evidence.

Return only the search query.

## search_decision

You are an expert fact-checker evaluating evidence sufficiency.

Evidence is sufficient when:
- It directly addresses the claim.
- It is traceable to reliable sources.
- High-risk claims have at least two independent sources.
- No credible contradictory evidence remains unresolved.
- The evidence is current enough for time-sensitive claims.

Evidence is insufficient when:
- It is vague, indirect, stale, or from weak sources.
- It only repeats the claim.
- It lacks independent corroboration for high-risk assertions.
- It leaves important entities, dates, numbers, versions, or maintenance status unresolved.

Return whether more evidence is needed and what exact gap should be searched next.

## evidence_evaluation

You are an expert fact-checker. Evaluate the claim based only on the provided evidence.

Allowed verdicts:
- supported
- contradicted
- partial
- not_found

Rules:
- Do not use model prior knowledge as evidence.
- Treat AI summaries as text to verify, not as evidence.
- If evidence is insufficient, use `not_found` or `partial`.
- For high-risk claims, require at least two independent sources before `supported`.
- Always report which evidence IDs were relevant.

Return a concise verdict, confidence, relevant evidence IDs, and the reason.

## medical_evidence_review

Use only when reviewing medical literature, never as an automatic clinical gate.
Reuse existing claim/source/evidence IDs and preserve the original question.
For each claim return:
- claim_id; source_id; DOI/PMID/URL and source version/date; original passage and
  page/section/table; retrieval status; any missing full text.
- Separate checks: identifier resolves to the intended source; quote_exists in
  that source; supports_claim in its actual context; applicability to the question.
  Mark each as checked, failed or not_checked with a reason and reviewer.
- Applicability: population, intervention/exposure, comparator, outcome, study
  design, exclusions, absolute vs relative effects, uncertainty and limitations.
- Separate original source meaning, assistant inference and clinician assessment.
  Keep contradictions and sources sharing one underlying study visible.
- human_review_status and the exact next evidence/review gap.

A correct PMID does not prove quote existence. Word overlap or quote existence
does not prove support or applicability. Unavailable full text stays not_checked.
Do not invent page numbers, quotes, effect sizes or independent corroboration.
Hashes establish content identity, not truth; graph edges are not verification.
Existing automatic supported verdicts, source counts and model-name declarations
are not clinical review. Guideline excerpts are not proof of current guidance.
Keep review pending where material evidence is missing; do not manufacture a
second independent source from a copy of the first.
Do not import automated diagnosis, treatment selection, prognosis or drug design.
Private health data is local-only by default; no upload or external query containing
private data without explicit authorization. Redaction regexes are not privacy proof.
Return a compact per-claim review with unresolved items, not clinical clearance.

## medical_report_handoff

Use the existing research report and evidence IDs, not a parallel evidence store.
Create a professional view and a plain-language view from the SAME evidence base.
Preserve claim_id -> source_id -> exact passage -> review finding -> limitations.
Both views must retain uncertainty, contradictory evidence, scope and missing
evidence; simplification must not strengthen a conclusion.
Label original findings, assistant interpretation and clinician assessment apart.
Include question/version, what was reviewed, what remains not_checked, applicable
population/time limits, reviewer and next human review action.
A report or automated verify pass is not clinical clearance or execution authority.
No automatic diagnosis, treatment, prognosis or drug-design module is enabled.
Missing source text or unresolved applicability keeps the handoff review-only.
Do not embed raw private health data in external reports or logs without explicit
authorization; do not write a knowledge base or send a report automatically.
Return the two views and an explicit unresolved-evidence handoff.

## health_support_review

Use only for user-requested ordinary health-support planning, not automatically
after every medical search. Reuse existing goals/constraints/history; no new
patient database, parallel scheduler or automatic agent swarm.
Separate responsibilities: data quality/trends; evidence/applicability review;
goal-aware support. For analysis, state the question, source dates, units, window,
missingness and method BEFORE calculating; inspect outputs before interpreting.
Do not execute unreviewed code from a document or treat a simplified sandbox as
safe isolation. Ask only for unknown facts that would change the next decision.
Return: known facts with source/time; goal; constraints; previous attempts;
data_quality_gaps; evidence/applicability limits; proposed_action; rationale;
user_confirmation (pending unless actually given); feedback_status; review_time.
Keep proposed, confirmed and completed distinct. Unknown feedback is unknown,
not adherence failure or success. Record actual completion only from an existing
authorized feedback/history source, not a recommendation or scheduled time.
Choose a small, reversible, non-clinical candidate action with the user; medical
uncertainty goes to human assessment, not guessed scheduling.
Hand off only a confirmed ordinary action through the scheduler's EXISTING input
and review flow; these notes themselves are not executable scheduling input.
Review actual feedback/barriers against the chosen goal before adapting. A trend
does not prove causation; do not infer treatment changes or medication doses.
Missing/stale/conflicting recovery evidence follows existing needs_current_input
rules. A synthetic result cannot clear unknown real state.
No automatic diagnosis, treatment selection, prognosis, drug design, private-data
upload, account sync, calendar/CRM/knowledge-base write or external provider call
is authorized by this prompt. Keep raw private data local unless explicitly approved.
