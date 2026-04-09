"""Shadow mode for Signal B: runs but doesn't vote, tracks counterfactual performance.

Signal B runs on every request and its prediction is recorded alongside the actual
routing outcome. Every `eval_window` requests, we compute whether including Signal B
would have improved accuracy. After `promote_after` consecutive positive windows,
Signal B is auto-promoted to active participation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("uncommon-route.shadow")


@dataclass
class ShadowRecord:
    signal_b_prediction: int | None
    ensemble_prediction: int
    gold_tier: int | None  # None if unknown (production, no label)


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
        self._consecutive_wins = 0
        self._promoted = False

    @property
    def promoted(self) -> bool:
        return self._promoted

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def consecutive_wins(self) -> int:
        return self._consecutive_wins

    def record(self, signal_b_pred: int | None, ensemble_pred: int, gold_tier: int | None = None) -> None:
        self._records.append(ShadowRecord(
            signal_b_prediction=signal_b_pred,
            ensemble_prediction=ensemble_pred,
            gold_tier=gold_tier,
        ))
        if len(self._records) >= self._eval_window:
            self._evaluate_window()

    def _evaluate_window(self) -> None:
        """Check if Signal B would have helped in the current window."""
        window = self._records[-self._eval_window:]
        labeled = [r for r in window if r.gold_tier is not None]
        if len(labeled) < 10:
            # Not enough labeled data to evaluate
            self._records = self._records[-self._eval_window:]
            return

        # Current accuracy (without Signal B)
        current_correct = sum(1 for r in labeled if r.ensemble_prediction == r.gold_tier)
        current_acc = current_correct / len(labeled)

        # Counterfactual: in cases where A and C disagreed with B,
        # would B's vote have been correct more often?
        b_would_help = 0
        b_would_hurt = 0
        for r in labeled:
            if r.signal_b_prediction is None:
                continue
            if r.signal_b_prediction == r.ensemble_prediction:
                continue  # B agrees, no impact
            if r.signal_b_prediction == r.gold_tier:
                b_would_help += 1
            elif r.ensemble_prediction == r.gold_tier:
                b_would_hurt += 1

        net_benefit = b_would_help - b_would_hurt
        improvement = net_benefit / len(labeled) if labeled else 0.0

        if improvement >= self._min_improvement:
            self._consecutive_wins += 1
            logger.info(
                f"Shadow eval: Signal B wins ({b_would_help} help vs {b_would_hurt} hurt, "
                f"improvement={improvement:.3f}). Streak: {self._consecutive_wins}/{self._promote_after}"
            )
        else:
            self._consecutive_wins = 0
            logger.info(
                f"Shadow eval: Signal B neutral/negative ({b_would_help} help vs {b_would_hurt} hurt, "
                f"improvement={improvement:.3f}). Streak reset."
            )

        if self._consecutive_wins >= self._promote_after:
            self._promoted = True
            logger.info("Shadow mode: Signal B PROMOTED to active participation.")

        # Keep only last window to bound memory
        self._records = self._records[-self._eval_window:]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "consecutive_wins": self._consecutive_wins,
                "promoted": self._promoted,
                "record_count": self.record_count,
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
