"""Model selection with cost estimation and fallback chain.

Supports two selection modes:
  1. **Tier-based** (legacy): picks from a pre-assigned model list per tier.
  2. **Pool-based** (v2): all discovered models compete, complexity score
     adjusts cost-vs-quality weights dynamically.
"""

from __future__ import annotations
from dataclasses import replace
from uncommon_route.model_experience import CandidateExperience
from uncommon_route.router.config import BASELINE_MODEL, DEFAULT_MODEL_PRICING
from uncommon_route.router.quality import (
    apply_quality_guards,
    continuity_alignment_score,
    quality_alignment_score,
    quality_rank,
    request_capability_lane,
    scoring_served_quality_target,
)
from uncommon_route.router.structural import estimate_output_budget
from uncommon_route.router.types import (
    AnswerDepth,
    BanditConfig,
    CapabilityLane,
    CandidateScore,
    FallbackOption,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConstraints,
    RoutingDecision,
    RoutingFailureCode,
    RoutingFeatures,
    RoutingInfeasibility,
    RoutingInfeasibleError,
    RoutingMode,
    SelectionWeights,
    ServedQuality,
    Tier,
    TierConfig,
    WorkloadHints,
)

import logging
import math
import random

logger = logging.getLogger("uncommon-route")


_rng = random.Random()

_UNKNOWN_MODEL_PRICING = ModelPricing(5.0, 25.0)


def _pricing_for_model(model: str, pricing: dict[str, ModelPricing]) -> ModelPricing:
    """Use conservative pricing for unknown models instead of treating them as free."""
    return pricing.get(model) or DEFAULT_MODEL_PRICING.get(model) or _UNKNOWN_MODEL_PRICING


def _stabilize_agent_step_selection(features: RoutingFeatures) -> bool:
    """Disable stochastic exploration for the current agent/tool step.

    This is intentionally not a hard model lock and does not make routing
    session-level. It only removes Thompson-sampling randomness on steps where
    the current request carries tool/protocol state.
    """
    return bool(
        features.is_agentic
        or features.has_tool_results
        or features.needs_tool_calling
        or features.step_type in {"tool-selection", "tool-result-followup"}
    )


def _calc_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, ModelPricing],
    *,
    input_cost_multiplier: float = 1.0,
) -> float:
    mp = _pricing_for_model(model, pricing)
    effective_multiplier = max(0.1, min(2.0, input_cost_multiplier))
    return (
        (input_tokens / 1_000_000) * mp.input_price * effective_multiplier
        + (output_tokens / 1_000_000) * mp.output_price
    )


def _supports_requirements(
    model: str,
    requirements: RequestRequirements,
    capabilities: dict[str, ModelCapabilities],
) -> tuple[bool, list[str]]:
    cap = capabilities.get(model, ModelCapabilities())
    missing: list[str] = []
    if requirements.needs_tool_calling and not cap.tool_calling:
        missing.append("tool_calling")
    if requirements.needs_vision and not cap.vision:
        missing.append("vision")
    return (not missing), missing


def _filter_candidates(
    candidates: list[str],
    requirements: RequestRequirements,
    capabilities: dict[str, ModelCapabilities],
) -> tuple[list[str], dict[str, list[str]]]:
    filtered: list[str] = []
    excluded: dict[str, list[str]] = {}
    for candidate in candidates:
        ok, missing = _supports_requirements(
            candidate, requirements, capabilities)
        if ok:
            filtered.append(candidate)
        else:
            excluded[candidate] = missing
    return filtered, excluded


def _provider_name(model: str) -> str:
    return model.split("/", 1)[0].strip().lower()


def _apply_constraints(
    candidates: list[str],
    constraints: RoutingConstraints,
    capabilities: dict[str, ModelCapabilities],
) -> tuple[list[str], str | None, tuple[str, ...]]:
    allowed_models = set(constraints.allowed_models)
    allowed_providers = {provider.lower()
                         for provider in constraints.allowed_providers}
    filtered = list(candidates)
    applied: list[str] = []

    if constraints.free_only:
        filtered = [
            candidate
            for candidate in filtered
            if capabilities.get(candidate, ModelCapabilities()).free
        ]
        applied.append("free-only")
        if not filtered:
            return [], "free-only", tuple(applied)

    if constraints.local_only:
        filtered = [
            candidate
            for candidate in filtered
            if capabilities.get(candidate, ModelCapabilities()).local
        ]
        applied.append("local-only")
        if not filtered:
            return [], "local-only", tuple(applied)

    if allowed_models:
        filtered = [
            candidate for candidate in filtered if candidate in allowed_models]
        applied.append("model-subset")
        if not filtered:
            return [], "model-subset", tuple(applied)

    if allowed_providers:
        filtered = [
            candidate
            for candidate in filtered
            if _provider_name(candidate) in allowed_providers
        ]
        applied.append("provider-subset")
        if not filtered:
            return [], "provider-subset", tuple(applied)

    return filtered, None, tuple(applied)


def _raise_no_available_models() -> None:
    raise RoutingInfeasibleError(RoutingInfeasibility(
        code=RoutingFailureCode.NO_AVAILABLE_MODELS,
        message="No routed models are available in the current pool.",
    ))


def _raise_capability_infeasible(
    *,
    available_models: list[str],
    candidate_count: int,
    requirements: RequestRequirements,
    excluded: dict[str, list[str]],
    constraints: RoutingConstraints,
) -> None:
    missing = sorted({miss for values in excluded.values() for miss in values})
    if not missing:
        if requirements.needs_tool_calling:
            missing.append("tool_calling")
        if requirements.needs_vision:
            missing.append("vision")
    detail = ", ".join(missing) if missing else "requested capabilities"
    raise RoutingInfeasibleError(RoutingInfeasibility(
        code=RoutingFailureCode.CAPABILITY_REQUIREMENTS_UNMET,
        message=f"No routed model satisfied required capabilities: {detail}.",
        available_model_count=len(available_models),
        candidate_count=candidate_count,
        constraint_tags=constraints.tags(),
        missing_capabilities=tuple(missing),
    ))


def _raise_constraint_infeasible(
    *,
    available_models: list[str],
    candidate_count: int,
    constraints: RoutingConstraints,
    failed_constraint: str,
    applied_constraints: tuple[str, ...],
) -> None:
    if failed_constraint == "model-subset":
        code = RoutingFailureCode.ALLOWLIST_EXHAUSTED
        base_message = "No routed model satisfied the allowed_models constraint"
    elif failed_constraint == "provider-subset":
        code = RoutingFailureCode.ALLOWLIST_EXHAUSTED
        base_message = "No routed model satisfied the allowed_providers constraint"
    elif failed_constraint == "free-only":
        code = RoutingFailureCode.ROUTING_CONSTRAINTS_UNMET
        base_message = "No routed model satisfied the free_only constraint"
    else:
        code = RoutingFailureCode.ROUTING_CONSTRAINTS_UNMET
        base_message = "No routed model satisfied the local_only constraint"

    prior_constraints = [
        tag for tag in applied_constraints if tag != failed_constraint]
    if prior_constraints:
        message = f"{base_message} after applying {', '.join(prior_constraints)}."
    else:
        message = f"{base_message}."

    raise RoutingInfeasibleError(RoutingInfeasibility(
        code=code,
        message=message,
        available_model_count=len(available_models),
        candidate_count=candidate_count,
        constraint_tags=constraints.tags(),
        failed_constraints=applied_constraints,
    ))


def _raise_budget_infeasible(
    *,
    available_models: list[str],
    candidate_count: int,
    constraints: RoutingConstraints,
    max_cost: float,
    cheapest_cost: float | None,
) -> None:
    if cheapest_cost is None:
        message = f"No routed model satisfied the max_cost constraint (${max_cost:.6f})."
    else:
        message = (
            f"No routed model satisfied the max_cost constraint (${max_cost:.6f}); "
            f"cheapest feasible candidate costs ${cheapest_cost:.6f}."
        )
    raise RoutingInfeasibleError(RoutingInfeasibility(
        code=RoutingFailureCode.BUDGET_EXCEEDED,
        message=message,
        available_model_count=len(available_models),
        candidate_count=candidate_count,
        constraint_tags=constraints.tags(),
        max_cost=max_cost,
        cheapest_cost=cheapest_cost,
    ))


def select_model(
    tier: Tier,
    mode: RoutingMode,
    confidence: float,
    method: str,
    reasoning: str,
    tier_configs: dict[Tier, TierConfig],
    estimated_input_tokens: int,
    max_output_tokens: int,
    prompt: str = "",
    pricing: dict[str, ModelPricing] | None = None,
    model_capabilities: dict[str, ModelCapabilities] | None = None,
    request_requirements: RequestRequirements | None = None,
    constraints: RoutingConstraints | None = None,
    workload_hints: WorkloadHints | None = None,
    routing_features: RoutingFeatures | None = None,
    answer_depth: AnswerDepth = AnswerDepth.STANDARD,
    answer_depth_multiplier: float = 1.0,
    agentic_score: float = 0.0,
    user_keyed_models: set[str] | None = None,
    selection_weights: SelectionWeights | None = None,
    bandit_config: BanditConfig | None = None,
    model_experience: object | None = None,
) -> RoutingDecision:
    pricing = DEFAULT_MODEL_PRICING if pricing is None else pricing
    capabilities = {} if model_capabilities is None else model_capabilities
    requirements = request_requirements or RequestRequirements()
    hard_constraints = constraints or RoutingConstraints()
    hints = workload_hints or WorkloadHints()
    weights = selection_weights or SelectionWeights()
    effective_features = routing_features or RoutingFeatures()
    effective_bandit = bandit_config or BanditConfig()
    step_stable = _stabilize_agent_step_selection(effective_features)
    if step_stable and effective_bandit.enabled:
        effective_bandit = replace(effective_bandit, enabled=False)
    lane = request_capability_lane(effective_features)
    tc = tier_configs.get(tier, TierConfig())
    configured_candidates = [candidate for candidate in [
        tc.primary, *tc.fallback] if candidate]
    if not configured_candidates:
        configured_candidates = list(pricing.keys())
    if not configured_candidates:
        _raise_no_available_models()

    filtered_candidates, excluded = _filter_candidates(
        configured_candidates, requirements, capabilities)
    if not filtered_candidates:
        _raise_capability_infeasible(
            available_models=configured_candidates,
            candidate_count=len(configured_candidates),
            requirements=requirements,
            excluded=excluded,
            constraints=hard_constraints,
        )

    candidates, failed_constraint, applied_constraints = _apply_constraints(
        filtered_candidates,
        hard_constraints,
        capabilities,
    )
    if failed_constraint is not None:
        _raise_constraint_infeasible(
            available_models=configured_candidates,
            candidate_count=len(filtered_candidates),
            constraints=hard_constraints,
            failed_constraint=failed_constraint,
            applied_constraints=applied_constraints,
        )

    capability_notes: list[str] = []
    if excluded:
        capability_notes.extend(
            sorted({miss for missing in excluded.values()
                   for miss in missing}),
        )
    if capability_notes:
        reasoning = (
            f"{reasoning} | caps={','.join(capability_notes)} "
            f"({len(filtered_candidates)}/{len(configured_candidates)})"
        )

    if tc.hard_pin and tc.primary in candidates:
        scoring_candidates = [tc.primary]
        reasoning = f"{reasoning} | chooser=hard-pin"
    elif tc.hard_pin:
        scoring_candidates = candidates
        reasoning = f"{reasoning} | chooser=hard-pin-relaxed"
    else:
        scoring_candidates = candidates

    quality_guard = apply_quality_guards(
        scoring_candidates,
        mode=mode,
        tier=tier,
        lane=lane,
        capabilities=capabilities,
        continuity_floor=effective_features.continuity_quality_floor,
        step_risk=effective_features.step_risk,
    )
    scoring_candidates = quality_guard.allowed_models
    reasoning = f"{reasoning} | {' | '.join(quality_guard.notes)}"
    if step_stable:
        reasoning = f"{reasoning} | step-stable=no-bandit"
    alignment_target = scoring_served_quality_target(
        mode,
        tier,
        quality_guard.target,
        quality_guard.floor,
    )
    if alignment_target is not quality_guard.target:
        reasoning = f"{reasoning} | served-quality-score-target={alignment_target.value}"

    # R2-Router: estimate optimal output budget from prompt + tier
    budget = estimate_output_budget(prompt, tier.value)
    effective_output = min(max_output_tokens, max(
        1, int(budget * max(0.1, answer_depth_multiplier))))

    candidate_scores = _score_candidates(
        scoring_candidates,
        mode=mode,
        tier=tier,
        lane=lane,
        effective_output=effective_output,
        estimated_input_tokens=estimated_input_tokens,
        pricing=pricing,
        capabilities=capabilities,
        requirements=requirements,
        weights=weights,
        target_quality=alignment_target,
        previous_served_quality=effective_features.previous_served_quality,
        quality_by_model=quality_guard.quality_by_model,
        user_keyed_models=user_keyed_models,
        bandit_config=effective_bandit,
        model_experience=model_experience,
    )
    candidate_scores.sort(key=lambda item: item.total, reverse=True)
    if hard_constraints.max_cost is not None:
        affordable_scores = [
            item for item in candidate_scores
            if item.predicted_cost <= hard_constraints.max_cost
        ]
        if not affordable_scores:
            cheapest_cost = (
                min(item.predicted_cost for item in candidate_scores)
                if candidate_scores else None
            )
            _raise_budget_infeasible(
                available_models=configured_candidates,
                candidate_count=len(candidate_scores),
                constraints=hard_constraints,
                max_cost=hard_constraints.max_cost,
                cheapest_cost=cheapest_cost,
            )
        candidate_scores = affordable_scores
    if user_keyed_models:
        keyed_scores = [
            item for item in candidate_scores if item.model in user_keyed_models]
        if keyed_scores:
            unkeyed_scores = [
                item for item in candidate_scores if item.model not in user_keyed_models]
            candidate_scores = keyed_scores + unkeyed_scores
    model = candidate_scores[0].model
    cost = candidate_scores[0].predicted_cost
    if user_keyed_models and model in user_keyed_models:
        reasoning = f"byok-preferred ({model}) | {reasoning}"
    if "chooser=hard-pin" not in reasoning:
        reasoning = f"{reasoning} | chooser=adaptive"

    bp = pricing.get(BASELINE_MODEL, ModelPricing(5.0, 25.0))
    baseline_cost = (
        (estimated_input_tokens / 1_000_000) * bp.input_price
        + (effective_output / 1_000_000) * bp.output_price
    )

    savings = max(0.0, (baseline_cost - cost) /
                  baseline_cost) if baseline_cost > 0 else 0.0

    # Build fallback chain in configured mode order. Costs are attached for visibility.
    chain: list[FallbackOption] = []
    if tc.hard_pin:
        fallback_models = candidates
    else:
        fallback_models = [scored.model for scored in candidate_scores]
    for fb_model in fallback_models:
        exp = _experience_snapshot(model_experience, fb_model, mode, tier)
        fb_cost = _calc_cost(
            fb_model,
            estimated_input_tokens,
            effective_output,
            pricing,
            input_cost_multiplier=exp.input_cost_multiplier,
        )
        chain.append(FallbackOption(
            model=fb_model,
            cost_estimate=fb_cost,
            suggested_output_budget=effective_output,
        ))

    return RoutingDecision(
        model=model,
        tier=tier,
        capability_lane=lane,
        served_quality=quality_guard.quality_by_model.get(model, quality_guard.floor),
        served_quality_target=quality_guard.target,
        served_quality_floor=quality_guard.floor,
        continuity_quality_floor=quality_guard.continuity_floor,
        mode=mode,
        confidence=confidence,
        raw_confidence=confidence,
        method=method,  # type: ignore[arg-type]
        reasoning=reasoning,
        cost_estimate=cost,
        baseline_cost=baseline_cost,
        savings=savings,
        complexity=_tier_complexity_anchor(tier),
        agentic_score=agentic_score,
        constraints=hard_constraints,
        workload_hints=hints,
        routing_features=effective_features,
        answer_depth=answer_depth,
        suggested_output_budget=effective_output,
        fallback_chain=chain,
        candidate_scores=candidate_scores,
    )


def get_fallback_chain(tier: Tier, tier_configs: dict[Tier, TierConfig]) -> list[str]:
    tc = tier_configs[tier]
    return [tc.primary, *tc.fallback]


def _score_candidates(
    candidates: list[str],
    *,
    mode: RoutingMode,
    tier: Tier,
    lane: CapabilityLane,
    effective_output: int,
    estimated_input_tokens: int,
    pricing: dict[str, ModelPricing],
    capabilities: dict[str, ModelCapabilities],
    requirements: RequestRequirements,
    weights: SelectionWeights,
    target_quality: ServedQuality,
    previous_served_quality: ServedQuality | None,
    quality_by_model: dict[str, ServedQuality],
    user_keyed_models: set[str] | None,
    bandit_config: BanditConfig,
    model_experience: object | None,
) -> list[CandidateScore]:
    experience = {
        model: _experience_snapshot(model_experience, model, mode, tier)
        for model in candidates
    }
    costs = {
        model: _calc_cost(
            model,
            estimated_input_tokens,
            effective_output,
            pricing,
            input_cost_multiplier=experience[model].input_cost_multiplier,
        )
        for model in candidates
    }
    cost_scores = _normalize_inverse(costs)
    ranked: list[CandidateScore] = []
    candidate_count = len(candidates)
    cheapest_cost = min(costs.values()) if costs else 0.0
    bucket_pulls = _bucket_pulls(model_experience, mode, tier)
    bandit_active = bandit_config.enabled and tier in bandit_config.enabled_tiers

    for index, model in enumerate(candidates):
        cap = capabilities.get(model, ModelCapabilities())
        exp = experience[model]
        editorial = 1.0 / (index + 1)
        reasoning_bias = 1.0 if requirements.prefers_reasoning and cap.reasoning else 0.0
        candidate_quality = quality_by_model.get(model, ServedQuality.ECONOMY)
        quality_alignment = quality_alignment_score(candidate_quality, target_quality)
        continuity_bias = continuity_alignment_score(candidate_quality, previous_served_quality)
        byok = 1.0 if user_keyed_models and model in user_keyed_models else 0.0
        free_bias = 1.0 if cap.free else 0.0
        local_bias = 1.0 if cap.local else 0.0
        exploration_bonus = _bandit_bonus(
            enabled=bandit_active,
            bandit_config=bandit_config,
            candidate_cost=costs[model],
            cheapest_cost=cheapest_cost,
            reliability=exp.reliability,
            samples=exp.samples,
            bucket_pulls=bucket_pulls,
        )
        bandit_mean = exp.reward_mean
        total = (
            weights.editorial * editorial
            + weights.cost * cost_scores[model]
            + weights.latency * exp.latency
            + weights.reliability * exp.reliability
            + weights.feedback * exp.feedback
            + weights.cache_affinity * exp.cache_affinity
            + weights.byok * byok
            + weights.free_bias * free_bias
            + weights.local_bias * local_bias
            + weights.reasoning_bias * reasoning_bias
            + weights.quality_alignment * quality_alignment
            + weights.continuity * continuity_bias
        )
        if bandit_active:
            total += bandit_config.reward_weight * (bandit_mean - 0.5)
            total += exploration_bonus
        # Break ties slightly in favor of earlier curated candidates.
        total += 0.002 * (candidate_count - index)
        ranked.append(CandidateScore(
            model=model,
            total=total,
            predicted_cost=costs[model],
            effective_cost_multiplier=exp.input_cost_multiplier,
            editorial=editorial,
            cost=cost_scores[model],
            latency=exp.latency,
            reliability=exp.reliability,
            feedback=exp.feedback,
            cache_affinity=exp.cache_affinity,
            byok=byok,
            free_bias=free_bias,
            local_bias=local_bias,
            reasoning_bias=reasoning_bias,
            quality_alignment=quality_alignment,
            continuity_bias=continuity_bias,
            served_quality=candidate_quality.value,
            bandit_mean=bandit_mean,
            exploration_bonus=exploration_bonus,
            samples=exp.samples,
        ))
    return ranked


def _normalize_inverse(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    minimum = min(values.values())
    maximum = max(values.values())
    if maximum <= minimum:
        return {key: 0.5 for key in values}
    return {
        key: 1.0 - ((value - minimum) / (maximum - minimum))
        for key, value in values.items()
    }


def _tier_complexity_anchor(tier: Tier) -> float:
    return {
        Tier.SIMPLE: 0.15,
        Tier.MEDIUM: 0.42,
        Tier.COMPLEX: 0.86,
    }.get(tier, 0.33)


def _experience_snapshot(
    store: object | None,
    model: str,
    mode: RoutingMode,
    tier: Tier,
) -> CandidateExperience:
    if store is None or not hasattr(store, "snapshot"):
        return CandidateExperience()
    try:
        snapshot = store.snapshot(model, mode, tier)
    except TypeError:
        snapshot = store.snapshot(model, mode)
    if isinstance(snapshot, CandidateExperience):
        return snapshot
    return CandidateExperience()


def _bucket_pulls(
    store: object | None,
    mode: RoutingMode,
    tier: Tier,
) -> int:
    if store is None or not hasattr(store, "bucket_pulls"):
        return 0
    try:
        pulls = int(store.bucket_pulls(mode, tier))
    except Exception:
        try:
            pulls = int(store.bucket_pulls(mode))
        except Exception:
            return 0
    return max(0, pulls)


def _bandit_bonus(
    *,
    enabled: bool,
    bandit_config: BanditConfig,
    candidate_cost: float,
    cheapest_cost: float,
    reliability: float,
    samples: int,
    bucket_pulls: int,
) -> float:
    if not enabled:
        return 0.0
    if cheapest_cost > 0 and candidate_cost > (cheapest_cost * bandit_config.max_cost_ratio):
        return 0.0
    if samples >= bandit_config.min_samples_for_guardrail and reliability < bandit_config.min_reliability:
        return 0.0
    if samples < bandit_config.warmup_pulls:
        return bandit_config.exploration_weight
    return bandit_config.exploration_weight * math.sqrt(
        math.log(max(2, bucket_pulls + 1)) / (samples + 1),
    )


# ---------------------------------------------------------------------------
# Pool-based selection (v2) — all models compete
# ---------------------------------------------------------------------------

def _derive_tier(complexity: float) -> Tier:
    """Map continuous complexity back to the public 3-band tier model."""
    if complexity < 0.33:
        return Tier.SIMPLE
    if complexity < 0.67:
        return Tier.MEDIUM
    return Tier.COMPLEX


def _quality_prior_scores(
    models: list[str],
    benchmark_quality: dict[str, float] | None = None,
) -> dict[str, float]:
    """Quality prior from benchmark data.  Returns 0.5 (neutral) for
    unknown models — the experience system learns actual quality over time.
    """
    if benchmark_quality:
        return {m: benchmark_quality.get(m, 0.5) for m in models}
    return {m: 0.5 for m in models}


def _apply_cost_sanity_guard(
    ranked: list[CandidateScore],
    *,
    mode: RoutingMode,
) -> tuple[list[CandidateScore], str]:
    """Prevent AUTO/FAST exploration from preferring dominated expensive peers.

    Thompson sampling intentionally explores, but it should not let a same-
    quality, lower-prior, materially more expensive model beat a cheaper peer
    with comparable or stronger benchmark quality. BEST is left quality-first.
    """
    if mode is RoutingMode.BEST or len(ranked) < 2:
        return ranked, ""

    selected = ranked[0]
    selected_cost = max(0.0, selected.predicted_cost)
    if selected_cost <= 0:
        return ranked, ""

    selected_quality_rank = quality_rank(selected.served_quality)
    quality_tolerance = 0.03
    material_savings = 0.25

    alternatives = [
        score
        for score in ranked[1:]
        if quality_rank(score.served_quality) >= selected_quality_rank
        and score.editorial >= selected.editorial - quality_tolerance
        and score.predicted_cost > 0
        and score.predicted_cost <= selected_cost * (1.0 - material_savings)
    ]
    if not alternatives:
        return ranked, ""

    replacement = max(
        alternatives,
        key=lambda score: (
            quality_rank(score.served_quality),
            score.editorial,
            -score.predicted_cost,
            score.total,
        ),
    )
    if replacement.model == selected.model:
        return ranked, ""

    reordered = [replacement]
    reordered.extend(score for score in ranked if score.model != replacement.model)
    note = (
        "cost-sanity="
        f"{selected.model}->{replacement.model}"
        f"({selected_cost:.6f}->{replacement.predicted_cost:.6f})"
    )
    return reordered, note


def _normalized_costs(
    models: list[str],
    pricing: dict[str, ModelPricing],
) -> dict[str, float]:
    """Log-scale cost normalization.

    Uses log(1 + cost) to prevent free models from having an absolute
    cost advantage.  The gap between $0 and $0.30 is compressed vs linear.
    """
    raw = {}
    for m in models:
        mp = _pricing_for_model(m, pricing)
        raw[m] = math.log1p(mp.input_price + mp.output_price)
    if not raw:
        return {}
    lo = min(raw.values())
    hi = max(raw.values())
    span = hi - lo
    if span <= 0:
        return {m: 0.5 for m in models}
    return {m: (raw[m] - lo) / span for m in models}


def select_from_pool(
    complexity: float,
    mode: RoutingMode,
    confidence: float,
    reasoning_text: str,
    available_models: list[str],
    estimated_input_tokens: int,
    max_output_tokens: int,
    prompt: str,
    pricing: dict[str, ModelPricing],
    capabilities: dict[str, ModelCapabilities],
    requirements: RequestRequirements,
    constraints: RoutingConstraints | None = None,
    workload_hints: WorkloadHints | None = None,
    routing_features: RoutingFeatures | None = None,
    answer_depth: AnswerDepth = AnswerDepth.STANDARD,
    answer_depth_multiplier: float = 1.0,
    agentic_score: float = 0.0,
    user_keyed_models: set[str] | None = None,
    selection_weights: SelectionWeights | None = None,
    bandit_config: BanditConfig | None = None,
    model_experience: object | None = None,
    raw_confidence: float | None = None,
    confidence_source: str = "classifier",
    calibration_version: str = "",
    calibration_sample_count: int = 0,
    calibration_temperature: float = 1.0,
    calibration_applied_tags: tuple[str, ...] = (),
) -> RoutingDecision:
    """Select the best model from the full discovered pool.

    Unlike ``select_model`` which picks from a per-tier list, this
    evaluates ALL available models and lets ``complexity`` drive the
    cost-vs-quality trade-off via weight interpolation.
    """
    weights = selection_weights or SelectionWeights()
    bc = bandit_config or BanditConfig()
    hard_constraints = constraints or RoutingConstraints()
    hints = workload_hints or WorkloadHints()
    tier = _derive_tier(complexity)
    effective_features = routing_features or RoutingFeatures()
    step_stable = _stabilize_agent_step_selection(effective_features)
    if step_stable and bc.enabled:
        bc = replace(bc, enabled=False)
    lane = request_capability_lane(effective_features)

    if not available_models:
        _raise_no_available_models()

    filtered_candidates, excluded = _filter_candidates(
        available_models, requirements, capabilities)
    if not filtered_candidates:
        _raise_capability_infeasible(
            available_models=available_models,
            candidate_count=len(available_models),
            requirements=requirements,
            excluded=excluded,
            constraints=hard_constraints,
        )

    candidates, failed_constraint, applied_constraints = _apply_constraints(
        filtered_candidates,
        hard_constraints,
        capabilities,
    )
    if failed_constraint is not None:
        _raise_constraint_infeasible(
            available_models=available_models,
            candidate_count=len(filtered_candidates),
            constraints=hard_constraints,
            failed_constraint=failed_constraint,
            applied_constraints=applied_constraints,
        )

    difficulty_tier_label = tier.value
    budget = estimate_output_budget(prompt, difficulty_tier_label)
    effective_output = min(max_output_tokens, max(
        1, int(budget * max(0.1, answer_depth_multiplier))))

    quality_guard = apply_quality_guards(
        candidates,
        mode=mode,
        tier=tier,
        lane=lane,
        capabilities=capabilities,
        continuity_floor=effective_features.continuity_quality_floor,
        step_risk=effective_features.step_risk,
    )
    candidates = quality_guard.allowed_models
    alignment_target = scoring_served_quality_target(
        mode,
        tier,
        quality_guard.target,
        quality_guard.floor,
    )

    benchmark_quality: dict[str, float] | None = None
    try:
        from uncommon_route.benchmark import get_benchmark_cache
        benchmark_quality = get_benchmark_cache().get_all_qualities(candidates)
    except Exception as exc:
        logger.warning("Benchmark quality unavailable: %s", exc)
    quality_priors = _quality_prior_scores(
        candidates, benchmark_quality=benchmark_quality)
    experience = {}
    for m in candidates:
        exp_snapshot = _experience_snapshot(model_experience, m, mode, tier)
        if exp_snapshot.samples == 0:
            best_alt = exp_snapshot
            for alt_tier in (Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX):
                alt = _experience_snapshot(model_experience, m, mode, alt_tier)
                if alt.samples > best_alt.samples:
                    best_alt = alt
            exp_snapshot = best_alt
        experience[m] = exp_snapshot
    dollar_costs = {
        m: _calc_cost(m, estimated_input_tokens, effective_output, pricing,
                      input_cost_multiplier=experience[m].input_cost_multiplier)
        for m in candidates
    }
    cheapest_cost = min(dollar_costs.values()) if dollar_costs else 0.0
    log_dollar_costs = {m: math.log1p(c * 1000)
                        for m, c in dollar_costs.items()}
    max_log_dc = max(log_dollar_costs.values()) if log_dollar_costs else 1.0
    min_log_dc = min(log_dollar_costs.values()) if log_dollar_costs else 0.0
    span_dc = max_log_dc - min_log_dc
    actual_cost_norm = {m: (
        log_dollar_costs[m] - min_log_dc) / span_dc if span_dc > 0 else 0.5 for m in dollar_costs}
    if hard_constraints.max_cost is not None:
        affordable = [
            model for model in candidates if dollar_costs[model] <= hard_constraints.max_cost]
        if affordable:
            candidates = affordable
            quality_priors = _quality_prior_scores(
                candidates, benchmark_quality=benchmark_quality)
            experience = {m: experience[m] for m in candidates}
            dollar_costs = {m: dollar_costs[m] for m in candidates}
            cheapest_cost = min(dollar_costs.values()) if dollar_costs else 0.0
        else:
            _raise_budget_infeasible(
                available_models=available_models,
                candidate_count=len(candidates),
                constraints=hard_constraints,
                max_cost=hard_constraints.max_cost,
                cheapest_cost=cheapest_cost if dollar_costs else None,
            )

    mu = complexity
    bandit_active = bc.enabled and tier in bc.enabled_tiers
    prior_n = max(0.0, float(bc.prior_n))

    # Mode controls quality-vs-cost preference:
    #   FAST  → strongly prefer cheap (low cost_sensitivity = quality matters less)
    #   AUTO  → balanced
    #   BEST  → strongly prefer quality (high cost_sensitivity = cost matters less)
    # Quality weight: how much does quality matter vs cost.
    #   FAST: cost-dominant — pick the cheapest decent model
    #   AUTO: balanced — best quality-per-dollar
    #   BEST: quality-only — pick the highest quality, ignore cost
    mode_quality_weight = {
        RoutingMode.FAST: 0.35,
        RoutingMode.AUTO: 0.65,
        RoutingMode.BEST: 1.0,
    }
    base_q_weight = mode_quality_weight.get(mode, 0.65)
    q_weight = base_q_weight + mu * (1.0 - base_q_weight) * 0.8

    # Relative quality gate: exclude models below X% of the best available.
    mode_gate_fraction = {
        RoutingMode.FAST: 0.50,
        RoutingMode.AUTO: 0.60,
        RoutingMode.BEST: 0.85,
    }
    gate_fraction = mode_gate_fraction.get(mode, 0.60)

    ranked: list[CandidateScore] = []
    all_predicted_qualities: dict[str, float] = {}

    for model in candidates:
        cap = capabilities.get(model, ModelCapabilities())
        exp = experience[model]
        benchmark_q = quality_priors.get(model, 0.5)
        cost_norm = actual_cost_norm.get(model, 0.5)
        reasoning_bias = 1.0 if requirements.prefers_reasoning and cap.reasoning else 0.0
        candidate_quality = quality_guard.quality_by_model.get(model, quality_guard.floor)
        quality_alignment = quality_alignment_score(candidate_quality, alignment_target)
        continuity_bias = continuity_alignment_score(candidate_quality, effective_features.previous_served_quality)
        byok = 1.0 if user_keyed_models and model in user_keyed_models else 0.0
        free_bias = 1.0 if cap.free else 0.0
        local_bias = 1.0 if cap.local else 0.0

        base_quality = (
            (prior_n * benchmark_q + exp.samples * exp.reward_mean)
            / (prior_n + exp.samples)
        )

        predicted_quality = base_quality

        # Thompson Sampling: tighter exploration at higher difficulty
        # so COMPLEX tasks rely more on benchmark ranking.
        exploration_scale = max(3.0, 4.0 + mu * 6.0)
        ts_alpha = max(0.5, exploration_scale * base_quality)
        ts_beta = max(0.5, exploration_scale * (1.0 - base_quality))
        if bandit_active:
            predicted_quality = _rng.betavariate(ts_alpha, ts_beta)
        exploration_bonus = 0.0
        all_predicted_qualities[model] = predicted_quality

        auxiliary = (
            weights.latency * exp.latency
            + weights.reliability * exp.reliability
            + weights.feedback * exp.feedback
            + weights.cache_affinity * exp.cache_affinity
            + weights.byok * byok
            + weights.free_bias * free_bias
            + weights.local_bias * local_bias
            + weights.reasoning_bias * reasoning_bias
            + weights.quality_alignment * quality_alignment
            + weights.continuity * continuity_bias
        )
        total = (
            q_weight * predicted_quality
            - (1.0 - q_weight) * actual_cost_norm[model]
            + auxiliary
        )

        ranked.append(CandidateScore(
            model=model,
            total=total,
            predicted_cost=dollar_costs[model],
            predicted_quality=predicted_quality,
            effective_cost_multiplier=exp.input_cost_multiplier,
            editorial=benchmark_q,
            cost=cost_norm,
            latency=exp.latency,
            reliability=exp.reliability,
            feedback=exp.feedback,
            cache_affinity=exp.cache_affinity,
            byok=byok,
            free_bias=free_bias,
            local_bias=local_bias,
            reasoning_bias=reasoning_bias,
            quality_alignment=quality_alignment,
            continuity_bias=continuity_bias,
            served_quality=candidate_quality.value,
            bandit_mean=exp.reward_mean,
            exploration_bonus=exploration_bonus,
            samples=exp.samples,
        ))

    # Relative quality gate: exclude models below gate_fraction of best
    best_quality = max(all_predicted_qualities.values()
                       ) if all_predicted_qualities else 0.5
    quality_gate = best_quality * gate_fraction

    gated = [s for s in ranked if s.predicted_quality >= quality_gate]
    if gated:
        gated.sort(key=lambda s: s.total, reverse=True)
        below = [s for s in ranked if s.predicted_quality < quality_gate]
        below.sort(key=lambda s: s.total, reverse=True)
        ranked = gated + below
    else:
        ranked.sort(key=lambda s: s.predicted_quality, reverse=True)

    ranked, cost_guard_note = _apply_cost_sanity_guard(ranked, mode=mode)

    if user_keyed_models:
        keyed = [s for s in ranked if s.model in user_keyed_models]
        if keyed:
            unkeyed = [s for s in ranked if s.model not in user_keyed_models]
            ranked = keyed + unkeyed

    selected = ranked[0]
    model = selected.model
    cost = selected.predicted_cost

    bp = pricing.get(BASELINE_MODEL, ModelPricing(5.0, 25.0))
    baseline_cost = (
        (estimated_input_tokens / 1_000_000) * bp.input_price
        + (effective_output / 1_000_000) * bp.output_price
    )
    savings = max(0.0, (baseline_cost - cost) /
                  baseline_cost) if baseline_cost > 0 else 0.0

    chain = [
        FallbackOption(model=s.model, cost_estimate=s.predicted_cost,
                       suggested_output_budget=effective_output)
        for s in ranked
    ]

    method_note = "pool"
    if user_keyed_models and model in user_keyed_models:
        method_note = f"byok-preferred ({model}) | pool"
    constraint_tags = hard_constraints.tags()
    hint_tags = hints.tags()
    reasoning_parts = [
        reasoning_text,
        f"chooser=pool(complexity={complexity:.2f})",
        f"mode={mode.value}",
        f"depth={answer_depth.value}",
    ]
    capability_notes = sorted(
        {miss for missing in excluded.values() for miss in missing})
    if capability_notes:
        reasoning_parts.append(
            f"required_caps={','.join(capability_notes)} ({len(filtered_candidates)}/{len(available_models)})"
        )
    if constraint_tags:
        reasoning_parts.append(f"constraints={','.join(constraint_tags)}")
    if hint_tags:
        reasoning_parts.append(f"hints={','.join(hint_tags)}")
    if step_stable:
        reasoning_parts.append("step-stable=no-bandit")
    if cost_guard_note:
        reasoning_parts.append(cost_guard_note)
    reasoning_parts.extend(quality_guard.notes)
    if alignment_target is not quality_guard.target:
        reasoning_parts.append(f"served-quality-score-target={alignment_target.value}")

    return RoutingDecision(
        model=model,
        tier=tier,
        capability_lane=lane,
        served_quality=quality_guard.quality_by_model.get(model, quality_guard.floor),
        served_quality_target=quality_guard.target,
        served_quality_floor=quality_guard.floor,
        continuity_quality_floor=quality_guard.continuity_floor,
        mode=mode,
        confidence=confidence,
        raw_confidence=confidence if raw_confidence is None else raw_confidence,
        confidence_source=confidence_source,
        calibration_version=calibration_version,
        calibration_sample_count=calibration_sample_count,
        calibration_temperature=calibration_temperature,
        calibration_applied_tags=calibration_applied_tags,
        method=method_note,
        reasoning=" | ".join(reasoning_parts),
        cost_estimate=cost,
        baseline_cost=baseline_cost,
        savings=savings,
        complexity=complexity,
        agentic_score=agentic_score,
        constraints=hard_constraints,
        workload_hints=hints,
        routing_features=effective_features,
        answer_depth=answer_depth,
        suggested_output_budget=effective_output,
        fallback_chain=chain,
        candidate_scores=ranked,
    )
