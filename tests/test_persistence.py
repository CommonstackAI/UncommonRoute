import json
from pathlib import Path
from uncommon_route.persistence import LearnedState, save_state, load_state, reset_state


def test_save_load_roundtrip(tmp_path):
    state = LearnedState(
        signal_weights=[0.55, 0.45],
        calibration_temperature=0.75,
        shadow_consecutive_wins=1,
        shadow_promoted=False,
        embedding_index_size=487,
        model_priors={"deepseek": {"alpha": 5, "beta": 2}},
    )
    save_state(state, tmp_path)
    loaded = load_state(tmp_path)
    assert loaded.signal_weights == [0.55, 0.45]
    assert loaded.calibration_temperature == 0.75
    assert loaded.shadow_consecutive_wins == 1
    assert not loaded.shadow_promoted
    assert loaded.embedding_index_size == 487
    assert loaded.model_priors["deepseek"]["alpha"] == 5


def test_load_missing_dir_returns_defaults(tmp_path):
    empty = tmp_path / "nonexistent"
    state = load_state(empty)
    assert state.signal_weights == [0.55, 0.45]
    assert state.calibration_temperature == 1.0
    assert state.embedding_index_size == 0
    assert state.model_priors == {}


def test_schema_version_stored(tmp_path):
    state = LearnedState()
    save_state(state, tmp_path)
    with open(tmp_path / "learned_state.json") as f:
        data = json.load(f)
    assert data["schema_version"] == 1


def test_corrupt_file_returns_defaults(tmp_path):
    (tmp_path / "learned_state.json").write_text("NOT JSON")
    state = load_state(tmp_path)
    assert state.signal_weights == [0.55, 0.45]


def test_reset_state(tmp_path):
    state = LearnedState(signal_weights=[0.1, 0.9], shadow_promoted=True)
    save_state(state, tmp_path)
    reset_state(tmp_path)
    loaded = load_state(tmp_path)
    assert loaded.signal_weights == [0.55, 0.45]
    assert not loaded.shadow_promoted
