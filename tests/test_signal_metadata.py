"""Tests for Signal A: metadata-based tier heuristics (no benchmark labels)."""

from uncommon_route.signals.metadata import MetadataSignal


def _make_row(messages=None, step_index=1, total_steps=1):
    """Helper — NO benchmark or scenario fields (production-like)."""
    if messages is None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Fix this bug."},
        ]
    return {
        "messages": messages,
        "step_index": step_index,
        "total_steps": total_steps,
    }


def test_short_no_tools_predicts_low():
    sig = MetadataSignal()
    vote = sig.predict(_make_row(messages=[{"role": "user", "content": "hello"}]))
    assert vote.tier_id == 0
    assert vote.confidence >= 0.5


def test_medium_no_tools_predicts_low():
    msgs = [
        {"role": "user", "content": "What is X?"},
        {"role": "assistant", "content": "X is..."},
        {"role": "user", "content": "And Y?"},
        {"role": "assistant", "content": "Y is..."},
        {"role": "user", "content": "Thanks"},
        {"role": "assistant", "content": "You're welcome"},
    ]
    sig = MetadataSignal()
    vote = sig.predict(_make_row(messages=msgs))
    assert vote.tier_id == 0


def test_heavy_tools_predicts_high_with_damped_confidence():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Fix the bug."},
        {"role": "assistant", "content": "Let me check.", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "file content", "tool_call_id": "1"},
        {"role": "assistant", "content": "I see.", "tool_calls": [{"id": "2"}]},
        {"role": "tool", "content": "more content", "tool_call_id": "2"},
        {"role": "assistant", "content": "Found it.", "tool_calls": [{"id": "3"}]},
        {"role": "tool", "content": "result", "tool_call_id": "3"},
        {"role": "assistant", "content": "Now fixing.", "tool_calls": [{"id": "4"}]},
        {"role": "tool", "content": "done", "tool_call_id": "4"},
        {"role": "user", "content": "Great, now test it."},
    ]
    sig = MetadataSignal()
    vote = sig.predict(_make_row(messages=msgs))
    assert vote.tier_id == 3
    assert 0.0 < vote.confidence <= 0.3


def test_light_tools_predicts_mid():
    msgs = [
        {"role": "user", "content": "Read the file"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "content": "content", "tool_call_id": "t1"},
    ]
    sig = MetadataSignal()
    vote = sig.predict(_make_row(messages=msgs))
    assert 1 <= vote.tier_id <= 2


def test_no_benchmark_field_works():
    sig = MetadataSignal()
    row = {"messages": [{"role": "user", "content": "hello"}]}
    vote = sig.predict(row)
    assert vote.tier_id is not None
    assert 0.0 <= vote.confidence <= 1.0


def test_empty_messages():
    sig = MetadataSignal()
    vote = sig.predict({"messages": []})
    assert vote.tier_id == 0
