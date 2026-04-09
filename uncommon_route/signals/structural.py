"""Signal B: structural text classifier (wraps v1's ScriptAgnosticClassifier).

Classifies the last user message using structural features, Unicode block
proportions, and character n-grams. Maps v1's 3-tier output to v2's 4-tier
system using the complexity score for mid_high distinction.
"""

from __future__ import annotations

from typing import Any

from uncommon_route.signals.base import TierVote
from uncommon_route.signals.embedding import _extract_last_user_message
from uncommon_route.router.classifier import classify
from uncommon_route.router.types import Tier


def _map_v1_to_v2_tier_id(tier: Tier | None, complexity: float) -> int | None:
    if tier is None:
        return None
    if tier is Tier.SIMPLE:
        return 0  # low
    if tier is Tier.MEDIUM:
        return 1  # mid
    # COMPLEX → split into mid_high vs high based on complexity
    if complexity >= 0.80:
        return 3  # high
    return 2  # mid_high


class StructuralSignal:
    """Predict tier from text structure using v1's learned classifier."""

    def predict(self, row: dict[str, Any]) -> TierVote:
        messages = row.get("messages", [])
        text = _extract_last_user_message(messages)
        if not text.strip():
            return TierVote(tier_id=None, confidence=0.0)

        result = classify(text)
        tier_id = _map_v1_to_v2_tier_id(result.tier, result.complexity)

        if tier_id is None:
            return TierVote(tier_id=None, confidence=max(0.0, min(1.0, result.confidence)))

        return TierVote(
            tier_id=tier_id,
            confidence=max(0.0, min(1.0, result.confidence)),
        )
