"""Weighted ensemble over v2 signals with risk_tolerance control."""

from __future__ import annotations

from dataclasses import dataclass

from uncommon_route.signals.base import TierVote


@dataclass(frozen=True, slots=True)
class EnsembleResult:
    tier_id: int | None
    confidence: float
    method: str  # "direct" | "conservative" | "abstain"
    tier_scores: list[float]


class Ensemble:
    def __init__(
        self,
        weights: list[float],
        risk_tolerance: float = 0.5,
        direct_threshold: float = 0.55,
    ):
        self._weights = weights
        self._risk_tolerance = risk_tolerance
        self._threshold = direct_threshold + (0.5 - risk_tolerance) * 0.3

    def decide(self, votes: list[TierVote]) -> EnsembleResult:
        if len(votes) != len(self._weights):
            raise ValueError(f"Expected {len(self._weights)} votes, got {len(votes)}")
        tier_scores = [0.0, 0.0, 0.0, 0.0]
        total_weight = 0.0

        for vote, weight in zip(votes, self._weights):
            if vote.tier_id is None:
                continue
            w = vote.confidence * weight
            tier_scores[vote.tier_id] += w
            total_weight += w

        if total_weight == 0:
            return EnsembleResult(tier_id=None, confidence=0.0, method="abstain", tier_scores=tier_scores)

        normalized = [s / total_weight for s in tier_scores]
        best_tier = max(range(4), key=lambda i: normalized[i])
        confidence = normalized[best_tier]

        if confidence >= self._threshold:
            return EnsembleResult(tier_id=best_tier, confidence=confidence, method="direct", tier_scores=normalized)

        safe_tier = min(best_tier + 1, 3)
        return EnsembleResult(tier_id=safe_tier, confidence=confidence, method="conservative", tier_scores=normalized)
