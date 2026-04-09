"""Shadow mode for Signal B: runs but doesn't vote, tracks counterfactual performance.

Signal B runs on every request and its prediction is recorded alongside the
individual signal votes and the routing outcome. Every `eval_window` requests
(non-overlapping batches), we replay the 3-signal weighted ensemble to compute
whether including Signal B would have actually changed and improved the result.
After `promote_after` consecutive positive windows, Signal B is auto-promoted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("uncommon-route.shadow")


@dataclass
class ShadowRecord:
    signal_a_pred: int | None
    signal_a_conf: float
    signal_b_pred: int | None
    signal_b_conf: float
    signal_c_pred: int | None
    signal_c_conf: float
    ensemble_2sig_tier: int
    gold_tier: int | None  # None if unknown (production)


def _simulate_3sig_tier(
    rec: ShadowRecord,
    weights_2sig: tuple[float, float] = (0.55, 0.45),
    weights_3sig: tuple[float, float, float] = (0.50, 0.10, 0.40),
    threshold: float = 0.55,
) -> int:
    """Replay the 3-signal weighted ensemble to get counterfactual tier."""
    tier_scores = [0.0, 0.0, 0.0, 0.0]
    total = 0.0
    for pred, conf, w in [
        (rec.signal_a_pred, rec.signal_a_conf, weights_3sig[0]),
        (rec.signal_b_pred, rec.signal_b_conf, weights_3sig[1]),
        (rec.signal_c_pred, rec.signal_c_conf, weights_3sig[2]),
    ]:
        if pred is not None:
            wt = conf * w
            tier_scores[pred] += wt
            total += wt
    if total == 0:
        return 1  # default mid
    normalized = [s / total for s in tier_scores]
    best = max(range(4), key=lambda i: normalized[i])
    if normalized[best] >= threshold:
        return best
    return min(best + 1, 3)


class ShadowTracker:
    """Track Signal B's shadow performance and decide when to promote it."""

    def __init__(
        self,
        eval_window: int = 200,
        promote_after: int = 3,
        min_improvement: float = 0.005,
    ):
        self._eval_window = eval_window
        self._promote_after = promote_after
        self._min_improvement = min_improvement
        self._records: list[ShadowRecord] = []
        self._pending: list[ShadowRecord] = []  # accumulates until eval_window
        self._consecutive_wins = 0
        self._promoted = False

    @property
    def promoted(self) -> bool:
        return self._promoted

    @property
    def record_count(self) -> int:
        return len(self._pending)

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    def record(
        self,
        signal_a_pred: int | None, signal_a_conf: float,
        signal_b_pred: int | None, signal_b_conf: float,
        signal_c_pred: int | None, signal_c_conf: float,
        ensemble_2sig_tier: int,
        gold_tier: int | None = None,
    ) -> None:
        self._pending.append(ShadowRecord(
            signal_a_pred=signal_a_pred, signal_a_conf=signal_a_conf,
            signal_b_pred=signal_b_pred, signal_b_conf=signal_b_conf,
            signal_c_pred=signal_c_pred, signal_c_conf=signal_c_conf,
            ensemble_2sig_tier=ensemble_2sig_tier,
            gold_tier=gold_tier,
        ))
        # Non-overlapping batches: evaluate only when we hit exactly eval_window
        if len(self._pending) >= self._eval_window:
            self._evaluate_window()

    def _evaluate_window(self) -> None:
        """Replay 3-signal ensemble on the batch and compare to 2-signal outcome."""
        batch = self._pending[:self._eval_window]
        self._pending = self._pending[self._eval_window:]  # drain the batch

        labeled = [r for r in batch if r.gold_tier is not None]
        if len(labeled) < 10:
            return

        # Compare 2-signal vs replayed 3-signal
        correct_2sig = 0
        correct_3sig = 0
        for r in labeled:
            tier_3sig = _simulate_3sig_tier(r)
            if r.ensemble_2sig_tier == r.gold_tier:
                correct_2sig += 1
            if tier_3sig == r.gold_tier:
                correct_3sig += 1

        improvement = (correct_3sig - correct_2sig) / len(labeled)

        if improvement >= self._min_improvement:
            self._consecutive_wins += 1
            logger.info(
                f"Shadow eval: 3-signal would improve by {improvement:.3f} "
                f"({correct_3sig}/{len(labeled)} vs {correct_2sig}/{len(labeled)}). "
                f"Streak: {self._consecutive_wins}/{self._promote_after}"
            )
        else:
            self._consecutive_wins = 0
            logger.info(
                f"Shadow eval: 3-signal no improvement ({improvement:.3f}). Streak reset."
            )

        if self._consecutive_wins >= self._promote_after:
            self._promoted = True
            logger.info("Shadow mode: Signal B PROMOTED to active participation.")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "consecutive_wins": self._consecutive_wins,
                "promoted": self._promoted,
                "version": 1,
            }, f)

    @classmethod
    def load(cls, path: Path, **kwargs) -> "ShadowTracker":
        tracker = cls(**kwargs)
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            tracker._consecutive_wins = data.get("consecutive_wins", 0)
            tracker._promoted = data.get("promoted", False)
        return tracker
