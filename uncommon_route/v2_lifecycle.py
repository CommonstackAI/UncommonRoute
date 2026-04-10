"""v2 lifecycle: startup, shutdown, per-request hooks.

Centralizes all v2 state management so proxy.py only needs 3 call sites:
  1. on_startup()  — in _on_startup()
  2. on_shutdown() — in _lifespan finally
  3. on_route_complete() — after each route() call
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from uncommon_route.observability import RoutingMetrics, RoutingLogEntry
from uncommon_route.persistence import LearnedState, save_state, load_state
from uncommon_route.learning.weights import SignalWeightTracker
from uncommon_route.learning.shadow import ShadowTracker
from uncommon_route.learning.index_growth import EmbeddingIndexManager

logger = logging.getLogger("uncommon-route.v2")

# ─── Singletons ───
_metrics: RoutingMetrics | None = None
_shadow: ShadowTracker | None = None
_weight_tracker: SignalWeightTracker | None = None
_index_manager: EmbeddingIndexManager | None = None
_state_dir: Path | None = None
_initialized = False


def on_startup(data_dir: Path | None = None) -> None:
    """Initialize all v2 subsystems. Call once at proxy startup."""
    global _metrics, _shadow, _weight_tracker, _index_manager, _state_dir, _initialized

    if _initialized:
        return
    _initialized = True

    if data_dir is None:
        from uncommon_route.paths import data_dir as get_data_dir
        data_dir = get_data_dir()
    _state_dir = data_dir

    # Load persisted state
    state = load_state(data_dir)
    logger.info(
        "v2 lifecycle started: weights=%s, shadow_promoted=%s, shadow_streak=%d",
        state.signal_weights, state.shadow_promoted, state.shadow_consecutive_wins,
    )

    # Initialize subsystems from persisted state
    _metrics = RoutingMetrics()
    _weight_tracker = SignalWeightTracker(initial_weights=state.signal_weights)
    _shadow = ShadowTracker(eval_window=200, promote_after=3)
    _shadow._consecutive_wins = state.shadow_consecutive_wins
    _shadow._promoted = state.shadow_promoted

    # Embedding index manager for index growth
    try:
        splits_dir = data_dir / "v2_splits"
        emb_path = splits_dir / "seed_embeddings.npy"
        labels_path = splits_dir / "seed_labels.json"
        if emb_path.exists() and labels_path.exists():
            _index_manager = EmbeddingIndexManager(
                index_path=emb_path, labels_path=labels_path,
                max_size=10_000, dedup_threshold=0.95,
            )
            logger.info("v2 embedding index manager loaded (%d entries)", _index_manager.size)
    except Exception as e:
        logger.warning("v2 embedding index manager init failed: %s", e)


def on_shutdown() -> None:
    """Persist all v2 state. Call at proxy shutdown."""
    if _state_dir is None or not _initialized:
        return

    # Save embedding index if it grew
    if _index_manager:
        try:
            _index_manager.save()
            logger.info("v2 embedding index saved (%d entries)", _index_manager.size)
        except Exception as e:
            logger.warning("v2 embedding index save failed: %s", e)

    state = LearnedState(
        signal_weights=_weight_tracker.weights if _weight_tracker else [0.55, 0.45],
        calibration_temperature=1.0,
        shadow_consecutive_wins=_shadow.consecutive_wins if _shadow else 0,
        shadow_promoted=_shadow.promoted if _shadow else False,
        embedding_index_size=_index_manager.size if _index_manager else 0,
        model_priors={},
    )
    save_state(state, _state_dir)
    logger.info("v2 lifecycle shutdown: state saved to %s", _state_dir)

    if _metrics:
        snap = _metrics.snapshot()
        logger.info("v2 session metrics: %s", snap)


def on_route_complete(
    *,
    request_id: str,
    tier_id: int,
    model: str,
    method: str,
    confidence: float,
    signal_a_tier: int | None,
    signal_a_conf: float,
    signal_b_tier: int | None,
    signal_b_conf: float,
    signal_c_tier: int | None,
    signal_c_conf: float,
) -> None:
    """Record a completed routing decision. Call after each route()."""
    if not _initialized:
        return

    # 1. Record v2 metrics
    if _metrics:
        signals_agreed = (signal_a_tier == signal_c_tier) if signal_c_tier is not None else True
        _metrics.record_routing(
            tier=tier_id, model=model, method=method,
            confidence=confidence, signals_agreed=signals_agreed,
        )

    # 2. Shadow tracking for Signal B
    if _shadow:
        _shadow.record(
            signal_a_pred=signal_a_tier, signal_a_conf=signal_a_conf,
            signal_b_pred=signal_b_tier, signal_b_conf=signal_b_conf,
            signal_c_pred=signal_c_tier, signal_c_conf=signal_c_conf,
            ensemble_2sig_tier=tier_id,
            gold_tier=None,  # unknown in production
        )

    # 3. Structured log
    log_entry = RoutingLogEntry(
        request_id=request_id,
        signals={
            "metadata": {"tier": signal_a_tier, "confidence": signal_a_conf},
            "structural": {"tier": signal_b_tier, "confidence": signal_b_conf, "shadow": True},
            "embedding": {"tier": signal_c_tier, "confidence": signal_c_conf},
        },
        decision_tier=tier_id,
        decision_confidence=confidence,
        method=method,
        model=model,
    )
    logger.debug("v2 routing: %s", log_entry.to_json())


def on_feedback(
    *,
    actual_tier: int,
    signal_predictions: list[int | None],
    signal_abstained: list[bool],
) -> None:
    """Update signal weights from feedback. Call when user provides ok/weak/strong."""
    if _weight_tracker:
        _weight_tracker.update(signal_predictions, signal_abstained, actual_tier)


def get_metrics() -> dict[str, Any] | None:
    """Get current v2 metrics snapshot."""
    return _metrics.snapshot() if _metrics else None


def on_confident_routing(embedding: "np.ndarray", tier_id: int) -> bool:
    """Add a high-confidence routing to the embedding index. Returns True if added."""
    if _index_manager is None:
        return False
    try:
        return _index_manager.add(embedding, tier_id)
    except Exception:
        return False


def is_signal_b_promoted() -> bool:
    """Check if shadow mode has promoted Signal B."""
    return _shadow.promoted if _shadow else False


def get_index_size() -> int:
    """Current embedding index size."""
    return _index_manager.size if _index_manager else 0
