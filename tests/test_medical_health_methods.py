from pathlib import Path

import pytest

from research_workflow.mcp_tools import get_claim_verification_prompt as mcp_get_prompt
from research_workflow.mcp_tools import list_claim_verification_prompts
from research_workflow.prompt_registry import load_claim_verification_prompts
from research_workflow.routing import build_routing_plan, routing_plan_to_markdown


ROOT = Path(__file__).resolve().parents[1]
MEDICAL_PROMPTS = ["medical_evidence_review", "medical_report_handoff"]


def test_explicit_medical_profile_exposes_review_guidance_not_extra_execution():
    plan = build_routing_plan("public literature question", profile="medical_high_risk")
    assert plan.to_dict()["review_prompts"] == MEDICAL_PROMPTS
    assert sum(len(stage.providers) for stage in plan.stages) <= 6
    assert "not executed checks or clinical clearance" in routing_plan_to_markdown(plan)
    assert "health_support_review" not in plan.review_prompts


@pytest.mark.parametrize("query,profile", [
    ("ordinary public policy question", "policy_primary"),
    ("health literature", "default"),
    ("API SDK", "technical_primary"),
])
def test_other_profiles_do_not_silently_enable_medical_or_health_methods(query, profile):
    assert build_routing_plan(query, profile=profile).review_prompts == []


def test_known_url_medical_review_preserves_existing_fetch_route():
    plan = build_routing_plan("https://example.test/paper", profile="medical_high_risk")
    assert plan.route_kind == "known_url"
    assert not plan.stages
    assert plan.external_acquisition[0]["adapter"] == "web_content_fetcher"
    assert plan.review_prompts == MEDICAL_PROMPTS


def test_packaged_and_source_prompts_match_and_are_available_through_existing_mcp():
    source = ROOT / "docs/claim_verification_prompts.md"
    packaged = ROOT / "src/research_workflow/assets/docs/claim_verification_prompts.md"
    assert source.read_bytes() == packaged.read_bytes()
    prompts = load_claim_verification_prompts(packaged)
    exposed = list_claim_verification_prompts()["prompts"]
    for name in [*MEDICAL_PROMPTS, "health_support_review"]:
        assert mcp_get_prompt(name)["text"] == prompts[name].text == exposed[name]["text"]


def test_templates_retain_independent_review_checks_and_feedback_boundaries():
    prompts = load_claim_verification_prompts()
    evidence = prompts["medical_evidence_review"].text
    for required in ("quote_exists", "supports_claim", "applicability", "not_checked",
                     "assistant inference", "clinician assessment", "local-only"):
        assert required in evidence
    handoff = prompts["medical_report_handoff"].text
    assert "SAME evidence base" in handoff
    assert "simplification must not strengthen" in handoff
    support = prompts["health_support_review"].text
    for required in ("proposed, confirmed and completed distinct", "needs_current_input",
                     "Unknown feedback is unknown", "EXISTING input", "not executable",
                     "calendar/CRM/knowledge-base", "No automatic diagnosis"):
        assert required in support


def test_research_discovery_links_the_on_demand_method_guide():
    discovery = (ROOT / "integrations/AGENT_DISCOVERY.md").read_text(encoding="utf-8")
    assert "docs/medical-health-methods.md" in discovery
    assert (ROOT / "docs/medical-health-methods.md").is_file()
