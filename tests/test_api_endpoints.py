from uncommon_route.api_v2 import route_preview, build_explain_response


def test_route_preview_returns_valid():
    result = route_preview(prompt="What is 2+2?", risk_tolerance=0.5)
    assert 0 <= result["tier"] <= 3
    assert "tier_name" in result
    assert 0.0 <= result["confidence"] <= 1.0
    assert "signals" in result
    assert len(result["signals"]) >= 2


def test_route_preview_risk_tolerance_effect():
    low_risk = route_preview("Design a distributed system", risk_tolerance=0.1)
    high_risk = route_preview("Design a distributed system", risk_tolerance=0.9)
    assert high_risk["tier"] <= low_risk["tier"]


def test_build_explain_response():
    explain = build_explain_response(
        signal_a={"tier": 0, "confidence": 0.85, "reasoning": "mtrag scenario"},
        signal_b={"tier": 1, "confidence": 0.70, "reasoning": "medium text", "shadow": True},
        signal_c={"tier": 0, "confidence": 0.80, "reasoning": "3 nearest neighbors are low"},
        decision_tier=0, decision_confidence=0.82,
        method="direct", model="deepseek-chat",
        cost_estimate=0.0003, cost_baseline=0.024,
    )
    assert explain["decision"]["tier"] == 0
    assert len(explain["signals"]) == 3
    assert explain["signals"][1]["shadow"] is True
    assert explain["cost"]["savings_ratio"] > 0
