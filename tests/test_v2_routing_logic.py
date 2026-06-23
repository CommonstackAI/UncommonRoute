from __future__ import annotations

from uncommon_route.router import api, selector
from uncommon_route.router.types import (
    CandidateScore,
    CapabilityLane,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingFeatures,
    RoutingMode,
    ServedQuality,
    Tier,
)
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


def test_direct_route_infers_plain_fail_verification_as_high_risk() -> None:
    features = api._infer_routing_features_from_messages(
        [
            {"role": "user", "content": "Run the verification."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {
                "role": "tool",
                "content": (
                    "<returncode>0</returncode>\n"
                    "<output>Final verification:\n  n=66: FAIL\n  n=67: OK\n</output>"
                ),
            },
        ],
        max_output_tokens=4096,
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None
    assert features.verification_failed is True


def test_direct_route_does_not_treat_plain_fail_status_as_verification_failure() -> None:
    features = api._infer_routing_features_from_messages(
        [
            {"role": "user", "content": "Inspect the service status."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {
                "role": "tool",
                "content": "<returncode>0</returncode>\n<output>Status: FAIL\nmanual flag only</output>",
            },
        ],
        max_output_tokens=4096,
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "low"
    assert features.verification_failed is False


def test_direct_route_classifies_wrong_test_label_as_invocation_failure() -> None:
    features = api._infer_routing_features_from_messages(
        [
            {"role": "user", "content": "Run the target test."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {
                "role": "tool",
                "content": (
                    "<returncode>1</returncode>\n"
                    "<output>FileStoragePermissionsTests "
                    "(unittest.loader._FailedTest.FileStoragePermissionsTests) ... ERROR\n"
                    "AttributeError: module 'file_storage.tests' has no attribute "
                    "'FileStoragePermissionsTests'\nFAILED (errors=1)</output>"
                ),
            },
        ],
        max_output_tokens=4096,
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is Tier.MEDIUM
    assert features.tier_cap_reason == "invocation-recovery"
    assert features.verification_failed is False
    assert features.failure_kind == "invocation"


def test_late_invocation_failure_does_not_trigger_premium_verification_rescue(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.80)))
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
        "The test label failed to load; inspect the correct test name.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {
                "role": "tool",
                "content": (
                    "<returncode>1</returncode>\n"
                    "<output>SomeCase (unittest.loader._FailedTest.SomeCase) ... ERROR\n"
                    "AttributeError: module 'tests.foo' has no attribute 'SomeCase'\n"
                    "FAILED (errors=1)</output>"
                ),
            },
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="high",
            tier_floor=Tier.MEDIUM,
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="invocation-recovery",
            is_agentic=True,
            is_coding=True,
            agent_step_count=80,
            agent_pressure=0.90,
            failure_kind="invocation",
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.model == "minimax/minimax-m2.7"
    assert "tier-cap-preserved(invocation-recovery)" in decision.reasoning
    assert "pressure-rescue=verification-review" not in decision.reasoning


def test_agent_pressure_counts_tool_cycles_not_raw_tool_messages() -> None:
    messages = [{"role": "user", "content": "Fix the issue."}]
    for index in range(12):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "bash"}, "id": f"call_{index}"}],
            }
        )
        messages.append(
            {
                "role": "tool",
                "content": "<returncode>1</returncode>\n<output>AssertionError</output>",
            }
        )

    agent_step_count, agent_pressure = api._agent_state_pressure(messages, "high")

    assert agent_step_count == 12
    assert agent_pressure >= 0.70
    assert not api.pressure_rescue_active(
        agent_pressure=agent_pressure,
        agent_step_count=agent_step_count,
        has_tool_results=True,
        is_agentic=True,
        is_coding=True,
    )


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


def test_structural_high_with_high_substance_text_stays_complex(monkeypatch) -> None:
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


def test_short_high_substance_prompt_reaches_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.72)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Design a privacy-preserving analytics pipeline with consent tracking and auditability.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 2
    assert "v2:substance-structural-high-floor" in result.signals_text


def test_high_substance_prompt_with_medium_structure_reaches_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(1, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.99)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Analyze legal and operational risks in a cross-border data processing agreement.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 2
    assert "v2:substance-complexity-floor" in result.signals_text


def test_short_simple_generation_does_not_get_soft_medium_floor(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(1, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.97)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Write one concise subject line for a meeting reminder.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 0
    assert "v2:structural-medium-floor" not in result.signals_text


def test_standalone_low_risk_cap_softens_when_current_prompt_is_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.98)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "deepseek/deepseek-v3.2": ModelPricing(0.28, 0.42),
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
    }
    caps = {model: ModelCapabilities() for model in pricing}

    decision = api.route(
        "设计一个支持千万级用户的实时协同文档系统，说明冲突解决、存储、同步协议和灾备。",
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_risk="low",
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="low-risk",
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert "tier-cap-softened(current-complex-evidence)" in decision.reasoning


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


def test_routine_success_tool_result_caps_current_step_to_simple(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(0, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.57)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "deepseek/deepseek-v3.2": ModelPricing(0.28, 0.42),
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = api.route(
        "Tests passed.",
        messages=[
            {"role": "user", "content": "Run pytest."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>678 passed</output>"},
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
            tier_cap_reason="routine-success",
            is_agentic=True,
            agent_step_count=2,
            agent_pressure=0.08,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.SIMPLE
    assert "routine-success-cap=SIMPLE" in decision.reasoning
    assert "tier-cap=SIMPLE" in decision.reasoning


def test_early_semantic_failure_stays_medium_until_final_verification(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(1, 0.50)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.57)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "deepseek/deepseek-v3.2": ModelPricing(0.28, 0.42),
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = api.route(
        "Parser tests failed.",
        messages=[
            {"role": "user", "content": "Run parser tests."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>1</returncode>\n<output>AssertionError: expected 4 got 3</output>"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="high",
            tier_floor=Tier.MEDIUM,
            is_agentic=True,
            is_coding=True,
            agent_step_count=2,
            agent_pressure=0.45,
            verification_failed=True,
            failure_kind="semantic",
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert "early-semantic-failure-cap=MEDIUM" in decision.reasoning
    assert "tier-cap-preserved(early-semantic-failure)" in decision.reasoning


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
            agent_step_count=28,
            agent_pressure=0.70,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert "tier-cap-softened(" in decision.reasoning
    assert "tier-cap=MEDIUM" not in decision.reasoning


def test_high_pressure_short_observation_promotes_rescue_review_quality(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.95)))
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
        "Continue after a short observation in a long failing repair loop.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
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
            tier_cap_reason="short-observation",
            is_agentic=True,
            is_coding=True,
            agent_step_count=28,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert decision.served_quality is ServedQuality.PREMIUM
    assert "tier-cap-softened(agent-pressure=0.80)" in decision.reasoning
    assert "served-quality=tier(premium" in decision.reasoning
    assert "pressure-rescue=premium-window" in decision.reasoning
    assert "premium-cost-benefit=" not in decision.reasoning


def test_high_pressure_late_agent_loop_rebids_premium_against_balanced(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.95)))
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
        "Continue after many observations in a long failing repair loop.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
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
            tier_cap_reason="short-observation",
            is_agentic=True,
            is_coding=True,
            agent_step_count=80,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.model == "minimax/minimax-m2.7"
    assert "agent-pressure-floor=" not in decision.reasoning
    assert "tier-cap-preserved(short-observation)" in decision.reasoning
    assert "pressure-rescue=rolling-rebid" in decision.reasoning


def test_late_premium_cost_guard_accepts_quality_near_peer_even_with_score_gap() -> None:
    ranked = [
        CandidateScore(
            model="anthropic/claude-opus-4.6",
            total=1.22,
            predicted_cost=0.052,
            predicted_quality=0.801,
            editorial=0.90,
            quality_prior_confidence=0.80,
            served_quality=ServedQuality.PREMIUM.value,
        ),
        CandidateScore(
            model="minimax/minimax-m2.7",
            total=0.877,
            predicted_cost=0.0025,
            predicted_quality=0.676,
            editorial=0.65,
            quality_prior_confidence=0.35,
            served_quality=ServedQuality.BALANCED.value,
        ),
    ]

    reranked, note = selector._apply_premium_cost_benefit_guard(
        ranked,
        mode=RoutingMode.AUTO,
        tier=Tier.COMPLEX,
        complexity=0.90,
        confidence=0.62,
        features=RoutingFeatures(
            step_risk="normal",
            has_tool_results=True,
            is_agentic=True,
            is_coding=True,
            agent_step_count=80,
            agent_pressure=0.90,
        ),
    )

    assert reranked[0].model == "minimax/minimax-m2.7"
    assert note.startswith("premium-cost-benefit=")


def test_auto_medium_routine_premium_guard_prefers_balanced_near_peer() -> None:
    ranked = [
        CandidateScore(
            model="google/gemini-2.5-pro",
            total=1.12,
            predicted_cost=0.012,
            predicted_quality=0.801,
            editorial=0.85,
            served_quality=ServedQuality.PREMIUM.value,
        ),
        CandidateScore(
            model="moonshot/kimi-k2.5",
            total=0.98,
            predicted_cost=0.002,
            predicted_quality=0.681,
            editorial=0.70,
            served_quality=ServedQuality.BALANCED.value,
        ),
    ]

    reranked, note = selector._apply_routine_premium_guard(
        ranked,
        mode=RoutingMode.AUTO,
        tier=Tier.MEDIUM,
        lane=CapabilityLane.GENERAL,
        target=ServedQuality.BALANCED,
        requirements=RequestRequirements(),
        features=RoutingFeatures(),
    )

    assert reranked[0].model == "moonshot/kimi-k2.5"
    assert note.startswith("routine-premium-guard=")


def test_low_pressure_short_observation_still_preserves_medium_cap(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(3, 0.95)))
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
        "Continue after a short observation.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
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
            tier_cap_reason="short-observation",
            is_agentic=True,
            is_coding=True,
            agent_step_count=4,
            agent_pressure=0.20,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.model == "minimax/minimax-m2.7"
    assert "tier-cap-preserved(short-observation)" in decision.reasoning
    assert "tier-cap=MEDIUM" in decision.reasoning


def test_high_pressure_simple_step_only_promotes_one_public_tier(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.80)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.95)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "deepseek/deepseek-v3.2": ModelPricing(0.28, 0.42),
        "minimax/minimax-m2.7": ModelPricing(0.30, 1.20),
        "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = api.route(
        "Continue after a routine tool observation in a long repair loop.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
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
            agent_step_count=28,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.served_quality_target is ServedQuality.BALANCED
    assert decision.model == "minimax/minimax-m2.7"
    assert "agent-pressure-floor=MEDIUM(from=SIMPLE)" in decision.reasoning
    assert "pressure-rescue=step-up" in decision.reasoning
    assert "pressure-rescue=premium-window" not in decision.reasoning


def test_high_pressure_routine_medium_step_stays_capped_without_complex_evidence(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(1, 0.80)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(1, 0.95)))
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
        "Continue after a medium-risk observation in a long repair loop.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
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
            tier_cap_reason="short-observation",
            is_agentic=True,
            is_coding=True,
            agent_step_count=28,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.served_quality_target is ServedQuality.BALANCED
    assert decision.model == "minimax/minimax-m2.7"
    assert "agent-pressure-floor=COMPLEX(from=MEDIUM)" not in decision.reasoning
    assert "tier-cap-preserved(short-observation)" in decision.reasoning
    assert "pressure-rescue=step-up" in decision.reasoning
    assert "pressure-rescue=premium-window" not in decision.reasoning


def test_high_pressure_low_risk_late_loop_does_not_stick_to_complex(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(1, 0.95)))
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
        "Continue after an empty tool result in a long repair loop.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output></output>"},
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
            agent_step_count=120,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.model == "minimax/minimax-m2.7"
    assert "agent-pressure-floor=" not in decision.reasoning
    assert "tier-cap-preserved(low-risk)" in decision.reasoning
    assert "pressure-rescue=rolling-rebid" in decision.reasoning
    assert "tier-cap=MEDIUM" in decision.reasoning


def test_high_pressure_agent_loop_rebids_after_rescue_window(monkeypatch) -> None:
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.96)))
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
        "Continue.",
        messages=[
            {"role": "user", "content": "Fix the issue."},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>partial observation</output>"},
        ],
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-selection",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="normal",
            is_agentic=True,
            is_coding=True,
            agent_step_count=120,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert decision.model == "minimax/minimax-m2.7"
    assert "agent-pressure-floor=" not in decision.reasoning
    assert "pressure-rescue=rolling-rebid" in decision.reasoning


def test_late_explicit_verification_failure_gets_premium_correction(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.801,
                "minimax/minimax-m2.7": 0.676,
                "deepseek/deepseek-v3.2": 0.743,
                "google/gemini-3-flash-preview": 0.680,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(3, 0.30)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(None, 0.0)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.96)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }
    messages = [
        {"role": "user", "content": "Fix the issue."},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "bash"}}]},
        {
            "role": "tool",
            "content": (
                "<returncode>0</returncode>\n"
                "<output>Final verification:\n  n=66: FAIL\n  n=67: OK\n</output>"
            ),
        },
    ]

    decision = api.route(
        "Final verification:\n  n=66: FAIL",
        messages=messages,
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="low",
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="routine-success",
            is_agentic=True,
            is_coding=True,
            agent_step_count=176,
            agent_pressure=0.80,
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.COMPLEX
    assert decision.served_quality is ServedQuality.PREMIUM
    assert "step-risk=high" in decision.reasoning
    assert "pressure-rescue=verification-review" in decision.reasoning
    assert "served-quality-score-target=economy" not in decision.reasoning


def test_tool_result_fail_line_is_treated_as_error() -> None:
    text = """
    <returncode>0</returncode>
    <output>
    Final verification:
    n=66: FAIL - original: "xxxxxxxx''", parsed: "xxxxxxxx'''"
    </output>
    """

    assert api._tool_result_is_error({"role": "tool"}, text) is True
