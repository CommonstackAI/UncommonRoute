from __future__ import annotations

from uncommon_route.router import api
from uncommon_route.router.types import ModelCapabilities, ModelPricing, RoutingFeatures, Tier
from uncommon_route.signals.base import TierVote


class _FakeSignal:
    def __init__(self, vote: TierVote) -> None:
        self.vote = vote
        self._embed_fn = None

    def predict(self, row: dict) -> TierVote:
        return self.vote


def test_direct_route_infers_low_risk_tool_result_features() -> None:
    features = api._infer_routing_features_from_messages(
        [
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>Done</output>"},
        ],
        max_output_tokens=4096,
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM
    assert features.tier_cap_reason == "low-risk"


def test_direct_route_infers_high_risk_tool_result_features() -> None:
    features = api._infer_routing_features_from_messages(
        [
            {"role": "user", "content": "Run tests."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {
                "role": "tool",
                "content": "<returncode>1</returncode>\n<output>Traceback: AssertionError</output>",
            },
        ],
        max_output_tokens=4096,
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


def test_soft_structural_floor_keeps_short_implementation_out_of_economy(monkeypatch) -> None:
    """A short implementation ask should not be flattened to SIMPLE by A+C."""
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.44)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Implement a Python CLI app with tests, packaging, error handling, and documentation.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 1
    assert "v2:structural-medium-floor" in result.signals_text


def test_structural_high_without_non_structural_support_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 1.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.44)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Create a Python project and plan the implementation.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 1
    assert "v2:structural=3(1.00)[active]" in result.signals_text
    assert "v2:structural-high-cap=1" in result.signals_text


def test_structural_high_with_non_structural_support_can_reach_mid_high(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(2, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 1.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Resolve a regression in a Python package with tests and a minimal patch.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(is_coding=True),
        context_features=None,
    )

    assert result.tier_id == 2
    assert "v2:structural=3(1.00)[active]" in result.signals_text
    assert "v2:structural-high-cap=2" in result.signals_text


def test_structural_high_with_moderate_embedding_complex_support_keeps_high(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 1.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.57)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Resolve a subtle library behavior change without breaking existing compatibility tests.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(is_coding=True),
        context_features=None,
    )

    assert result.tier_id == 3
    assert "v2:structural=3(1.00)[active]" in result.signals_text
    assert "v2:embedding=3(0.57)" in result.signals_text
    assert not any(part.startswith("v2:structural-high-cap=") for part in result.signals_text)


def test_structural_high_with_explicit_complexity_markers_stays_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 1.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.44)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Design a distributed consensus algorithm with Byzantine faults and formal correctness proofs.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(is_coding=True),
        context_features=None,
    )

    assert result.tier_id == 2
    assert "v2:structural-high-cap=2" in result.signals_text


def test_tool_heavy_metadata_only_route_is_capped_when_embedding_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 0.80)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    messages = [
        {"role": "user", "content": "Fix the bug."},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "content": "Done"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "content": "Done"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "content": "Done"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {"role": "tool", "content": "Done"},
    ]

    result = api._v2_classify(
        "Done",
        system_prompt=None,
        messages=messages,
        routing_features=RoutingFeatures(step_type="tool-result-followup", has_tool_results=True),
        context_features=None,
    )

    assert result.tier_id == 1
    assert result.confidence == 0.30
    assert "v2:structural=3(0.80)[shadow]" in result.signals_text
    assert "v2:weak-metadata-only-cap" in result.signals_text


def test_embedding_supported_agent_pressure_softens_medium_cap(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.72)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = api.route(
        "Continue after a short tool result.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "Done"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="low",
            tier_cap=Tier.MEDIUM,
            is_agentic=True,
            is_coding=True,
            agent_step_count=18,
            agent_pressure=0.72,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert "tier-cap-softened(" in decision.reasoning
    assert "tier-cap=MEDIUM" not in decision.reasoning


def test_current_step_tier_cap_is_not_softened_by_stale_embedding(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.98)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = api.route(
        "Done",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>Done</output>"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="low",
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="low-risk",
            is_agentic=True,
            is_coding=True,
            agent_step_count=18,
            agent_pressure=0.72,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert "tier-cap-preserved(low-risk)" in decision.reasoning
    assert "tier-cap=MEDIUM" in decision.reasoning
    assert "tier-cap-softened(" not in decision.reasoning


def test_environment_routine_low_risk_cap_is_not_softened_by_agent_pressure(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.95)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "deepseek/deepseek-v3.2": ModelPricing(0.28, 0.42),
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = api.route(
        "Continue after a successful routine command.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>Done</output>"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="low",
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="environment-or-routine",
            is_agentic=True,
            is_coding=True,
            agent_step_count=40,
            agent_pressure=0.90,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert "tier-cap-preserved(environment-or-routine:low-risk-step)" in decision.reasoning
    assert "tier-cap-softened(" not in decision.reasoning


def test_high_pressure_routine_cap_can_soften_when_current_signals_still_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.72)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = api.route(
        "Continue after reading the relevant source file.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>source code excerpt</output>"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="normal",
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="environment-or-routine",
            is_agentic=True,
            is_coding=True,
            agent_step_count=30,
            agent_pressure=0.70,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert "tier-cap-softened(" in decision.reasoning
    assert "tier-cap=MEDIUM" not in decision.reasoning
