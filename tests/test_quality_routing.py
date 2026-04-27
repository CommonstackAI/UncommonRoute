from __future__ import annotations

import pytest

from uncommon_route.router.quality import (
    apply_quality_guards,
    model_served_quality,
    scoring_served_quality_target,
    target_served_quality,
)
from uncommon_route.router.selector import select_from_pool
from uncommon_route.router.types import (
    BanditConfig,
    CapabilityLane,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingFeatures,
    RoutingMode,
    SelectionWeights,
    ServedQuality,
    Tier,
)


def _caps() -> dict[str, ModelCapabilities]:
    return {
        "anthropic/claude-opus-4-7": ModelCapabilities(tool_calling=True, vision=True, reasoning=True),
        "anthropic/claude-sonnet-4-6": ModelCapabilities(tool_calling=True, vision=True),
        "anthropic/claude-haiku-4-5": ModelCapabilities(tool_calling=True, vision=True),
        "minimax/minimax-m2.7": ModelCapabilities(tool_calling=True),
        "minimax/minimax-m2": ModelCapabilities(tool_calling=True),
        "openai/gpt-5.4-pro-2026-03-05": ModelCapabilities(tool_calling=True, vision=True, reasoning=True),
        "google/gemini-2.5-pro": ModelCapabilities(tool_calling=True, vision=True),
    }


def test_target_served_quality_respects_mode_and_complexity() -> None:
    assert target_served_quality(RoutingMode.AUTO, Tier.SIMPLE) is ServedQuality.ECONOMY
    assert target_served_quality(RoutingMode.AUTO, Tier.MEDIUM) is ServedQuality.BALANCED
    assert target_served_quality(RoutingMode.AUTO, Tier.COMPLEX) is ServedQuality.PREMIUM
    assert target_served_quality(RoutingMode.FAST, Tier.COMPLEX) is ServedQuality.BALANCED
    assert target_served_quality(RoutingMode.BEST, Tier.SIMPLE) is ServedQuality.BALANCED


def test_model_served_quality_anchors_anthropic_tool_safe_lane() -> None:
    caps = _caps()
    assert model_served_quality("minimax/minimax-m2", CapabilityLane.ANTHROPIC_TOOL_SAFE, caps["minimax/minimax-m2"]) is ServedQuality.ECONOMY
    assert model_served_quality("minimax/minimax-m2.7", CapabilityLane.ANTHROPIC_TOOL_SAFE, caps["minimax/minimax-m2.7"]) is ServedQuality.BALANCED
    assert model_served_quality("anthropic/claude-opus-4-7", CapabilityLane.ANTHROPIC_TOOL_SAFE, caps["anthropic/claude-opus-4-7"]) is ServedQuality.PREMIUM


def test_model_served_quality_normalizes_moonshot_provider_aliases() -> None:
    caps = ModelCapabilities(tool_calling=True)

    assert model_served_quality("moonshot/kimi-k2.5", CapabilityLane.GENERAL, caps) is ServedQuality.BALANCED
    assert model_served_quality("moonshotai/kimi-k2.5", CapabilityLane.GENERAL, caps) is ServedQuality.BALANCED


def test_openai_nano_is_not_premium_for_reasoning_lane() -> None:
    caps = ModelCapabilities(tool_calling=True, reasoning=True)

    assert model_served_quality(
        "openai/gpt-5.4-nano-2026-03-17",
        CapabilityLane.REASONING,
        caps,
    ) is ServedQuality.ECONOMY


def test_quality_guards_keep_auto_complex_at_balanced_floor_without_opus_only_pool() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2",
            "minimax/minimax-m2.7",
            "anthropic/claude-opus-4-7",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.COMPLEX,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
    )

    assert guard.target is ServedQuality.PREMIUM
    assert "minimax/minimax-m2" not in guard.allowed_models
    assert set(guard.allowed_models) == {
        "minimax/minimax-m2.7",
        "anthropic/claude-opus-4-7",
    }
    assert "served-quality-target-preferred=premium(1/3)" in guard.notes


def test_scoring_target_uses_balanced_floor_for_auto_complex() -> None:
    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
    ) is ServedQuality.BALANCED
    assert scoring_served_quality_target(
        RoutingMode.BEST,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
    ) is ServedQuality.PREMIUM


def test_quality_guards_keep_best_complex_premium_only_when_available() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2.7",
            "anthropic/claude-opus-4-7",
        ],
        mode=RoutingMode.BEST,
        tier=Tier.COMPLEX,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
    )

    assert guard.target is ServedQuality.PREMIUM
    assert guard.allowed_models == ["anthropic/claude-opus-4-7"]


def test_quality_guards_filter_high_risk_medium_flow_to_balanced_or_higher() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2",
            "minimax/minimax-m2.7",
            "anthropic/claude-sonnet-4-6",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        step_risk="high",
    )

    assert guard.target is ServedQuality.BALANCED
    assert guard.floor is ServedQuality.BALANCED
    assert "minimax/minimax-m2" not in guard.allowed_models
    assert set(guard.allowed_models) == {
        "minimax/minimax-m2.7",
        "anthropic/claude-sonnet-4-6",
    }
    assert "step-risk=high" in guard.notes


def test_quality_guards_enforce_high_risk_floor_even_when_target_is_economy() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2",
            "minimax/minimax-m2.7",
            "anthropic/claude-haiku-4-5",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.SIMPLE,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        step_risk="high",
    )

    assert guard.target is ServedQuality.ECONOMY
    assert guard.floor is ServedQuality.BALANCED
    assert guard.allowed_models == ["minimax/minimax-m2.7"]
    assert "step-risk-floor=balanced" in guard.notes


def test_quality_guards_enforce_high_risk_floor_in_fast_mode() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2",
            "minimax/minimax-m2.7",
            "anthropic/claude-haiku-4-5",
        ],
        mode=RoutingMode.FAST,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        step_risk="high",
    )

    assert guard.target is ServedQuality.ECONOMY
    assert guard.floor is ServedQuality.BALANCED
    assert guard.allowed_models == ["minimax/minimax-m2.7"]


def test_quality_guards_allow_low_risk_medium_economy_to_compete() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "minimax/minimax-m2",
            "minimax/minimax-m2.7",
            "anthropic/claude-sonnet-4-6",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        step_risk="low",
    )

    assert guard.target is ServedQuality.BALANCED
    assert guard.floor is ServedQuality.ECONOMY
    assert set(guard.allowed_models) == {
        "minimax/minimax-m2",
        "minimax/minimax-m2.7",
        "anthropic/claude-sonnet-4-6",
    }
    assert "step-risk=low" in guard.notes


def test_quality_guards_keep_auto_continuity_soft_to_avoid_premium_lock() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-opus-4-7",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        continuity_floor=ServedQuality.PREMIUM,
    )

    assert guard.continuity_floor is None
    assert set(guard.allowed_models) == {
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-7",
    }
    assert "continuity-soft=premium" in guard.notes


def test_quality_guards_respect_best_continuity_floor_when_available() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "anthropic/claude-sonnet-4-6",
            "anthropic/claude-opus-4-7",
        ],
        mode=RoutingMode.BEST,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        continuity_floor=ServedQuality.PREMIUM,
    )

    assert guard.continuity_floor is ServedQuality.PREMIUM
    assert guard.allowed_models == ["anthropic/claude-opus-4-7"]


def test_quality_guards_keep_auto_continuity_soft_when_floor_unavailable() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        ["anthropic/claude-sonnet-4-6"],
        mode=RoutingMode.AUTO,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
        capabilities=caps,
        continuity_floor=ServedQuality.PREMIUM,
    )

    assert guard.allowed_models == ["anthropic/claude-sonnet-4-6"]
    assert "continuity-soft=premium" in guard.notes


def test_auto_complex_prefers_balanced_floor_when_quality_is_tied() -> None:
    pricing = {
        "minimax/minimax-m2.7-test": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-test": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True)
        for model in pricing
    }

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.9,
        reasoning_text="test",
        available_models=list(pricing),
        estimated_input_tokens=4_000,
        max_output_tokens=1_000,
        prompt="Implement a multi-step agent workflow.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.0,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            cache_affinity=0.0,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
            quality_alignment=0.20,
            continuity=0.0,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "minimax/minimax-m2.7-test"
    assert "served-quality-score-target=balanced" in decision.reasoning
    minimax = next(score for score in decision.candidate_scores if score.model.startswith("minimax/"))
    opus = next(score for score in decision.candidate_scores if score.model.startswith("anthropic/"))
    assert minimax.quality_alignment == 1.0
    assert opus.quality_alignment == 0.82


def test_missing_pricing_is_not_treated_as_free() -> None:
    pricing = {
        "known/cheap": ModelPricing(0.10, 0.20),
    }
    caps = {
        "unknown/mystery": ModelCapabilities(tool_calling=True),
        "known/cheap": ModelCapabilities(tool_calling=True),
    }

    decision = select_from_pool(
        complexity=0.20,
        mode=RoutingMode.AUTO,
        confidence=0.9,
        reasoning_text="test",
        available_models=["unknown/mystery", "known/cheap"],
        estimated_input_tokens=4_000,
        max_output_tokens=1_000,
        prompt="Say hello.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.0,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            cache_affinity=0.0,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
            quality_alignment=0.0,
            continuity=0.0,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "known/cheap"
    unknown = next(score for score in decision.candidate_scores if score.model == "unknown/mystery")
    known = next(score for score in decision.candidate_scores if score.model == "known/cheap")
    assert unknown.predicted_cost > known.predicted_cost


def test_agentic_step_disables_random_bandit_sampling_without_model_lock() -> None:
    pricing = {
        "minimax/minimax-m2.7-test": ModelPricing(0.30, 1.20),
        "moonshot/kimi-k2.5-test": ModelPricing(0.60, 3.00),
        "anthropic/claude-sonnet-test": ModelPricing(3.00, 15.00),
        "anthropic/claude-opus-test": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }
    features = RoutingFeatures(
        step_type="tool-result-followup",
        has_tool_results=True,
        needs_tool_calling=True,
        is_agentic=True,
        session_present=True,
    )

    selected: list[str] = []
    last_reasoning = ""
    for _ in range(30):
        decision = select_from_pool(
            complexity=0.86,
            mode=RoutingMode.AUTO,
            confidence=0.9,
            reasoning_text="test-session",
            available_models=list(pricing),
            estimated_input_tokens=4_000,
            max_output_tokens=1_000,
            prompt="Continue the same agentic coding task.",
            pricing=pricing,
            capabilities=caps,
            requirements=RequestRequirements(needs_tool_calling=True),
            routing_features=features,
            bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.COMPLEX,)),
        )
        selected.append(decision.model)
        last_reasoning = decision.reasoning

    assert len(set(selected)) == 1
    assert "step-stable=no-bandit" in last_reasoning


def test_agentic_step_disables_random_bandit_sampling_without_session_id() -> None:
    pricing = {
        "minimax/minimax-m2.7-test": ModelPricing(0.30, 1.20),
        "moonshot/kimi-k2.5-test": ModelPricing(0.60, 3.00),
        "anthropic/claude-opus-test": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }
    features = RoutingFeatures(
        step_type="tool-result-followup",
        has_tool_results=True,
        needs_tool_calling=True,
        is_agentic=True,
        session_present=False,
    )

    selected: list[str] = []
    last_reasoning = ""
    for _ in range(30):
        decision = select_from_pool(
            complexity=0.86,
            mode=RoutingMode.AUTO,
            confidence=0.9,
            reasoning_text="test-sessionless-agent-step",
            available_models=list(pricing),
            estimated_input_tokens=4_000,
            max_output_tokens=1_000,
            prompt="Continue from this tool result.",
            pricing=pricing,
            capabilities=caps,
            requirements=RequestRequirements(needs_tool_calling=True),
            routing_features=features,
            bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.COMPLEX,)),
        )
        selected.append(decision.model)
        last_reasoning = decision.reasoning

    assert len(set(selected)) == 1
    assert "step-stable=no-bandit" in last_reasoning


def test_low_risk_medium_step_can_select_economy_when_cost_dominates() -> None:
    pricing = {
        "minimax/minimax-m2-test": ModelPricing(0.05, 0.20),
        "minimax/minimax-m2.7-test": ModelPricing(3.00, 12.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True)
        for model in pricing
    }

    decision = select_from_pool(
        complexity=0.50,
        mode=RoutingMode.AUTO,
        confidence=0.9,
        reasoning_text="test-low-risk-cost",
        available_models=list(pricing),
        estimated_input_tokens=4_000,
        max_output_tokens=1_000,
        prompt="Done.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
            step_risk="low",
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
        ),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.0,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            cache_affinity=0.0,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
            quality_alignment=0.0,
            continuity=0.0,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "minimax/minimax-m2-test"
    assert "step-risk=low" in decision.reasoning


def test_low_risk_tool_result_step_sets_medium_cap_not_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Create a weather-cli project."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "Done"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Create a weather-cli project.",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM
    assert "risk:low" in features.tags()


def test_high_risk_anthropic_tool_result_sets_medium_floor_and_context_length() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features, extract_context_features

    traceback_text = "Traceback (most recent call last):\nAssertionError: expected 200"
    body = {
        "messages": [
            {"role": "user", "content": "Run the tests."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": traceback_text,
                        "is_error": True,
                    }
                ],
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run the tests.",
    )
    context = extract_context_features(body, step_type, "Run the tests.")

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None
    assert context["ctx_tool_result_length"] > 0


def test_previous_tool_error_does_not_poison_unrelated_new_user_step() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "Traceback (most recent call last):\nAssertionError: boom"},
            {"role": "user", "content": "thanks"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="thanks",
        session_id="session-1",
    )

    assert features.step_type == "tool-selection"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM


def test_retry_after_previous_tool_error_remains_high_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "Traceback (most recent call last):\nAssertionError: boom"},
            {"role": "user", "content": "try again"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="try again",
        session_id="session-1",
    )

    assert features.step_type == "tool-selection"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


def test_reasoning_effort_sets_reasoning_preference_and_medium_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [{"role": "user", "content": "Think carefully and solve this."}],
        "reasoning_effort": "high",
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Think carefully and solve this.",
    )

    assert features.prefers_reasoning is True
    assert features.tier_floor is Tier.MEDIUM
    assert features.request_requirements().prefers_reasoning is True
    assert "reasoning" in features.tags()


def test_anthropic_thinking_sets_reasoning_preference_and_medium_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [{"role": "user", "content": "Solve this with extended thinking."}],
        "thinking": {"type": "enabled", "budget_tokens": 1024},
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Solve this with extended thinking.",
    )

    assert features.prefers_reasoning is True
    assert features.tier_floor is Tier.MEDIUM


def test_low_reasoning_effort_prefers_reasoning_without_forcing_tier_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [{"role": "user", "content": "Summarize this sentence."}],
        "reasoning": {"effort": "low"},
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Summarize this sentence.",
    )

    assert features.prefers_reasoning is True
    assert features.tier_floor is None


def test_short_successful_tool_result_is_low_risk_even_for_implementation_prompt() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Implement the weather CLI."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "Done"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Implement the weather CLI.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM


def test_successful_test_summary_with_zero_failed_is_not_high_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "12 passed, 0 failed, 1 skipped in 0.42s"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM


def test_real_failure_text_after_zero_failed_summary_stays_high_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "12 passed, 0 failed\npost-test cleanup failed"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


def test_nonzero_error_summary_is_high_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "11 passed, 1 error in 0.42s"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


@pytest.mark.parametrize("tool_output", [
    "463 passed, 1 warning in 167.95s",
    "Ran 42 tests in 3.2s\nOK",
    "Test Suites: 3 passed, 3 total\nTests: 27 passed, 27 total",
    "ok  \tgithub.com/acme/project/pkg\t0.123s",
    "12 passed, 0 failed, 0 errors in 0.42s",
])
def test_common_successful_tool_summaries_are_low_risk(tool_output: str) -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": tool_output},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM


@pytest.mark.parametrize("tool_output", [
    "FAILED tests/test_router.py::test_route - AssertionError",
    "11 passed, 1 error in 0.42s",
    "Command failed with exit code 1",
    "Process completed with exit code 1.",
    "exit status 2",
])
def test_common_failed_tool_summaries_are_high_risk(tool_output: str) -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": tool_output},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


def test_auto_continuity_does_not_force_expensive_premium_only_pool() -> None:
    pricing = {
        "minimax/minimax-m2.7-test": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-test": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.9,
        reasoning_text="test-continuity-cost",
        available_models=list(pricing),
        estimated_input_tokens=8_000,
        max_output_tokens=2_000,
        prompt="Continue the same complex tool session.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
            previous_served_quality=ServedQuality.PREMIUM,
            continuity_quality_floor=ServedQuality.PREMIUM,
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
            session_present=True,
        ),
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.COMPLEX,)),
    )

    assert decision.model == "minimax/minimax-m2.7-test"
    assert [score.model for score in decision.candidate_scores] == [
        "minimax/minimax-m2.7-test",
        "anthropic/claude-opus-test",
    ]
    assert decision.continuity_quality_floor is None
    assert "continuity-soft=premium" in decision.reasoning
    assert "served-quality-floor=balanced" in decision.reasoning
