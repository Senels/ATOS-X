from app.ai.assistant import ExecutiveAssistant


def test_assistant_has_management_level_domain_coverage():
    a = ExecutiveAssistant()
    caps = a.capabilities()
    for domain in ("market", "strategy", "ai", "agents", "risk", "execution", "portfolio", "data", "system"):
        assert domain in caps["domains"]
    assert "inspect_risk" in caps["read"]
    assert "retrain_ai" in caps["action"]


def test_market_moving_actions_require_confirmation():
    a = ExecutiveAssistant()
    plan = a.plan("close_position", live=True)
    assert plan.allowed_by_policy
    assert plan.requires_confirmation
    assert not plan.reversible


def test_unknown_action_is_denied():
    plan = ExecutiveAssistant().plan("delete_exchange_account", live=True)
    assert not plan.allowed_by_policy


def test_explanation_is_management_grade():
    out = ExecutiveAssistant().explain({
        "verdict": "BUY", "confidence": 0.82, "reason": "trend + AI agreement",
    })
    assert out["decision"] == "APPROVE"
    assert out["confidence_pct"] == 82.0
    assert "AI" in out["management_message"] or "risk" in out["management_message"]
