"""Tests for per-transport content capture (extraction + SSE assembly)."""

from __future__ import annotations

import json

from uncommon_route.content_capture import (
    extract_assistant_blocks_anthropic,
    extract_assistant_blocks_openai_chat,
    extract_assistant_blocks_openai_responses,
    parse_stream_assistant_content,
    truncate_content_payload,
)


class TestExtractAnthropic:
    def test_text_only(self) -> None:
        body = json.dumps(
            {
                "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn",
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_anthropic(body)
        assert text == "hello"
        assert calls == []
        assert finish == "end_turn"

    def test_tool_use(self) -> None:
        body = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "calling"},
                    {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "/a"}},
                ],
                "stop_reason": "tool_use",
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_anthropic(body)
        assert text == "calling"
        assert len(calls) == 1
        assert calls[0]["name"] == "Read"
        assert calls[0]["input"] == {"path": "/a"}
        assert finish == "tool_use"


class TestExtractOpenAIResponses:
    def test_empty_bytes_returns_empty_tuple(self) -> None:
        text, calls, finish = extract_assistant_blocks_openai_responses(b"")
        assert text == ""
        assert calls == []
        assert finish == ""

    def test_text_and_function_call(self) -> None:
        body = json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "hi"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "fc_1",
                        "name": "Read",
                        "arguments": '{"path":"/a"}',
                    },
                ],
                "status": "completed",
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_openai_responses(body)
        assert text == "hi"
        assert len(calls) == 1
        assert calls[0]["name"] == "Read"
        assert calls[0]["input"] == {"path": "/a"}
        assert finish == "completed"


class TestExtractOpenAIChat:
    def test_text(self) -> None:
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_openai_chat(body)
        assert text == "hi"
        assert calls == []
        assert finish == "stop"

    def test_tool_calls(self) -> None:
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc_1",
                                    "function": {
                                        "name": "Read",
                                        "arguments": '{"path":"/a"}',
                                    },
                                    "type": "function",
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_openai_chat(body)
        assert text == ""
        assert len(calls) == 1
        assert calls[0]["name"] == "Read"
        assert finish == "tool_calls"


class TestStreamAssemblyAnthropic:
    def test_basic_text_stream(self) -> None:
        chunks = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"m1"}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hel"}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        text, calls, finish = parse_stream_assistant_content(
            chunks, "anthropic-messages"
        )
        assert text == "hello"
        assert calls == []
        assert finish == "end_turn"


class TestStreamAssemblyOpenAIChat:
    def test_text_stream(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        text, calls, finish = parse_stream_assistant_content(chunks, "openai-chat")
        assert text == "hello"
        assert calls == []
        assert finish == "stop"


class TestTruncate:
    def test_under_cap_no_change(self) -> None:
        row = {
            "request_id": "r",
            "request_messages": [{"role": "user", "content": "hi"}],
            "response_text": "ok",
        }
        out, truncated = truncate_content_payload(row, cap_bytes=10_000)
        assert truncated is False
        assert out["response_text"] == "ok"

    def test_over_cap_drops_response_text_first(self) -> None:
        big = "x" * 100_000
        row = {
            "request_id": "r",
            "request_messages": [{"role": "user", "content": "small"}],
            "response_text": big,
            "response_tool_calls": None,
        }
        out, truncated = truncate_content_payload(row, cap_bytes=10_000)
        assert truncated is True
        assert out["response_text"] == ""
