from __future__ import annotations

import time

import pytest

from uncommon_route.benchmark import (
    BenchmarkCache,
    BenchmarkProvider,
    LocalFileProvider,
    ModelBenchmarkEntry,
    QualityEstimate,
)
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


def test_quality_prior_exact_match_keeps_full_confidence() -> None:
    cache = BenchmarkCache()
    now = time.time()
    cache._sources = {
        "pinchbench": {
            "anthropic/claude-opus-4.6": ModelBenchmarkEntry(
                overall=0.822,
                raw={"runs": 40},
                fetched_at=now,
            ),
        },
    }
    cache._source_weights = {"pinchbench": 1.0}
    cache._build_index()

    estimate = cache.get_quality_estimate("anthropic/claude-opus-4.6")

    assert estimate.score == pytest.approx(0.822)
    assert estimate.raw_score == pytest.approx(0.822)
    assert estimate.source == "exact:pinchbench"
    assert estimate.matched_model == "anthropic/claude-opus-4.6"
    assert estimate.sample_count == 40
    assert estimate.confidence == pytest.approx(1.0)


def test_quality_prior_family_match_is_shrunk_and_labeled() -> None:
    cache = BenchmarkCache()
    now = time.time()
    cache._sources = {
        "pinchbench": {
            "minimax/minimax-m2.1": ModelBenchmarkEntry(
                overall=0.816,
                raw={"runs": 45},
                fetched_at=now,
            ),
        },
    }
    cache._source_weights = {"pinchbench": 1.0}
    cache._build_index()

    estimate = cache.get_quality_estimate("minimax/minimax-m2.7")

    assert estimate.source == "family:pinchbench"
    assert estimate.match_type == "family"
    assert estimate.matched_model == "minimax/minimax-m2.1"
    assert estimate.raw_score == pytest.approx(0.816)
    assert estimate.score == pytest.approx(0.7528)
    assert estimate.score < estimate.raw_score
    assert estimate.sample_count == 45
    assert estimate.confidence == pytest.approx(0.8)


def test_benchmark_cache_refreshes_stale_sources_without_static_priors() -> None:
    class DummyProvider(BenchmarkProvider):
        source_name = "dummy"

        @property
        def refresh_interval_s(self) -> float:
            return 10.0

        def __init__(self) -> None:
            self.calls = 0

        async def fetch(self) -> dict[str, ModelBenchmarkEntry]:
            self.calls += 1
            return {
                "provider/model-a": ModelBenchmarkEntry(
                    overall=0.90,
                    raw={"runs": 20},
                    fetched_at=time.time(),
                )
            }

    provider = DummyProvider()
    cache = BenchmarkCache(_providers=[provider], _source_weights={"dummy": 1.0})
    cache._sources = {
        "dummy": {
            "provider/model-a": ModelBenchmarkEntry(
                overall=0.20,
                raw={"runs": 20},
                fetched_at=0.0,
            )
        }
    }
    cache._build_index()

    assert cache.needs_refresh(now=provider.refresh_interval_s + 1.0)
    assert cache.refresh_if_stale(background=False)
    assert provider.calls == 1
    assert cache.get_quality("provider/model-a") == pytest.approx(0.90)


def test_missing_local_benchmark_file_does_not_force_refresh_loop(tmp_path) -> None:
    cache = BenchmarkCache(
        _providers=[LocalFileProvider(tmp_path / "missing-benchmark-quality.json")]
    )
    cache._sources = {}

    assert not cache.needs_refresh()


def test_candidate_scores_include_quality_prior_evidence(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_quality_estimates(self, models):
            return {
                model: QualityEstimate(
                    score=0.7528,
                    raw_score=0.816,
                    source="family:pinchbench",
                    matched_model="minimax/minimax-m2.1",
                    match_type="family",
                    sample_count=45,
                    confidence=0.8,
                )
                for model in models
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.40,
        mode=RoutingMode.AUTO,
        confidence=0.80,
        reasoning_text="quality-prior-evidence",
        available_models=list(pricing),
        estimated_input_tokens=2_000,
        max_output_tokens=1_000,
        prompt="Summarize a short file.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(step_risk="low"),
        bandit_config=BanditConfig(enabled=False),
    )

    score = next(item for item in decision.candidate_scores if item.model == "minimax/minimax-m2.7")
    assert score.editorial == pytest.approx(0.7528)
    assert score.quality_prior_raw == pytest.approx(0.816)
    assert score.quality_prior_source == "family:pinchbench"
    assert score.quality_prior_match_type == "family"
    assert score.quality_prior_matched_model == "minimax/minimax-m2.1"
    assert score.quality_prior_samples == 45
    assert score.quality_prior_confidence == pytest.approx(0.8)


def test_bandit_sampling_uses_quality_prior_evidence_strength(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark
    import uncommon_route.router.selector as selector

    class DummyBenchmarkCache:
        def get_all_quality_estimates(self, models):
            return {
                model: QualityEstimate(
                    score=0.75,
                    raw_score=0.75,
                    source="exact:pinchbench",
                    matched_model=model,
                    match_type="exact",
                    sample_count=49,
                    confidence=1.0,
                )
                for model in models
            }

    concentrations: list[float] = []

    def fake_sample(alpha: float, beta: float) -> float:
        concentrations.append(alpha + beta)
        return alpha / (alpha + beta)

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    monkeypatch.setattr(selector._rng, "betavariate", fake_sample)

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.90,
        reasoning_text="quality-prior-evidence-strength",
        available_models=list(pricing),
        estimated_input_tokens=4_000,
        max_output_tokens=1_000,
        prompt="Solve a hard coding task.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        bandit_config=BanditConfig(
            enabled=True,
            prior_n=5.0,
            enabled_tiers=(Tier.COMPLEX,),
        ),
    )

    assert concentrations
    assert min(concentrations) >= 35.0


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


def test_non_reasoning_model_name_is_not_reasoning_premium() -> None:
    caps = ModelCapabilities(tool_calling=True, reasoning=True)

    assert model_served_quality(
        "x-ai/grok-4-1-fast-non-reasoning",
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


def test_quality_guards_raise_explicit_complex_reasoning_floor_to_premium() -> None:
    caps = {
        "x-ai/grok-4-1-fast-non-reasoning": ModelCapabilities(tool_calling=True),
        "zai-org/glm-4.7": ModelCapabilities(tool_calling=True),
        "moonshotai/kimi-k2-thinking": ModelCapabilities(tool_calling=True, reasoning=True),
    }

    guard = apply_quality_guards(
        list(caps),
        mode=RoutingMode.AUTO,
        tier=Tier.COMPLEX,
        lane=CapabilityLane.REASONING,
        capabilities=caps,
    )

    assert guard.floor is ServedQuality.PREMIUM
    assert guard.allowed_models == ["moonshotai/kimi-k2-thinking"]
    assert "lane-floor=premium" in guard.notes


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


def test_scoring_target_does_not_use_pressure_alone_for_complex_rescue() -> None:
    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
        complexity=0.90,
        confidence=0.70,
        step_risk="normal",
        agent_pressure=0.70,
    ) is ServedQuality.BALANCED

    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
        complexity=0.90,
        confidence=0.70,
        step_risk="high",
        agent_pressure=0.60,
    ) is ServedQuality.PREMIUM


def test_scoring_target_uses_premium_for_initial_complex_planning() -> None:
    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
        complexity=0.90,
        confidence=0.35,
        step_risk="normal",
        is_agentic=True,
        is_coding=True,
        has_tool_results=False,
        session_present=False,
        agent_step_count=0,
        agent_pressure=0.0,
    ) is ServedQuality.PREMIUM

    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.COMPLEX,
        ServedQuality.PREMIUM,
        ServedQuality.BALANCED,
        complexity=0.90,
        confidence=0.35,
        step_risk="normal",
        is_agentic=True,
        is_coding=True,
        has_tool_results=False,
        session_present=False,
        agent_step_count=3,
        agent_pressure=0.0,
    ) is ServedQuality.BALANCED


def test_scoring_target_uses_economy_for_low_risk_auto_medium() -> None:
    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.MEDIUM,
        ServedQuality.BALANCED,
        ServedQuality.ECONOMY,
        step_risk="low",
    ) is ServedQuality.ECONOMY
    assert scoring_served_quality_target(
        RoutingMode.AUTO,
        Tier.SIMPLE,
        ServedQuality.ECONOMY,
        ServedQuality.BALANCED,
        step_risk="high",
    ) is ServedQuality.BALANCED


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


def test_quality_guards_keep_auto_high_risk_complex_as_scored_floor_pool() -> None:
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
        step_risk="high",
    )

    assert guard.target is ServedQuality.PREMIUM
    assert guard.floor is ServedQuality.BALANCED
    assert set(guard.allowed_models) == {
        "minimax/minimax-m2.7",
        "anthropic/claude-opus-4-7",
    }
    assert "served-quality-target-preferred=premium(1/3)" in guard.notes
    assert "served-quality>=floor(2/3)" in guard.notes


def test_quality_guards_keep_economy_available_for_current_low_risk_step() -> None:
    caps = _caps()
    guard = apply_quality_guards(
        [
            "deepseek/deepseek-v3.2",
            "google/gemini-3-flash-preview",
            "minimax/minimax-m2.7",
            "anthropic/claude-opus-4-7",
        ],
        mode=RoutingMode.AUTO,
        tier=Tier.COMPLEX,
        lane=CapabilityLane.GENERAL,
        capabilities=caps,
        step_risk="low",
        agent_pressure=0.90,
        is_agentic=True,
        has_tool_results=True,
    )

    assert guard.target is ServedQuality.PREMIUM
    assert guard.floor is ServedQuality.ECONOMY
    assert set(guard.allowed_models) == {
        "deepseek/deepseek-v3.2",
        "google/gemini-3-flash-preview",
        "minimax/minimax-m2.7",
        "anthropic/claude-opus-4-7",
    }
    assert "agent-pressure-floor=balanced" not in guard.notes
    assert "served-quality>=floor(4/4)" in guard.notes


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


def test_auto_simple_low_risk_prefers_adequate_economy_model(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.10,
        mode=RoutingMode.AUTO,
        confidence=0.80,
        reasoning_text="test-simple-low-risk-economy-fit",
        available_models=list(pricing),
        estimated_input_tokens=3_500,
        max_output_tokens=1_000,
        prompt="List the next file to inspect.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(step_risk="low"),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "deepseek/deepseek-v3.2"
    assert "economy-fit=cost-aware" in decision.reasoning
    assert "served-quality-fit-weight=0.22" in decision.reasoning


def test_auto_low_risk_medium_can_select_adequate_economy_model(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.40,
        mode=RoutingMode.AUTO,
        confidence=0.82,
        reasoning_text="test-medium-low-risk-economy-fit",
        available_models=list(pricing),
        estimated_input_tokens=3_500,
        max_output_tokens=1_000,
        prompt="The previous command succeeded with a short file list; choose the next lookup.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(step_risk="low"),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "deepseek/deepseek-v3.2"
    assert "served-quality-score-target=economy" in decision.reasoning
    assert "economy-fit=cost-aware(q=0.50)" in decision.reasoning


def test_auto_low_risk_complex_step_can_use_economy_despite_high_agent_pressure(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.61,
        reasoning_text="test-complex-low-risk-current-step",
        available_models=list(pricing),
        estimated_input_tokens=12_000,
        max_output_tokens=1_000,
        prompt="The latest command succeeded with a short file listing.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(
            step_risk="low",
            is_agentic=True,
            is_coding=True,
            agent_pressure=0.90,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "deepseek/deepseek-v3.2"
    assert decision.served_quality_floor is ServedQuality.ECONOMY
    assert "agent-pressure-floor=balanced" not in decision.reasoning
    assert "served-quality-score-target=economy" in decision.reasoning


def test_auto_low_risk_standalone_complex_keeps_balanced_floor(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "minimax/minimax-m2.7": 0.749,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities() for model in pricing}

    decision = select_from_pool(
        complexity=0.68,
        mode=RoutingMode.AUTO,
        confidence=0.61,
        reasoning_text="test-standalone-complex-low-risk",
        available_models=list(pricing),
        estimated_input_tokens=2_000,
        max_output_tokens=1_000,
        prompt="Design a large collaboration system.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(step_risk="low"),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.served_quality_floor is ServedQuality.BALANCED
    assert decision.model == "minimax/minimax-m2.7"


def test_auto_high_risk_complex_low_confidence_prefers_balanced_model(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.486,
        reasoning_text="test-low-confidence-complex-failure",
        available_models=list(pricing),
        estimated_input_tokens=30_000,
        max_output_tokens=1_000,
        prompt="Traceback from a failing test after a code edit.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
            step_risk="high",
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
            is_coding=True,
        ),
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.COMPLEX,)),
    )

    assert decision.model == "minimax/minimax-m2.7"
    assert "served-quality-score-target=balanced" in decision.reasoning
    assert "served-quality>=floor(2/4)" in decision.reasoning


def test_auto_medium_blocks_premium_bandit_exploration_for_routine_steps(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark
    import uncommon_route.router.selector as selector

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    draws = iter([0.10, 0.95, 0.20])
    monkeypatch.setattr(selector._rng, "betavariate", lambda _alpha, _beta: next(draws))

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.40,
        mode=RoutingMode.AUTO,
        confidence=0.816,
        reasoning_text="test-medium-routine",
        available_models=list(pricing),
        estimated_input_tokens=3_500,
        max_output_tokens=1_000,
        prompt="Plan a straightforward package error-message fix.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(),
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.MEDIUM,)),
    )

    opus = next(score for score in decision.candidate_scores if score.model == "anthropic/claude-opus-4.6")
    assert decision.model == "minimax/minimax-m2.7"
    assert opus.predicted_quality == pytest.approx(0.832)
    assert "routine-exploration=base-prior" in decision.reasoning
    assert "premium-exploration=base-prior" not in decision.reasoning
    fallback_models = [item.model for item in decision.fallback_chain]
    assert fallback_models[0] == "minimax/minimax-m2.7"
    assert fallback_models.index("anthropic/claude-opus-4.6") > fallback_models.index("deepseek/deepseek-v3.2")


def test_auto_medium_allows_premium_exploration_for_high_risk_steps(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark
    import uncommon_route.router.selector as selector

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    draws = iter([0.99, 0.10, 0.10, 0.10])
    monkeypatch.setattr(selector._rng, "betavariate", lambda _alpha, _beta: next(draws))

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.40,
        mode=RoutingMode.AUTO,
        confidence=0.816,
        reasoning_text="test-medium-high-risk",
        available_models=list(pricing),
        estimated_input_tokens=3_500,
        max_output_tokens=1_000,
        prompt="A failing regression test produced a domain traceback.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(),
        routing_features=RoutingFeatures(step_risk="high"),
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.MEDIUM,)),
    )

    assert decision.model == "anthropic/claude-opus-4.6"
    assert "premium-exploration=base-prior" not in decision.reasoning


def test_auto_high_confidence_hard_complex_can_select_opus(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.832,
                "google/gemini-3-flash-preview": 0.683,
                "minimax/minimax-m2.7": 0.849,
                "deepseek/deepseek-v3.2": 0.743,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "google/gemini-3-flash-preview": ModelPricing(0.5, 3.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
        "deepseek/deepseek-v3.2": ModelPricing(0.252, 0.378),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.90,
        reasoning_text="test-confident-hard-complex",
        available_models=list(pricing),
        estimated_input_tokens=30_000,
        max_output_tokens=1_000,
        prompt="Prove and repair a deep algorithmic correctness bug.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
            step_risk="high",
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
            is_coding=True,
        ),
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.COMPLEX,)),
    )

    assert decision.model == "anthropic/claude-opus-4.6"
    assert "served-quality-fit-weight=" in decision.reasoning


def test_auto_complex_uses_balanced_near_peer_when_premium_margin_is_too_expensive(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.801,
                "minimax/minimax-m2.7": 0.780,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.62,
        reasoning_text="test-premium-cost-benefit",
        available_models=list(pricing),
        estimated_input_tokens=30_000,
        max_output_tokens=1_000,
        prompt="Continue after a failing test in a long coding session.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            step_risk="high",
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
            is_coding=True,
            session_present=True,
            agent_step_count=30,
            agent_pressure=0.60,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "minimax/minimax-m2.7"
    assert "premium-cost-benefit=anthropic/claude-opus-4.6->minimax/minimax-m2.7" in decision.reasoning


def test_auto_complex_keeps_premium_when_scoring_advantage_is_material(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "anthropic/claude-opus-4.6": 0.801,
                "minimax/minimax-m2.7": 0.600,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())

    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "minimax/minimax-m2.7": ModelPricing(0.3, 1.2),
    }
    caps = {
        model: ModelCapabilities(tool_calling=True, reasoning=model.startswith("anthropic/"))
        for model in pricing
    }

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.62,
        reasoning_text="test-premium-material-advantage",
        available_models=list(pricing),
        estimated_input_tokens=30_000,
        max_output_tokens=1_000,
        prompt="Continue after a failing test in a long coding session.",
        pricing=pricing,
        capabilities=caps,
        requirements=RequestRequirements(needs_tool_calling=True),
        routing_features=RoutingFeatures(
            step_risk="high",
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            is_agentic=True,
            is_coding=True,
            session_present=True,
            agent_step_count=30,
            agent_pressure=0.80,
        ),
        bandit_config=BanditConfig(enabled=False),
    )

    assert decision.model == "anthropic/claude-opus-4.6"
    assert "premium-cost-benefit=" not in decision.reasoning


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


def test_normal_tool_selection_does_not_force_medium_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "List files and show current git status before deciding next command."},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="List files and show current git status before deciding next command.",
        session_id="session-1",
    )

    assert features.step_type == "tool-selection"
    assert features.step_risk == "normal"
    assert features.tier_floor is None
    assert features.tier_cap is None


def test_system_json_directive_sets_structured_output_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "system", "content": "Respond in JSON format."},
            {"role": "user", "content": "list 3 colors"},
        ],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="list 3 colors",
    )

    assert features.needs_structured_output is True
    assert features.tier_floor is Tier.MEDIUM


def test_contextual_followup_can_floor_operational_risk_question_to_medium() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Design a real-time collaboration backend with CRDTs and websocket fanout."},
            {"role": "assistant", "content": "Use CRDT documents and websocket sessions."},
            {"role": "user", "content": "What are the top three operational risks?"},
        ],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="What are the top three operational risks?",
    )

    assert features.step_risk == "normal"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is None


def test_vision_chart_analysis_sets_medium_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this chart and extract the trend."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            }
        ],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Describe this chart and extract the trend.",
    )

    assert features.needs_vision is True
    assert features.tier_floor is Tier.MEDIUM


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


def test_prior_anthropic_thinking_blocks_do_not_force_medium_floor() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "This was prior hidden reasoning."},
                    {"type": "text", "text": "Hello."},
                ],
            },
            {"role": "user", "content": "hello"},
        ],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="hello",
    )

    assert features.prefers_reasoning is False
    assert features.tier_floor is None
    assert features.request_requirements().prefers_reasoning is False


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
    assert features.tier_cap_reason == "routine-success"


def test_short_probe_output_is_medium_capped_but_not_low_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Fix the response bytes behavior."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-probe",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "python -c \\"print(False)\\""}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-probe",
                "content": "<returncode>0</returncode>\n<output>False\n</output>",
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Fix the response bytes behavior.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "normal"
    assert features.tier_floor is None
    assert features.tier_cap is Tier.MEDIUM
    assert features.tier_cap_reason == "short-observation"


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


def test_long_successful_source_output_is_not_capped_as_environment_recovery() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    source_output = "\n".join(
        f"{idx}: def method_{idx}(self): return self.value_{idx}  # AttributeError handled nearby"
        for idx in range(120)
    )
    body = {
        "messages": [
            {"role": "user", "content": "Fix the object behavior regression."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-source",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "cd /testbed && sed -n \\"1,220p\\" package/module.py"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-source",
                "content": f"<returncode>0</returncode>\n<output>{source_output}</output>",
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Fix the object behavior regression.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "normal"
    assert features.tier_floor is None
    assert features.tier_cap is None


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


def test_long_tool_trajectory_records_agent_pressure() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    messages = [{"role": "user", "content": "Fix the bug."}]
    for idx in range(8):
        messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
            ],
        })
        messages.append({"role": "tool", "content": f"<returncode>0</returncode>\n<output>Done {idx}</output>"})

    body = {
        "messages": messages,
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Fix the bug.",
        session_id="session-1",
    )

    assert features.agent_step_count >= 16
    assert features.agent_pressure >= 0.35
    assert any(tag.startswith("agent-pressure:") for tag in features.tags())


@pytest.mark.parametrize("tool_output", [
    "FAILED tests/test_router.py::test_route - AssertionError",
    "Final verification:\n  n=66: FAIL\n  n=67: OK",
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


def test_plain_fail_status_without_validation_context_is_not_high_risk() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Inspect service status."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "content": "<returncode>0</returncode>\n<output>Status: FAIL\nmanual flag only</output>"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Inspect service status.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "normal"
    assert features.tier_floor is None


def test_empty_nonzero_pip_install_result_is_high_risk_but_medium_capped() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run the repro."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-install",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "cd /testbed && pip install -e . --quiet"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-install",
                "content": "<returncode>1</returncode>\n<output>\n</output>",
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run the repro.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is Tier.MEDIUM


def test_dependency_import_error_is_high_risk_but_medium_capped() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run the repro."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-repro",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "cd /testbed && python -c \\"import astropy\\""}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-repro",
                "content": (
                    "<returncode>1</returncode>\n<output>Traceback (most recent call last):\n"
                    "  File \"/opt/miniconda3/lib/python3.11/site-packages/numpy/__init__.py\", line 792\n"
                    "AttributeError: module 'numpy' has no attribute 'product'\n</output>"
                ),
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run the repro.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is Tier.MEDIUM


def test_missing_dependency_from_test_runner_is_medium_capped() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run the framework tests."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-test",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": (
                                '{"command": "cd /testbed && python -m django test '
                                'httpwrappers --settings=tests.test_sqlite"}'
                            ),
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-test",
                "content": (
                    "<returncode>0</returncode>\n<output>Traceback (most recent call last):\n"
                    "  File \"/testbed/django/db/backends/sqlite3/introspection.py\", line 4, in <module>\n"
                    "    import sqlparse\n"
                    "ModuleNotFoundError: No module named 'sqlparse'\n</output>"
                ),
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run the framework tests.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is Tier.MEDIUM
    assert features.tier_cap_reason == "environment-recovery"
    assert features.verification_failed is False
    assert features.failure_kind == "environment"


def test_wrong_test_label_is_high_risk_but_invocation_capped() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Run the target test."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-test",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": (
                                '{"command": "cd /testbed && python tests/runtests.py '
                                'file_storage.tests.FileStoragePermissionsTests"}'
                            ),
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-test",
                "content": (
                    "<returncode>1</returncode>\n<output>"
                    "FileStoragePermissionsTests (unittest.loader._FailedTest.FileStoragePermissionsTests) ... ERROR\n"
                    "AttributeError: module 'file_storage.tests' has no attribute 'FileStoragePermissionsTests'\n"
                    "FAILED (errors=1)</output>"
                ),
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Run the target test.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk == "high"
    assert features.tier_floor is Tier.MEDIUM
    assert features.tier_cap is Tier.MEDIUM
    assert features.tier_cap_reason == "invocation-recovery"
    assert features.verification_failed is False
    assert features.failure_kind == "invocation"


def test_read_only_source_output_with_failure_words_is_not_tool_failure() -> None:
    from uncommon_route.proxy import _classify_step, _extract_routing_features

    body = {
        "messages": [
            {"role": "user", "content": "Inspect this source file."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-sed",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "sed -n \\"1,80p\\" /testbed/tests/test_utils/tests.py"}',
                        },
                    },
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-sed",
                "content": (
                    "<returncode>0</returncode>\n<output>"
                    "with self.assertRaisesMessage(AssertionError, msg):\n"
                    "    self.assertURLEqual(left, right)\n"
                    "# The test should fail if the helper regresses.\n"
                    "</output>"
                ),
            },
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    step_type, tool_names = _classify_step(body)
    features = _extract_routing_features(
        body,
        step_type=step_type,
        tool_names=tool_names,
        prompt="Inspect this source file.",
        session_id="session-1",
    )

    assert features.step_type == "tool-result-followup"
    assert features.step_risk in {"low", "normal"}
    assert features.tier_floor is None
    assert features.verification_failed is False
    assert features.failure_kind == ""


def test_environment_recovery_cap_is_not_softened_by_agent_pressure(monkeypatch) -> None:
    import uncommon_route.router.api as api
    from uncommon_route.signals.base import TierVote

    def fake_classify(*_args, **_kwargs):
        return api.V2ClassifyResult(
            complexity=0.90,
            confidence=0.80,
            tier_id=3,
            method="direct",
            signals_text=("stub-high",),
            vote_a=TierVote(3, 0.80),
            vote_b=TierVote(3, 1.00),
            vote_c=TierVote(3, 0.80),
            query_embedding=None,
        )

    monkeypatch.setattr(api, "_v2_classify", fake_classify)
    pricing = {
        "anthropic/claude-opus-test": ModelPricing(5.00, 25.00),
        "minimax/minimax-m2.7-test": ModelPricing(0.30, 1.20),
    }
    caps = {model: ModelCapabilities(tool_calling=True) for model in pricing}

    decision = api.route(
        prompt="Install the missing dependency and rerun the tests.",
        routing_mode=RoutingMode.AUTO,
        available_models=list(pricing),
        pricing=pricing,
        model_capabilities=caps,
        routing_features=RoutingFeatures(
            step_type="tool-result-followup",
            has_tool_results=True,
            needs_tool_calling=True,
            step_risk="high",
            is_agentic=True,
            is_coding=True,
            session_present=True,
            agent_step_count=40,
            agent_pressure=1.0,
            tier_floor=Tier.MEDIUM,
            tier_cap=Tier.MEDIUM,
            tier_cap_reason="environment-recovery",
        ),
        record_lifecycle=False,
    )

    assert decision.tier is Tier.MEDIUM
    assert "tier-cap-preserved(environment-recovery)" in decision.reasoning


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
