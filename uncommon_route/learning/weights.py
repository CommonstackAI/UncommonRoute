"""Signal weight tracker with decaying learning rate and abstention exclusion."""

from __future__ import annotations

import json
from pathlib import Path


class SignalWeightTracker:
    def __init__(self, initial_weights: list[float]) -> None:
        self._weights = list(initial_weights)
        self._update_count = 0

    @property
    def weights(self) -> list[float]:
        return list(self._weights)

    def update(
        self,
        predictions: list[int | None],
        abstained: list[bool],
        actual_tier: int,
    ) -> None:
        lr = max(0.01, 0.1 / (1 + self._update_count * 0.001))
        self._update_count += 1
        for i in range(len(self._weights)):
            if abstained[i]:
                continue
            if predictions[i] == actual_tier:
                self._weights[i] *= (1 + lr)
            else:
                self._weights[i] *= (1 - lr)
        self._normalize()

    def _normalize(self) -> None:
        total = sum(self._weights)
        if total > 0:
            self._weights = [w / total for w in self._weights]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"weights": self._weights, "update_count": self._update_count, "version": 1}, f)

    @classmethod
    def load(cls, path: Path) -> "SignalWeightTracker":
        with open(path) as f:
            data = json.load(f)
        tracker = cls(initial_weights=data["weights"])
        tracker._update_count = data["update_count"]
        return tracker
