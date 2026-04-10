"""v2 lifecycle: startup, shutdown, per-request hooks.

Centralizes all v2 state management so proxy.py only needs a few call sites:
  1. on_startup()          — in _on_startup()
  2. on_shutdown()         — in _lifespan finally
  3. on_route_complete()   — called from route() after each decision
  4. on_feedback()         — called from proxy feedback endpoint
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uncommon_route.observability import RoutingMetrics, RoutingLogEntry
from uncommon_route.persistence import LearnedState, save_state, load_state
from uncommon_route.learning.weights import SignalWeightTracker
from uncommon_route.learning.shadow import ShadowTracker
from uncommon_route.learning.index_growth import EmbeddingIndexManager
from uncommon_route.v2_tiers import V1_TO_V2, TIER_TO_ID

logger = logging.getLogger("uncommon-route.v2")

# ─── Per-request prediction cache (for feedback → weight update) ───

@dataclass
class _RecentPrediction:
    tier_id: int
    signal_a_tier: int | None
    signal_a_abstained: bool
    signal_c_tier: int | None
    signal_c_abstained: bool

_recent_predictions: deque[tuple[str, _RecentPrediction]] = deque(maxlen=500)
_pending_telemetry: dict[str, Any] = {}  # request_id → TelemetryRecord (Stage 1, awaiting outcome)

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

    # Embedding index manager
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


def associate_request_id(request_id: str) -> None:
    """Link a request_id to the most recent un-linked prediction.

    Called by proxy after route() returns and request_id is assigned.
    """
    # Find the most recent prediction with empty request_id and link it
    for i in range(len(_recent_predictions) - 1, -1, -1):
        rid, pred = _recent_predictions[i]
        if rid == "":
            _recent_predictions[i] = (request_id, pred)
            return


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
    query_embedding: Any = None,
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

    # 2. Shadow tracking for Signal B (gold_tier filled later via on_feedback)
    if _shadow:
        _shadow.record(
            signal_a_pred=signal_a_tier, signal_a_conf=signal_a_conf,
            signal_b_pred=signal_b_tier, signal_b_conf=signal_b_conf,
            signal_c_pred=signal_c_tier, signal_c_conf=signal_c_conf,
            ensemble_2sig_tier=tier_id,
            gold_tier=None,  # filled retroactively by on_feedback
        )

    # 3. Index growth on high-confidence unanimous routing
    if (
        _index_manager
        and query_embedding is not None
        and confidence >= 0.7
        and signal_a_tier is not None
        and signal_a_tier == signal_c_tier
    ):
        try:
            added = _index_manager.add(query_embedding, tier_id)
            if added:
                logger.debug("v2 index growth: added entry for tier %d (size=%d)", tier_id, _index_manager.size)
        except Exception as e:
            logger.debug("v2 index growth failed: %s", e)

    # 4. Cache signal predictions for feedback (request_id linked later by proxy)
    _recent_predictions.append(("", _RecentPrediction(
        tier_id=tier_id,
        signal_a_tier=signal_a_tier,
        signal_a_abstained=signal_a_tier is None,
        signal_c_tier=signal_c_tier,
        signal_c_abstained=signal_c_tier is None,
    )))

    # 5. Telemetry (opt-in, pseudonymous)
    try:
        from uncommon_route import telemetry as _telem
        if _telem.is_enabled():
            import time
            emb_list = None
            if query_embedding is not None:
                token_est = max(20, int(confidence * 100))  # rough proxy
                emb_list = _telem.prepare_embedding(query_embedding, token_est)
            rec = _telem.TelemetryRecord(
                schema_version=1,
                client_version="0.5.0",
                platform=sys.platform if "sys" in dir() else __import__("sys").platform,
                timestamp_day=time.strftime("%Y-%m-%d"),
                predicted_tier=tier_id,
                routed_model=model,
                confidence=confidence,
                routing_method=method,
                message_count=0,  # not available here; filled by proxy if wired
                has_tools=False,
                tool_count=0,
                embedding=emb_list,
            )
            # Stage 1 skeleton — outcome filled later by proxy
            _pending_telemetry[request_id] = rec
    except Exception:
        pass

    # 6. Structured log
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


def on_feedback(*, request_id: str, signal: str, routed_tier_v1: str) -> None:
    """Update signal weights and shadow labels from user feedback.

    Args:
        request_id: The request that was rated.
        signal: "ok", "weak", or "strong".
        routed_tier_v1: The v1-style tier name (SIMPLE/MEDIUM/COMPLEX) that was routed.
    """
    # Map v1 tier name to v2 tier_id
    v2_tier_name = V1_TO_V2.get(routed_tier_v1.upper(), routed_tier_v1.lower())
    if v2_tier_name not in TIER_TO_ID:
        return
    routed_tier_id = TIER_TO_ID[v2_tier_name]

    # Determine "actual" tier from feedback
    if signal == "ok":
        actual_tier = routed_tier_id  # routing was correct
    elif signal == "weak":
        actual_tier = min(routed_tier_id + 1, 3)  # needed stronger model
    elif signal == "strong":
        actual_tier = max(routed_tier_id - 1, 0)  # could have used cheaper
    else:
        return

    # Look up per-request signal predictions
    pred = None
    for rid, p in reversed(_recent_predictions):
        if rid == request_id:
            pred = p
            break

    if pred and _weight_tracker:
        _weight_tracker.update(
            predictions=[pred.signal_a_tier, pred.signal_c_tier],
            abstained=[pred.signal_a_abstained, pred.signal_c_abstained],
            actual_tier=actual_tier,
        )
        logger.debug(
            "v2 weight update: request=%s signal=%s actual_tier=%d weights=%s",
            request_id, signal, actual_tier, _weight_tracker.weights,
        )

    # Retroactively label shadow records for evaluation
    if _shadow and _shadow._pending:
        # Label the most recent pending record with feedback-derived tier
        for rec in reversed(_shadow._pending):
            if rec.gold_tier is None:
                rec.gold_tier = actual_tier
                break


def complete_telemetry(
    request_id: str,
    outcome: str = "success",
    outcome_reason: str | None = None,
    final_tier: int = -1,
    final_model: str = "",
    cascaded: bool = False,
    cascade_from_tier: int | None = None,
) -> None:
    """Stage 2: fill outcome fields and buffer the telemetry record."""
    rec = _pending_telemetry.pop(request_id, None)
    if rec is None:
        return
    try:
        from uncommon_route import telemetry as _telem
        rec.outcome = outcome
        rec.outcome_reason = outcome_reason
        rec.final_tier = final_tier
        rec.final_model = final_model
        rec.cascaded = cascaded
        rec.cascade_from_tier = cascade_from_tier
        _telem.buffer_record(rec)
    except Exception:
        pass


def get_metrics() -> dict[str, Any] | None:
    """Get current v2 metrics snapshot."""
    return _metrics.snapshot() if _metrics else None


def is_signal_b_promoted() -> bool:
    """Check if shadow mode has promoted Signal B."""
    return _shadow.promoted if _shadow else False


def get_index_size() -> int:
    """Current embedding index size."""
    return _index_manager.size if _index_manager else 0
