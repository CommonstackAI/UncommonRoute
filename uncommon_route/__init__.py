"""UncommonRoute — local LLM router with mode-based routing and local-first cost control."""

from uncommon_route.router.api import route
from uncommon_route.router.types import (
    AnswerDepth,
    AnswerDepthConfig,
    BanditConfig,
    CandidateScore,
    FallbackOption,
    HintAdjustments,
    ModelCapabilities,
    ModelPricing,
    ModeConfig,
    RequestRequirements,
    RoutingConfig,
    RoutingConstraints,
    RoutingDecision,
    RoutingMode,
    ScoringConfig,
    ScoringResult,
    SelectionWeights,
    Tier,
    TierConfig,
    WorkloadHints,
)
from uncommon_route.router.config import (
    DEFAULT_CONFIG,
    DEFAULT_MODEL_PRICING,
    VIRTUAL_MODEL_IDS,
    get_bandit_config,
    get_mode_config,
    get_mode_tiers,
    get_selection_weights,
)
from uncommon_route.router.classifier import classify
from uncommon_route.router.selector import get_fallback_chain, select_model
from uncommon_route.session import derive_session_id
from uncommon_route.spend_control import (
    CheckResult,
    FileSpendControlStorage,
    InMemorySpendControlStorage,
    SpendControl,
    SpendControlStorage,
    SpendLimits,
    SpendRecord,
    SpendingStatus,
    format_duration,
)
from uncommon_route.openclaw import install as openclaw_install
from uncommon_route.openclaw import uninstall as openclaw_uninstall
from uncommon_route.openclaw import status as openclaw_status
from uncommon_route.providers import (
    ProvidersConfig,
    ProviderEntry,
    add_provider,
    load_providers,
    remove_provider,
    save_providers,
    select_preferred_model,
)
from uncommon_route.feedback import (
    FeedbackCollector,
    FeedbackResult,
    FeedbackSignal,
)
from uncommon_route.artifacts import ArtifactRecord, ArtifactStore
from uncommon_route.composition import (
    CompositionPolicy,
    CompositionResult,
    DEFAULT_COMPOSITION_POLICY,
    compose_messages,
    compose_messages_semantic,
    load_composition_policy,
)
from uncommon_route.semantic import (
    DEFAULT_SIDECHANNEL_CONFIG,
    QualityFallbackPolicy,
    SemanticCallResult,
    SemanticCompressor,
    SideChannelConfig,
    SideChannelTaskConfig,
    score_semantic_quality,
)
from uncommon_route.stats import (
    RouteRecord,
    RouteStats,
    RouteStatsStorage,
    FileRouteStatsStorage,
    InMemoryRouteStatsStorage,
    StatsSummary,
    TierSummary,
    ModelSummary,
)
from uncommon_route.model_experience import (
    CandidateExperience,
    FileModelExperienceStorage,
    InMemoryModelExperienceStorage,
    ModelExperienceRecord,
    ModelExperienceStore,
    ModelExperienceStorage,
)
from uncommon_route.routing_config_store import (
    FileRoutingConfigStorage,
    InMemoryRoutingConfigStorage,
    RoutingConfigStorage,
    RoutingConfigStore,
)
from uncommon_route.connections_store import (
    ConnectionsStorage,
    FileConnectionsStorage,
    InMemoryConnectionsStorage,
    ConnectionsStore,
    PrimaryConnection,
    EffectivePrimaryConnection,
    resolve_primary_connection,
    mask_api_key,
)

__all__ = [
    # Router
    "route",
    "classify",
    "select_model",
    "get_fallback_chain",
    "Tier",
    "RoutingMode",
    "RoutingDecision",
    "RoutingConfig",
    "RoutingConstraints",
    "WorkloadHints",
    "AnswerDepth",
    "AnswerDepthConfig",
    "HintAdjustments",
    "ModeConfig",
    "ScoringConfig",
    "ScoringResult",
    "BanditConfig",
    "SelectionWeights",
    "TierConfig",
    "ModelPricing",
    "ModelCapabilities",
    "RequestRequirements",
    "CandidateScore",
    "FallbackOption",
    "DEFAULT_CONFIG",
    "DEFAULT_MODEL_PRICING",
    "VIRTUAL_MODEL_IDS",
    "get_bandit_config",
    "get_selection_weights",
    "get_mode_config",
    "get_mode_tiers",
    # Session
    "derive_session_id",
    # Spend control
    "SpendControl",
    "SpendLimits",
    "SpendRecord",
    "SpendingStatus",
    "CheckResult",
    "SpendControlStorage",
    "FileSpendControlStorage",
    "InMemorySpendControlStorage",
    "format_duration",
    # OpenClaw
    "openclaw_install",
    "openclaw_uninstall",
    "openclaw_status",
    # Providers (BYOK)
    "ProvidersConfig",
    "ProviderEntry",
    "add_provider",
    "load_providers",
    "remove_provider",
    "save_providers",
    "select_preferred_model",
    # Feedback
    "FeedbackCollector",
    "FeedbackResult",
    "FeedbackSignal",
    # Composition / artifacts
    "ArtifactRecord",
    "ArtifactStore",
    "CompositionPolicy",
    "CompositionResult",
    "DEFAULT_COMPOSITION_POLICY",
    "compose_messages",
    "compose_messages_semantic",
    "load_composition_policy",
    "DEFAULT_SIDECHANNEL_CONFIG",
    "QualityFallbackPolicy",
    "SemanticCallResult",
    "SemanticCompressor",
    "SideChannelConfig",
    "SideChannelTaskConfig",
    "score_semantic_quality",
    # Adaptive model selection memory
    "CandidateExperience",
    "FileModelExperienceStorage",
    "InMemoryModelExperienceStorage",
    "ModelExperienceRecord",
    "ModelExperienceStore",
    "ModelExperienceStorage",
    "RoutingConfigStorage",
    "FileRoutingConfigStorage",
    "InMemoryRoutingConfigStorage",
    "RoutingConfigStore",
    "ConnectionsStorage",
    "FileConnectionsStorage",
    "InMemoryConnectionsStorage",
    "ConnectionsStore",
    "PrimaryConnection",
    "EffectivePrimaryConnection",
    "resolve_primary_connection",
    "mask_api_key",
    # Stats
    "RouteRecord",
    "RouteStats",
    "RouteStatsStorage",
    "FileRouteStatsStorage",
    "InMemoryRouteStatsStorage",
    "StatsSummary",
    "TierSummary",
    "ModelSummary",
]
