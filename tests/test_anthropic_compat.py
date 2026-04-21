"""Tests for Anthropic Messages API ↔ OpenAI Chat Completions conversion."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient

from uncommon_route.anthropic_compat import (
    anthropic_to_openai_response,
    anthropic_to_openai_request,
    AnthropicToOpenAIStreamConverter,
    openai_to_anthropic_response,
    openai_to_anthropic_request,
    anthropic_error_response,
    OpenAIToAnthropicStreamConverter,
)
from uncommon_route.model_map import DiscoveredModel, ModelMapper
from uncommon_route.providers import ProviderEntry, ProvidersConfig
from uncommon_route.proxy import create_app
from uncommon_route.router.types import (
    CapabilityLane,
    ModelCapabilities,
    ModelPricing,
    RoutingDecision,
    RoutingFeatures,
    RoutingMode,
    ServedQuality,
    Tier,
)
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.traces import InMemoryTraceStorage, TraceStore


# =========================================================================
# Request conversion: Anthropic → OpenAI
# =========================================================================

class TestAnthropicToOpenAIRequest:
    def test_basic_text_message(self) -> None:
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        out = anthropic_to_openai_request(body)
        assert out["model"] == "claude-sonnet-4-20250514"
        assert out["max_tokens"] == 1024
        assert len(out["messages"]) == 1
        assert out["messages"][0] == {"role": "user", "content": "Hello"}

    def test_system_string(self) -> None:
        body = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": "You are a helpful assistant",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        out = anthropic_to_openai_request(body)
        assert out["messages"][0] == {"role": "system", "content": "You are a helpful assistant"}
        assert out["messages"][1] == {"role": "user", "content": "Hi"}

    def test_system_content_blocks(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "system": [
                {"type": "text", "text": "Line one"},
                {"type": "text", "text": "Line two"},
            ],
            "messages": [{"role": "user", "content": "Go"}],
        }
        out = anthropic_to_openai_request(body)
        assert out["messages"][0]["role"] == "system"
        assert "Line one" in out["messages"][0]["content"]
        assert "Line two" in out["messages"][0]["content"]

    def test_cache_control_blocks_are_preserved(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "system": [
                {"type": "text", "text": "Stable policy", "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Continue", "cache_control": {"type": "ephemeral"}},
                ],
            }],
        }

        out = anthropic_to_openai_request(body)

        assert out["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert out["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_thinking_blocks_are_preserved_in_preview(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Plan it"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Need to inspect requirements first.",
                            "signature": "sig_123",
                        },
                        {"type": "text", "text": "Let me think."},
                    ],
                },
            ],
        }

        out = anthropic_to_openai_request(body)

        assert out["messages"][1]["role"] == "assistant"
        assert out["messages"][1]["content"][0]["type"] == "thinking"
        assert out["messages"][1]["content"][0]["signature"] == "sig_123"

    def test_user_content_blocks(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                ],
            }],
        }
        out = anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == "What is this?"

    def test_assistant_with_tool_use(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Let me check."},
                        {
                            "type": "tool_use",
                            "id": "toolu_01",
                            "name": "get_weather",
                            "input": {"city": "NYC"},
                        },
                    ],
                },
            ],
        }
        out = anthropic_to_openai_request(body)
        assistant = out["messages"][1]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "Let me check."
        assert len(assistant["tool_calls"]) == 1
        tc = assistant["tool_calls"][0]
        assert tc["id"] == "toolu_01"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "NYC"}

    def test_tool_result_becomes_tool_message(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_01", "content": "72F sunny"},
                ],
            }],
        }
        out = anthropic_to_openai_request(body)
        assert out["messages"][0]["role"] == "tool"
        assert out["messages"][0]["tool_call_id"] == "toolu_01"
        assert out["messages"][0]["content"] == "72F sunny"

    def test_tool_result_with_content_blocks(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "result data"}],
                    },
                ],
            }],
        }
        out = anthropic_to_openai_request(body)
        assert out["messages"][0]["content"] == "result data"

    def test_tools_conversion(self) -> None:
        body = {
            "model": "m",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "go"}],
            "tools": [{
                "name": "get_weather",
                "description": "Get weather for a city",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }],
        }
        out = anthropic_to_openai_request(body)
        assert len(out["tools"]) == 1
        t = out["tools"][0]
        assert t["type"] == "function"
        assert t["function"]["name"] == "get_weather"
        assert t["function"]["parameters"]["type"] == "object"

    def test_tool_choice_auto(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "auto"},
        }
        assert anthropic_to_openai_request(body)["tool_choice"] == "auto"

    def test_tool_choice_any(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "any"},
        }
        assert anthropic_to_openai_request(body)["tool_choice"] == "required"

    def test_tool_choice_specific(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "tool", "name": "my_func"},
        }
        tc = anthropic_to_openai_request(body)["tool_choice"]
        assert tc == {"type": "function", "function": {"name": "my_func"}}

    def test_stop_sequences(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "stop_sequences": ["END", "STOP"],
        }
        assert anthropic_to_openai_request(body)["stop"] == ["END", "STOP"]

    def test_optional_params_passthrough(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "temperature": 0.5, "top_p": 0.9, "stream": True,
        }
        out = anthropic_to_openai_request(body)
        assert out["temperature"] == 0.5
        assert out["top_p"] == 0.9
        assert out["stream"] is True

    def test_top_k_dropped(self) -> None:
        body = {
            "model": "m", "max_tokens": 1,
            "messages": [{"role": "user", "content": "x"}],
            "top_k": 40,
        }
        out = anthropic_to_openai_request(body)
        assert "top_k" not in out


# =========================================================================
# Response conversion: OpenAI → Anthropic
# =========================================================================

class TestOpenAIToAnthropicResponse:
    def test_text_response(self) -> None:
        oai = {
            "id": "chatcmpl-123",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        out = openai_to_anthropic_response(oai, "claude-sonnet-4-20250514")
        assert out["type"] == "message"
        assert out["role"] == "assistant"
        assert out["model"] == "claude-sonnet-4-20250514"
        assert out["stop_reason"] == "end_turn"
        assert out["content"] == [{"type": "text", "text": "Hello!"}]
        assert out["usage"]["input_tokens"] == 10


class TestAnthropicToOpenAIResponse:
    def test_text_and_tool_response(self) -> None:
        anth = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "model": "anthropic/claude-sonnet-4-6",
            "content": [
                {"type": "text", "text": "I'll check that."},
                {"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "test"}},
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 12,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 50,
                "output_tokens": 7,
            },
        }

        out = anthropic_to_openai_response(anth, "anthropic/claude-sonnet-4.6")

        assert out["object"] == "chat.completion"
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        assert out["choices"][0]["message"]["content"] == "I'll check that."
        assert out["choices"][0]["message"]["tool_calls"][0]["id"] == "call_1"
        assert json.loads(out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"q": "test"}
        assert out["usage"]["prompt_tokens"] == 162
        assert out["usage"]["completion_tokens"] == 7
        assert out["usage"]["prompt_tokens_details"]["cached_tokens"] == 100


class TestOpenAIToAnthropicRequest:
    def test_openai_request_roundtrips_tools_and_system(self) -> None:
        body = {
            "model": "claude-sonnet",
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "You are stable", "cache_control": {"type": "ephemeral"}}]},
                {"role": "user", "content": "Check weather"},
                {
                    "role": "assistant",
                    "content": "Calling tool",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": "{\"city\":\"SF\"}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "58F"},
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }],
            "tool_choice": "required",
        }

        out = openai_to_anthropic_request(body)

        assert out["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert out["tools"][0]["name"] == "weather"
        assert out["tool_choice"] == {"type": "any"}
        assert any(block["type"] == "tool_use" for block in out["messages"][1]["content"])
        assert out["messages"][2]["content"][0]["type"] == "tool_result"

    def test_openai_request_preserves_thinking_blocks(self) -> None:
        body = {
            "model": "claude-sonnet",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "Plan it"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Need to inspect requirements first.",
                            "signature": "sig_123",
                        },
                        {"type": "text", "text": "Let me think."},
                    ],
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": "{\"city\":\"SF\"}"},
                    }],
                },
            ],
        }

        out = openai_to_anthropic_request(body)

        assert out["messages"][1]["content"][0]["type"] == "thinking"
        assert out["messages"][1]["content"][0]["signature"] == "sig_123"
        assert out["messages"][1]["content"][-1]["type"] == "tool_use"

    def test_tool_calls_response(self) -> None:
        oai = {
            "id": "chatcmpl-456",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "NYC"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 15, "total_tokens": 35},
        }
        out = openai_to_anthropic_response(oai, "model-x")
        assert out["stop_reason"] == "tool_use"
        assert len(out["content"]) == 1
        block = out["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "call_abc"
        assert block["name"] == "get_weather"
        assert block["input"] == {"city": "NYC"}

    def test_mixed_text_and_tools(self) -> None:
        oai = {
            "id": "chatcmpl-789",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "I'll check that.",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q": "test"}'},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15},
        }
        out = openai_to_anthropic_response(oai, "m")
        assert len(out["content"]) == 2
        assert out["content"][0]["type"] == "text"
        assert out["content"][1]["type"] == "tool_use"

    def test_length_finish_reason(self) -> None:
        oai = {
            "choices": [{
                "message": {"role": "assistant", "content": "truncated..."},
                "finish_reason": "length",
            }],
            "usage": {},
        }
        assert openai_to_anthropic_response(oai, "m")["stop_reason"] == "max_tokens"


# =========================================================================
# Error conversion
# =========================================================================

class TestErrorConversion:
    def test_rate_limit_error(self) -> None:
        out = anthropic_error_response(429, "Too many requests")
        assert out["type"] == "error"
        assert out["error"]["type"] == "rate_limit_error"
        assert out["error"]["message"] == "Too many requests"

    def test_auth_error(self) -> None:
        out = anthropic_error_response(401, "Invalid key")
        assert out["error"]["type"] == "authentication_error"

    def test_unknown_status(self) -> None:
        out = anthropic_error_response(599, "weird")
        assert out["error"]["type"] == "api_error"


# =========================================================================
# Streaming conversion: OpenAI SSE → Anthropic SSE
# =========================================================================

def _make_oai_sse(data: dict) -> bytes:
    """Build a single OpenAI SSE line."""
    return f"data: {json.dumps(data)}\n\n".encode()


def _parse_anthropic_events(raw_events: list[bytes]) -> list[dict]:
    """Parse Anthropic SSE events into (event_type, data) tuples."""
    results: list[dict] = []
    for raw in raw_events:
        text = raw.decode()
        lines = text.strip().split("\n")
        event_type = ""
        data_str = ""
        for line in lines:
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
        if data_str:
            parsed = json.loads(data_str)
            parsed["_event"] = event_type
            results.append(parsed)
    return results


def _parse_openai_events(raw_events: list[bytes]) -> tuple[list[dict], bool]:
    results: list[dict] = []
    done = False
    for raw in raw_events:
        text = raw.decode()
        for line in text.strip().split("\n"):
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                done = True
                continue
            results.append(json.loads(payload))
    return results, done


class TestStreamConverter:
    def test_basic_text_stream(self) -> None:
        converter = OpenAIToAnthropicStreamConverter(model="test-model")

        chunk1 = _make_oai_sse({
            "choices": [{"delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        })
        chunk2 = _make_oai_sse({
            "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
        })
        chunk3 = _make_oai_sse({
            "choices": [{"delta": {"content": " world"}, "finish_reason": None}],
        })
        chunk4 = _make_oai_sse({
            "choices": [{"delta": {}, "finish_reason": "stop"}],
        })

        all_events: list[bytes] = []
        for c in [chunk1, chunk2, chunk3, chunk4]:
            all_events.extend(converter.feed(c))
        all_events.extend(converter.finish())

        parsed = _parse_anthropic_events(all_events)
        event_types = [e["_event"] for e in parsed]

        assert "message_start" in event_types
        assert "ping" in event_types
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "content_block_stop" in event_types
        assert "message_delta" in event_types
        assert "message_stop" in event_types

        msg_start = next(e for e in parsed if e["_event"] == "message_start")
        assert msg_start["message"]["model"] == "test-model"
        assert msg_start["message"]["role"] == "assistant"

        deltas = [e for e in parsed if e["_event"] == "content_block_delta"]
        text_parts = [d["delta"]["text"] for d in deltas if d["delta"].get("type") == "text_delta"]
        assert "Hello" in text_parts
        assert " world" in text_parts

        msg_delta = next(e for e in parsed if e["_event"] == "message_delta")
        assert msg_delta["delta"]["stop_reason"] == "end_turn"

    def test_tool_call_stream(self) -> None:
        converter = OpenAIToAnthropicStreamConverter(model="m")

        chunks = [
            _make_oai_sse({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}),
            _make_oai_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "search", "arguments": ""},
            }]}, "finish_reason": None}]}),
            _make_oai_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": '{"q":'},
            }]}, "finish_reason": None}]}),
            _make_oai_sse({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "function": {"arguments": '"test"}'},
            }]}, "finish_reason": None}]}),
            _make_oai_sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        ]

        all_events: list[bytes] = []
        for c in chunks:
            all_events.extend(converter.feed(c))
        all_events.extend(converter.finish())

        parsed = _parse_anthropic_events(all_events)

        block_starts = [e for e in parsed if e["_event"] == "content_block_start"]
        assert len(block_starts) == 1
        assert block_starts[0]["content_block"]["type"] == "tool_use"
        assert block_starts[0]["content_block"]["id"] == "call_1"
        assert block_starts[0]["content_block"]["name"] == "search"

        json_deltas = [
            e for e in parsed
            if e["_event"] == "content_block_delta" and e["delta"].get("type") == "input_json_delta"
        ]
        joined = "".join(d["delta"]["partial_json"] for d in json_deltas)
        assert json.loads(joined) == {"q": "test"}

        msg_delta = next(e for e in parsed if e["_event"] == "message_delta")
        assert msg_delta["delta"]["stop_reason"] == "tool_use"

    def test_done_signal(self) -> None:
        converter = OpenAIToAnthropicStreamConverter(model="m")

        events = converter.feed(
            _make_oai_sse({"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]})
        )
        events.extend(converter.feed(b"data: [DONE]\n\n"))
        events.extend(converter.finish())

        parsed = _parse_anthropic_events(events)
        assert any(e["_event"] == "message_stop" for e in parsed)

    def test_empty_finish_calls_are_idempotent(self) -> None:
        converter = OpenAIToAnthropicStreamConverter(model="m")
        converter.feed(_make_oai_sse({
            "choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}],
        }))
        converter.finish()
        second = converter.finish()
        assert second == []


class TestAnthropicToOpenAIStreamConverter:
    def test_basic_text_stream(self) -> None:
        converter = AnthropicToOpenAIStreamConverter(model="anthropic/claude-sonnet-4.6")
        chunks = [
            (
                b'event: message_start\n'
                b'data: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"anthropic/claude-sonnet-4-6","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":21,"cache_read_input_tokens":100,"cache_creation_input_tokens":20,"output_tokens":0}}}\n\n'
            ),
            (
                b'event: content_block_start\n'
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
            ),
            (
                b'event: content_block_delta\n'
                b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
            ),
            (
                b'event: message_delta\n'
                b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":7}}\n\n'
            ),
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        all_events: list[bytes] = []
        for chunk in chunks:
            all_events.extend(converter.feed(chunk))
        all_events.extend(converter.finish())

        parsed, done = _parse_openai_events(all_events)

        assert done is True
        assert parsed[0]["choices"][0]["delta"]["role"] == "assistant"
        assert any(event["choices"][0]["delta"].get("content") == "Hello" for event in parsed if event.get("choices"))
        finish = next(event for event in parsed if event.get("choices") and event["choices"][0]["finish_reason"] == "stop")
        assert finish["choices"][0]["finish_reason"] == "stop"
        usage_chunk = next(event for event in parsed if event.get("choices") == [])
        assert usage_chunk["usage"]["prompt_tokens"] == 141
        assert usage_chunk["usage"]["completion_tokens"] == 7


# =========================================================================
# Integration: /v1/messages endpoint on the proxy
# =========================================================================

@pytest.fixture
def messages_client() -> TestClient:
    spend_control = SpendControl(storage=InMemorySpendControlStorage())
    app = create_app(
        upstream="http://127.0.0.1:1/fake",
        spend_control=spend_control,
    )
    return TestClient(app, raise_server_exceptions=False)


def _build_seed_mapper(*model_ids: str) -> ModelMapper:
    mapper = ModelMapper("https://api.commonstack.ai/v1")
    for model_id in model_ids:
        provider = model_id.split("/", 1)[0] if "/" in model_id else "unknown"
        mapper._pool[model_id] = DiscoveredModel(
            id=model_id,
            provider=provider,
            owned_by=provider,
            pricing=ModelPricing(0.2, 0.8),
            capabilities=ModelCapabilities(tool_calling=True, vision=False, reasoning=False),
        )
        mapper._upstream_models.add(model_id)
    mapper._discovered = True

    async def _fake_discover(api_key: str | None = None) -> int:
        return len(mapper._pool)

    mapper.discover = _fake_discover  # type: ignore[method-assign]
    return mapper


class TestMessagesEndpoint:
    def test_no_upstream_returns_anthropic_error(self) -> None:
        app = create_app(upstream="")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/messages", json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 503
        data = resp.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "overloaded_error"

    def test_upstream_unreachable_returns_anthropic_error(
        self,
        messages_client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class FailingClient:
            is_closed = False

            async def post(self, *args, **kwargs):
                raise httpx.ConnectError("connection refused")

        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: FailingClient())
        resp = messages_client.post("/v1/messages", json={
            "model": "some-model",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 502
        data = resp.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "api_error"
        assert "unreachable" in data["error"]["message"].lower()

    def test_virtual_model_debug_still_works(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "uncommon-route/auto",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "/debug what is 2+2"}],
        })
        assert resp.status_code == 200

    def test_spend_limit_returns_anthropic_error(self) -> None:
        sc = SpendControl(storage=InMemorySpendControlStorage())
        sc.set_limit("session", 0.001)
        sc.record(0.01)
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=sc,
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/messages", json={
            "model": "uncommon-route/auto",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 429
        data = resp.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "rate_limit_error"

    def test_system_prompt_preserved(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "uncommon-route/auto",
            "max_tokens": 100,
            "system": "You are a coding assistant",
            "messages": [{"role": "user", "content": "/debug explain recursion"}],
        })
        assert resp.status_code == 200

    def test_explicit_anthropic_model_uses_native_messages_transport_with_cache_breakpoints(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "anthropic/claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "pong"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 12,
                        "cache_read_input_tokens": 1200,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 1,
                    },
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            app = create_app(upstream="https://api.commonstack.ai/v1")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                "/v1/messages",
                json={
                    "model": "anthropic/claude-sonnet-4.6",
                    "max_tokens": 16,
                    "system": "You are terse.",
                    "tools": [{
                        "name": "bash",
                        "description": "Run shell commands",
                        "input_schema": {"type": "object", "properties": {}},
                    }],
                    "messages": [{"role": "user", "content": "Reply with exactly pong"}],
                },
                headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )

            assert resp.status_code == 200
            assert resp.json()["type"] == "message"
            assert captured["url"] == "https://api.commonstack.ai/v1/messages"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["model"] == "anthropic/claude-sonnet-4.6"
            assert body["system"][-1]["cache_control"]["type"] == "ephemeral"
            assert body["tools"][-1]["cache_control"]["type"] == "ephemeral"
            headers = captured["headers"]
            assert isinstance(headers, dict)
            assert headers["x-api-key"] == "env-key-123"
            assert headers["anthropic-version"] == "2023-06-01"
            assert headers["anthropic-beta"] == "prompt-caching-2024-07-31"
        finally:
            asyncio.run(async_client.aclose())


class TestAutoRouting:
    """All /v1/messages requests are auto-routed regardless of model name."""

    def test_claude_model_gets_routed(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "/debug hello"}],
        })
        assert resp.status_code == 200

    def test_arbitrary_model_gets_routed(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "anything-here",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "/debug hello"}],
        })
        assert resp.status_code == 200

    def test_routing_headers_present(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert "x-uncommon-route-model" in resp.headers
        assert "x-uncommon-route-tier" in resp.headers

    def test_debug_returns_anthropic_format(self, messages_client: TestClient) -> None:
        resp = messages_client.post("/v1/messages", json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "/debug what is 2+2"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert any(b["type"] == "text" for b in data["content"])


class TestTransportRouting:
    def test_virtual_messages_tool_steps_filter_candidates_to_native_anthropic_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}
        routed: dict[str, object] = {}

        def fake_route(*args, **kwargs):
            routed["available_models"] = list(kwargs.get("available_models") or [])
            return RoutingDecision(
                model="minimax/minimax-m2.1",
                tier=Tier.MEDIUM,
                capability_lane=kwargs["routing_features"].capability_lane or CapabilityLane.ANTHROPIC_TOOL_SAFE,
                served_quality=ServedQuality.ECONOMY,
                served_quality_target=ServedQuality.BALANCED,
                served_quality_floor=ServedQuality.ECONOMY,
                continuity_quality_floor=kwargs["routing_features"].continuity_quality_floor,
                mode=RoutingMode.AUTO,
                confidence=0.92,
                method="pool",
                reasoning="forced minimax route for transport-safe pool test",
                cost_estimate=0.001,
                baseline_cost=0.004,
                savings=0.75,
                raw_confidence=0.92,
                complexity=0.5,
                routing_features=kwargs["routing_features"],
            )

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_minimax_safe_pool",
                    "type": "message",
                    "role": "assistant",
                    "model": "minimax/minimax-m2.1",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 18, "output_tokens": 2},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setattr("uncommon_route.proxy.route", fake_route)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            mapper = _build_seed_mapper(
                "deepseek/deepseek-v3.2",
                "minimax/minimax-m2.1",
            )
            app = create_app(
                upstream="https://api.commonstack.ai/v1",
                model_mapper=mapper,
                spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/messages",
                json={
                    "model": "uncommon-route/auto",
                    "max_tokens": 64,
                    "tools": [{
                        "name": "get_weather",
                        "description": "Get weather",
                        "input_schema": {"type": "object", "properties": {}},
                    }],
                    "messages": [{"role": "user", "content": "Check weather in Hong Kong"}],
                },
                headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
            )

            assert resp.status_code == 200
            assert routed["available_models"] == ["minimax/minimax-m2.1"]
            assert resp.headers["x-uncommon-route-requested-transport"] == "anthropic-messages"
            assert resp.headers["x-uncommon-route-transport"] == "anthropic-messages"
            assert captured["url"] == "https://api.commonstack.ai/v1/messages"
        finally:
            asyncio.run(async_client.aclose())

    def test_virtual_messages_tool_steps_fail_closed_when_no_transport_safe_models_exist(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = {"route": False, "upstream": False}

        def fake_route(*args, **kwargs):
            called["route"] = True
            raise AssertionError("route() should not run when no transport-safe models exist")

        def handler(request: httpx.Request) -> httpx.Response:
            called["upstream"] = True
            raise AssertionError("Upstream request should not be attempted when routing is infeasible")

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setattr("uncommon_route.proxy.route", fake_route)

        try:
            mapper = _build_seed_mapper("deepseek/deepseek-v3.2")
            app = create_app(
                upstream="https://api.commonstack.ai/v1",
                model_mapper=mapper,
                spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/messages",
                json={
                    "model": "uncommon-route/auto",
                    "max_tokens": 64,
                    "tools": [{
                        "name": "mkdir",
                        "description": "Create directories",
                        "input_schema": {"type": "object", "properties": {}},
                    }],
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "id": "toolu_99",
                                "name": "mkdir",
                                "input": {"path": "weather-cli"},
                            }],
                        },
                        {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": "toolu_99",
                                "content": [{"type": "text", "text": "done"}],
                            }],
                        },
                    ],
                },
                headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
            )

            assert resp.status_code == 400
            payload = resp.json()
            assert payload["error"]["code"] == "routing_constraints_unmet"
            assert "native anthropic transport" in payload["error"]["message"].lower()
            assert payload["error"]["details"]["failed_constraints"] == ["anthropic-native-transport"]
            assert payload["error"]["details"]["missing_capabilities"] == ["anthropic-tool-transport"]
            assert called["route"] is False
            assert called["upstream"] is False
        finally:
            asyncio.run(async_client.aclose())

    def test_messages_use_native_anthropic_transport_for_minimax_and_preserve_tool_result_blocks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_minimax",
                    "type": "message",
                    "role": "assistant",
                    "model": "minimax/minimax-m2.1",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 21, "output_tokens": 2},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            app = create_app(upstream="https://api.commonstack.ai/v1")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/messages",
                json={
                    "model": "minimax/minimax-m2.1",
                    "max_tokens": 64,
                    "tools": [{
                        "name": "get_weather",
                        "description": "Get weather",
                        "input_schema": {"type": "object", "properties": {}},
                    }],
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "id": "toolu_01",
                                "name": "get_weather",
                                "input": {"city": "Hong Kong"},
                            }],
                        },
                        {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": "toolu_01",
                                "content": [{"type": "text", "text": "28C and sunny"}],
                            }],
                        },
                    ],
                },
                headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
            )

            assert resp.status_code == 200
            assert resp.headers["x-uncommon-route-requested-transport"] == "anthropic-messages"
            assert resp.headers["x-uncommon-route-transport"] == "anthropic-messages"
            assert resp.headers["x-uncommon-route-transport-source"] == "agentic-ingress"
            assert "minimax anthropic transport" in resp.headers["x-uncommon-route-transport-reason"]
            assert captured["url"] == "https://api.commonstack.ai/v1/messages"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["messages"][1]["content"][0]["type"] == "tool_result"
            assert body["messages"][1]["content"][0]["content"][0]["text"] == "28C and sunny"
            headers = captured["headers"]
            assert isinstance(headers, dict)
            assert headers["x-api-key"] == "env-key-123"
        finally:
            asyncio.run(async_client.aclose())

    def test_messages_with_thinking_blocks_force_source_body_reuse_for_native_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_native_thinking",
                    "type": "message",
                    "role": "assistant",
                    "model": "anthropic/claude-opus-4-7",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 21, "output_tokens": 2},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setattr("uncommon_route.proxy._can_reuse_native_anthropic_body", lambda **kwargs: False)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            app = create_app(upstream="https://api.commonstack.ai/v1")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/messages",
                json={
                    "model": "anthropic/claude-opus-4-7",
                    "max_tokens": 64,
                    "messages": [
                        {"role": "user", "content": "Plan it"},
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "thinking",
                                    "thinking": "Need to inspect requirements first.",
                                    "signature": "sig_123",
                                },
                                {
                                    "type": "tool_use",
                                    "id": "toolu_01",
                                    "name": "get_weather",
                                    "input": {"city": "Hong Kong"},
                                },
                            ],
                        },
                        {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": "toolu_01",
                                "content": [{"type": "text", "text": "28C and sunny"}],
                            }],
                        },
                    ],
                },
                headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
            )

            assert resp.status_code == 200
            assert captured["url"] == "https://api.commonstack.ai/v1/messages"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["messages"][1]["content"][0]["type"] == "thinking"
            assert body["messages"][1]["content"][0]["signature"] == "sig_123"
        finally:
            asyncio.run(async_client.aclose())

    def test_messages_use_minimax_anthropic_endpoint_for_direct_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_minimax_byok",
                    "type": "message",
                    "role": "assistant",
                    "model": "minimax/minimax-m2.1",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)

        try:
            providers = ProvidersConfig(providers={
                "minimax": ProviderEntry(
                    name="minimax",
                    api_key="mm-key-123",
                    base_url="https://api.minimax.io/v1",
                    models=["minimax/minimax-m2.1"],
                ),
            })
            app = create_app(
                upstream="https://api.commonstack.ai/v1",
                providers_config=providers,
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/v1/messages", json={
                "model": "minimax/minimax-m2.1",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            })

            assert resp.status_code == 200
            assert captured["url"] == "https://api.minimax.io/anthropic/v1/messages"
            headers = captured["headers"]
            assert isinstance(headers, dict)
            assert headers["x-api-key"] == "mm-key-123"
        finally:
            asyncio.run(async_client.aclose())

    def test_chat_completions_keep_openai_transport_for_minimax_models(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl_minimax",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "minimax/minimax-m2.1",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            app = create_app(upstream="https://api.commonstack.ai/v1")
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/v1/chat/completions", json={
                "model": "minimax/minimax-m2.1",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            })

            assert resp.status_code == 200
            assert resp.headers["x-uncommon-route-requested-transport"] == "openai-chat"
            assert resp.headers["x-uncommon-route-transport"] == "openai-chat"
            assert resp.headers["x-uncommon-route-transport-source"] == "ingress-policy"
            assert captured["url"] == "https://api.commonstack.ai/v1/chat/completions"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["model"] == "minimax/minimax-m2.1"
        finally:
            asyncio.run(async_client.aclose())

    def test_virtual_messages_trace_exposes_transport_reasoning_for_minimax(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_virtual_minimax",
                    "type": "message",
                    "role": "assistant",
                    "model": "minimax/minimax-m2.1",
                    "content": [{"type": "text", "text": "done"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 18, "output_tokens": 2},
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_ADMIN_TOKEN", "test-admin")
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")
        monkeypatch.setattr(
            "uncommon_route.proxy.route",
            lambda *args, **kwargs: RoutingDecision(
                model="minimax/minimax-m2.1",
                tier=Tier.MEDIUM,
                capability_lane=CapabilityLane.ANTHROPIC_TOOL_SAFE,
                served_quality=ServedQuality.ECONOMY,
                served_quality_target=ServedQuality.BALANCED,
                served_quality_floor=ServedQuality.ECONOMY,
                continuity_quality_floor=None,
                mode=RoutingMode.AUTO,
                confidence=0.91,
                method="pool",
                reasoning="forced minimax route for transport test",
                cost_estimate=0.001,
                baseline_cost=0.005,
                savings=0.8,
                raw_confidence=0.91,
                complexity=0.5,
                routing_features=RoutingFeatures(
                    step_type="tool-result-followup",
                    has_tool_results=True,
                    is_agentic=True,
                ),
            ),
        )

        try:
            traces = TraceStore(storage=InMemoryTraceStorage())
            app = create_app(
                upstream="https://api.commonstack.ai/v1",
                spend_control=SpendControl(storage=InMemorySpendControlStorage()),
                trace_store=traces,
            )
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/v1/messages",
                json={
                    "model": "uncommon-route/auto",
                    "max_tokens": 64,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "id": "toolu_02",
                                "name": "get_weather",
                                "input": {"city": "Hong Kong"},
                            }],
                        },
                        {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": "toolu_02",
                                "content": [{"type": "text", "text": "28C and sunny"}],
                            }],
                        },
                    ],
                },
                headers={"anthropic-beta": "interleaved-thinking-2025-05-14"},
            )

            assert resp.status_code == 200
            request_id = resp.headers["x-uncommon-route-request-id"]
            trace = traces.find(request_id)
            assert trace is not None
            assert trace["requested_transport"] == "anthropic-messages"
            assert trace["transport"] == "anthropic-messages"
            assert trace["transport_preference_source"] == "agentic-ingress"
            assert "minimax anthropic transport" in trace["transport_reason"]
            assert trace["attempts_payload"][0]["transport"] == "anthropic-messages"
            assert trace["attempts_payload"][0]["requested_transport"] == "anthropic-messages"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["messages"][1]["content"][0]["type"] == "tool_result"
            assert body["messages"][1]["content"][0]["content"][0]["text"] == "28C and sunny"
        finally:
            asyncio.run(async_client.aclose())


class TestNativeAnthropicTransportForChatCompletions:
    def test_chat_completions_uses_native_messages_transport_for_anthropic_models(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "msg_native",
                    "type": "message",
                    "role": "assistant",
                    "model": "anthropic/claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "pong"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 12,
                        "cache_read_input_tokens": 1200,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 1,
                    },
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")

        try:
            app = create_app(upstream="https://api.commonstack.ai/v1")
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "anthropic/claude-sonnet-4.6",
                    "max_tokens": 16,
                    "messages": [
                        {"role": "system", "content": "You are terse."},
                        {"role": "user", "content": "Reply with exactly pong"},
                    ],
                    "tools": [{
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "description": "Run shell commands",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }],
                },
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["object"] == "chat.completion"
            assert data["choices"][0]["message"]["content"] == "pong"
            assert data["usage"]["prompt_tokens_details"]["cached_tokens"] == 1200
            assert captured["url"] == "https://api.commonstack.ai/v1/messages"
            body = captured["body"]
            assert isinstance(body, dict)
            assert body["system"][-1]["cache_control"]["type"] == "ephemeral"
            assert body["tools"][-1]["cache_control"]["type"] == "ephemeral"
            headers = captured["headers"]
            assert isinstance(headers, dict)
            assert headers["x-api-key"] == "env-key-123"
            assert headers["anthropic-version"] == "2023-06-01"
        finally:
            asyncio.run(async_client.aclose())


class TestAuthPriority:
    """Env key takes precedence over client-provided auth."""

    def test_env_key_overrides_request_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "env-key-123")
        app = create_app(upstream="http://127.0.0.1:1/fake")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"x-api-key": "client-key-should-be-ignored"},
        )
        assert resp.status_code == 502

    def test_client_header_used_when_no_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UNCOMMON_ROUTE_API_KEY", raising=False)
        monkeypatch.delenv("COMMONSTACK_API_KEY", raising=False)
        app = create_app(upstream="http://127.0.0.1:1/fake")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "some-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": "Bearer client-key"},
        )
        assert resp.status_code == 502
