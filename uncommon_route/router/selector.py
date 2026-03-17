"""Model selection with cost estimation and fallback chain.

Supports two selection modes:
  1. **Tier-based** (legacy): picks from a pre-assigned model list per tier.
  2. **Pool-based** (v2): all discovered models compete, complexity score
     adjusts cost-vs-quality weights dynamically.
"""

from __future__ import annotations

import math

from uncommon_route.router.types import (
    AnswerDepth,
    BanditConfig,
    CandidateScore,
    FallbackOption,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConstraints,
    RoutingDecision,
    RoutingMode,
    SelectionWeights,
    Tier,
    TierConfig,
    WorkloadHints,
)
from uncommon_route.router.structural import estimate_output_budget
from uncommon_route.router.config import BASELINE_MODEL, DEFAULT_MODEL_PRICING
from uncommon_route.model_experience import CandidateExperience


def _calc_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, ModelPricing],
    *,
    input_cost_multiplier: float = 1.0,
) -> float:
    mp = pricing.get(model, ModelPricing(0, 0))
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
    if requirements.prefers_reasoning and not cap.reasoning:
        missing.append("reasoning")
    return (not missing), missing


def _filter_candidates(
    candidates: list[str],
    requirements: RequestRequirements,
    capabilities: dict[str, ModelCapabilities],
) -> tuple[list[str], dict[str, list[str]]]:
    filtered: list[str] = []
    excluded: dict[str, list[str]] = {}
    for candidate in candidates:
        ok, missing = _supports_requirements(candidate, requirements, capabilities)
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
) -> list[str]:
    allowed_models = set(constraints.allowed_models)
    allowed_providers = {provider.lower() for provider in constraints.allowed_providers}
    filtered: list[str] = []
    for candidate in candidates:
        cap = capabilities.get(candidate, ModelCapabilities())
        if constraints.free_only and not cap.free:
            continue
        if constraints.local_only and not cap.local:
            continue
        if allowed_models and candidate not in allowed_models:
            continue
        if allowed_providers and _provider_name(candidate) not in allowed_providers:
            continue
        filtered.append(candidate)
    return filtered


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
    answer_depth: AnswerDepth = AnswerDepth.STANDARD,
    answer_depth_multiplier: float = 1.0,
    agentic_score: float = 0.0,
    user_keyed_models: set[str] | None = None,
    selection_weights: SelectionWeights | None = None,
    bandit_config: BanditConfig | None = None,
    model_experience: object | None = None,
) -> RoutingDecision:
    pricing = pricing or DEFAULT_MODEL_PRICING
    capabilities = model_capabilities or {}
    requirements = request_requirements or RequestRequirements()
    hard_constraints = constraints or RoutingConstraints()
    hints = workload_hints or WorkloadHints()
    weights = selection_weights or SelectionWeights()
    tc = tier_configs[tier]
    configured_candidates = [candidate for candidate in [tc.primary, *tc.fallback] if candidate]
    if not configured_candidates:
        configured_candidates = list(pricing.keys())
    constrained_candidates = _apply_constraints(configured_candidates, hard_constraints, capabilities)
    constraint_relaxed = bool(hard_constraints.tags()) and not constrained_candidates
    filtered_source = constrained_candidates or configured_candidates
    filtered_candidates, excluded = _filter_candidates(filtered_source, requirements, capabilities)
    candidates = filtered_candidates or filtered_source

    capability_notes: list[str] = []
    if filtered_candidates:
        capability_notes.extend(
            sorted({miss for missing in excluded.values() for miss in missing}),
        )
    elif excluded:
        capability_notes.append("capability-relaxed")
    if capability_notes:
        kept = len(filtered_candidates) if filtered_candidates else len(configured_candidates)
        reasoning = f"{reasoning} | caps={','.join(capability_notes)} ({kept}/{len(configured_candidates)})"
    if constraint_relaxed:
        reasoning = f"{reasoning} | constraints=relaxed"

    if tc.hard_pin and tc.primary in candidates:
        scoring_candidates = [tc.primary]
        reasoning = f"{reasoning} | chooser=hard-pin"
    elif tc.hard_pin:
        scoring_candidates = candidates
        reasoning = f"{reasoning} | chooser=hard-pin-relaxed"
    else:
        scoring_candidates = candidates

    # R2-Router: estimate optimal output budget from prompt + tier
    budget = estimate_output_budget(prompt, tier.value)
    effective_output = min(max_output_tokens, max(1, int(budget * max(0.1, answer_depth_multiplier))))

    candidate_scores = _score_candidates(
        scoring_candidates,
        mode=mode,
        tier=tier,
        effective_output=effective_output,
        estimated_input_tokens=estimated_input_tokens,
        pricing=pricing,
        capabilities=capabilities,
        requirements=requirements,
        weights=weights,
        user_keyed_models=user_keyed_models,
        bandit_config=bandit_config or BanditConfig(),
        model_experience=model_experience,
    )
    candidate_scores.sort(key=lambda item: item.total, reverse=True)
    if user_keyed_models:
        keyed_scores = [item for item in candidate_scores if item.model in user_keyed_models]
        if keyed_scores:
            unkeyed_scores = [item for item in candidate_scores if item.model not in user_keyed_models]
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

    savings = max(0.0, (baseline_cost - cost) / baseline_cost) if baseline_cost > 0 else 0.0

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
        mode=mode,
        confidence=confidence,
        method=method,  # type: ignore[arg-type]
        reasoning=reasoning,
        cost_estimate=cost,
        baseline_cost=baseline_cost,
        savings=savings,
        complexity=_tier_complexity_anchor(tier),
        agentic_score=agentic_score,
        constraints=hard_constraints,
        workload_hints=hints,
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
    effective_output: int,
    estimated_input_tokens: int,
    pricing: dict[str, ModelPricing],
    capabilities: dict[str, ModelCapabilities],
    requirements: RequestRequirements,
    weights: SelectionWeights,
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
    snapshot = store.snapshot(model, mode, tier)
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
    pricing: dict[str, ModelPricing],
) -> dict[str, float]:
    """Price-based quality prior: expensive models assumed higher quality.

    Replaces the position-based ``editorial`` score from the tier path.
    Range [0, 1], most expensive = 1.0, cheapest = 0.0.
    """
    costs = {}
    for m in models:
        mp = pricing.get(m, ModelPricing(0, 0))
        costs[m] = mp.input_price + mp.output_price
    if not costs:
        return {}
    ranked = sorted(costs.keys(), key=lambda m: costs[m], reverse=True)
    n = len(ranked)
    return {m: 1.0 - (i / max(n - 1, 1)) for i, m in enumerate(ranked)}


def _normalized_costs(
    models: list[str],
    pricing: dict[str, ModelPricing],
) -> dict[str, float]:
    """Normalize costs to [0, 1].  Cheapest = 0, most expensive = 1."""
    raw = {}
    for m in models:
        mp = pricing.get(m, ModelPricing(0, 0))
        raw[m] = mp.input_price + mp.output_price
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
    answer_depth: AnswerDepth = AnswerDepth.STANDARD,
    answer_depth_multiplier: float = 1.0,
    agentic_score: float = 0.0,
    user_keyed_models: set[str] | None = None,
    selection_weights: SelectionWeights | None = None,
    bandit_config: BanditConfig | None = None,
    model_experience: object | None = None,
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

    candidates = _filter_candidates(available_models, requirements, capabilities)[0]
    if not candidates:
        candidates = available_models
    constrained_candidates = _apply_constraints(candidates, hard_constraints, capabilities)
    constraint_relaxed = bool(hard_constraints.tags()) and not constrained_candidates
    if constrained_candidates:
        candidates = constrained_candidates

    budget = estimate_output_budget(prompt, tier.value)
    effective_output = min(max_output_tokens, max(1, int(budget * max(0.1, answer_depth_multiplier))))

    quality_priors = _quality_prior_scores(candidates, pricing)
    norm_costs = _normalized_costs(candidates, pricing)
    experience = {
        m: _experience_snapshot(model_experience, m, mode, tier)
        for m in candidates
    }
    dollar_costs = {
        m: _calc_cost(m, estimated_input_tokens, effective_output, pricing,
                       input_cost_multiplier=experience[m].input_cost_multiplier)
        for m in candidates
    }
    cheapest_cost = min(dollar_costs.values()) if dollar_costs else 0.0
    if hard_constraints.max_cost is not None:
        affordable = [model for model in candidates if dollar_costs[model] <= hard_constraints.max_cost]
        if affordable:
            candidates = affordable
            quality_priors = _quality_prior_scores(candidates, pricing)
            norm_costs = _normalized_costs(candidates, pricing)
            experience = {m: experience[m] for m in candidates}
            dollar_costs = {m: dollar_costs[m] for m in candidates}
            cheapest_cost = min(dollar_costs.values()) if dollar_costs else 0.0
        else:
            constraint_relaxed = True
    bucket_pulls_count = _bucket_pulls(model_experience, mode, tier)

    mu = complexity
    bandit_active = bc.enabled and tier in bc.enabled_tiers

    ranked: list[CandidateScore] = []
    for model in candidates:
        cap = capabilities.get(model, ModelCapabilities())
        exp = experience[model]
        quality_prior = quality_priors.get(model, 0.5)
        cost_norm = norm_costs.get(model, 0.5)
        reasoning_bias = 1.0 if requirements.prefers_reasoning and cap.reasoning else 0.0
        byok = 1.0 if user_keyed_models and model in user_keyed_models else 0.0
        free_bias = 1.0 if cap.free else 0.0
        local_bias = 1.0 if cap.local else 0.0

        exploration_bonus = _bandit_bonus(
            enabled=bandit_active,
            bandit_config=bc,
            candidate_cost=dollar_costs[model],
            cheapest_cost=cheapest_cost,
            reliability=exp.reliability,
            samples=exp.samples,
            bucket_pulls=bucket_pulls_count,
        )

        # PROTEUS-inspired scoring (γ derived from existing weights):
        #   score = base_quality + w_q·μ·quality_prior - w_c·(1-μ)·cost + auxiliary
        base_quality = exp.reward_mean
        quality_term = weights.editorial * mu * quality_prior
        cost_penalty = weights.cost * (1.0 - mu) * cost_norm

        auxiliary = (
            weights.latency * exp.latency
            + weights.reliability * exp.reliability
            + weights.feedback * exp.feedback
            + weights.cache_affinity * exp.cache_affinity
            + weights.byok * byok
            + weights.free_bias * free_bias
            + weights.local_bias * local_bias
            + weights.reasoning_bias * reasoning_bias
        )

        total = base_quality + quality_term - cost_penalty + auxiliary
        if bandit_active:
            total += exploration_bonus

        ranked.append(CandidateScore(
            model=model,
            total=total,
            predicted_cost=dollar_costs[model],
            effective_cost_multiplier=exp.input_cost_multiplier,
            editorial=quality_prior,
            cost=cost_norm,
            latency=exp.latency,
            reliability=exp.reliability,
            feedback=exp.feedback,
            cache_affinity=exp.cache_affinity,
            byok=byok,
            free_bias=free_bias,
            local_bias=local_bias,
            reasoning_bias=reasoning_bias,
            bandit_mean=exp.reward_mean,
            exploration_bonus=exploration_bonus,
            samples=exp.samples,
        ))

    ranked.sort(key=lambda s: s.total, reverse=True)

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
    savings = max(0.0, (baseline_cost - cost) / baseline_cost) if baseline_cost > 0 else 0.0

    chain = [
        FallbackOption(model=s.model, cost_estimate=s.predicted_cost, suggested_output_budget=effective_output)
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
    if constraint_tags:
        reasoning_parts.append(f"constraints={','.join(constraint_tags)}")
    if hint_tags:
        reasoning_parts.append(f"hints={','.join(hint_tags)}")
    if constraint_relaxed:
        reasoning_parts.append("constraints=relaxed")

    return RoutingDecision(
        model=model,
        tier=tier,
        mode=mode,
        confidence=confidence,
        method=method_note,
        reasoning=" | ".join(reasoning_parts),
        cost_estimate=cost,
        baseline_cost=baseline_cost,
        savings=savings,
        complexity=complexity,
        agentic_score=agentic_score,
        constraints=hard_constraints,
        workload_hints=hints,
        answer_depth=answer_depth,
        suggested_output_budget=effective_output,
        fallback_chain=chain,
        candidate_scores=ranked,
    )
