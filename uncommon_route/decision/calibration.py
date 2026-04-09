"""Platt/temperature scaling for ensemble confidence calibration."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PlattCalibrator:
    temperature: float = 1.0

    def calibrate(self, raw_confidence: float) -> float:
        safe = max(1e-6, min(1.0 - 1e-6, raw_confidence))
        logit = math.log(safe / (1.0 - safe))
        scaled = logit / max(0.05, self.temperature)
        return 1.0 / (1.0 + math.exp(-scaled))


def fit_platt_from_evals(
    evals: list[dict],
    min_temperature: float = 0.5,
    max_temperature: float = 3.0,
    step: float = 0.05,
) -> PlattCalibrator:
    best_temp = 1.0
    best_ece = float("inf")
    temp = min_temperature
    while temp <= max_temperature + 1e-9:
        ece = _compute_ece(evals, temp)
        if ece < best_ece:
            best_ece = ece
            best_temp = round(temp, 4)
        temp += step
    return PlattCalibrator(temperature=best_temp)


def _compute_ece(evals: list[dict], temperature: float, buckets: int = 10) -> float:
    if not evals:
        return 0.0
    cal = PlattCalibrator(temperature=temperature)
    bucket_data: list[list[tuple[float, float]]] = [[] for _ in range(buckets)]
    for item in evals:
        conf = cal.calibrate(item["confidence"])
        correct = 1.0 if item["correct"] else 0.0
        idx = min(int(conf * buckets), buckets - 1)
        bucket_data[idx].append((conf, correct))
    ece = 0.0
    total = len(evals)
    for bucket in bucket_data:
        if not bucket:
            continue
        avg_conf = sum(c for c, _ in bucket) / len(bucket)
        avg_acc = sum(a for _, a in bucket) / len(bucket)
        ece += abs(avg_conf - avg_acc) * len(bucket) / total
    return ece


def save_calibrator(cal: PlattCalibrator, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"temperature": cal.temperature, "version": 1}, f)


def load_calibrator(path: Path) -> PlattCalibrator:
    with open(path) as f:
        data = json.load(f)
    return PlattCalibrator(temperature=data["temperature"])
