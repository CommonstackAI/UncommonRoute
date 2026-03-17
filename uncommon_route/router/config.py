"""Default routing configuration."""

from uncommon_route.model_map import infer_capabilities
from uncommon_route.router.types import (
    BanditConfig,
    ModeConfig,
    ModelCapabilities,
    ModelPricing,
    RoutingConfig,
    RoutingMode,
    ScoringConfig,
    SelectionWeights,
    Tier,
    TierConfig,
)

DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    "nvidia/gpt-oss-120b": ModelPricing(0.0, 0.0),
    "google/gemini-2.5-flash-lite": ModelPricing(0.10, 0.40),
    "deepseek/deepseek-chat": ModelPricing(0.28, 0.42, cached_input_price=0.28, cache_write_price=0.28),
    "deepseek/deepseek-reasoner": ModelPricing(0.28, 0.42, cached_input_price=0.28, cache_write_price=0.28),
    "moonshot/kimi-k2.5": ModelPricing(0.60, 3.00),
    "minimax/minimax-m2.5": ModelPricing(0.30, 1.20),
    "xai/grok-4-1-fast-reasoning": ModelPricing(0.20, 0.50),
    "xai/grok-4-1-fast-non-reasoning": ModelPricing(0.20, 1.50),
    "xai/grok-4-0709": ModelPricing(0.20, 1.50),
    "xai/grok-code-fast-1": ModelPricing(0.20, 1.50),
    "google/gemini-2.5-flash": ModelPricing(0.30, 2.50),
    "google/gemini-2.5-pro": ModelPricing(1.25, 10.00),
    "google/gemini-3-pro-preview": ModelPricing(2.00, 12.00),
    "google/gemini-3.1-pro": ModelPricing(2.00, 12.00),
    "openai/gpt-4o-mini": ModelPricing(0.15, 0.60, cached_input_price=0.075),
    "openai/gpt-4o": ModelPricing(2.50, 10.00, cached_input_price=1.25),
    "openai/gpt-5.2": ModelPricing(1.75, 14.00, cached_input_price=0.875),
    "openai/gpt-5.2-codex": ModelPricing(1.75, 14.00, cached_input_price=0.875),
    "openai/o1-mini": ModelPricing(1.10, 4.40, cached_input_price=0.55),
    "openai/o3": ModelPricing(2.00, 8.00, cached_input_price=1.00),
    "openai/o4-mini": ModelPricing(1.10, 4.40, cached_input_price=0.55),
    "anthropic/claude-haiku-4.5": ModelPricing(1.00, 5.00, cached_input_price=0.10, cache_write_price=1.25),
    "anthropic/claude-sonnet-4.6": ModelPricing(3.00, 15.00, cached_input_price=0.30, cache_write_price=3.75),
    "anthropic/claude-opus-4.6": ModelPricing(5.00, 25.00, cached_input_price=0.50, cache_write_price=6.25),
}

BASELINE_MODEL = "anthropic/claude-opus-4.6"
FREE_MODEL = "nvidia/gpt-oss-120b"

VIRTUAL_MODEL_IDS: dict[RoutingMode, str] = {
    RoutingMode.AUTO: "uncommon-route/auto",
    RoutingMode.FAST: "uncommon-route/fast",
    RoutingMode.BEST: "uncommon-route/best",
}

VIRTUAL_MODEL_ALIASES: dict[str, RoutingMode] = {
    mode.value: mode for mode in RoutingMode
}
DEFAULT_MODEL_CAPABILITIES: dict[str, ModelCapabilities] = {
    model_id: infer_capabilities(
        model_id,
        pricing,
        has_explicit_pricing=True,
    )
    for model_id, pricing in DEFAULT_MODEL_PRICING.items()
}


def _discovery_managed_tiers() -> dict[Tier, TierConfig]:
    return {
        Tier.SIMPLE: TierConfig(),
        Tier.MEDIUM: TierConfig(),
        Tier.COMPLEX: TierConfig(),
    }


def routing_mode_from_model(model_id: str) -> RoutingMode | None:
    normalized = (model_id or "").strip().lower()
    if normalized in VIRTUAL_MODEL_ALIASES:
        return VIRTUAL_MODEL_ALIASES[normalized]
    for mode, virtual_model in VIRTUAL_MODEL_IDS.items():
        if normalized == virtual_model:
            return mode
    return None


def virtual_model_entries() -> list[dict[str, str]]:
    return [
        {"id": VIRTUAL_MODEL_IDS[RoutingMode.AUTO], "object": "model", "owned_by": "uncommon-route"},
        {"id": VIRTUAL_MODEL_IDS[RoutingMode.FAST], "object": "model", "owned_by": "uncommon-route"},
        {"id": VIRTUAL_MODEL_IDS[RoutingMode.BEST], "object": "model", "owned_by": "uncommon-route"},
    ]


def get_mode_config(config: RoutingConfig, mode: RoutingMode) -> ModeConfig:
    return config.modes.get(mode, config.modes.get(RoutingMode.AUTO, ModeConfig()))


def get_mode_tiers(config: RoutingConfig, mode: RoutingMode) -> dict[Tier, TierConfig]:
    return get_mode_config(config, mode).tiers


def get_selection_weights(config: RoutingConfig, mode: RoutingMode) -> SelectionWeights:
    return get_mode_config(config, mode).selection


def get_bandit_config(config: RoutingConfig, mode: RoutingMode) -> BanditConfig:
    return get_mode_config(config, mode).bandit


DEFAULT_CONFIG = RoutingConfig(
    version="4.0",
    scoring=ScoringConfig(),
    modes={
        RoutingMode.AUTO: ModeConfig(
            tiers=_discovery_managed_tiers(),
            selection=SelectionWeights(
                editorial=0.40,
                cost=0.12,
                latency=0.08,
                reliability=0.10,
                feedback=0.10,
                cache_affinity=0.11,
                byok=0.08,
                free_bias=0.01,
                local_bias=0.01,
                reasoning_bias=0.03,
            ),
            bandit=BanditConfig(
                enabled=True,
                reward_weight=0.10,
                exploration_weight=0.16,
                warmup_pulls=2,
                min_samples_for_guardrail=3,
                min_reliability=0.25,
                max_cost_ratio=2.8,
                enabled_tiers=(Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX),
            ),
        ),
        RoutingMode.FAST: ModeConfig(
            tiers=_discovery_managed_tiers(),
            selection=SelectionWeights(
                editorial=0.20,
                cost=0.26,
                latency=0.18,
                reliability=0.13,
                feedback=0.08,
                cache_affinity=0.08,
                byok=0.05,
                free_bias=0.01,
                local_bias=0.01,
                reasoning_bias=0.00,
            ),
            bandit=BanditConfig(
                enabled=True,
                reward_weight=0.08,
                exploration_weight=0.18,
                warmup_pulls=3,
                min_samples_for_guardrail=3,
                min_reliability=0.30,
                max_cost_ratio=1.7,
                enabled_tiers=(Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX),
            ),
        ),
        RoutingMode.BEST: ModeConfig(
            tiers=_discovery_managed_tiers(),
            selection=SelectionWeights(
                editorial=0.52,
                cost=0.04,
                latency=0.06,
                reliability=0.12,
                feedback=0.11,
                cache_affinity=0.08,
                byok=0.05,
                free_bias=0.00,
                local_bias=0.00,
                reasoning_bias=0.06,
            ),
            bandit=BanditConfig(
                enabled=True,
                reward_weight=0.06,
                exploration_weight=0.08,
                warmup_pulls=1,
                min_samples_for_guardrail=4,
                min_reliability=0.35,
                max_cost_ratio=2.2,
                enabled_tiers=(Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX),
            ),
        ),
    },
    model_capabilities=DEFAULT_MODEL_CAPABILITIES,
    free_model=FREE_MODEL,
)
