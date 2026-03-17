"""Public API — the route() entry point."""

from __future__ import annotations

from dataclasses import replace

from uncommon_route.router.types import (
    AnswerDepth,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConfig,
    RoutingConstraints,
    RoutingDecision,
    RoutingMode,
    Tier,
    WorkloadHints,
)
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.classifier import classify
from uncommon_route.router.selector import select_from_pool
from uncommon_route.router.structural import estimate_tokens
from uncommon_route.router.config import (
    DEFAULT_CONFIG,
    get_bandit_config,
    get_selection_weights,
)


def _adjust_selection_weights(
    config: RoutingConfig,
    mode: RoutingMode,
    hints: WorkloadHints,
):
    weights = get_selection_weights(config, mode)
    adjustments = config.hint_adjustments
    if not any((hints.is_agentic, hints.is_coding)):
        return weights
    return replace(
        weights,
        latency=weights.latency + (adjustments.agentic_latency_bias if hints.is_agentic else 0.0),
        reliability=weights.reliability + (adjustments.agentic_reliability_bias if hints.is_agentic else 0.0),
        cache_affinity=weights.cache_affinity + (adjustments.agentic_cache_affinity_bias if hints.is_agentic else 0.0),
        reasoning_bias=weights.reasoning_bias + (adjustments.coding_reasoning_bias if hints.is_coding else 0.0),
    )


def route(
    prompt: str,
    system_prompt: str | None = None,
    max_output_tokens: int = 4096,
    config: RoutingConfig | None = None,
    routing_mode: RoutingMode | str = RoutingMode.AUTO,
    request_requirements: RequestRequirements | None = None,
    routing_constraints: RoutingConstraints | None = None,
    workload_hints: WorkloadHints | None = None,
    answer_depth: AnswerDepth | str = AnswerDepth.STANDARD,
    user_keyed_models: set[str] | None = None,
    model_experience: object | None = None,
    tier_cap: Tier | None = None,
    tier_floor: Tier | None = None,
    pricing: dict[str, ModelPricing] | None = None,
    available_models: list[str] | None = None,
    model_capabilities: dict[str, ModelCapabilities] | None = None,
) -> RoutingDecision:
    """Route a prompt to the best model.

    This is the main entry point. <1ms, pure local, no external calls.
    All available models compete via pool-based scoring; ``complexity``
    from the classifier drives the cost-vs-quality trade-off.

    When ``available_models`` is not provided, falls back to the static
    model list from ``DEFAULT_MODEL_PRICING``.
    """
    cfg = config or DEFAULT_CONFIG
    requirements = request_requirements or RequestRequirements()
    constraints = routing_constraints or RoutingConstraints()
    prompt_lower = prompt.lower() if prompt else ""
    hints = workload_hints or WorkloadHints(
        is_coding=any(
            marker in prompt_lower
            for marker in ("code", "function", "debug", "refactor", "python", "typescript", "javascript", "rust")
        ),
        needs_structured_output=any(
            marker in prompt_lower
            for marker in ("json", "structured", "schema", "yaml", "xml")
        ),
    )
    mode = routing_mode if isinstance(routing_mode, RoutingMode) else RoutingMode(routing_mode)
    depth = answer_depth if isinstance(answer_depth, AnswerDepth) else AnswerDepth(str(answer_depth).strip().lower())

    estimated_tokens = estimate_tokens(prompt)
    result = classify(prompt, system_prompt, cfg.scoring)

    sel_weights = _adjust_selection_weights(cfg, mode, hints)
    bc = get_bandit_config(cfg, mode)
    caps = model_capabilities or cfg.model_capabilities
    pool = available_models or list(DEFAULT_MODEL_PRICING.keys())
    effective_pricing = pricing or DEFAULT_MODEL_PRICING
    inferred_prefers_reasoning = any(signal.startswith("reasoning-pref:") for signal in result.signals)
    effective_requirements = RequestRequirements(
        needs_tool_calling=requirements.needs_tool_calling,
        needs_vision=requirements.needs_vision,
        prefers_reasoning=requirements.prefers_reasoning or inferred_prefers_reasoning or hints.is_coding,
    )

    complexity = result.complexity
    if prompt and any(kw in prompt_lower for kw in ("json", "structured", "schema")):
        hints = replace(hints, needs_structured_output=True)
    if hints.needs_structured_output:
        complexity = max(complexity, cfg.hint_adjustments.structured_output_complexity_floor)
    if hints.is_agentic:
        complexity = max(complexity, cfg.hint_adjustments.agentic_complexity_floor)
    if hints.is_coding:
        complexity = min(1.0, complexity + cfg.hint_adjustments.coding_complexity_boost)
    if tier_floor is not None:
        floor_min = {Tier.SIMPLE: 0.0, Tier.MEDIUM: 0.33, Tier.COMPLEX: 0.67}
        complexity = max(complexity, floor_min.get(tier_floor, 0.0))
    if tier_cap is not None:
        cap_max = {Tier.SIMPLE: 0.33, Tier.MEDIUM: 0.67, Tier.COMPLEX: 1.0}
        complexity = min(complexity, cap_max.get(tier_cap, 1.0))

    reasoning = f"score={result.score:.3f} | {', '.join(result.signals)}"

    return select_from_pool(
        complexity=complexity,
        mode=mode,
        confidence=result.confidence,
        reasoning_text=reasoning,
        available_models=pool,
        estimated_input_tokens=estimated_tokens,
        max_output_tokens=max_output_tokens,
        prompt=prompt,
        pricing=effective_pricing,
        capabilities=caps,
        requirements=effective_requirements,
        constraints=constraints,
        workload_hints=hints,
        answer_depth=depth,
        answer_depth_multiplier=cfg.answer_depth.multiplier(depth),
        agentic_score=result.agentic_score,
        user_keyed_models=user_keyed_models,
        selection_weights=sel_weights,
        bandit_config=bc,
        model_experience=model_experience,
    )
