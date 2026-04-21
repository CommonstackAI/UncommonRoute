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


def _extract_latest_system_message(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "system":
            continue
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            return content
    return None


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
        system_prompt = _extract_latest_system_message(messages)
        if not text.strip():
            return TierVote(tier_id=None, confidence=0.0)

        result = classify(text, system_prompt=system_prompt)
        tier_id = _map_v1_to_v2_tier_id(result.tier, result.complexity)
        confidence = max(0.0, min(1.0, result.confidence))

        # Dampen confidence for very short prompts — structural features
        # are unreliable when there's little text to analyze.
        # Use word count for Latin scripts, character count for CJK.
        length_text = text if not system_prompt else f"{system_prompt}\n\n{text}"
        word_count = len(length_text.split())
        if word_count <= 2 and len(length_text) > 15:
            # CJK or no-space script: use character length instead
            word_count = len(length_text) // 3  # rough CJK word estimate
        if word_count <= 8:
            confidence = min(confidence, 0.50)
        elif word_count <= 15:
            confidence = min(confidence, 0.70)

        if tier_id is None:
            return TierVote(tier_id=None, confidence=confidence)

        return TierVote(tier_id=tier_id, confidence=confidence)
