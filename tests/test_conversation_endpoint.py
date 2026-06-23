"""End-to-end tests for the /v1/sessions/{id}/conversation endpoint."""

from __future__ import annotations

from pathlib import Path

from uncommon_route.traces import (
    FileTraceStorage,
    RequestTrace,
    TraceStore,
)


def _make_store(tmp_path: Path) -> TraceStore:
    storage = FileTraceStorage(base_dir=tmp_path / "traces")
    return TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1_000_000.0)


def _record_turn(
    store: TraceStore,
    *,
    ts: float,
    request_id: str,
    session_id: str,
    request_messages: list[dict],
    response_text: str,
    response_tool_calls: list[dict] | None = None,
    msg_hashes: list[str] | None = None,
    decision_tier: str = "MEDIUM",
) -> None:
    store.record(
        RequestTrace(
            timestamp=ts,
            request_id=request_id,
            model="x",
            status_code=200,
            session_id=session_id,
            decision_tier=decision_tier,
            request_messages=request_messages,
            response_text=response_text,
            response_tool_calls=response_tool_calls,
            msg_hashes=msg_hashes or [],
        )
    )


def test_assemble_two_turn_conversation(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    _record_turn(
        store,
        ts=1.0,
        request_id="t1",
        session_id="s1",
        request_messages=[{"role": "user", "content": "hello"}],
        response_text="hi there",
        msg_hashes=["h_user"],
    )
    _record_turn(
        store,
        ts=2.0,
        request_id="t2",
        session_id="s1",
        request_messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "how are you"},
        ],
        response_text="great thanks",
        msg_hashes=["h_user", "h_asst1", "h_user2"],
    )

    out = _assemble_conversation(store, "s1")
    assert out is not None
    assert out["session_id"] == "s1"
    assert out["turn_count"] == 2
    assert out["content_available"] is True
    assert out["compact_breaks"] == []
    roles = [m["role"] for m in out["messages"]]
    assert roles[0] == "user"
    assert "assistant" in roles
    # Last message is the second turn's response.
    assert out["messages"][-1]["role"] == "assistant"
    assert out["messages"][-1]["text"] == "great thanks"


def test_compact_break_detected(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    _record_turn(
        store,
        ts=1.0,
        request_id="t1",
        session_id="s2",
        request_messages=[{"role": "user", "content": "old1"}],
        response_text="reply1",
        msg_hashes=["a", "b"],
    )
    _record_turn(
        store,
        ts=2.0,
        request_id="t2",
        session_id="s2",
        request_messages=[{"role": "user", "content": "compacted summary"}],
        response_text="reply2",
        msg_hashes=["c"],  # NOT a strict extension of [a,b]
    )
    out = _assemble_conversation(store, "s2")
    assert out["compact_breaks"] == [1]


def test_no_session_returns_none(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    assert _assemble_conversation(store, "nonexistent") is None


def test_pre_capture_assistants_have_no_decision_card(tmp_path: Path) -> None:
    """When the proxy starts mid-conversation, the LAST turn's backbone
    contains assistant messages from before our capture window. Those
    assistant messages must be surfaced (so the chat reads continuously)
    but with decision=None — only captured turns get decision cards, and
    the final response is appended after the backbone."""
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    # Captured 1 turn whose backbone reflects 3 prior assistant rounds
    # (3 pre-capture assistants the proxy never saw).
    _record_turn(
        store,
        ts=10.0,
        request_id="captured1",
        session_id="s_split",
        request_messages=[
            {"role": "user", "content": "first message (pre-capture)"},
            {"role": "assistant", "content": "pre-capture reply 1"},
            {"role": "user", "content": "follow-up 1"},
            {"role": "assistant", "content": "pre-capture reply 2"},
            {"role": "user", "content": "follow-up 2"},
            {"role": "assistant", "content": "pre-capture reply 3"},
            {"role": "user", "content": "current question"},
        ],
        response_text="captured final answer",
        msg_hashes=["h1", "h2", "h3", "h4", "h5", "h6", "h7"],
    )

    out = _assemble_conversation(store, "s_split")
    # 4 user msgs + 3 pre-capture assistants + 1 captured-as-response assistant = 8.
    assistants = [m for m in out["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 4
    # First three assistants (pre-capture in backbone) have no decision.
    for a in assistants[:3]:
        assert a["decision"] is None
        assert a["request_id"] is None
    # The final assistant (response of the captured turn) has its decision.
    final = assistants[-1]
    assert final["decision"] is not None
    assert final["request_id"] == "captured1"
    assert final["text"] == "captured final answer"


def test_content_unavailable_when_capture_was_off(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    store.record(
        RequestTrace(
            timestamp=1.0,
            request_id="t1",
            model="x",
            status_code=200,
            session_id="s3",
            request_messages=None,
            response_text="",
            msg_hashes=[],
        )
    )
    out = _assemble_conversation(store, "s3")
    assert out["content_available"] is False
    assert out["turn_count"] == 1
    # Still surfaces decisions.
    assert "messages" in out
