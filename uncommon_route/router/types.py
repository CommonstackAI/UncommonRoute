"""Core type definitions for UncommonRoute."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


class RoutingMode(str, Enum):
    AUTO = "auto"
    FAST = "fast"
    BEST = "best"


class AnswerDepth(str, Enum):
    BRIEF = "brief"
    STANDARD = "standard"
    DEEP = "deep"


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    tool_calling: bool = False
    vision: bool = False
    reasoning: bool = False
    free: bool = False
    local: bool = False
    responses: bool = False


@dataclass(frozen=True, slots=True)
class RequestRequirements:
    needs_tool_calling: bool = False
    needs_vision: bool = False
    prefers_reasoning: bool = False


@dataclass(frozen=True, slots=True)
class RoutingConstraints:
    free_only: bool = False
    local_only: bool = False
    allowed_models: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    max_cost: float | None = None

    def tags(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.free_only:
            labels.append("free-only")
        if self.local_only:
            labels.append("local-only")
        if self.allowed_models:
            labels.append("model-subset")
        if self.allowed_providers:
            labels.append("provider-subset")
        if self.max_cost is not None:
            labels.append("budget-cap")
        return tuple(labels)


@dataclass(frozen=True, slots=True)
class WorkloadHints:
    is_agentic: bool = False
    is_coding: bool = False
    needs_structured_output: bool = False

    def tags(self) -> tuple[str, ...]:
        labels: list[str] = []
        if self.is_agentic:
            labels.append("tool-heavy")
        if self.is_coding:
            labels.append("coding")
        if self.needs_structured_output:
            labels.append("structured-output")
        return tuple(labels)


@dataclass(frozen=True, slots=True)
class DimensionScore:
    name: str
    score: float  # [-1, 1]
    signal: str | None = None


@dataclass(frozen=True, slots=True)
class ScoringResult:
    score: float
    tier: Tier | None  # None = ambiguous
    confidence: float  # [0, 1]
    signals: list[str]
    dimensions: list[DimensionScore] = field(default_factory=list)
    agentic_score: float = 0.0
    complexity: float = 0.33  # continuous 0.0-1.0 complexity score


@dataclass(frozen=True, slots=True)
class FallbackOption:
    """One option in the cost-aware fallback chain."""

    model: str
    cost_estimate: float
    suggested_output_budget: int


@dataclass(frozen=True, slots=True)
class CandidateScore:
    model: str
    total: float
    predicted_cost: float
    effective_cost_multiplier: float = 1.0
    editorial: float = 0.0
    cost: float = 0.0
    latency: float = 0.0
    reliability: float = 0.0
    feedback: float = 0.0
    cache_affinity: float = 0.0
    byok: float = 0.0
    free_bias: float = 0.0
    local_bias: float = 0.0
    reasoning_bias: float = 0.0
    bandit_mean: float = 0.5
    exploration_bonus: float = 0.0
    samples: int = 0


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    model: str
    tier: Tier
    mode: RoutingMode
    confidence: float
    method: str
    reasoning: str
    cost_estimate: float
    baseline_cost: float
    savings: float  # 0-1
    complexity: float = 0.33
    agentic_score: float = 0.0
    constraints: RoutingConstraints = field(default_factory=RoutingConstraints)
    workload_hints: WorkloadHints = field(default_factory=WorkloadHints)
    answer_depth: AnswerDepth = AnswerDepth.STANDARD
    suggested_output_budget: int = 4096
    fallback_chain: list[FallbackOption] = field(default_factory=list)
    candidate_scores: list[CandidateScore] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TierConfig:
    primary: str = ""
    fallback: list[str] = field(default_factory=list)
    hard_pin: bool = False


@dataclass(frozen=True, slots=True)
class ModelPricing:
    input_price: float  # per 1M tokens
    output_price: float  # per 1M tokens
    cached_input_price: float | None = None  # per 1M cached-read tokens
    cache_write_price: float | None = None  # per 1M cache-write / cache-create tokens


@dataclass(frozen=True, slots=True)
class SelectionWeights:
    editorial: float = 0.4
    cost: float = 0.2
    latency: float = 0.1
    reliability: float = 0.1
    feedback: float = 0.1
    cache_affinity: float = 0.05
    byok: float = 0.05
    free_bias: float = 0.0
    local_bias: float = 0.0
    reasoning_bias: float = 0.05


@dataclass(frozen=True, slots=True)
class BanditConfig:
    enabled: bool = True
    reward_weight: float = 0.12
    exploration_weight: float = 0.18
    warmup_pulls: int = 2
    min_samples_for_guardrail: int = 3
    min_reliability: float = 0.25
    max_cost_ratio: float = 3.0
    enabled_tiers: tuple[Tier, ...] = (Tier.SIMPLE, Tier.MEDIUM)


@dataclass(frozen=True, slots=True)
class HintAdjustments:
    structured_output_complexity_floor: float = 0.40
    coding_complexity_boost: float = 0.08
    agentic_complexity_floor: float = 0.40
    agentic_latency_bias: float = 0.05
    agentic_reliability_bias: float = 0.06
    agentic_cache_affinity_bias: float = 0.08
    coding_reasoning_bias: float = 0.05


@dataclass(frozen=True, slots=True)
class AnswerDepthConfig:
    brief_multiplier: float = 0.60
    standard_multiplier: float = 1.00
    deep_multiplier: float = 1.45

    def multiplier(self, depth: AnswerDepth) -> float:
        if depth is AnswerDepth.BRIEF:
            return self.brief_multiplier
        if depth is AnswerDepth.DEEP:
            return self.deep_multiplier
        return self.standard_multiplier


@dataclass(frozen=True, slots=True)
class ModeConfig:
    tiers: dict[Tier, TierConfig] = field(default_factory=dict)
    selection: SelectionWeights = field(default_factory=SelectionWeights)
    bandit: BanditConfig = field(default_factory=BanditConfig)


@dataclass
class StructuralWeights:
    """Weights for language-agnostic structural features."""

    normalized_length: float = 0.05
    enumeration_density: float = 0.10
    sentence_count: float = 0.08
    code_markers: float = 0.07
    math_symbols: float = 0.06
    nesting_depth: float = 0.03
    vocabulary_diversity: float = 0.03
    avg_word_length: float = 0.03
    alphabetic_ratio: float = 0.03
    functional_intent: float = 0.06
    unique_concept_density: float = 0.07
    requirement_phrases: float = 0.06


@dataclass
class KeywordWeights:
    """Weights for keyword-based features (language-specific, secondary)."""

    code_presence: float = 0.06
    reasoning_markers: float = 0.06
    technical_terms: float = 0.04
    creative_markers: float = 0.02
    simple_indicators: float = 0.02
    imperative_verbs: float = 0.02
    constraint_count: float = 0.02
    output_format: float = 0.02
    domain_specificity: float = 0.01
    agentic_task: float = 0.02
    analytical_verbs: float = 0.04
    multi_step_patterns: float = 0.04


@dataclass
class TierBoundaries:
    simple_medium: float = -0.02
    medium_complex: float = 0.15


@dataclass
class ScoringConfig:
    structural_weights: StructuralWeights = field(default_factory=StructuralWeights)
    keyword_weights: KeywordWeights = field(default_factory=KeywordWeights)
    tier_boundaries: TierBoundaries = field(default_factory=TierBoundaries)
    confidence_steepness: float = 18.0
    confidence_threshold: float = 0.55


@dataclass
class RoutingConfig:
    version: str = "4.0"
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    modes: dict[RoutingMode, ModeConfig] = field(default_factory=dict)
    hint_adjustments: HintAdjustments = field(default_factory=HintAdjustments)
    answer_depth: AnswerDepthConfig = field(default_factory=AnswerDepthConfig)
    model_capabilities: dict[str, ModelCapabilities] = field(default_factory=dict)
    free_model: str = "nvidia/gpt-oss-120b"
    max_tokens_force_complex: int = 100_000
    structured_output_min_tier: Tier = Tier.MEDIUM
    ambiguous_default_tier: Tier = Tier.MEDIUM
