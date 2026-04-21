"""Signal A: metadata-based tier heuristics.

Uses ONLY features available in production — NO benchmark labels.
Key features: message_count, has_tool_calls, tool_message_count, step_ratio.

Data analysis (LLMRouterBench):
  has_tool_calls:  low=16%, mid=62%, mid_high=80%, high=98%
  message_count:   low=7.3,  mid=8.6, mid_high=12.6, high=12.1
  step_index mean: low=1.5,  mid=3.2, mid_high=5.5,  high=5.2
"""

from __future__ import annotations

from typing import Any

from uncommon_route.signals.base import TierVote


def _has_tool_calls(messages: list[dict[str, Any]]) -> bool:
    return any(
        m.get("role") == "tool" or m.get("tool_calls")
        for m in messages
    )


def _count_tool_related_messages(messages: list[dict[str, Any]]) -> int:
    """Count messages related to tool usage.

    Counts both tool_calls (assistant messages invoking tools) and tool responses
    (role='tool'), so a single tool invocation contributes 2 to the count.
    """
    return sum(1 for m in messages if m.get("role") == "tool" or m.get("tool_calls"))


class MetadataSignal:
    """Predict tier from conversation metadata — NO benchmark labels used.

    Uses only features available in production: message structure,
    tool usage, and conversation depth.
    """

    def predict(self, row: dict[str, Any]) -> TierVote:
        messages = row.get("messages", [])
        msg_count = len(messages)
        has_tools = _has_tool_calls(messages)
        tool_msg_count = _count_tool_related_messages(messages)

        # ─── Production-only heuristics ───
        # Uses ONLY: msg_count, has_tools, tool_msg_count
        # NO benchmark, scenario, step_index, or total_steps

        # Strongest signal: tool usage (16% low vs 98% high)
        if not has_tools:
            if msg_count <= 3:
                return TierVote(tier_id=0, confidence=0.75)
            if msg_count <= 6:
                return TierVote(tier_id=0, confidence=0.65)
            return TierVote(tier_id=0, confidence=0.55)

        # Has tools — differentiate by depth.
        # In multi-step agent workflows, tool messages accumulate from prior
        # steps but the CURRENT step may be simple. The global "has_tools →
        # tier 3" prior breaks here — per-step difficulty is not correlated
        # with tool-call count. Dampen hard so the embedding signal (which
        # reads the actual last-user content) can dominate.
        multi_step_workflow = msg_count > 6 and tool_msg_count > 4
        conf_cap = 0.30 if multi_step_workflow else 1.0

        if tool_msg_count >= 8 and msg_count > 10:
            return TierVote(tier_id=3, confidence=min(0.75, conf_cap))

        if tool_msg_count >= 4 and msg_count > 8:
            return TierVote(tier_id=3, confidence=min(0.65, conf_cap))

        if tool_msg_count >= 4:
            return TierVote(tier_id=2, confidence=min(0.55, conf_cap))

        if msg_count > 6:
            return TierVote(tier_id=2, confidence=min(0.50, conf_cap))

        # Light tools, short conversation → mid
        return TierVote(tier_id=1, confidence=0.50)
