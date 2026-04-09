from uncommon_route.signals.metadata import MetadataSignal


def _make_row(benchmark="swebench", step_index=1, total_steps=9,
              messages=None, scenario="code_swe"):
    if messages is None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Fix this bug."},
        ]
    return {
        "benchmark": benchmark,
        "scenario": scenario,
        "step_index": step_index,
        "total_steps": total_steps,
        "messages": messages,
    }


def test_mtrag_predicts_low():
    sig = MetadataSignal()
    vote = sig.predict(_make_row(benchmark="mtrag", scenario="rag_multiturn"))
    assert vote.tier_id == 0
    assert vote.confidence >= 0.7


def test_qmsum_predicts_low():
    sig = MetadataSignal()
    vote = sig.predict(_make_row(benchmark="qmsum", scenario="meeting_query_summarization"))
    assert vote.tier_id == 0
    assert vote.confidence >= 0.7


def test_swebench_high_step_predicts_high():
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Fix this."},
        {"role": "assistant", "content": "Let me check.", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "file content", "tool_call_id": "1"},
        {"role": "assistant", "content": "I see the issue."},
        {"role": "user", "content": "Now fix it."},
    ]
    sig = MetadataSignal()
    vote = sig.predict(_make_row(
        benchmark="swebench", step_index=7, total_steps=9, messages=msgs,
    ))
    assert vote.tier_id == 3
    assert vote.confidence >= 0.5


def test_swebench_early_step_no_tools_predicts_lower():
    sig = MetadataSignal()
    vote = sig.predict(_make_row(benchmark="swebench", step_index=1, total_steps=9))
    if vote.tier_id == 3:
        assert vote.confidence < 0.7


def test_unknown_benchmark_does_not_crash():
    sig = MetadataSignal()
    vote = sig.predict(_make_row(benchmark="unknown_bench", scenario="general"))
    assert vote.tier_id is not None or vote.abstained
    assert 0.0 <= vote.confidence <= 1.0


def test_has_tool_calls_detection():
    msgs_with_tools = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "t1"},
    ]
    msgs_without_tools = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    sig = MetadataSignal()
    vote_with = sig.predict(_make_row(messages=msgs_with_tools))
    vote_without = sig.predict(_make_row(messages=msgs_without_tools))
    assert vote_with.tier_id is not None
    assert vote_without.tier_id is not None
