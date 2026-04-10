"""Tests for v2 lifecycle: startup, shutdown, per-request hooks."""

import json
from pathlib import Path

import uncommon_route.v2_lifecycle as lc


def _reset_lifecycle():
    """Reset lifecycle singletons for test isolation."""
    lc._metrics = None
    lc._shadow = None
    lc._weight_tracker = None
    lc._index_manager = None
    lc._state_dir = None
    lc._initialized = False
    lc._recent_predictions.clear()


def test_on_startup_initializes_all(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    assert lc._metrics is not None
    assert lc._shadow is not None
    assert lc._weight_tracker is not None
    assert lc._initialized
    _reset_lifecycle()


def test_on_shutdown_persists_state(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    # Record some activity
    lc.on_route_complete(
        request_id="test_1", tier_id=0, model="deepseek",
        method="direct", confidence=0.8,
        signal_a_tier=0, signal_a_conf=0.8,
        signal_b_tier=1, signal_b_conf=0.7,
        signal_c_tier=0, signal_c_conf=0.8,
    )
    lc.on_shutdown()
    # Verify state file exists and is valid
    state_file = tmp_path / "learned_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert "signal_weights" in data
    assert data["schema_version"] == 1
    _reset_lifecycle()


def test_on_route_complete_records_metrics(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    lc.on_route_complete(
        request_id="req_1", tier_id=0, model="test-model",
        method="direct", confidence=0.9,
        signal_a_tier=0, signal_a_conf=0.9,
        signal_b_tier=1, signal_b_conf=0.6,
        signal_c_tier=0, signal_c_conf=0.85,
    )
    metrics = lc.get_metrics()
    assert metrics is not None
    assert metrics["total_requests"] == 1
    assert metrics["requests_by_tier"][0] == 1
    _reset_lifecycle()


def test_on_route_complete_feeds_shadow(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    lc.on_route_complete(
        request_id="req_1", tier_id=0, model="test",
        method="direct", confidence=0.9,
        signal_a_tier=0, signal_a_conf=0.9,
        signal_b_tier=1, signal_b_conf=0.6,
        signal_c_tier=0, signal_c_conf=0.85,
    )
    assert lc._shadow.record_count == 1
    _reset_lifecycle()


def test_on_feedback_updates_weights(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    old_weights = list(lc._weight_tracker.weights)
    # Simulate: Signal A predicted 0, Signal C predicted 2 (disagree)
    lc.on_route_complete(
        request_id="", tier_id=1, model="test", method="direct", confidence=0.8,
        signal_a_tier=0, signal_a_conf=0.8,
        signal_b_tier=1, signal_b_conf=0.7,
        signal_c_tier=2, signal_c_conf=0.8,
    )
    lc.associate_request_id("req_feedback_test")
    # Feedback: "weak" on SIMPLE → actual_tier = min(0+1,3) = 1
    # Signal A predicted 0 (wrong), Signal C predicted 2 (wrong) → both penalized
    # But different predictions → weight ratio changes after normalization
    lc.on_feedback(request_id="req_feedback_test", signal="ok", routed_tier_v1="SIMPLE")
    new_weights = lc._weight_tracker.weights
    # Signal A predicted 0, actual=0 (ok→same) → rewarded
    # Signal C predicted 2, actual=0 → penalized
    # Weights should diverge
    assert new_weights[0] > old_weights[0]  # A rewarded
    _reset_lifecycle()


def test_persistence_roundtrip(tmp_path):
    """Startup → activity → shutdown → restart → state restored."""
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    # Simulate a routing + feedback that changes weights
    lc.on_route_complete(
        request_id="", tier_id=1, model="test", method="direct", confidence=0.8,
        signal_a_tier=0, signal_a_conf=0.8,
        signal_b_tier=1, signal_b_conf=0.7,
        signal_c_tier=1, signal_c_conf=0.8,
    )
    lc.associate_request_id("req_persist")
    lc.on_feedback(request_id="req_persist", signal="weak", routed_tier_v1="MEDIUM")
    weights_before = list(lc._weight_tracker.weights)
    lc.on_shutdown()
    # Restart
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    weights_after = lc._weight_tracker.weights
    # Weights should be restored
    for a, b in zip(weights_before, weights_after):
        assert abs(a - b) < 1e-9
    _reset_lifecycle()


def test_signal_b_not_promoted_initially(tmp_path):
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    assert not lc.is_signal_b_promoted()
    _reset_lifecycle()


def test_index_manager_initialized(tmp_path):
    """EmbeddingIndexManager should be initialized if seed index exists."""
    _reset_lifecycle()
    # Create a minimal seed index
    import numpy as np
    import json
    splits = tmp_path / "v2_splits"
    splits.mkdir()
    embs = np.random.randn(5, 384).astype(np.float32)
    embs = embs / np.linalg.norm(embs, axis=1, keepdims=True)
    np.save(splits / "seed_embeddings.npy", embs)
    with open(splits / "seed_labels.json", "w") as f:
        json.dump([0, 1, 2, 3, 0], f)

    lc.on_startup(tmp_path)
    assert lc._index_manager is not None
    assert lc._index_manager.size == 5

    # Test growth via on_route_complete with high confidence + unanimous signals
    new_vec = np.random.randn(384).astype(np.float32)
    new_vec = new_vec / np.linalg.norm(new_vec)
    lc.on_route_complete(
        request_id="growth_test", tier_id=1, model="test", method="direct",
        confidence=0.9,  # >= 0.7 threshold
        signal_a_tier=1, signal_a_conf=0.9,
        signal_b_tier=1, signal_b_conf=0.7,
        signal_c_tier=1, signal_c_conf=0.9,  # signal_a == signal_c → unanimous
        query_embedding=new_vec,
    )
    assert lc._index_manager.size == 6

    # Test save on shutdown
    lc.on_shutdown()
    assert (splits / "seed_embeddings.npy").exists()
    _reset_lifecycle()


def test_idempotent_startup(tmp_path):
    """Calling on_startup twice should not crash or re-initialize."""
    _reset_lifecycle()
    lc.on_startup(tmp_path)
    lc.on_startup(tmp_path)  # second call should be no-op
    assert lc._initialized
    _reset_lifecycle()
