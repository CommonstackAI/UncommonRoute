"""Public API — the route() entry point.

v2: Uses multi-signal ensemble (metadata heuristics + embedding KNN) instead
of v1's single text classifier. The ensemble determines tier, which maps to
a complexity score. Model selection (select_from_pool) is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from uncommon_route.calibration import get_active_route_confidence_calibrator
from uncommon_route.router.types import (
    AnswerDepth,
    ModelCapabilities,
    ModelPricing,
    RequestRequirements,
    RoutingConfig,
    RoutingConstraints,
    RoutingDecision,
    RoutingFeatures,
    RoutingMode,
    Tier,
    WorkloadHints,
)
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.selector import select_from_pool, _derive_tier
from uncommon_route.router.structural import estimate_tokens
from uncommon_route.router.config import (
    DEFAULT_CONFIG,
    get_bandit_config,
    get_selection_weights,
)
from uncommon_route.signals.base import TierVote
from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.decision.ensemble import Ensemble

logger = logging.getLogger("uncommon-route")

# ─── v2 Signal Singletons ───

_v2_sig_a: MetadataSignal | None = None
_v2_sig_c: EmbeddingSignal | None = None
_v2_initialized = False

# tier_id → complexity (inverse of _derive_tier boundaries)
_TIER_ID_TO_COMPLEXITY = {0: 0.0, 1: 0.40, 2: 0.68, 3: 0.90}


def _ensure_v2_signals() -> None:
    global _v2_sig_a, _v2_sig_c, _v2_initialized
    if _v2_initialized:
        return
    _v2_initialized = True
    _v2_sig_a = MetadataSignal()
    # Try to load embedding index from default location
    try:
        from uncommon_route.paths import data_dir
        from pathlib import Path
        splits_dir = data_dir() / "v2_splits"
        emb_path = splits_dir / "seed_embeddings.npy"
        labels_path = splits_dir / "seed_labels.json"
        if emb_path.exists() and labels_path.exists():
            _v2_sig_c = EmbeddingSignal(
                index_path=emb_path,
                labels_path=labels_path,
                model_name="BAAI/bge-small-en-v1.5",
            )
            logger.info("v2 embedding signal loaded from %s", splits_dir)
        else:
            _v2_sig_c = EmbeddingSignal(model_name=None)
            logger.info("v2 embedding index not found at %s — Signal C will abstain", splits_dir)
    except Exception as e:
        _v2_sig_c = EmbeddingSignal(model_name=None)
        logger.warning("v2 embedding signal init failed: %s — Signal C will abstain", e)


def _build_signal_row(
    prompt: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]] | None,
    routing_features: RoutingFeatures | None,
    context_features: dict[str, float] | None,
) -> dict[str, Any]:
    """Build a row dict for v2 signals from available proxy data."""
    # If full messages available, normalize content to strings
    if messages:
        msgs = []
        for m in messages:
            content = m.get("content", "")
            if not isinstance(content, str):
                # Content-array (e.g. vision) → extract text parts only
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    content = " ".join(parts)
                else:
                    content = str(content)
            msgs.append({"role": m.get("role", "user"), "content": content})
    else:
        # Reconstruct from prompt + system_prompt
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})

    # Estimate step_index from conversation depth
    msg_count = len(msgs)
    step_index = max(1, msg_count // 2)  # rough: 2 messages per turn
    # Estimate total_steps higher than current to avoid step_ratio=1.0
    # A typical agent session has 5-10 steps; use 10 as a reasonable ceiling
    total_steps = max(step_index + 3, 10)

    # Determine scenario from step_type
    scenario = "general"
    if routing_features:
        if routing_features.is_coding:
            scenario = "code_swe"
        elif routing_features.step_type == "tool-result-followup":
            scenario = "general_agent"

    return {
        "messages": msgs,
        "benchmark": "",  # not available in production
        "scenario": scenario,
        "step_index": step_index,
        "total_steps": total_steps,
    }


def _v2_classify(
    prompt: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]] | None,
    routing_features: RoutingFeatures | None,
    context_features: dict[str, float] | None,
    risk_tolerance: float = 0.5,
) -> tuple[float, float, tuple[str, ...]]:
    """Run v2 signal ensemble. Returns (complexity, confidence, signals)."""
    _ensure_v2_signals()

    row = _build_signal_row(prompt, system_prompt, messages, routing_features, context_features)
    vote_a = _v2_sig_a.predict(row) if _v2_sig_a else TierVote(tier_id=1, confidence=0.4)
    vote_c = _v2_sig_c.predict(row) if _v2_sig_c else TierVote(tier_id=None, confidence=0.0)

    # Build ensemble with active (non-abstaining) signals
    active_votes = [vote_a]
    active_weights = [0.55]
    if not vote_c.abstained:
        active_votes.append(vote_c)
        active_weights.append(0.45)

    ensemble = Ensemble(weights=active_weights, risk_tolerance=risk_tolerance)
    result = ensemble.decide(active_votes)

    tier_id = result.tier_id if result.tier_id is not None else 1
    complexity = _TIER_ID_TO_COMPLEXITY.get(tier_id, 0.40)
    confidence = result.confidence

    signals = (
        f"v2:metadata={vote_a.tier_id}({vote_a.confidence:.2f})",
        f"v2:embedding={vote_c.tier_id}({vote_c.confidence:.2f})",
        f"v2:tier={tier_id} complexity={complexity:.2f} method={result.method}",
    )

    return complexity, confidence, signals


def route(
    prompt: str,
    system_prompt: str | None = None,
    max_output_tokens: int = 4096,
    config: RoutingConfig | None = None,
    routing_mode: RoutingMode | str = RoutingMode.AUTO,
    request_requirements: RequestRequirements | None = None,
    routing_constraints: RoutingConstraints | None = None,
    workload_hints: WorkloadHints | None = None,
    routing_features: RoutingFeatures | None = None,
    answer_depth: AnswerDepth | str = AnswerDepth.STANDARD,
    user_keyed_models: set[str] | None = None,
    model_experience: object | None = None,
    route_confidence_calibrator: object | None = None,
    context_features: dict[str, float] | None = None,
    pricing: dict[str, ModelPricing] | None = None,
    available_models: list[str] | None = None,
    model_capabilities: dict[str, ModelCapabilities] | None = None,
    messages: list[dict[str, Any]] | None = None,
    # Legacy parameters — accepted but ignored
    tier_cap: Tier | None = None,
    tier_floor: Tier | None = None,
) -> RoutingDecision:
    """Route a prompt to the best model using v2 multi-signal ensemble.

    The v2 ensemble (metadata heuristics + embedding KNN) determines the
    difficulty tier. Model selection via Thompson Sampling + multi-factor
    scoring is unchanged from v1.
    """
    cfg = config or DEFAULT_CONFIG
    constraints = routing_constraints or RoutingConstraints()
    features = routing_features or RoutingFeatures()
    requirements = features.request_requirements() if routing_features else (request_requirements or RequestRequirements())
    hints = features.workload_hints() if routing_features else (workload_hints or WorkloadHints())
    mode = routing_mode if isinstance(routing_mode, RoutingMode) else RoutingMode(routing_mode)
    depth = answer_depth if isinstance(answer_depth, AnswerDepth) else AnswerDepth(str(answer_depth).strip().lower())
    effective_max_output_tokens = features.requested_max_output_tokens or max_output_tokens

    estimated_tokens = estimate_tokens(prompt)

    # ─── v2: multi-signal ensemble replaces v1 classify() ───
    complexity, v2_confidence, v2_signals = _v2_classify(
        prompt, system_prompt, messages, features, context_features,
    )

    sel_weights = get_selection_weights(cfg, mode)
    bc = get_bandit_config(cfg, mode)
    caps = cfg.model_capabilities if model_capabilities is None else model_capabilities
    pool = list(DEFAULT_MODEL_PRICING.keys()) if available_models is None else available_models
    effective_pricing = DEFAULT_MODEL_PRICING if pricing is None else pricing

    confidence_calibrator = route_confidence_calibrator or get_active_route_confidence_calibrator()

    final_tier = _derive_tier(complexity)
    confidence_estimate = confidence_calibrator.calibrate(
        v2_confidence,
        mode=mode,
        tier=final_tier,
        complexity=complexity,
        step_type=features.step_type,
        answer_depth=depth,
        constraint_tags=constraints.tags(),
        hint_tags=hints.tags(),
        feature_tags=features.tags(),
        streaming=features.streaming,
    )
    reasoning = ", ".join(v2_signals)

    return select_from_pool(
        complexity=complexity,
        mode=mode,
        confidence=confidence_estimate.confidence,
        reasoning_text=reasoning,
        available_models=pool,
        estimated_input_tokens=estimated_tokens,
        max_output_tokens=effective_max_output_tokens,
        prompt=prompt,
        pricing=effective_pricing,
        capabilities=caps,
        requirements=requirements,
        constraints=constraints,
        workload_hints=hints,
        routing_features=features,
        answer_depth=depth,
        answer_depth_multiplier=cfg.answer_depth.multiplier(depth),
        agentic_score=0.0,
        user_keyed_models=user_keyed_models,
        selection_weights=sel_weights,
        bandit_config=bc,
        model_experience=model_experience,
        raw_confidence=confidence_estimate.raw_confidence,
        confidence_source=confidence_estimate.source,
        calibration_version=confidence_estimate.version,
        calibration_sample_count=confidence_estimate.sample_count,
        calibration_temperature=confidence_estimate.temperature,
        calibration_applied_tags=confidence_estimate.applied_adjustments,
    )
