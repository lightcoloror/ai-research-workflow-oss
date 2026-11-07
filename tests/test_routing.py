from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace

from research_workflow.evidence import extract_evidence_from_sources
from research_workflow.mcp_server import handle_request
from research_workflow.models import Source, SourceType
from research_workflow.release_check import run_release_check
from research_workflow.routing import build_routing_plan, classify_route, coverage_gate
from research_workflow.search import (
    ProviderSearchResult,
    load_provider_stats,
    sanitize_cloud_query,
    search_firecrawl_developer,
    search_github_cli,
    update_provider_stats,
)
from research_workflow.search_pipeline import run_search_pipeline
from research_workflow.store import append_jsonl
from research_workflow.web import ResearchWebApp


def _source(index: int, provider: str = "exa", source_type: SourceType = SourceType.PROJECT) -> Source:
    return Source(
        id=f"src_{provider}_{index}",
        url=f"https://example{index}.test/item/{index}",
        title=f"Result {index}",
        source_type=source_type,
        publisher=f"example{index}.test",
        search_engine=provider,
        snippet=f"Evidence passage {index}",
    )


def test_route_classifier_covers_all_route_kinds() -> None:
    fixtures = {
        "https://docs.python.org/3/": "known_url",
        "看看 Reddit 上大家怎么评价这个产品": "social_platform",
        "这个药物治疗是否安全": "high_risk_factcheck",
        "FActScore systematic review paper": "academic",
        "GitHub API webhook error": "developer",
        "深度研究事实核查开源项目": "deep_research",
        "北京周末天气怎么样": "chinese_web",
        "best coffee grinder": "daily_web",
    }
    assert {query: classify_route(query) for query in fixtures} == fixtures


def test_explicit_providers_force_manual_without_expanding_pool() -> None:
    plan = build_routing_plan(
        "Python SDK error",
        provider_pool=["searxng", "exa", "github_cli"],
        explicit_providers=["exa"],
    )
    assert plan.routing_strategy == "manual"
    assert [stage.providers for stage in plan.stages] == [["exa"]]


def test_adaptive_developer_route_is_free_first_and_budgeted() -> None:
    plan = build_routing_plan(
        "Python SDK retry error",
        profile="technical_primary",
        provider_pool=["firecrawl_developer", "github_cli", "exa", "searxng", "brave"],
    )
    assert plan.route_kind == "developer"
    assert plan.routing_strategy == "adaptive_staged"
    assert plan.stages[0].providers == ["firecrawl_developer", "github_cli"]
    assert sum(len(stage.providers) for stage in plan.stages) <= 5


def test_profiles_can_raise_retrieval_requirements_without_changing_mode() -> None:
    assert classify_route("ordinary public policy question", profile="policy_primary") == "high_risk_factcheck"
    assert classify_route("ordinary market overview", profile="anti_seo") == "deep_research"


def test_firecrawl_developer_maps_artifacts_and_passages(monkeypatch) -> None:
    captured: dict = {}

    def fake_request(url: str, **kwargs):
        captured.update({"url": url, **kwargs})
        return {
            "results": [
                {
                    "id": "issue:owner/repo#42",
                    "type": "issue",
                    "url": "https://github.com/owner/repo/issues/42",
                    "title": "Retry does not honor 429",
                    "repository": "owner/repo",
                    "passages": [
                        {"text": "The client treated 429 as terminal.", "citation_url": "https://github.com/owner/repo/issues/42#issuecomment-1"}
                    ],
                }
            ],
            "coverage": {"total": 1},
        }

    monkeypatch.setattr("research_workflow.search._json_request", fake_request)
    sources = search_firecrawl_developer("retry token=secret-value", max_results=5)
    assert captured["url"].endswith("/v2/search/developer")
    assert captured["body"]["k"] == 5
    assert "secret-value" not in captured["body"]["query"]
    assert sources[0].artifact_id == "issue:owner/repo#42"
    evidence = extract_evidence_from_sources(sources)
    assert evidence[0].extraction_method == "provider_matched_passage"
    assert evidence[0].evidence_url.endswith("#issuecomment-1")


def test_github_cli_uses_argument_list_and_maps_updated_time(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr("research_workflow.search.shutil.which", lambda name: "C:/Tools/gh.exe")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "fullName": "owner/repo",
                        "url": "https://github.com/owner/repo",
                        "description": "A useful repository",
                        "updatedAt": "2026-08-20T12:00:00Z",
                    }
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("research_workflow.search.subprocess.run", fake_run)
    sources = search_github_cli("useful repo", max_results=3)
    assert captured["command"][:3] == ["C:/Tools/gh.exe", "search", "repos"]
    assert captured["shell"] is False if "shell" in captured else True
    assert sources[0].title == "owner/repo"
    assert sources[0].published_at == "2026-08-20T12:00:00Z"


def test_cloud_query_redacts_credentials_paths_and_private_repositories(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_PRIVATE_REPOSITORIES", "private-owner/secret-repo")
    query = r"debug API_KEY=sample-value D:\example-workspace\private private-owner/secret-repo token tvly-secretsecretsecret"
    cleaned = sanitize_cloud_query(query)
    assert "sample-value" not in cleaned
    assert "D:\\example-workspace" not in cleaned
    assert "private-owner/secret-repo" not in cleaned


def test_route_specific_provider_stats_preserve_global_compatibility(tmp_path) -> None:
    path = tmp_path / "provider_stats.json"
    update_provider_stats(
        path,
        [{"provider": "exa", "route_kind": "developer", "status": "ok", "source_count": 2, "elapsed_seconds": 0.1}],
    )
    stats = load_provider_stats(path)
    assert stats["exa"]["ok_count"] == 1
    assert stats["exa"]["routes"]["developer"]["ok_count"] == 1


def test_adaptive_pipeline_continues_after_limited_stage(monkeypatch, tmp_path) -> None:
    calls: list[list[str]] = []

    def fake_search(query: str, **kwargs):
        providers = list(kwargs["providers"])
        calls.append(providers)
        if "firecrawl_developer" in providers:
            return ProviderSearchResult([], [{"provider": item, "status": "limited", "source_count": 0} for item in providers], ["quota exceeded"])
        sources = [_source(index, providers[0]) for index in range(1, 5)]
        return ProviderSearchResult(sources, [{"provider": providers[0], "status": "ok", "source_count": len(sources)}], [])

    monkeypatch.setattr("research_workflow.search_pipeline.multi_source_search_with_status", fake_search)
    result = run_search_pipeline(
        topic="Python SDK retry error",
        data_dir=tmp_path,
        routing_strategy="adaptive_staged",
        profile="technical_primary",
        search_providers=["firecrawl_developer", "github_cli", "exa", "searxng", "brave"],
    )
    assert calls[0] == ["firecrawl_developer", "github_cli"]
    assert ["exa"] in calls
    assert result.stop_reason in {"coverage_gate_passed", "provider_budget_exhausted"}
    assert "quota exceeded" in result.warning


def test_external_payload_becomes_formal_search_evidence(tmp_path) -> None:
    result = run_search_pipeline(
        topic="Reddit product feedback",
        data_dir=tmp_path,
        no_network=True,
        routing_strategy="adaptive_staged",
        external_payloads=[
            {
                "adapter": "agent_reach",
                "sources": [{"url": "https://www.reddit.com/r/test/comments/1", "title": "Discussion"}],
                "evidence": [{"text": "Several users reported the same issue.", "url": "https://www.reddit.com/r/test/comments/1"}],
            }
        ],
    )
    assert result.external_acquisition[0]["adapter"] == "agent_reach"
    assert result.sources
    assert any(item.quote_or_paraphrase.startswith("Several users") for item in result.evidence)


def test_high_risk_coverage_never_passes_with_one_source() -> None:
    gate = coverage_gate(
        "high_risk_factcheck",
        [_source(1, source_type=SourceType.OFFICIAL)],
        [{"provider": "searxng", "status": "ok", "source_count": 1}],
    )
    assert gate["passed"] is False
    assert gate["checks"]["provider_count"] is False


def test_http_and_mcp_route_return_same_core_shape(tmp_path) -> None:
    app = ResearchWebApp(tmp_path)
    http_result = app.route({"topic": "GitHub API error", "profile": "technical_primary"})
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "research_route", "arguments": {"query": "GitHub API error", "profile": "technical_primary"}},
        }
    )
    assert response is not None and "result" in response
    mcp_result = json.loads(response["result"]["content"][0]["text"])
    for key in ("route_kind", "profile", "routing_strategy", "provider_budget", "stop_conditions"):
        assert http_result[key] == mcp_result[key]


def test_35_fixture_budget_reduces_calls_by_at_least_40_percent() -> None:
    fixtures = [f"ordinary question {index}" for index in range(15)]
    fixtures += [f"Python API error {index}" for index in range(10)]
    fixtures += [f"系统综述 paper {index}" for index in range(5)]
    fixtures += [f"医疗治疗安全证据 {index}" for index in range(5)]
    pool = ["searxng", "you", "brave", "tavily", "exa", "jina", "metaso", "serpapi", "firecrawl_developer", "github_cli"]
    adaptive_calls = sum(
        sum(len(stage.providers) for stage in build_routing_plan(query, provider_pool=pool).stages)
        for query in fixtures
    )
    legacy_calls = len(fixtures) * len(pool)
    assert len(fixtures) == 35
    assert adaptive_calls <= legacy_calls * 0.60




def test_web_deepen_skips_successful_providers_and_runs_one_gap_stage(monkeypatch, tmp_path) -> None:
    append_jsonl(
        tmp_path / "runs.jsonl",
        [
            {
                "id": "search_previous",
                "search_queries": [
                    {
                        "provider_statuses": [
                            {"provider": "searxng", "status": "ok", "source_count": 3},
                            {"provider": "you", "status": "limited", "source_count": 0},
                        ]
                    }
                ],
            }
        ],
    )
    captured: dict = {}

    class FakeResult:
        def to_dict(self) -> dict:
            return {"run_id": "search_next"}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return FakeResult()

    monkeypatch.setattr("research_workflow.web.run_search_pipeline", fake_pipeline)
    app = ResearchWebApp(tmp_path)
    result = app._search_impl(
        {
            "topic": "ordinary question",
            "deep": True,
            "previous_run_id": "search_previous",
            "routing_strategy": "adaptive_staged",
        }
    )
    assert result["run_id"] == "search_next"
    assert captured["skip_providers"] == ["searxng"]
    assert captured["max_adaptive_stages"] == 1


def test_web_ui_defaults_to_auto_route_and_keeps_manual_provider_mode() -> None:
    project_root = Path(__file__).resolve().parents[1]
    html = (project_root / "web" / "index.html").read_text(encoding="utf-8")
    javascript = (project_root / "web" / "app.js").read_text(encoding="utf-8")
    assert 'id="autoRouteToggle" type="checkbox" checked' in html
    assert 'id="searchProfile"' in html
    assert 'id="routingContent"' in html
    assert 'value="firecrawl_developer"' in html
    assert 'value="github_cli"' in html
    assert 'if (!state.autoRoute) payload.providers = selectedProviders();' in javascript
    assert 'payload.previous_run_id = state.currentRunId' in javascript


def test_release_check_uses_fresh_temporary_workspace_by_default(monkeypatch) -> None:
    observed: list[Path] = []

    def fake_release(config, work_dir: Path, *, run_tests: bool):
        assert work_dir.exists()
        observed.append(work_dir)
        return {"overall": "ok"}

    monkeypatch.setattr("research_workflow.release_check._run_release_check", fake_release)
    assert run_release_check(SimpleNamespace()) == {"overall": "ok"}
    assert run_release_check(SimpleNamespace()) == {"overall": "ok"}
    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert all(not path.exists() for path in observed)










