"""Public API — the route() entry point.

v2: Uses multi-signal ensemble (metadata heuristics + embedding KNN) instead
of v1's single text classifier. Signal B (structural) is conditionally active
on longer conversations and otherwise remains in shadow mode. PlattCalibrator
applied to ensemble confidence. Model selection unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
from uncommon_route.signals.structural import StructuralSignal
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.decision.calibration import PlattCalibrator, load_calibrator

logger = logging.getLogger("uncommon-route")

# ─── v2 Signal Singletons ───

_v2_sig_a: MetadataSignal | None = None
_v2_sig_b: StructuralSignal | None = None  # conditional + shadow
_v2_sig_c: EmbeddingSignal | None = None
_v2_calibrator: PlattCalibrator | None = None
_v2_initialized = False

# tier_id → complexity (inverse of _derive_tier boundaries)
_TIER_ID_TO_COMPLEXITY = {0: 0.0, 1: 0.40, 2: 0.68, 3: 0.90}


def _should_activate_signal_b(row: dict[str, Any]) -> bool:
    """Enable Signal B on longer conversations where it improves pass rate."""
    return len(row.get("messages", [])) >= 4


@dataclass(frozen=True)
class V2ClassifyResult:
    """Full result from v2 classification, including all signal votes."""
    complexity: float
    confidence: float
    tier_id: int
    method: str
    signals_text: tuple[str, ...]
    vote_a: TierVote
    vote_b: TierVote
    vote_c: TierVote
    query_embedding: Any = None  # cached for index growth (numpy array or None)


def _ensure_v2_signals() -> None:
    global _v2_sig_a, _v2_sig_b, _v2_sig_c, _v2_calibrator, _v2_initialized
    if _v2_initialized:
        return
    _v2_initialized = True
    _v2_sig_a = MetadataSignal()
    _v2_sig_b = StructuralSignal()  # always loaded for conditional/shadow use
    # Auto-deploy seed index from package data if not in user data dir
    try:
        ensure_seed_index_deployed()
    except Exception:
        pass
    # Try to load embedding index
    try:
        from uncommon_route.paths import data_dir
        from pathlib import Path
        splits_dir = data_dir() / "v2_splits"
        emb_path = splits_dir / "seed_embeddings.npy"
        labels_path = splits_dir / "seed_labels.json"
        if emb_path.exists() and labels_path.exists():
            _v2_sig_c = EmbeddingSignal(
                index_path=emb_path, labels_path=labels_path,
                model_name="BAAI/bge-small-en-v1.5",
            )
            logger.info("v2 embedding signal loaded from %s", splits_dir)
        else:
            _v2_sig_c = EmbeddingSignal(model_name=None)
            logger.info("v2 embedding index not found — Signal C will abstain")
    except Exception as e:
        _v2_sig_c = EmbeddingSignal(model_name=None)
        logger.warning("v2 embedding init failed: %s", e)
    # Try to load PlattCalibrator
    try:
        from uncommon_route.paths import data_dir
        cal_path = data_dir() / "v2_splits" / "calibration_params.json"
        if cal_path.exists():
            _v2_calibrator = load_calibrator(cal_path)
            logger.info("v2 PlattCalibrator loaded (temperature=%.2f)", _v2_calibrator.temperature)
        else:
            logger.info("v2 calibration params not found — using uncalibrated confidence")
    except Exception as e:
        logger.warning("v2 calibrator load failed: %s", e)


def _build_signal_row(
    prompt: str,
    system_prompt: str | None,
    messages: list[dict[str, Any]] | None,
    routing_features: RoutingFeatures | None,
    context_features: dict[str, float] | None,
) -> dict[str, Any]:
    """Build a row dict for v2 signals from available proxy data."""
    # If full messages available, normalize content to strings but preserve other keys
    if messages:
        msgs = []
        for m in messages:
            normalized = dict(m)  # preserve tool_calls, tool_call_id, name, etc.
            content = normalized.get("content", "")
            if not isinstance(content, str):
                if isinstance(content, list):
                    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                    content = " ".join(parts)
                else:
                    content = str(content)
                normalized["content"] = content
            msgs.append(normalized)
    else:
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})

    msg_count = len(msgs)
    step_index = max(1, msg_count // 2)
    total_steps = max(step_index + 3, 10)

    scenario = "general"
    if routing_features:
        if routing_features.is_coding:
            scenario = "code_swe"
        elif routing_features.step_type == "tool-result-followup":
            scenario = "general_agent"

    return {
        "messages": msgs,
        "benchmark": "",
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
) -> V2ClassifyResult:
    """Run v2 signal ensemble. Returns full result with all signal votes."""
    _ensure_v2_signals()

    row = _build_signal_row(prompt, system_prompt, messages, routing_features, context_features)
    vote_a = _v2_sig_a.predict(row) if _v2_sig_a else TierVote(tier_id=1, confidence=0.4)
    vote_b = _v2_sig_b.predict(row) if _v2_sig_b else TierVote(tier_id=None, confidence=0.0)
    vote_c = _v2_sig_c.predict(row) if _v2_sig_c else TierVote(tier_id=None, confidence=0.0)

    # Cache query embedding for potential index growth
    query_embedding = None
    if _v2_sig_c and _v2_sig_c._embed_fn:
        try:
            from uncommon_route.signals.embedding import _extract_last_user_message
            text = _extract_last_user_message(row.get("messages", []))
            if text.strip():
                query_embedding = _v2_sig_c._embed_fn(text)
        except Exception:
            pass

    # Signal B is active for longer conversations and can also be
    # globally promoted by the lifecycle tracker.
    from uncommon_route import v2_lifecycle as _lc
    use_signal_b = _should_activate_signal_b(row) or _lc.is_signal_b_promoted()

    # Get learned weights from tracker (falls back to defaults if not initialized)
    tracker_weights = _lc._weight_tracker.weights if _lc._weight_tracker else None

    # Build ensemble with active signals, using learned weights when available
    if use_signal_b:
        active_votes = [vote_a]
        active_weights = [tracker_weights[0] if tracker_weights and len(tracker_weights) >= 3 else 0.50]
        if not vote_b.abstained:
            active_votes.append(vote_b)
            active_weights.append(tracker_weights[1] if tracker_weights and len(tracker_weights) >= 3 else 0.10)
        if not vote_c.abstained:
            active_votes.append(vote_c)
            active_weights.append(tracker_weights[2] if tracker_weights and len(tracker_weights) >= 3 else 0.40)
    else:
        active_votes = [vote_a]
        active_weights = [tracker_weights[0] if tracker_weights and len(tracker_weights) >= 2 else 0.55]
        if not vote_c.abstained:
            active_votes.append(vote_c)
            active_weights.append(tracker_weights[1] if tracker_weights and len(tracker_weights) >= 2 else 0.45)

    ensemble = Ensemble(
        weights=active_weights,
        risk_tolerance=risk_tolerance,
        calibrator=_v2_calibrator,
    )
    result = ensemble.decide(active_votes)

    tier_id = result.tier_id if result.tier_id is not None else 1
    complexity = _TIER_ID_TO_COMPLEXITY.get(tier_id, 0.40)

    signals_text = (
        f"v2:metadata={vote_a.tier_id}({vote_a.confidence:.2f})",
        f"v2:structural={vote_b.tier_id}({vote_b.confidence:.2f})[{'active' if use_signal_b else 'shadow'}]",
        f"v2:embedding={vote_c.tier_id}({vote_c.confidence:.2f})",
        f"v2:tier={tier_id} complexity={complexity:.2f} method={result.method}",
    )

    return V2ClassifyResult(
        complexity=complexity,
        confidence=result.confidence,
        tier_id=tier_id,
        method=result.method,
        signals_text=signals_text,
        vote_a=vote_a,
        vote_b=vote_b,
        vote_c=vote_c,
        query_embedding=query_embedding,
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
    """Route a prompt to the best model using v2 multi-signal ensemble."""
    cfg = config or DEFAULT_CONFIG
    constraints = routing_constraints or RoutingConstraints()
    features = routing_features or RoutingFeatures()
    requirements = features.request_requirements() if routing_features else (request_requirements or RequestRequirements())
    hints = features.workload_hints() if routing_features else (workload_hints or WorkloadHints())
    mode = routing_mode if isinstance(routing_mode, RoutingMode) else RoutingMode(routing_mode)
    depth = answer_depth if isinstance(answer_depth, AnswerDepth) else AnswerDepth(str(answer_depth).strip().lower())
    effective_max_output_tokens = features.requested_max_output_tokens or max_output_tokens

    estimated_tokens = estimate_tokens(prompt)

    # ─── v2: multi-signal ensemble ───
    v2 = _v2_classify(prompt, system_prompt, messages, features, context_features)

    sel_weights = get_selection_weights(cfg, mode)
    bc = get_bandit_config(cfg, mode)
    caps = cfg.model_capabilities if model_capabilities is None else model_capabilities
    pool = list(DEFAULT_MODEL_PRICING.keys()) if available_models is None else available_models
    effective_pricing = DEFAULT_MODEL_PRICING if pricing is None else pricing

    # v1's route-confidence calibrator still runs on top of v2's ensemble confidence
    confidence_calibrator = route_confidence_calibrator or get_active_route_confidence_calibrator()

    final_tier = _derive_tier(v2.complexity)
    confidence_estimate = confidence_calibrator.calibrate(
        v2.confidence,
        mode=mode,
        tier=final_tier,
        complexity=v2.complexity,
        step_type=features.step_type,
        answer_depth=depth,
        constraint_tags=constraints.tags(),
        hint_tags=hints.tags(),
        feature_tags=features.tags(),
        streaming=features.streaming,
    )
    reasoning = ", ".join(v2.signals_text)

    decision = select_from_pool(
        complexity=v2.complexity,
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

    # ─── v2: record to lifecycle (metrics, shadow, logging) ───
    try:
        from uncommon_route.v2_lifecycle import on_route_complete
        on_route_complete(
            request_id="",  # filled by proxy if available
            tier_id=v2.tier_id,
            model=decision.model,
            method=v2.method,
            confidence=v2.confidence,
            signal_a_tier=v2.vote_a.tier_id,
            signal_a_conf=v2.vote_a.confidence,
            signal_b_tier=v2.vote_b.tier_id,
            signal_b_conf=v2.vote_b.confidence,
            signal_c_tier=v2.vote_c.tier_id,
            signal_c_conf=v2.vote_c.confidence,
            query_embedding=v2.query_embedding,
        )
    except Exception:
        pass  # lifecycle not initialized yet (e.g. during tests)

    return decision


def ensure_seed_index_deployed() -> None:
    """Copy seed index from package data to user data dir if not already present."""
    from uncommon_route.paths import data_dir
    from pathlib import Path
    import shutil

    user_splits = data_dir() / "v2_splits"
    pkg_splits = Path(__file__).resolve().parent.parent / "data" / "v2_splits"

    for fname in ("seed_embeddings.npy", "seed_labels.json"):
        user_file = user_splits / fname
        pkg_file = pkg_splits / fname
        if not user_file.exists() and pkg_file.exists():
            user_splits.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pkg_file, user_file)
