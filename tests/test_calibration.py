import json
from pathlib import Path

from uncommon_route.decision.calibration import (
    PlattCalibrator,
    fit_platt_from_evals,
    load_calibrator,
    save_calibrator,
)


def test_platt_calibrator_identity():
    cal = PlattCalibrator(temperature=1.0)
    assert abs(cal.calibrate(0.5) - 0.5) < 0.01
    assert abs(cal.calibrate(0.9) - 0.9) < 0.05


def test_platt_calibrator_high_temp_compresses():
    cal = PlattCalibrator(temperature=2.0)
    assert cal.calibrate(0.9) < 0.9
    assert cal.calibrate(0.1) > 0.1


def test_fit_platt_from_evals():
    evals = [
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.8, "correct": True},
        {"confidence": 0.7, "correct": False},
        {"confidence": 0.3, "correct": False},
        {"confidence": 0.2, "correct": False},
    ]
    cal = fit_platt_from_evals(evals)
    assert isinstance(cal, PlattCalibrator)
    assert 0.5 <= cal.temperature <= 3.0


def test_save_load_calibrator(tmp_path):
    cal = PlattCalibrator(temperature=1.5)
    path = tmp_path / "calibration.json"
    save_calibrator(cal, path)
    loaded = load_calibrator(path)
    assert abs(loaded.temperature - 1.5) < 0.001
