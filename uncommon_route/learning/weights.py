"""Signal weight tracker with decaying learning rate and abstention exclusion.

Persistence is handled by v2_lifecycle via LearnedState, not per-tracker files.
"""

from __future__ import annotations


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
