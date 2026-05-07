"""Capability-lane and served-quality helpers for routing."""

from __future__ import annotations

from dataclasses import dataclass

from uncommon_route.router.types import (
    CapabilityLane,
    ModelCapabilities,
    RoutingFeatures,
    RoutingMode,
    ServedQuality,
    Tier,
    pressure_rescue_premium_allowed,
)

_QUALITY_RANK = {
    ServedQuality.ECONOMY: 0,
    ServedQuality.BALANCED: 1,
    ServedQuality.PREMIUM: 2,
}
_REASONING_NEGATIVE_MARKERS = (
    "non-reason",
    "non_reason",
    "nonreason",
    "no-reason",
    "no_reason",
    "non-thinking",
)


def _provider_and_core(model_id: str) -> tuple[str, str]:
    value = str(model_id or "").strip().lower()
    if "/" in value:
        provider, core = value.split("/", 1)
        return provider, core
    return "", value


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(token in text for token in needles)


def quality_rank(value: ServedQuality | str | None) -> int:
    if isinstance(value, ServedQuality):
        return _QUALITY_RANK[value]
    normalized = str(value or "").strip().lower()
    for quality, rank in _QUALITY_RANK.items():
        if quality.value == normalized:
            return rank
    return _QUALITY_RANK[ServedQuality.ECONOMY]


def normalize_served_quality(value: ServedQuality | str | None) -> ServedQuality | None:
    if value is None:
        return None
    if isinstance(value, ServedQuality):
        return value
    normalized = str(value).strip().lower()
    for quality in ServedQuality:
        if quality.value == normalized:
            return quality
    return None


def request_capability_lane(features: RoutingFeatures | None) -> CapabilityLane:
    if features and features.capability_lane is not None:
        return features.capability_lane
    if features and features.needs_vision:
        return CapabilityLane.VISION
    if features and features.prefers_reasoning:
        return CapabilityLane.REASONING
    return CapabilityLane.GENERAL


def target_served_quality(mode: RoutingMode, tier: Tier) -> ServedQuality:
    if mode is RoutingMode.FAST:
        if tier is Tier.COMPLEX:
            return ServedQuality.BALANCED
        return ServedQuality.ECONOMY
    if mode is RoutingMode.BEST:
        if tier is Tier.SIMPLE:
            return ServedQuality.BALANCED
        return ServedQuality.PREMIUM
    if tier is Tier.SIMPLE:
        return ServedQuality.ECONOMY
    if tier is Tier.MEDIUM:
        return ServedQuality.BALANCED
    return ServedQuality.PREMIUM


def minimum_served_quality(mode: RoutingMode, tier: Tier) -> ServedQuality:
    if tier is Tier.COMPLEX:
        return ServedQuality.BALANCED
    if mode is RoutingMode.BEST:
        return ServedQuality.BALANCED
    return ServedQuality.ECONOMY


def stronger_quality(left: ServedQuality | None, right: ServedQuality | None) -> ServedQuality | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if quality_rank(left) >= quality_rank(right) else right


def model_served_quality(
    model_id: str,
    lane: CapabilityLane,
    capabilities: ModelCapabilities | None = None,
) -> ServedQuality:
    provider, core = _provider_and_core(model_id)
    caps = capabilities or ModelCapabilities()
    explicitly_non_reasoning = _contains_any(core, _REASONING_NEGATIVE_MARKERS)

    if lane is CapabilityLane.ANTHROPIC_TOOL_SAFE:
        if provider == "anthropic":
            if "opus" in core:
                return ServedQuality.PREMIUM
            if "sonnet" in core:
                return ServedQuality.BALANCED
            return ServedQuality.ECONOMY
        if provider == "minimax":
            if _contains_any(core, ("m2.5", "m2.7")):
                return ServedQuality.BALANCED
            return ServedQuality.ECONOMY

    if lane is CapabilityLane.REASONING:
        if explicitly_non_reasoning:
            return ServedQuality.ECONOMY
        if provider == "anthropic" and "opus" in core:
            return ServedQuality.PREMIUM
        if provider == "deepseek" and "r1" in core:
            return ServedQuality.PREMIUM
        if provider in {"xai", "x-ai"} and "reason" in core and not explicitly_non_reasoning:
            return ServedQuality.PREMIUM
        if ("thinking" in core or "reason" in core) and not explicitly_non_reasoning:
            return ServedQuality.PREMIUM
        if provider == "openai" and _contains_any(core, ("pro", "o3", "gpt-5.4", "gpt-5.2", "gpt-5")):
            if "nano" in core:
                return ServedQuality.ECONOMY
            if "mini" in core:
                return ServedQuality.BALANCED
            return ServedQuality.PREMIUM
        if provider == "google" and "pro" in core:
            return ServedQuality.PREMIUM
        if caps.reasoning and not explicitly_non_reasoning:
            return ServedQuality.BALANCED

    if lane is CapabilityLane.VISION:
        if provider == "anthropic" and "opus" in core:
            return ServedQuality.PREMIUM
        if provider == "google" and "pro" in core:
            return ServedQuality.PREMIUM
        if provider == "openai" and "pro" in core:
            return ServedQuality.PREMIUM
        if provider == "qwen" and "vl" in core:
            return ServedQuality.BALANCED
        if provider in {"anthropic", "google", "openai"}:
            return ServedQuality.BALANCED

    if provider == "anthropic":
        if "opus" in core:
            return ServedQuality.PREMIUM
        if "sonnet" in core:
            return ServedQuality.BALANCED
        return ServedQuality.ECONOMY
    if provider == "minimax":
        if _contains_any(core, ("m2.5", "m2.7")):
            return ServedQuality.BALANCED
        return ServedQuality.ECONOMY
    if provider == "google":
        if "pro" in core:
            return ServedQuality.PREMIUM
        if _contains_any(core, ("flash-lite", "flash")):
            return ServedQuality.ECONOMY
        return ServedQuality.BALANCED
    if provider == "openai":
        if "pro" in core:
            return ServedQuality.PREMIUM
        if _contains_any(core, ("nano", "4o-mini", "mini")):
            return ServedQuality.ECONOMY
        if _contains_any(core, ("gpt-5", "gpt-4.1", "gpt-oss", "o3", "o4")):
            return ServedQuality.BALANCED
    if provider in {"moonshot", "moonshotai"}:
        if "thinking" in core:
            return ServedQuality.PREMIUM
        return ServedQuality.BALANCED
    if provider == "deepseek":
        if "r1" in core:
            return ServedQuality.PREMIUM
        return ServedQuality.ECONOMY
    if provider == "zai-org":
        if _contains_any(core, ("4.5-air", "4.6", "5-turbo")):
            return ServedQuality.ECONOMY
        return ServedQuality.BALANCED
    if provider in {"qwen", "xai", "x-ai", "xiaomi", "bytedance-seed"}:
        return ServedQuality.BALANCED
    return ServedQuality.BALANCED if caps.reasoning or caps.vision else ServedQuality.ECONOMY


def quality_alignment_score(
    candidate_quality: ServedQuality,
    target_quality: ServedQuality,
) -> float:
    delta = quality_rank(candidate_quality) - quality_rank(target_quality)
    if delta == 0:
        return 1.0
    if delta > 0:
        return 0.82
    if delta == -1:
        return 0.18
    return 0.0


def scoring_served_quality_target(
    mode: RoutingMode,
    tier: Tier,
    target: ServedQuality,
    floor: ServedQuality,
    *,
    complexity: float | None = None,
    confidence: float | None = None,
    step_risk: str = "normal",
    is_agentic: bool = False,
    is_coding: bool = False,
    has_tool_results: bool = False,
    session_present: bool = False,
    agent_step_count: int = 0,
    agent_pressure: float = 0.0,
    verification_failed: bool = False,
) -> ServedQuality:
    """Return the quality level used for score alignment.

    AUTO+COMPLEX should mean "balanced or better, prefer quality when the
    classifier is confident the current step truly needs it", not "every
    traceback or agent step should become premium-only". BEST keeps the
    stricter premium target.
    """
    if quality_rank(floor) > quality_rank(target):
        return floor

    normalized_step_risk = str(step_risk or "normal").strip().lower()

    if (
        mode is RoutingMode.AUTO
        and normalized_step_risk == "low"
        and tier in {Tier.SIMPLE, Tier.MEDIUM}
        and agent_pressure < 0.55
    ):
        return floor

    if mode is RoutingMode.AUTO and tier is Tier.COMPLEX:
        initial_complex_planning = (
            target is ServedQuality.PREMIUM
            and complexity is not None
            and confidence is not None
            and complexity >= 0.86
            and confidence >= 0.30
            and (is_agentic or is_coding)
            and not has_tool_results
            and not session_present
            and agent_step_count == 0
            and normalized_step_risk != "low"
        )
        pressure_review = (
            target is ServedQuality.PREMIUM
            and pressure_rescue_premium_allowed(
                tier=tier,
                complexity=complexity,
                confidence=confidence,
                step_risk=normalized_step_risk,
                agent_pressure=agent_pressure,
                agent_step_count=agent_step_count,
                has_tool_results=has_tool_results,
                is_agentic=is_agentic,
                is_coding=is_coding,
                verification_failed=verification_failed,
            )
        )
        if initial_complex_planning or (
            target is ServedQuality.PREMIUM
            and complexity is not None
            and confidence is not None
            and complexity >= 0.86
            and confidence >= 0.55
            and normalized_step_risk == "high"
        ) or pressure_review:
            return target
        return floor
    return target


def continuity_alignment_score(
    candidate_quality: ServedQuality,
    previous_quality: ServedQuality | None,
) -> float:
    if previous_quality is None:
        return 0.0
    delta = quality_rank(candidate_quality) - quality_rank(previous_quality)
    if delta >= 0:
        return 0.18 if delta == 0 else 0.08
    return -0.22 * abs(delta)


@dataclass(frozen=True, slots=True)
class QualityGuardResult:
    allowed_models: list[str]
    quality_by_model: dict[str, ServedQuality]
    target: ServedQuality
    floor: ServedQuality
    continuity_floor: ServedQuality | None
    notes: tuple[str, ...]


def apply_quality_guards(
    candidates: list[str],
    *,
    mode: RoutingMode,
    tier: Tier,
    lane: CapabilityLane,
    capabilities: dict[str, ModelCapabilities],
    continuity_floor: ServedQuality | None = None,
    step_risk: str = "normal",
    agent_pressure: float = 0.0,
    is_agentic: bool = False,
    has_tool_results: bool = False,
) -> QualityGuardResult:
    quality_by_model = {
        model: model_served_quality(model, lane, capabilities.get(model))
        for model in candidates
    }
    target = target_served_quality(mode, tier)
    normalized_step_risk = str(step_risk or "normal").strip().lower()
    floor = minimum_served_quality(mode, tier)
    if (
        mode is RoutingMode.AUTO
        and normalized_step_risk == "low"
        and (tier is not Tier.COMPLEX or is_agentic or has_tool_results)
    ):
        # The public tier describes the whole request, but served-quality is a
        # per-step guard. A routine/successful agent step should not exclude
        # economy candidates just because the surrounding issue is complex.
        floor = ServedQuality.ECONOMY
    if lane is CapabilityLane.REASONING and tier is Tier.COMPLEX:
        lane_floor = ServedQuality.PREMIUM
    else:
        lane_floor = None
    if normalized_step_risk == "high":
        risk_floor = ServedQuality.BALANCED
    else:
        risk_floor = None
    hard_continuity_floor = continuity_floor if mode is RoutingMode.BEST else None
    effective_floor = stronger_quality(
        stronger_quality(stronger_quality(floor, lane_floor), risk_floor),
        hard_continuity_floor,
    ) or floor
    preferred_threshold = (
        stronger_quality(stronger_quality(target, effective_floor), hard_continuity_floor)
        or target
    )
    notes: list[str] = [
        f"lane={lane.value}",
        f"served-quality-target={target.value}",
        f"served-quality-floor={effective_floor.value}",
    ]
    if normalized_step_risk != "normal":
        notes.append(f"step-risk={normalized_step_risk}")
    if agent_pressure >= 0.35:
        notes.append(f"agent-pressure={agent_pressure:.2f}")
    if risk_floor is not None and normalized_step_risk == "high":
        notes.append(f"step-risk-floor={risk_floor.value}")
    elif risk_floor is not None:
        notes.append(f"agent-pressure-floor={risk_floor.value}")
    if lane_floor is not None:
        notes.append(f"lane-floor={lane_floor.value}")
    if continuity_floor is not None and hard_continuity_floor is None:
        notes.append(f"continuity-soft={continuity_floor.value}")
    # AUTO should route on model suitability, not collapse a high-risk complex
    # step into an Opus-only pool before scoring can compare price/quality.
    prefer_floor_pool = mode is RoutingMode.AUTO

    preferred = [
        model for model in candidates
        if quality_rank(quality_by_model[model]) >= quality_rank(preferred_threshold)
    ]
    if preferred and not prefer_floor_pool:
        if hard_continuity_floor is not None and quality_rank(preferred_threshold) > quality_rank(target):
            notes.append(f"continuity-floor={hard_continuity_floor.value}")
        notes.append(f"served-quality>=target({len(preferred)}/{len(candidates)})")
        return QualityGuardResult(
            allowed_models=preferred,
            quality_by_model=quality_by_model,
            target=target,
            floor=effective_floor,
            continuity_floor=hard_continuity_floor,
            notes=tuple(notes),
        )

    floor_candidates = [
        model for model in candidates
        if quality_rank(quality_by_model[model]) >= quality_rank(effective_floor)
    ]
    if floor_candidates:
        if hard_continuity_floor is not None:
            notes.append(f"continuity-floor-unavailable={hard_continuity_floor.value}")
        if prefer_floor_pool and preferred:
            notes.append(f"served-quality-target-preferred={target.value}({len(preferred)}/{len(candidates)})")
        notes.append(f"served-quality>=floor({len(floor_candidates)}/{len(candidates)})")
        return QualityGuardResult(
            allowed_models=floor_candidates,
            quality_by_model=quality_by_model,
            target=target,
            floor=effective_floor,
            continuity_floor=hard_continuity_floor,
            notes=tuple(notes),
        )

    if hard_continuity_floor is not None:
        notes.append(f"continuity-floor-unavailable={hard_continuity_floor.value}")
    notes.append("served-quality-floor-unavailable")
    return QualityGuardResult(
        allowed_models=list(candidates),
        quality_by_model=quality_by_model,
        target=target,
        floor=effective_floor,
        continuity_floor=hard_continuity_floor,
        notes=tuple(notes),
    )
