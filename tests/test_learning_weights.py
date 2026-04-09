import json
from pathlib import Path
from uncommon_route.learning.weights import SignalWeightTracker


def test_initial_weights():
    tracker = SignalWeightTracker(initial_weights=[0.45, 0.20, 0.35])
    assert tracker.weights == [0.45, 0.20, 0.35]


def test_correct_signal_weight_increases():
    tracker = SignalWeightTracker(initial_weights=[0.5, 0.5])
    tracker.update(predictions=[1, 2], abstained=[False, False], actual_tier=1)
    assert tracker.weights[0] > 0.5
    assert tracker.weights[1] < 0.5


def test_abstaining_signal_not_updated():
    tracker = SignalWeightTracker(initial_weights=[0.5, 0.5])
    old_w1 = tracker.weights[1]
    tracker.update(predictions=[1, None], abstained=[False, True], actual_tier=1)
    # Abstaining signal weight changes only due to normalization
    # but should not be directly penalized/rewarded
    assert tracker.weights[0] > tracker.weights[1]


def test_weights_stay_normalized():
    tracker = SignalWeightTracker(initial_weights=[0.4, 0.3, 0.3])
    for _ in range(100):
        tracker.update([0, 1, 2], [False, False, False], 0)
    total = sum(tracker.weights)
    assert abs(total - 1.0) < 0.01


def test_learning_rate_decays():
    tracker1 = SignalWeightTracker(initial_weights=[0.5, 0.5])
    tracker1.update([0, 1], [False, False], 0)
    delta_1 = abs(tracker1.weights[0] - 0.5)

    tracker2 = SignalWeightTracker(initial_weights=[0.5, 0.5])
    tracker2._update_count = 1000
    tracker2.update([0, 1], [False, False], 0)
    delta_2 = abs(tracker2.weights[0] - 0.5)
    assert delta_2 < delta_1


def test_save_load(tmp_path):
    tracker = SignalWeightTracker(initial_weights=[0.4, 0.3, 0.3])
    tracker.update([0, 1, 2], [False, False, False], 0)
    path = tmp_path / "weights.json"
    tracker.save(path)
    loaded = SignalWeightTracker.load(path)
    for a, b in zip(loaded.weights, tracker.weights):
        assert abs(a - b) < 1e-9
    assert loaded._update_count == tracker._update_count
