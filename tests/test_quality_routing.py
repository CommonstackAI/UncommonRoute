from __future__ import annotations

from uncommon_route.router.quality import apply_quality_guards, model_served_quality, target_served_quality
from uncommon_route.router.types import CapabilityLane, ModelCapabilities, RoutingMode, ServedQuality, Tier


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


def test_quality_guards_prevent_complex_tool_flow_from_dropping_below_premium_when_available() -> None:
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
    assert guard.allowed_models == ["anthropic/claude-opus-4-7"]


def test_quality_guards_filter_medium_tool_flow_to_balanced_or_higher() -> None:
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
    )

    assert guard.target is ServedQuality.BALANCED
    assert "minimax/minimax-m2" not in guard.allowed_models
    assert set(guard.allowed_models) == {
        "minimax/minimax-m2.7",
        "anthropic/claude-sonnet-4-6",
    }


def test_quality_guards_respect_continuity_floor_when_available() -> None:
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

    assert guard.continuity_floor is ServedQuality.PREMIUM
    assert guard.allowed_models == ["anthropic/claude-opus-4-7"]


def test_quality_guards_note_for_forced_continuity_downgrade_when_floor_unavailable() -> None:
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
    assert "continuity-floor-unavailable=premium" in guard.notes
