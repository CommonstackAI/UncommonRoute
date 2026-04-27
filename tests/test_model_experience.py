"""Tests for adaptive model selection memory."""

from __future__ import annotations

from uncommon_route import (
    DEFAULT_CONFIG,
    BanditConfig,
    FeedbackCollector,
    ModelPricing,
    RequestRequirements,
    RoutingMode,
    SelectionWeights,
    Tier,
    TierConfig,
    route,
    select_model,
)
from uncommon_route.model_experience import (
    InMemoryModelExperienceStorage,
    ModelExperienceStore,
)
from uncommon_route.model_map import _parse_upstream_pricing, infer_capabilities
from uncommon_route.router.config import get_selection_weights
from uncommon_route.router.selector import select_from_pool


def test_model_experience_defaults_neutral() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())

    snapshot = store.snapshot("moonshot/kimi-k2.5", RoutingMode.AUTO, Tier.SIMPLE)

    assert snapshot.reliability == 0.5
    assert snapshot.latency == 0.5
    assert snapshot.feedback == 0.5
    assert snapshot.cache_affinity == 0.5
    assert snapshot.input_cost_multiplier == 1.0
    assert snapshot.reward_mean == 0.5
    assert snapshot.samples == 0


def test_infer_capabilities_marks_only_zero_priced_models_as_free() -> None:
    low_cost = infer_capabilities(
        "openai/gpt-oss-120b",
        ModelPricing(0.05, 0.25),
        has_explicit_pricing=True,
    )
    assert low_cost.free is False

    zero_cost = infer_capabilities(
        "local/free-model",
        ModelPricing(0.0, 0.0),
        has_explicit_pricing=True,
    )
    assert zero_cost.free is True

    unknown_cost = infer_capabilities(
        "openai/gpt-4o",
        ModelPricing(0.0, 0.0),
        has_explicit_pricing=False,
    )
    assert unknown_cost.free is False


def test_infer_capabilities_marks_gpt5_family_as_reasoning() -> None:
    capabilities = infer_capabilities(
        "openai/gpt-5.2",
        ModelPricing(1.75, 14.0),
        has_explicit_pricing=True,
    )

    assert capabilities.reasoning is True


def test_invalid_upstream_pricing_is_not_treated_as_free() -> None:
    pricing = _parse_upstream_pricing({"prompt": "not-a-number", "completion": "0"})
    capabilities = infer_capabilities(
        "provider/ambiguous-model",
        pricing,
        has_explicit_pricing=True,
    )

    assert pricing.input_price > 0
    assert pricing.output_price > 0
    assert capabilities.free is False


def test_incomplete_upstream_pricing_is_not_treated_as_free() -> None:
    pricing = _parse_upstream_pricing({"prompt": "0"})
    capabilities = infer_capabilities(
        "provider/incomplete-model",
        pricing,
        has_explicit_pricing=True,
    )

    assert pricing.input_price > 0
    assert pricing.output_price > 0
    assert capabilities.free is False


def test_model_experience_updates_from_observation_and_feedback() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())

    for _ in range(4):
        store.observe(
            "google/gemini-2.5-flash-lite",
            RoutingMode.AUTO,
            Tier.SIMPLE,
            success=True,
            ttft_ms=220,
            tps=95,
            total_input_tokens=1000,
            uncached_input_tokens=400,
            cache_read_tokens=600,
            input_cost_multiplier=0.46,
        )
    store.record_feedback("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE, "ok")

    snapshot = store.snapshot("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE)

    assert snapshot.reliability > 0.7
    assert snapshot.latency > 0.6
    assert snapshot.feedback > 0.5
    assert snapshot.cache_affinity > 0.7
    assert snapshot.input_cost_multiplier < 0.7
    assert snapshot.reward_mean > 0.5  # only feedback affects reward now, not HTTP 200
    assert snapshot.samples == 1  # only feedback counts as quality signal, not HTTP 200


def test_select_model_bandit_explores_under_sampled_candidate() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    for _ in range(8):
        store.observe("alpha/model", RoutingMode.AUTO, Tier.SIMPLE, success=True, ttft_ms=200, tps=80)

    decision = select_model(
        tier=Tier.SIMPLE,
        mode=RoutingMode.AUTO,
        confidence=0.8,
        method="pool",
        reasoning="test",
        tier_configs={
            Tier.SIMPLE: TierConfig(primary="alpha/model", fallback=["beta/model"]),
        },
        estimated_input_tokens=100,
        max_output_tokens=100,
        pricing={
            "alpha/model": ModelPricing(1.0, 1.0),
            "beta/model": ModelPricing(1.0, 1.0),
        },
        request_requirements=RequestRequirements(),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.0,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
        ),
        bandit_config=BanditConfig(
            enabled=True,
            reward_weight=0.0,
            exploration_weight=0.3,
            warmup_pulls=2,
            min_samples_for_guardrail=3,
            min_reliability=0.2,
            max_cost_ratio=5.0,
            enabled_tiers=(Tier.SIMPLE,),
        ),
        model_experience=store,
    )

    assert decision.candidate_scores[0].exploration_bonus >= decision.candidate_scores[1].exploration_bonus


def test_select_model_bandit_guardrail_blocks_unreliable_candidate() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    for _ in range(5):
        store.observe("beta/model", RoutingMode.AUTO, Tier.SIMPLE, success=False)
    store.observe("alpha/model", RoutingMode.AUTO, Tier.SIMPLE, success=True, ttft_ms=250, tps=70)

    decision = select_model(
        tier=Tier.SIMPLE,
        mode=RoutingMode.AUTO,
        confidence=0.8,
        method="pool",
        reasoning="test",
        tier_configs={
            Tier.SIMPLE: TierConfig(primary="alpha/model", fallback=["beta/model"]),
        },
        estimated_input_tokens=100,
        max_output_tokens=100,
        pricing={
            "alpha/model": ModelPricing(1.0, 1.0),
            "beta/model": ModelPricing(1.0, 1.0),
        },
        request_requirements=RequestRequirements(),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.0,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
        ),
        bandit_config=BanditConfig(
            enabled=True,
            reward_weight=0.0,
            exploration_weight=0.3,
            warmup_pulls=2,
            min_samples_for_guardrail=3,
            min_reliability=0.4,
            max_cost_ratio=5.0,
            enabled_tiers=(Tier.SIMPLE,),
        ),
        model_experience=store,
    )

    beta_score = next(score for score in decision.candidate_scores if score.model == "beta/model")
    assert beta_score.exploration_bonus == 0.0
    assert decision.model == "alpha/model"


def test_route_adapts_to_model_experience() -> None:
    import uncommon_route.router.selector as selector

    rng_state = selector._rng.getstate()
    selector._rng.seed(7)
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    for _ in range(6):
        store.observe(
            "moonshot/kimi-k2.5",
            RoutingMode.AUTO,
            Tier.SIMPLE,
            success=False,
        )
        store.record_feedback("moonshot/kimi-k2.5", RoutingMode.AUTO, Tier.SIMPLE, "weak")
        store.observe(
            "google/gemini-2.5-flash-lite",
            RoutingMode.AUTO,
            Tier.SIMPLE,
            success=True,
            ttft_ms=180,
            tps=110,
        )
        store.record_feedback("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE, "ok")

    gemini_wins = 0
    try:
        for _ in range(10):
            decision = route("hello", model_experience=store)
            gemini_score = next((s for s in decision.candidate_scores if s.model == "google/gemini-2.5-flash-lite"), None)
            kimi_score = next((s for s in decision.candidate_scores if s.model == "moonshot/kimi-k2.5"), None)
            if gemini_score and kimi_score and gemini_score.predicted_quality > kimi_score.predicted_quality:
                gemini_wins += 1
        assert gemini_wins >= 5, f"Gemini (positive experience) should beat kimi (negative) majority of the time, got {gemini_wins}/10"
    finally:
        selector._rng.setstate(rng_state)


def test_select_from_pool_uses_local_feedback_prior_strength(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "alpha/model": 0.90,
                "beta/model": 0.70,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())

    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage(), alpha=0.25)
    for _ in range(5):
        store.record_feedback("alpha/model", RoutingMode.AUTO, Tier.SIMPLE, "weak")

    common_kwargs = dict(
        complexity=0.15,
        mode=RoutingMode.AUTO,
        confidence=0.8,
        reasoning_text="test",
        available_models=["alpha/model", "beta/model"],
        estimated_input_tokens=100,
        max_output_tokens=100,
        prompt="hello",
        pricing={
            "alpha/model": ModelPricing(1.0, 1.0),
            "beta/model": ModelPricing(1.0, 1.0),
        },
        capabilities={
            "alpha/model": infer_capabilities("alpha/model", ModelPricing(1.0, 1.0), has_explicit_pricing=True),
            "beta/model": infer_capabilities("beta/model", ModelPricing(1.0, 1.0), has_explicit_pricing=True),
        },
        requirements=RequestRequirements(),
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
        model_experience=store,
    )

    high_prior = select_from_pool(
        **common_kwargs,
        bandit_config=BanditConfig(enabled=False, prior_n=20.0),
    )
    low_prior = select_from_pool(
        **common_kwargs,
        bandit_config=BanditConfig(enabled=False, prior_n=5.0),
    )

    assert high_prior.model == "alpha/model"
    assert low_prior.model == "beta/model"

    high_alpha = next(score for score in high_prior.candidate_scores if score.model == "alpha/model")
    low_alpha = next(score for score in low_prior.candidate_scores if score.model == "alpha/model")
    assert high_alpha.predicted_quality > 0.70
    assert low_alpha.predicted_quality < 0.70


def test_select_from_pool_respects_bandit_enabled_tiers(monkeypatch) -> None:
    import uncommon_route.benchmark as benchmark

    class DummyBenchmarkCache:
        def get_all_qualities(self, models):
            return {
                "alpha/model": 0.90,
                "beta/model": 0.80,
            }

    monkeypatch.setattr(benchmark, "get_benchmark_cache", lambda: DummyBenchmarkCache())
    pricing = {
        "alpha/model": ModelPricing(1.0, 1.0),
        "beta/model": ModelPricing(1.0, 1.0),
    }
    capabilities = {
        model: infer_capabilities(model, model_pricing, has_explicit_pricing=True)
        for model, model_pricing in pricing.items()
    }

    decision = select_from_pool(
        complexity=0.90,
        mode=RoutingMode.AUTO,
        confidence=0.9,
        reasoning_text="test",
        available_models=list(pricing),
        estimated_input_tokens=1_000,
        max_output_tokens=100,
        prompt="Design a distributed system.",
        pricing=pricing,
        capabilities=capabilities,
        requirements=RequestRequirements(),
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
        bandit_config=BanditConfig(enabled=True, enabled_tiers=(Tier.SIMPLE,)),
    )

    alpha = next(score for score in decision.candidate_scores if score.model == "alpha/model")
    beta = next(score for score in decision.candidate_scores if score.model == "beta/model")
    assert decision.tier is Tier.COMPLEX
    assert alpha.predicted_quality == 0.90
    assert beta.predicted_quality == 0.80


def test_best_mode_uses_higher_quality_threshold() -> None:
    """BEST mode's higher threshold excludes lower-quality models."""
    pricing = {
        "anthropic/claude-opus-4.6": ModelPricing(5.0, 25.0),
        "xai/grok-4-1-fast-reasoning": ModelPricing(0.20, 0.50),
    }
    capabilities = {
        model: infer_capabilities(model, model_pricing, has_explicit_pricing=True)
        for model, model_pricing in pricing.items()
    }
    requirements = RequestRequirements(needs_tool_calling=True, prefers_reasoning=True)
    common = dict(
        complexity=0.67,
        confidence=0.9,
        reasoning_text="test",
        available_models=list(pricing),
        estimated_input_tokens=4_000,
        max_output_tokens=400,
        prompt="Find the function, inspect the bug, and explain the fix.",
        pricing=pricing,
        capabilities=capabilities,
        requirements=requirements,
        bandit_config=BanditConfig(enabled=False),
    )

    auto_decision = select_from_pool(
        mode=RoutingMode.AUTO,
        selection_weights=get_selection_weights(DEFAULT_CONFIG, RoutingMode.AUTO),
        **common,
    )
    best_decision = select_from_pool(
        mode=RoutingMode.BEST,
        selection_weights=get_selection_weights(DEFAULT_CONFIG, RoutingMode.BEST),
        **common,
    )

    opus_auto = next(s for s in auto_decision.candidate_scores if "opus" in s.model)
    opus_best = next(s for s in best_decision.candidate_scores if "opus" in s.model)
    assert opus_best.predicted_quality == opus_auto.predicted_quality, \
        "Same model should have same predicted quality regardless of mode"


def test_feedback_collector_updates_model_experience() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    feedback = FeedbackCollector(model_experience=store)
    feedback.capture(
        "req-1",
        {"s_length": 0.1},
        "SIMPLE",
        model="moonshot/kimi-k2.5",
        mode="auto",
    )

    result = feedback.submit("req-1", "weak")

    assert result.ok is True
    snapshot = store.snapshot("moonshot/kimi-k2.5", RoutingMode.AUTO, Tier.SIMPLE)
    assert snapshot.feedback < 0.5


def test_model_experience_summary_exposes_feedback_changes() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage(), now_fn=lambda: 1_000.0)
    store.observe("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE, success=True, ttft_ms=200, tps=100)
    store.record_feedback("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE, "ok")
    store.record_feedback("moonshot/kimi-k2.5", RoutingMode.AUTO, Tier.SIMPLE, "weak")

    summary = store.summary()

    assert summary["records"] == 2
    assert summary["active_buckets"] == 1
    assert summary["promoted_models"][0]["model"] == "google/gemini-2.5-flash-lite"
    assert summary["demoted_models"][0]["model"] == "moonshot/kimi-k2.5"
    assert summary["recent_feedback_changes"][0]["direction"] in {"promoted", "demoted"}
    assert "cache_hit_ratio" in summary["promoted_models"][0]


def test_select_model_prefers_cache_friendly_candidate_when_weights_allow() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    for _ in range(5):
        store.observe(
            "alpha/model",
            RoutingMode.BEST,
            Tier.MEDIUM,
            success=True,
            ttft_ms=250,
            tps=60,
            total_input_tokens=12_000,
            uncached_input_tokens=2_000,
            cache_read_tokens=10_000,
            input_cost_multiplier=0.25,
        )
        store.observe(
            "beta/model",
            RoutingMode.BEST,
            Tier.MEDIUM,
            success=True,
            ttft_ms=250,
            tps=60,
            total_input_tokens=12_000,
            uncached_input_tokens=12_000,
            cache_read_tokens=0,
            input_cost_multiplier=1.0,
        )

    decision = select_model(
        tier=Tier.MEDIUM,
        mode=RoutingMode.BEST,
        confidence=0.8,
        method="pool",
        reasoning="cache test",
        tier_configs={
            Tier.MEDIUM: TierConfig(primary="beta/model", fallback=["alpha/model"]),
        },
        estimated_input_tokens=12000,
        max_output_tokens=100,
        pricing={
            "alpha/model": ModelPricing(1.0, 1.0, cached_input_price=0.1),
            "beta/model": ModelPricing(1.0, 1.0, cached_input_price=0.1),
        },
        request_requirements=RequestRequirements(),
        selection_weights=SelectionWeights(
            editorial=0.0,
            cost=0.2,
            latency=0.0,
            reliability=0.0,
            feedback=0.0,
            cache_affinity=0.8,
            byok=0.0,
            free_bias=0.0,
            local_bias=0.0,
            reasoning_bias=0.0,
        ),
        bandit_config=BanditConfig(enabled=False),
        model_experience=store,
    )

    assert decision.model == "alpha/model"
    assert decision.candidate_scores[0].cache_affinity > decision.candidate_scores[1].cache_affinity


def test_model_experience_bucket_summary_filters_mode_and_tier() -> None:
    store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
    store.record_feedback("google/gemini-2.5-flash-lite", RoutingMode.AUTO, Tier.SIMPLE, "ok")
    store.record_feedback("anthropic/claude-haiku-4.5", RoutingMode.AUTO, Tier.MEDIUM, "ok")

    bucket = store.bucket_summary(RoutingMode.AUTO, Tier.SIMPLE)

    assert bucket["mode"] == "auto"
    assert bucket["tier"] == "SIMPLE"
    assert bucket["count"] == 1
    assert bucket["models"][0]["model"] == "google/gemini-2.5-flash-lite"
