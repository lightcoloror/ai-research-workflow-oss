from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .quality import quality_for_report


READINESS_STATES = ("ready", "candidate", "insufficient", "contradicted")
EVIDENCE_DIRECTIONS = ("supported", "partial", "contradicted", "not_found")
VOCABULARY = {
    "coverage": "Fraction of answer claims with local supporting or partial evidence.",
    "source_diversity": "Count of independent source identities supporting answer claims.",
    "freshness": "Whether time-sensitive claims are supported by evidence within their declared age limit.",
    "contradiction": "Whether local evidence contains an unresolved refutation or competing account.",
    "citation_support": "Whether supported claims expose traceable evidence and source citations.",
    "answer_readiness": "Delivery state: ready, candidate, insufficient, or contradicted.",
}


def load_answer_cases(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("cases", [payload])
    if not isinstance(payload, list):
        raise ValueError("Answer benchmark input must be an object, a list, or an object containing cases.")
    cases = [item for item in payload if isinstance(item, dict)]
    if not cases:
        raise ValueError("Answer benchmark input contains no cases.")
    return cases


def evaluate_answer_case(case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("id") or "case")
    question = str(case.get("question") or "").strip()
    as_of = _parse_datetime(str(case.get("as_of") or ""))
    sources = [dict(item) for item in case.get("sources", []) if isinstance(item, dict)]
    evidence = [dict(item) for item in case.get("evidence", []) if isinstance(item, dict)]
    claims = [dict(item) for item in case.get("answer_claims", []) if isinstance(item, dict)]
    source_by_id = {str(item.get("id") or ""): item for item in sources if item.get("id")}
    evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        evidence_by_claim.setdefault(str(item.get("claim_id") or ""), []).append(item)

    report = _quality_report(question, claims, sources, evidence, evidence_by_claim)
    quality = quality_for_report(report)
    quality_by_claim = {str(item.get("claim_id") or ""): item for item in quality.get("claim_quality", [])}
    claim_results = [
        _evaluate_claim(claim, evidence_by_claim.get(str(claim.get("id") or ""), []), source_by_id, quality_by_claim, as_of)
        for claim in claims
    ]
    summary = _vocabulary_summary(case, claim_results)
    readiness, reasons, gates = _decide_readiness(summary, claim_results)
    packet = _answer_packet(case_id, question, readiness, claim_results, source_by_id, evidence)
    fallback = _fallback_proposal(readiness, reasons, summary, claim_results)
    expected = str((case.get("expected") or {}).get("readiness") or "")
    result = {
        "schema": "research.answer-readiness.v1",
        "case_id": case_id,
        "question": question,
        "as_of": str(case.get("as_of") or ""),
        "retrieval": _retrieval_state(case, readiness),
        "vocabulary": summary,
        "readiness": readiness,
        "reasons": reasons,
        "decision_trace": gates,
        "claim_results": claim_results,
        "quality_summary": quality.get("quality_summary", {}),
        "answer_packet": packet,
        "fallback_proposal": fallback,
        "expected_readiness": expected,
        "expectation_match": not expected or expected == readiness,
        "input_hash": _stable_hash(case),
    }
    result["result_hash"] = _stable_hash({key: value for key, value in result.items() if key != "result_hash"})
    return result


def explain_answer_case(case: dict[str, Any]) -> dict[str, Any]:
    result = evaluate_answer_case(case)
    return {
        "schema": result["schema"],
        "case_id": result["case_id"],
        "question": result["question"],
        "readiness": result["readiness"],
        "reasons": result["reasons"],
        "vocabulary": result["vocabulary"],
        "decision_trace": result["decision_trace"],
        "claim_explanations": [
            {
                "claim_id": item["claim_id"],
                "claim": item["claim"],
                "status": item["status"],
                "supporting_evidence_ids": item["supporting_evidence_ids"],
                "contradicting_evidence_ids": item["contradicting_evidence_ids"],
                "freshness": item["freshness"],
                "citation_support": item["citation_support"],
                "source_diversity": item["source_diversity"],
                "issues": item["issues"],
            }
            for item in result["claim_results"]
        ],
        "fallback_proposal": result["fallback_proposal"],
        "result_hash": result["result_hash"],
    }


def run_answer_benchmark(path: str | Path) -> dict[str, Any]:
    cases = load_answer_cases(path)
    results = [evaluate_answer_case(case) for case in cases]
    counts = {state: sum(1 for item in results if item["readiness"] == state) for state in READINESS_STATES}
    failures = [
        {"case_id": item["case_id"], "expected": item["expected_readiness"], "actual": item["readiness"]}
        for item in results
        if not item["expectation_match"]
    ]
    output = {
        "schema": "research.answer-benchmark.v1",
        "overall": "ok" if not failures else "error",
        "case_count": len(results),
        "readiness_counts": counts,
        "expectation_failures": failures,
        "vocabulary": VOCABULARY,
        "results": results,
        "fixture_hash": _stable_hash(cases),
    }
    output["result_hash"] = _stable_hash({key: value for key, value in output.items() if key != "result_hash"})
    return output


def answer_result_to_markdown(result: dict[str, Any], *, explain: bool = False) -> str:
    if result.get("schema") == "research.answer-benchmark.v1":
        lines = [
            "# Offline Answer Readiness Benchmark",
            "",
            f"- Overall: `{result.get('overall')}`",
            f"- Cases: {result.get('case_count', 0)}",
            f"- Result hash: `{result.get('result_hash', '')}`",
            "",
            "| Case | Readiness | Expected | Match |",
            "| --- | --- | --- | --- |",
        ]
        for item in result.get("results", []):
            lines.append(
                f"| {_cell(item.get('case_id'))} | {item.get('readiness')} | {item.get('expected_readiness') or 'unspecified'} | {item.get('expectation_match')} |"
            )
        return "\n".join(lines) + "\n"
    lines = [
        f"# Answer Readiness: {result.get('case_id', '')}",
        "",
        f"- Readiness: `{result.get('readiness', '')}`",
        f"- Question: {_cell(result.get('question', ''))}",
        f"- Result hash: `{result.get('result_hash', '')}`",
        "",
        "## Reasons",
        "",
    ]
    lines.extend(f"- {item}" for item in result.get("reasons", []))
    lines.extend(["", "## Claims", "", "| Claim | Status | Sources | Freshness | Citations | Issues |", "| --- | --- | --- | --- | --- | --- |"])
    rows = result.get("claim_explanations") if explain else result.get("claim_results")
    for item in rows or []:
        lines.append(
            f"| {_cell(item.get('claim'))} | {item.get('status')} | {item.get('source_diversity', 0)} | {item.get('freshness')} | {item.get('citation_support')} | {_cell('; '.join(item.get('issues') or []) or 'none')} |"
        )
    lines.extend(["", "## Fallback Proposal", ""])
    fallback = result.get("fallback_proposal", {})
    lines.append(f"- Required: `{fallback.get('required', False)}`")
    lines.extend(f"- {item}" for item in fallback.get("actions", []))
    return "\n".join(lines) + "\n"


def _quality_report(
    question: str,
    claims: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    evidence_by_claim: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    normalized_claims = []
    citation_rows = []
    normalized_evidence = []
    for item in evidence:
        normalized_evidence.append(
            {
                "id": item.get("id", ""),
                "source_id": item.get("source_id", ""),
                "quote_or_paraphrase": item.get("quote_or_paraphrase") or item.get("text") or "",
                "support_direction": item.get("direction") or item.get("support_direction") or "not_found",
                "confidence": item.get("confidence", 0.8),
            }
        )
    for claim in claims:
        claim_id = str(claim.get("id") or "")
        rows = evidence_by_claim.get(claim_id, [])
        directions = {str(item.get("direction") or item.get("support_direction") or "not_found") for item in rows}
        if "contradicted" in directions:
            verdict = "contradicted"
        elif "supported" in directions:
            verdict = "supported"
        elif "partial" in directions:
            verdict = "partial"
        else:
            verdict = "not_found"
        evidence_ids = [str(item.get("id") or "") for item in rows if item.get("id")]
        source_ids = [str(item.get("source_id") or "") for item in rows if item.get("source_id")]
        citations = [str(item.get("citation") or "") for item in rows if item.get("citation")]
        normalized_claims.append(
            {
                "id": claim_id,
                "text": claim.get("text", ""),
                "risk_level": claim.get("risk_level", "normal"),
                "verdict": verdict,
                "confidence": claim.get("confidence", 0.8 if evidence_ids else 0),
                "evidence_ids": evidence_ids,
            }
        )
        citation_rows.append(
            {
                "claim_id": claim_id,
                "claim": claim.get("text", ""),
                "evidence_ids": evidence_ids,
                "source_ids": source_ids,
                "citations": citations,
            }
        )
    return {
        "topic": question,
        "findings": [str(item.get("text") or "") for item in claims],
        "claims": normalized_claims,
        "sources": sources,
        "evidence": normalized_evidence,
        "claim_citations": citation_rows,
    }


def _evaluate_claim(
    claim: dict[str, Any],
    evidence: list[dict[str, Any]],
    source_by_id: dict[str, dict[str, Any]],
    quality_by_claim: dict[str, dict[str, Any]],
    as_of: datetime | None,
) -> dict[str, Any]:
    claim_id = str(claim.get("id") or "")
    support = [item for item in evidence if str(item.get("direction") or item.get("support_direction")) in {"supported", "partial"}]
    contradict = [item for item in evidence if str(item.get("direction") or item.get("support_direction")) == "contradicted"]
    if contradict:
        status = "contradicted"
    elif any(str(item.get("direction") or item.get("support_direction")) == "supported" for item in support):
        status = "supported"
    elif support:
        status = "partial"
    else:
        status = "unsupported"
    source_ids = _dedupe(str(item.get("source_id") or "") for item in support)
    independence = _dedupe(_source_identity(source_by_id.get(source_id, {}), source_id) for source_id in source_ids)
    cited = [item for item in support if str(item.get("citation") or "").strip()]
    citation_support = "complete" if support and len(cited) == len(support) else "partial" if cited else "none"
    freshness, freshness_issues = _freshness(claim, support, source_by_id, as_of)
    missing_sources = [source_id for source_id in source_ids if source_id not in source_by_id]
    missing_urls = [source_id for source_id in source_ids if not str(source_by_id.get(source_id, {}).get("url") or "").strip()]
    issues = []
    if not support:
        issues.append("no_supporting_evidence")
    if support and citation_support != "complete":
        issues.append("incomplete_citations")
    if missing_sources:
        issues.append("missing_source_record")
    if missing_urls:
        issues.append("missing_source_url")
    if support and not independence:
        issues.append("no_independent_source_identity")
    if str(claim.get("risk_level") or "normal") == "high" and len(independence) < 2:
        issues.append("high_risk_needs_two_sources")
    if contradict and support:
        issues.append("unresolved_source_conflict")
    elif contradict:
        issues.append("claim_refuted_by_local_evidence")
    issues.extend(freshness_issues)
    return {
        "claim_id": claim_id,
        "claim": str(claim.get("text") or ""),
        "risk_level": str(claim.get("risk_level") or "normal"),
        "source_identities": independence,
        "status": status,
        "contradicting_source_ids": _dedupe(str(item.get("source_id") or "") for item in contradict),
        "supporting_evidence_ids": [str(item.get("id") or "") for item in support],
        "contradicting_evidence_ids": [str(item.get("id") or "") for item in contradict],
        "source_ids": source_ids,
        "source_diversity": len(independence),
        "citation_support": citation_support,
        "freshness": freshness,
        "evidence_strength": (quality_by_claim.get(claim_id) or {}).get("evidence_strength", "weak"),
        "issues": _dedupe(issues),
    }


def _vocabulary_summary(case: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    claim_count = len(claims)
    supported = [item for item in claims if item["status"] in {"supported", "partial"}]
    citation_values = [item["citation_support"] for item in supported]
    freshness_values = [item["freshness"] for item in claims if item["freshness"] != "not_required"]
    if not citation_values:
        citation_support = "none"
    elif all(item == "complete" for item in citation_values):
        citation_support = "complete"
    else:
        citation_support = "partial"
    if not freshness_values:
        freshness = "not_required"
    elif "stale" in freshness_values:
        freshness = "stale"
    elif "unknown" in freshness_values:
        freshness = "unknown"
    else:
        freshness = "current"
    return {
        "coverage": round(len(supported) / claim_count, 4) if claim_count else 0.0,
        "source_diversity": len(_dedupe(identity for item in supported for identity in item["source_identities"])),
        "freshness": freshness,
        "contradiction": "present" if any(item["status"] == "contradicted" for item in claims) else "none",
        "citation_support": citation_support,
        "answer_readiness": "pending",
        "retrieval_status": str((case.get("retrieval") or {}).get("status") or "unknown"),
    }


def _decide_readiness(summary: dict[str, Any], claims: list[dict[str, Any]]) -> tuple[str, list[str], list[dict[str, Any]]]:
    contradiction = summary["contradiction"] == "present"
    stale = summary["freshness"] == "stale"
    trace_failure = any("missing_source_record" in item["issues"] or "missing_source_url" in item["issues"] for item in claims)
    no_support = summary["coverage"] <= 0
    low_coverage = summary["coverage"] < 0.5
    all_supported = bool(claims) and all(item["status"] == "supported" for item in claims)
    citations_complete = summary["citation_support"] == "complete"
    diversity_ok = summary["source_diversity"] >= 2
    high_risk_ok = all(item["risk_level"] != "high" or item["source_diversity"] >= 2 for item in claims)
    gates = [
        {"gate": "no_unresolved_contradiction", "passed": not contradiction},
        {"gate": "support_coverage", "passed": not no_support and not low_coverage, "value": summary["coverage"]},
        {"gate": "source_trail", "passed": not trace_failure},
        {"gate": "freshness", "passed": not stale, "value": summary["freshness"]},
        {"gate": "complete_claim_support", "passed": all_supported},
        {"gate": "complete_citations", "passed": citations_complete},
        {"gate": "independent_sources", "passed": diversity_ok},
        {"gate": "high_risk_policy", "passed": high_risk_ok},
    ]
    if contradiction:
        readiness = "contradicted"
    elif no_support or low_coverage or trace_failure or stale:
        readiness = "insufficient"
    elif all_supported and citations_complete and diversity_ok and high_risk_ok:
        readiness = "ready"
    else:
        readiness = "candidate"
    summary["answer_readiness"] = readiness
    reasons = [item["gate"] for item in gates if not item["passed"]]
    return readiness, reasons or ["all_readiness_gates_passed"], gates


def _answer_packet(
    case_id: str,
    question: str,
    readiness: str,
    claims: list[dict[str, Any]],
    source_by_id: dict[str, dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    deliverable = []
    withheld = []
    used_sources: list[str] = []
    used_evidence: list[str] = []
    for item in claims:
        allowed = item["status"] in {"supported", "partial"} and not any(
            issue in item["issues"] for issue in {"missing_source_record", "missing_source_url", "stale_evidence", "unresolved_source_conflict"}
        )
        row = {
            "claim_id": item["claim_id"],
            "claim": item["claim"],
            "status": item["status"],
            "evidence_ids": item["supporting_evidence_ids"],
            "contradicting_evidence_ids": item["contradicting_evidence_ids"],
            "source_ids": item["source_ids"],
            "contradicting_source_ids": item["contradicting_source_ids"],
        }
        used_sources.extend(item["source_ids"] + item["contradicting_source_ids"])
        used_evidence.extend(item["supporting_evidence_ids"] + item["contradicting_evidence_ids"])
        if allowed:
            deliverable.append(row)
        else:
            row["issues"] = item["issues"]
            withheld.append(row)
    trail = [
        {
            "source_id": source_id,
            "url": source_by_id.get(source_id, {}).get("url", ""),
            "publisher": source_by_id.get(source_id, {}).get("publisher", ""),
            "source_type": source_by_id.get(source_id, {}).get("source_type", "unknown"),
            "local_path": source_by_id.get(source_id, {}).get("local_path", ""),
            "artifact_sha256": source_by_id.get(source_id, {}).get("artifact_sha256", ""),
        }
        for source_id in _dedupe(used_sources)
    ]
    evidence_by_id = {str(item.get("id") or ""): item for item in evidence}
    evidence_trail = [
        {
            "evidence_id": evidence_id,
            "source_id": evidence_by_id.get(evidence_id, {}).get("source_id", ""),
            "direction": evidence_by_id.get(evidence_id, {}).get("direction", "not_found"),
            "citation": evidence_by_id.get(evidence_id, {}).get("citation", ""),
            "quote_or_paraphrase": evidence_by_id.get(evidence_id, {}).get("quote_or_paraphrase") or evidence_by_id.get(evidence_id, {}).get("text") or "",
        }
        for evidence_id in _dedupe(used_evidence)
    ]
    return {
        "schema": "research.answer-packet.v1",
        "case_id": case_id,
        "question": question,
        "readiness": readiness,
        "deliverable_claims": deliverable,
        "withheld_claims": withheld,
        "source_trail": trail,
        "evidence_trail": evidence_trail,
        "guardrail": "Only deliverable_claims may be used in an answer; withheld claims remain explicit gaps.",
    }


def _fallback_proposal(readiness: str, reasons: list[str], summary: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    actions = []
    if readiness == "contradicted":
        actions.append("Preserve competing local evidence as separate positions; do not merge them into one fact.")
        actions.append("Seek an additional independent local or manually reviewed source before resolution.")
    if summary["freshness"] == "stale":
        actions.append("Find a newer registered source or local archive for time-sensitive claims.")
    if summary["retrieval_status"] in {"empty", "unreachable", "timeout"}:
        actions.append("Inspect registered local sources, WCF archives, or manually reviewed URLs; report unavailable if none exist.")
    if "complete_citations" in reasons or "source_trail" in reasons:
        actions.append("Attach local evidence IDs and source URLs before delivery.")
    if "support_coverage" in reasons or "complete_claim_support" in reasons:
        actions.append("Withhold unsupported claims and generate a local evidence-gap list.")
    if not actions and readiness != "ready":
        actions.append("Keep the packet as a candidate and collect an independent local source.")
    return {
        "required": readiness != "ready",
        "actions": _dedupe(actions),
        "network_executed": False,
        "model_memory_allowed": False,
        "proposal_only": True,
    }


def _freshness(
    claim: dict[str, Any],
    evidence: list[dict[str, Any]],
    source_by_id: dict[str, dict[str, Any]],
    as_of: datetime | None,
) -> tuple[str, list[str]]:
    if not bool(claim.get("requires_freshness", False)):
        return "not_required", []
    max_age = int(claim.get("max_age_days") or 365)
    if not as_of:
        return "unknown", ["freshness_as_of_missing"]
    dates = []
    for item in evidence:
        source = source_by_id.get(str(item.get("source_id") or ""), {})
        parsed = _parse_datetime(str(source.get("published_at") or source.get("retrieved_at") or ""))
        if parsed:
            dates.append(parsed)
    if not dates:
        return "unknown", ["freshness_date_missing"]
    newest = max(dates)
    if (as_of - newest).days > max_age:
        return "stale", ["stale_evidence"]
    return "current", []


def _retrieval_state(case: dict[str, Any], readiness: str) -> dict[str, Any]:
    retrieval = dict(case.get("retrieval") or {})
    return {
        "configured": bool(retrieval.get("configured", False)),
        "service_online": bool(retrieval.get("service_online", False)),
        "status": str(retrieval.get("status") or "unknown"),
        "result_count": int(retrieval.get("result_count") or 0),
        "answer_ready": readiness == "ready",
        "separation_rule": "configuration, service liveness, retrieval success, and answer readiness are independent states",
    }


def _source_identity(source: dict[str, Any], fallback: str) -> str:
    publisher = str(source.get("publisher") or "").strip().lower()
    if publisher:
        return publisher
    host = urlparse(str(source.get("url") or "")).hostname or ""
    return host.lower() or fallback


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dedupe(items: Any) -> list[str]:
    output = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def _cell(value: object) -> str:
    return " ".join(str(value).replace("|", "\\|").split())
