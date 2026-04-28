"""Tests for the JSONL-based trace storage layer."""

from __future__ import annotations

import json
import time
from pathlib import Path

from uncommon_route.traces import (
    FileTraceStorage,
    InMemoryTraceStorage,
    RequestTrace,
    TraceStore,
)


def _row(ts: float, request_id: str, **extra: object) -> dict:
    base = {
        "request_id": request_id,
        "timestamp": ts,
        "model": "x",
        "session_id": "s1",
    }
    base.update(extra)
    return base


class TestFileTraceStorageAppend:
    def test_append_creates_dated_jsonl(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        # 2026-04-27 00:30:00 UTC
        storage.append(_row(1777249800.0, "req1"))
        f = tmp_path / "2026-04-27.jsonl"
        assert f.exists()
        lines = f.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["request_id"] == "req1"

    def test_append_groups_by_utc_date(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1777249800.0, "a"))  # 2026-04-27
        storage.append(_row(1777336200.0, "b"))  # 2026-04-28
        assert (tmp_path / "2026-04-27.jsonl").exists()
        assert (tmp_path / "2026-04-28.jsonl").exists()

    def test_append_preserves_order_within_day(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1.0, "a"))
        storage.append(_row(2.0, "b"))
        storage.append(_row(3.0, "c"))
        f = tmp_path / "1970-01-01.jsonl"
        ids = [json.loads(line)["request_id"] for line in f.read_text().splitlines()]
        assert ids == ["a", "b", "c"]


class TestFileTraceStorageLoadRecentDays:
    def test_returns_only_requested_days(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        # 3 days of records.
        d0 = 1777248000.0  # 2026-04-27
        d1 = d0 + 86400
        d2 = d1 + 86400
        storage.append(_row(d0, "old"))
        storage.append(_row(d1, "mid"))
        storage.append(_row(d2, "new"))
        # Asking for last 2 days as of d2 returns mid + new.
        rows = list(storage.load_recent_days(days=2, now=d2 + 100))
        ids = [r["request_id"] for r in rows]
        assert ids == ["mid", "new"]

    def test_handles_missing_directory(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "absent")
        rows = list(storage.load_recent_days(days=2, now=time.time()))
        assert rows == []


class TestFileTraceStorageLoadForRequest:
    def test_finds_specific_request(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        ts = 1777249800.0
        storage.append(_row(ts, "wanted", request_messages=[{"role": "user", "content": "hi"}]))
        storage.append(_row(ts + 1, "other"))
        row = storage.load_for_request("wanted", ts)
        assert row is not None
        assert row["request_messages"][0]["content"] == "hi"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1777249800.0, "a"))
        assert storage.load_for_request("nope", 1777249800.0) is None


class TestFileTraceStoragePurge:
    def test_purge_removes_all_files(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1.0, "a"))
        storage.append(_row(86401.0, "b"))
        storage.purge()
        # Directory may still exist but should be empty of jsonl.
        leftovers = list(tmp_path.glob("*.jsonl"))
        assert leftovers == []


class TestInMemoryTraceStorage:
    def test_append_and_load(self) -> None:
        storage = InMemoryTraceStorage()
        storage.append(_row(1.0, "a"))
        storage.append(_row(2.0, "b"))
        rows = list(storage.load_recent_days(days=99, now=time.time()))
        ids = [r["request_id"] for r in rows]
        assert ids == ["a", "b"]

    def test_load_for_request_in_memory(self) -> None:
        storage = InMemoryTraceStorage()
        storage.append(_row(1.0, "a", request_messages=[{"role": "user", "content": "x"}]))
        row = storage.load_for_request("a", 1.0)
        assert row is not None
        assert row["request_messages"][0]["content"] == "x"


class TestRequestTraceNewFields:
    def test_v2_fields_have_defaults(self) -> None:
        t = RequestTrace(timestamp=1.0, request_id="r", model="m", status_code=200)
        assert t.messages_count == 0
        assert t.msg_hashes is None
        assert t.first_user_hash_v2 == ""
        assert t.system_hash == ""
        assert t.metadata_user_id == ""
        assert t.previous_response_id == ""
        assert t.user_agent == ""
        assert t.session_id_v2 == ""

    def test_content_fields_have_defaults(self) -> None:
        t = RequestTrace(timestamp=1.0, request_id="r", model="m", status_code=200)
        assert t.request_messages is None
        assert t.request_system == ""
        assert t.request_tools_count == 0
        assert t.response_text == ""
        assert t.response_tool_calls is None
        assert t.response_finish_reason == ""
        assert t.content_truncated is False


class TestTraceStoreHotColdSplit:
    def test_cold_fields_excluded_from_in_memory(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store = TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1000.0)
        store.record(
            RequestTrace(
                timestamp=999.0,
                request_id="r1",
                model="m",
                status_code=200,
                request_messages=[{"role": "user", "content": "hi"}],
                response_text="hello back",
            )
        )
        recent = store.recent(limit=10)
        assert len(recent) == 1
        # In-memory record's cold fields are empty after record() drops them.
        # (recent() reads from in-memory _records, not disk.)
        assert recent[0]["response_text"] == ""
        assert recent[0]["request_messages"] is None

    def test_load_content_returns_cold_fields(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store = TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1000.0)
        store.record(
            RequestTrace(
                timestamp=999.0,
                request_id="r1",
                model="m",
                status_code=200,
                request_messages=[{"role": "user", "content": "hi"}],
                response_text="hello back",
            )
        )
        cold = store.load_content("r1")
        assert cold is not None
        assert cold["request_messages"] == [{"role": "user", "content": "hi"}]
        assert cold["response_text"] == "hello back"


class TestTraceStoreFeedbackEvents:
    def test_feedback_event_overlays_trace_on_load(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store1 = TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1000.0)
        store1.record(
            RequestTrace(
                timestamp=999.0, request_id="r1", model="m", status_code=200
            )
        )
        ok = store1.record_feedback(
            "r1",
            signal="rerun_simple",
            ok=True,
            action="downgrade",
            from_tier="MEDIUM",
            to_tier="SIMPLE",
            reason="user-corrected",
        )
        assert ok is True
        # Re-load fresh.
        store2 = TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1000.0)
        recent = store2.recent(limit=10)
        assert len(recent) == 1
        assert recent[0]["feedback_signal"] == "rerun_simple"
        assert recent[0]["feedback_action"] == "downgrade"


def test_extract_session_v2_inputs_basic_request(monkeypatch) -> None:
    from starlette.requests import Request
    from uncommon_route.proxy import _extract_session_v2_inputs

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"user-agent", b"claude-code/1.0")],
        "path": "/v1/messages",
    }
    req = Request(scope=scope)
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {"user_id": "u_x"},
    }
    out = _extract_session_v2_inputs(req, body)
    assert out["messages_count"] == 1
    assert len(out["msg_hashes"]) == 1
    assert len(out["msg_hashes"][0]) == 16
    assert len(out["first_user_hash_v2"]) == 16
    assert out["metadata_user_id"] == "u_x"
    assert out["user_agent"] == "claude-code/1.0"
    assert len(out["session_id_v2"]) == 8


def test_extract_session_v2_inputs_uses_anthropic_metadata() -> None:
    from starlette.requests import Request
    from uncommon_route.proxy import _extract_session_v2_inputs

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"user-agent", b"claude-code/1.0")],
        "path": "/v1/messages",
    }
    req = Request(scope=scope)
    # Simulating the original Anthropic body — has metadata.user_id and a system list.
    anthropic_body = {
        "messages": [{"role": "user", "content": "hi"}],
        "system": [{"type": "text", "text": "you are a helper"}],
        "metadata": {"user_id": "user_anthropic_xyz"},
    }
    out = _extract_session_v2_inputs(req, anthropic_body)
    assert out["metadata_user_id"] == "user_anthropic_xyz"
    assert len(out["system_hash"]) == 16  # system list flattened then hashed
    assert out["previous_response_id"] == ""


def test_extract_session_v2_inputs_uses_responses_previous_id() -> None:
    from starlette.requests import Request
    from uncommon_route.proxy import _extract_session_v2_inputs

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [],
        "path": "/v1/responses",
    }
    req = Request(scope=scope)
    responses_body = {
        "messages": [{"role": "user", "content": "hi"}],
        "previous_response_id": "resp_abc123",
    }
    out = _extract_session_v2_inputs(req, responses_body)
    assert out["previous_response_id"] == "resp_abc123"


def test_capture_off_returns_empty_dict(monkeypatch) -> None:
    from uncommon_route.proxy import _capture_non_streaming

    monkeypatch.delenv("UNCOMMON_ROUTE_CAPTURE_CONTENT", raising=False)
    out = _capture_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        b'{"content":[{"type":"text","text":"hi back"}]}',
        "anthropic-messages",
    )
    assert out == {}


def test_capture_on_populates_cold_fields(monkeypatch) -> None:
    from uncommon_route.proxy import _capture_non_streaming

    monkeypatch.setenv("UNCOMMON_ROUTE_CAPTURE_CONTENT", "1")
    out = _capture_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}], "system": "s"},
        b'{"content":[{"type":"text","text":"hi back"}],"stop_reason":"end_turn"}',
        "anthropic-messages",
    )
    assert out["response_text"] == "hi back"
    assert out["response_finish_reason"] == "end_turn"
    assert out["request_messages"] == [{"role": "user", "content": "hi"}]
    assert out["request_system"] == "s"
    assert out["content_truncated"] is False


def test_capture_streaming_assembles_chunks(monkeypatch) -> None:
    from uncommon_route.proxy import _capture_streaming

    monkeypatch.setenv("UNCOMMON_ROUTE_CAPTURE_CONTENT", "1")
    chunks = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
        b'data: [DONE]\n\n',
    ]
    out = _capture_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        chunks,
        "openai-chat",
    )
    assert out["response_text"] == "hello"
    assert out["response_finish_reason"] == "stop"


def test_cli_traces_purge_clears_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UNCOMMON_ROUTE_DATA_DIR", str(tmp_path))
    from uncommon_route.cli import _cmd_traces_purge

    storage = FileTraceStorage(base_dir=tmp_path / "traces")
    storage.append({"request_id": "x", "timestamp": 1.0})
    assert list((tmp_path / "traces").glob("*.jsonl"))

    rc = _cmd_traces_purge()
    assert rc == 0
    assert list((tmp_path / "traces").glob("*.jsonl")) == []
