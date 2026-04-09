"""Tests for Signal B shadow mode tracker."""

from uncommon_route.learning.shadow import ShadowTracker


def test_not_promoted_initially():
    tracker = ShadowTracker(eval_window=5, promote_after=2)
    assert not tracker.promoted


def test_record_accumulates():
    tracker = ShadowTracker(eval_window=100)
    tracker.record(signal_b_pred=0, ensemble_pred=1, gold_tier=0)
    assert tracker.record_count == 1


def test_promotion_after_consecutive_wins():
    """Signal B gets promoted after enough consecutive positive eval windows."""
    tracker = ShadowTracker(eval_window=10, promote_after=2, min_improvement=0.0)

    # Window 1: Signal B would have helped (predict correct where ensemble didn't)
    for _ in range(5):
        tracker.record(signal_b_pred=0, ensemble_pred=1, gold_tier=0)  # B right, ensemble wrong
    for _ in range(5):
        tracker.record(signal_b_pred=1, ensemble_pred=1, gold_tier=1)  # both right

    # Window 2: Signal B would have helped again
    for _ in range(5):
        tracker.record(signal_b_pred=0, ensemble_pred=1, gold_tier=0)
    for _ in range(5):
        tracker.record(signal_b_pred=1, ensemble_pred=1, gold_tier=1)

    assert tracker.promoted


def test_no_promotion_when_b_hurts():
    """Signal B NOT promoted when it would have hurt accuracy."""
    tracker = ShadowTracker(eval_window=10, promote_after=2, min_improvement=0.005)

    # Window: Signal B is WRONG more often than ensemble
    for _ in range(5):
        tracker.record(signal_b_pred=3, ensemble_pred=0, gold_tier=0)  # B wrong, ensemble right
    for _ in range(5):
        tracker.record(signal_b_pred=1, ensemble_pred=1, gold_tier=1)  # both right

    assert not tracker.promoted
    assert tracker.consecutive_wins == 0


def test_streak_resets_on_bad_window():
    tracker = ShadowTracker(eval_window=10, promote_after=3, min_improvement=0.0)

    # Good window
    for _ in range(5):
        tracker.record(signal_b_pred=0, ensemble_pred=1, gold_tier=0)
    for _ in range(5):
        tracker.record(signal_b_pred=1, ensemble_pred=1, gold_tier=1)
    assert tracker.consecutive_wins == 1

    # Bad window — B hurts
    for _ in range(5):
        tracker.record(signal_b_pred=3, ensemble_pred=0, gold_tier=0)
    for _ in range(5):
        tracker.record(signal_b_pred=0, ensemble_pred=0, gold_tier=0)
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
        tracker.record(signal_b_pred=0, ensemble_pred=1, gold_tier=None)
    assert not tracker.promoted
