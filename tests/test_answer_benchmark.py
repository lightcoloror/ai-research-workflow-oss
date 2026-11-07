from __future__ import annotations

import json
from pathlib import Path

from research_workflow.answer_benchmark import (
    READINESS_STATES,
    VOCABULARY,
    evaluate_answer_case,
    explain_answer_case,
    load_answer_cases,
    run_answer_benchmark,
)
from research_workflow.cli import main as cli_main


FIXTURES = Path(__file__).parent / "fixtures" / "answer_readiness_cases.json"


def _case(case_id: str) -> dict:
    return next(item for item in load_answer_cases(FIXTURES) if item["id"] == case_id)


def test_offline_answer_benchmark_covers_required_states_and_failures() -> None:
    result = run_answer_benchmark(FIXTURES)

    assert result["overall"] == "ok"
    assert result["case_count"] == 14
    assert set(result["readiness_counts"]) == set(READINESS_STATES)
    assert result["readiness_counts"] == {
        "ready": 2,
        "candidate": 5,
        "insufficient": 5,
        "contradicted": 2,
    }
    assert result["expectation_failures"] == []
    assert set(VOCABULARY) == {
        "coverage",
        "source_diversity",
        "freshness",
        "contradiction",
        "citation_support",
        "answer_readiness",
    }


def test_retrieval_success_is_separate_from_answer_readiness() -> None:
    result = evaluate_answer_case(_case("retrieval_success_answer_unsupported"))

    assert result["retrieval"]["configured"] is True
    assert result["retrieval"]["service_online"] is True
    assert result["retrieval"]["status"] == "success"
    assert result["readiness"] == "insufficient"
    assert result["vocabulary"]["coverage"] == 0
    assert result["retrieval"]["answer_ready"] is False
    assert result["answer_packet"]["deliverable_claims"] == []
    assert result["answer_packet"]["withheld_claims"]


def test_ready_result_sets_answer_ready_without_changing_retrieval_state() -> None:
    result = evaluate_answer_case(_case("two_independent_sources_ready"))

    assert result["retrieval"]["status"] == "success"
    assert result["retrieval"]["service_online"] is False
    assert result["retrieval"]["answer_ready"] is True


def test_conflicts_are_preserved_with_both_evidence_directions() -> None:
    result = evaluate_answer_case(_case("conflicting_sources_contradicted"))
    claim = result["claim_results"][0]
    packet = result["answer_packet"]

    assert result["readiness"] == "contradicted"
    assert claim["supporting_evidence_ids"] == ["e1"]
    assert claim["contradicting_evidence_ids"] == ["e2"]
    assert packet["deliverable_claims"] == []
    assert {item["direction"] for item in packet["evidence_trail"]} == {"supported", "contradicted"}
    assert {item["source_id"] for item in packet["source_trail"]} == {"s1", "s2"}


def test_wcf_archive_fixture_keeps_local_path_and_frozen_hash() -> None:
    result = evaluate_answer_case(_case("wcf_single_source_candidate"))
    source = result["answer_packet"]["source_trail"][0]
    evidence = result["answer_packet"]["evidence_trail"][0]

    assert result["readiness"] == "candidate"
    assert source["local_path"].endswith("/web-content-fetcher/output/evidence/1dce8e2b8f71be76/extracted_text.md")
    assert source["artifact_sha256"] == "8c1e8564424fdb68b8b7bdff3e16173a2e3599e9b71620637251486c5c4d5ed6"
    assert "documentation examples" in evidence["quote_or_paraphrase"]


def test_stale_and_missing_source_paths_are_insufficient() -> None:
    stale = evaluate_answer_case(_case("stale_time_sensitive_insufficient"))
    detached = evaluate_answer_case(_case("missing_source_url_insufficient"))

    assert stale["readiness"] == "insufficient"
    assert stale["vocabulary"]["freshness"] == "stale"
    assert "stale_evidence" in stale["claim_results"][0]["issues"]
    assert detached["readiness"] == "insufficient"
    assert "missing_source_url" in detached["claim_results"][0]["issues"]


def test_newer_source_prevents_stale_source_from_overriding_freshness() -> None:
    case = {
        "id": "mixed-freshness",
        "question": "Which source controls time-sensitive freshness?",
        "as_of": "2026-07-31T00:00:00Z",
        "retrieval": {
            "configured": True,
            "service_online": False,
            "status": "local_fixture",
            "result_count": 2,
        },
        "answer_claims": [
            {
                "id": "c1",
                "text": "A current local record supports the time-sensitive claim.",
                "requires_freshness": True,
                "max_age_days": 7,
            }
        ],
        "sources": [
            {
                "id": "stale",
                "url": "local://source/stale",
                "publisher": "archive",
                "published_at": "2025-01-01T00:00:00Z",
            },
            {
                "id": "current",
                "url": "local://source/current",
                "publisher": "current-record",
                "published_at": "2026-07-30T00:00:00Z",
            },
        ],
        "evidence": [
            {
                "id": "e-stale",
                "claim_id": "c1",
                "source_id": "stale",
                "direction": "supported",
                "citation": "local://source/stale",
                "text": "Historical support.",
            },
            {
                "id": "e-current",
                "claim_id": "c1",
                "source_id": "current",
                "direction": "supported",
                "citation": "local://source/current",
                "text": "Current support.",
            },
        ],
    }

    result = evaluate_answer_case(case)

    assert result["readiness"] == "ready"
    assert result["vocabulary"]["freshness"] == "current"
    assert "stale_evidence" not in result["claim_results"][0]["issues"]


def test_every_deliverable_claim_has_evidence_and_source_trails() -> None:
    for case in load_answer_cases(FIXTURES):
        packet = evaluate_answer_case(case)["answer_packet"]
        evidence_ids = {item["evidence_id"] for item in packet["evidence_trail"]}
        source_ids = {item["source_id"] for item in packet["source_trail"]}
        for claim in packet["deliverable_claims"]:
            assert claim["evidence_ids"]
            assert claim["source_ids"]
            assert set(claim["evidence_ids"]) <= evidence_ids
            assert set(claim["source_ids"]) <= source_ids


def test_evaluation_and_explanation_are_deterministic() -> None:
    case = _case("high_risk_two_sources_ready")

    first = evaluate_answer_case(case)
    second = evaluate_answer_case(case)
    first_explain = explain_answer_case(case)
    second_explain = explain_answer_case(case)

    assert first == second
    assert first["result_hash"] == second["result_hash"]
    assert first_explain == second_explain
    assert first_explain["result_hash"] == first["result_hash"]


def test_cli_answer_commands_are_read_only(tmp_path, capsys) -> None:
    absent_data_dir = tmp_path / "must-not-be-created"

    assert cli_main(
        [
            "--data-dir",
            str(absent_data_dir),
            "answer-evaluate",
            str(FIXTURES),
            "--case-id",
            "two_independent_sources_ready",
            "--format",
            "json",
        ]
    ) == 0
    evaluated = json.loads(capsys.readouterr().out)
    assert evaluated["readiness"] == "ready"
    assert absent_data_dir.exists() is False

    assert cli_main(["answer-explain", str(FIXTURES), "--case-id", "empty_retrieval_insufficient", "--format", "json"]) == 0
    explained = json.loads(capsys.readouterr().out)
    assert explained["readiness"] == "insufficient"
    assert explained["fallback_proposal"]["model_memory_allowed"] is False

    assert cli_main(["answer-benchmark", str(FIXTURES), "--format", "json"]) == 0
    benchmark = json.loads(capsys.readouterr().out)
    assert benchmark["overall"] == "ok"
    assert benchmark["case_count"] == 14
