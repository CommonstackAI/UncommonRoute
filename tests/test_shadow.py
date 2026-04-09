"""Tests for Signal B shadow mode tracker."""

from uncommon_route.learning.shadow import ShadowTracker, _simulate_3sig_tier, ShadowRecord


def _record(tracker, a_pred, b_pred, c_pred, ens_tier, gold,
            a_conf=0.8, b_conf=0.7, c_conf=0.8):
    """Helper to record with full signal data."""
    tracker.record(
        signal_a_pred=a_pred, signal_a_conf=a_conf,
        signal_b_pred=b_pred, signal_b_conf=b_conf,
        signal_c_pred=c_pred, signal_c_conf=c_conf,
        ensemble_2sig_tier=ens_tier,
        gold_tier=gold,
    )


def test_not_promoted_initially():
    tracker = ShadowTracker(eval_window=5, promote_after=2)
    assert not tracker.promoted


def test_record_accumulates():
    tracker = ShadowTracker(eval_window=100)
    _record(tracker, a_pred=0, b_pred=1, c_pred=0, ens_tier=0, gold=0)
    assert tracker.record_count == 1


def test_non_overlapping_windows():
    """Evaluation fires once per eval_window batch, not on every request."""
    tracker = ShadowTracker(eval_window=5, promote_after=10, min_improvement=-1.0)
    # Fill 12 records — should fire exactly 2 evaluations (at 5 and 10)
    for i in range(12):
        _record(tracker, a_pred=0, b_pred=0, c_pred=0, ens_tier=0, gold=0)
    # After draining 2 batches of 5, 2 remain pending
    assert tracker.record_count == 2


def test_promotion_after_consecutive_wins():
    """Signal B promoted after enough consecutive positive windows (via replay).

    For B to tip the replay, A and C must be close. We use A=0(conf=0.51),
    C=1(conf=0.49) so 2-sig picks tier 0. With B=1(conf=0.9), 3-sig tips to tier 1.
    """
    tracker = ShadowTracker(eval_window=10, promote_after=2, min_improvement=0.0)

    for _window in range(2):
        for _ in range(5):
            # 2-sig: tier 0 (A barely wins). 3-sig: B tips to tier 1. gold=1 → B helps.
            _record(tracker, a_pred=0, b_pred=1, c_pred=1, ens_tier=0, gold=1,
                    a_conf=0.51, b_conf=0.9, c_conf=0.49)
        for _ in range(5):
            # All agree, no impact
            _record(tracker, a_pred=0, b_pred=0, c_pred=0, ens_tier=0, gold=0)

    assert tracker.promoted


def test_no_promotion_when_b_hurts():
    """Signal B NOT promoted when replay shows 3-signal would hurt."""
    tracker = ShadowTracker(eval_window=10, promote_after=2, min_improvement=0.005)

    for _ in range(5):
        # 2-sig: tier 0 (correct). B tips to tier 1 (wrong). gold=0 → B hurts.
        _record(tracker, a_pred=0, b_pred=1, c_pred=1, ens_tier=0, gold=0,
                a_conf=0.51, b_conf=0.9, c_conf=0.49)
    for _ in range(5):
        _record(tracker, a_pred=1, b_pred=1, c_pred=1, ens_tier=1, gold=1)

    assert not tracker.promoted
    assert tracker.consecutive_wins == 0


def test_streak_resets_on_bad_window():
    tracker = ShadowTracker(eval_window=10, promote_after=3, min_improvement=0.0)

    # Good window: B tips correctly
    for _ in range(5):
        _record(tracker, a_pred=0, b_pred=1, c_pred=1, ens_tier=0, gold=1,
                a_conf=0.51, b_conf=0.9, c_conf=0.49)
    for _ in range(5):
        _record(tracker, a_pred=0, b_pred=0, c_pred=0, ens_tier=0, gold=0)
    assert tracker.consecutive_wins == 1

    # Bad window: B tips incorrectly
    for _ in range(5):
        _record(tracker, a_pred=0, b_pred=1, c_pred=1, ens_tier=0, gold=0,
                a_conf=0.51, b_conf=0.9, c_conf=0.49)
    for _ in range(5):
        _record(tracker, a_pred=0, b_pred=0, c_pred=0, ens_tier=0, gold=0)
    assert tracker.consecutive_wins == 0


def test_save_load(tmp_path):
    tracker = ShadowTracker(eval_window=100, promote_after=3)
    tracker._consecutive_wins = 2
    tracker._promoted = False
    path = tmp_path / "shadow.json"
    tracker.save(path)

    loaded = ShadowTracker.load(path, eval_window=100, promote_after=3)
    assert loaded.consecutive_wins == 2
    assert not loaded.promoted


def test_no_eval_without_labels():
    """Window with no labeled data should not crash or promote."""
    tracker = ShadowTracker(eval_window=5, promote_after=1)
    for _ in range(10):
        _record(tracker, a_pred=0, b_pred=1, c_pred=0, ens_tier=0, gold=None)
    assert not tracker.promoted


def test_simulate_3sig_tier():
    """The 3-signal replay function should compute weighted vote correctly."""
    rec = ShadowRecord(
        signal_a_pred=0, signal_a_conf=0.8,
        signal_b_pred=1, signal_b_conf=0.7,
        signal_c_pred=0, signal_c_conf=0.8,
        ensemble_2sig_tier=0,
        gold_tier=0,
    )
    # A=0 (0.8*0.50=0.40), B=1 (0.7*0.10=0.07), C=0 (0.8*0.40=0.32)
    # tier_0 = 0.72, tier_1 = 0.07, total=0.79
    # normalized: tier_0 = 0.72/0.79 ≈ 0.91 — well above threshold → direct → tier 0
    tier = _simulate_3sig_tier(rec)
    assert tier == 0  # B at weight 0.10 can't override A+C agreement
