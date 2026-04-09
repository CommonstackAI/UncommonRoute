"""Signal A: metadata-based tier heuristics.

Uses only request metadata (no text analysis). Works from day one with zero training.
Empirical basis: ~65% cross-validated accuracy on LLMRouterBench.
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
    """Predict tier from conversation metadata: step position, message count, tool usage."""

    def predict(self, row: dict[str, Any]) -> TierVote:
        benchmark = row.get("benchmark", "")
        scenario = row.get("scenario", "")
        step_index = row.get("step_index", 1)
        total_steps = row.get("total_steps", 1)
        messages = row.get("messages", [])
        msg_count = len(messages)
        has_tools = _has_tool_calls(messages)
        tool_msg_count = _count_tool_related_messages(messages)
        step_ratio = step_index / total_steps if total_steps > 0 else 0.0

        # --- Scenario-based prior ---
        if scenario in ("rag_multiturn",) or benchmark == "mtrag":
            return TierVote(tier_id=0, confidence=0.85)

        if scenario in ("meeting_query_summarization",) or benchmark == "qmsum":
            if msg_count > 6 and has_tools:
                return TierVote(tier_id=1, confidence=0.6)
            return TierVote(tier_id=0, confidence=0.80)

        if benchmark == "pinchbench":
            if has_tools and msg_count > 10:
                return TierVote(tier_id=1, confidence=0.55)
            return TierVote(tier_id=0, confidence=0.65)

        # --- SWE-bench and code scenarios ---
        if benchmark == "swebench" or scenario == "code_swe":
            if has_tools and step_ratio > 0.5:
                return TierVote(tier_id=3, confidence=0.75)
            if has_tools and msg_count > 8:
                return TierVote(tier_id=3, confidence=0.65)
            if has_tools and tool_msg_count >= 4:
                return TierVote(tier_id=3, confidence=0.60)
            if has_tools:
                return TierVote(tier_id=3, confidence=0.55)
            if step_index <= 2 and msg_count <= 4:
                return TierVote(tier_id=1, confidence=0.50)
            return TierVote(tier_id=3, confidence=0.50)

        # --- Unknown / general ---
        if has_tools and msg_count > 8:
            return TierVote(tier_id=2, confidence=0.45)
        if has_tools:
            return TierVote(tier_id=1, confidence=0.45)
        return TierVote(tier_id=1, confidence=0.40)
