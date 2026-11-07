# Claim Verification Prompts

Updated: 2026-06-04 20:05:00 | Codex (GPT-5)

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
