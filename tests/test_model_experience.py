"""Tests for adaptive model selection memory."""

from __future__ import annotations

from uncommon_route import (
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
from uncommon_route.model_map import infer_capabilities


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
    assert snapshot.reward_mean > 0.7
    assert snapshot.samples == 4


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

    assert decision.model == "beta/model"
    assert decision.candidate_scores[0].model == "beta/model"
    assert decision.candidate_scores[0].exploration_bonus > decision.candidate_scores[1].exploration_bonus


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

    decision = route("hello", model_experience=store)

    assert decision.tier.value == "SIMPLE"
    assert decision.model == "google/gemini-2.5-flash-lite"
    assert decision.candidate_scores[0].model == "google/gemini-2.5-flash-lite"
    assert any(score.model == "moonshot/kimi-k2.5" for score in decision.candidate_scores)


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
