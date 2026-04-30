"""Anthropic Messages API ↔ OpenAI Chat Completions format conversion.

Converts between the two API formats so the router can accept Anthropic
Messages requests (``POST /v1/messages``) while forwarding to an
OpenAI-compatible upstream.

Key differences handled:
  - ``system`` as top-level param vs. system message in ``messages``
  - Content blocks (``[{"type":"text","text":"..."}]``) vs. flat strings
  - Tool calling format (``input_schema`` vs. ``function.parameters``)
  - SSE event format (typed events vs. ``data:`` lines)
"""

from __future__ import annotations

from copy import deepcopy
import json
import time
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# Finish-reason / stop-reason mapping
# ---------------------------------------------------------------------------

_FINISH_TO_STOP: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}

_STOP_TO_FINISH: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}

_STATUS_TO_ERROR_TYPE: dict[int, str] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
    500: "api_error",
    502: "api_error",
    503: "overloaded_error",
    504: "api_error",
}

_PASSTHROUGH_CONTENT_BLOCK_TYPES = {"thinking", "redacted_thinking"}


# ---------------------------------------------------------------------------
# Request conversion: Anthropic → OpenAI
# ---------------------------------------------------------------------------

def _flatten_content_blocks(blocks: list[dict[str, Any]]) -> str:
    """Join text blocks into a single string."""
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "\n".join(parts) if parts else ""


def _preserve_text_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preserved: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        item = {"type": "text", "text": block.get("text", "")}
        if "cache_control" in block:
            item["cache_control"] = block["cache_control"]
        preserved.append(item)
    return preserved


def _preserve_passthrough_block(block: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    if block.get("type") not in _PASSTHROUGH_CONTENT_BLOCK_TYPES:
        return None
    return deepcopy(block)


def _should_preserve_block_content(blocks: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(block, dict)
        and (
            "cache_control" in block
            or block.get("type") in _PASSTHROUGH_CONTENT_BLOCK_TYPES
        )
        for block in blocks
    )


def anthropic_to_openai_request(body: dict[str, Any]) -> dict[str, Any]:
    """Convert an Anthropic Messages request body to OpenAI Chat Completions."""
    out: dict[str, Any] = {}

    out["model"] = body.get("model", "")
    out["max_tokens"] = body.get("max_tokens", 4096)

    messages: list[dict[str, Any]] = []

    # System prompt → system message
    system = body.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            preserved = _preserve_text_blocks(system)
            if preserved and any("cache_control" in block for block in preserved):
                messages.append({"role": "system", "content": preserved})
            else:
                text = _flatten_content_blocks(system)
                if text:
                    messages.append({"role": "system", "content": text})

    for msg in body.get("messages", []):
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            _convert_user_message(content, messages)
        elif role == "assistant":
            _convert_assistant_message(content, messages)

    out["messages"] = messages

    if "stream" in body:
        out["stream"] = body["stream"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    if "top_p" in body:
        out["top_p"] = body["top_p"]
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]

    # Tools
    tools = body.get("tools")
    if tools:
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
                **({"cache_control": t.get("cache_control")} if t.get("cache_control") is not None else {}),
            }
            for t in tools
        ]

    # tool_choice
    tc = body.get("tool_choice")
    if tc is not None:
        out["tool_choice"] = _convert_tool_choice(tc)

    return out


def openai_to_anthropic_request(body: dict[str, Any]) -> dict[str, Any]:
    """Convert an OpenAI Chat Completions request body to Anthropic Messages."""
    out: dict[str, Any] = {
        "model": body.get("model", ""),
        "max_tokens": body.get("max_tokens", 4096),
    }

    system_blocks: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []

    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            system_blocks.extend(_openai_content_to_anthropic_blocks(content))
            continue

        if role == "user":
            messages.append({
                "role": "user",
                "content": _openai_content_to_anthropic_blocks(content),
            })
            continue

        if role == "assistant":
            blocks = _openai_content_to_anthropic_blocks(content)
            for tc in msg.get("tool_calls", []) or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                try:
                    input_data = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    input_data = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": input_data,
                })
            messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue

        if role == "tool":
            tool_result = content
            if isinstance(tool_result, list):
                tool_result = _flatten_content_blocks(tool_result)
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(tool_result or ""),
                }],
            })

    if system_blocks:
        out["system"] = system_blocks
    out["messages"] = messages

    if "stream" in body:
        out["stream"] = body["stream"]
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    if "top_p" in body:
        out["top_p"] = body["top_p"]
    if "stop" in body:
        stop = body.get("stop")
        if isinstance(stop, list):
            out["stop_sequences"] = stop
        elif isinstance(stop, str):
            out["stop_sequences"] = [stop]

    tools = body.get("tools")
    if tools:
        out["tools"] = [
            {
                "name": t.get("function", {}).get("name", ""),
                "description": t.get("function", {}).get("description", ""),
                "input_schema": t.get("function", {}).get("parameters", {}),
                **({"cache_control": t["cache_control"]} if isinstance(t, dict) and "cache_control" in t else {}),
            }
            for t in tools
            if isinstance(t, dict)
        ]

    tool_choice = body.get("tool_choice")
    if tool_choice is not None:
        out["tool_choice"] = _convert_tool_choice_to_anthropic(tool_choice)

    return out


def _convert_user_message(
    content: str | list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> None:
    if isinstance(content, str):
        messages.append({"role": "user", "content": content})
        return

    text_parts: list[str] = []
    preserved_blocks: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
            item = {"type": "text", "text": block.get("text", "")}
            if "cache_control" in block:
                item["cache_control"] = block["cache_control"]
            preserved_blocks.append(item)
        elif btype in _PASSTHROUGH_CONTENT_BLOCK_TYPES:
            preserved = _preserve_passthrough_block(block)
            if preserved is not None:
                preserved_blocks.append(preserved)
        elif btype == "tool_result":
            tool_results.append(block)

    if preserved_blocks and _should_preserve_block_content(preserved_blocks):
        messages.append({"role": "user", "content": preserved_blocks})
    elif text_parts:
        messages.append({"role": "user", "content": "\n".join(text_parts)})

    for tr in tool_results:
        tr_content = tr.get("content", "")
        if isinstance(tr_content, list):
            tr_content = _flatten_content_blocks(tr_content)
        messages.append({
            "role": "tool",
            "tool_call_id": tr.get("tool_use_id", ""),
            "content": str(tr_content),
        })


def _convert_assistant_message(
    content: str | list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> None:
    if isinstance(content, str):
        messages.append({"role": "assistant", "content": content})
        return

    text_parts: list[str] = []
    preserved_blocks: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
            item = {"type": "text", "text": block.get("text", "")}
            if "cache_control" in block:
                item["cache_control"] = block["cache_control"]
            preserved_blocks.append(item)
        elif btype in _PASSTHROUGH_CONTENT_BLOCK_TYPES:
            preserved = _preserve_passthrough_block(block)
            if preserved is not None:
                preserved_blocks.append(preserved)
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    assistant_msg: dict[str, Any] = {
        "role": "assistant",
        "content": (
            preserved_blocks
            if preserved_blocks and _should_preserve_block_content(preserved_blocks)
            else ("\n".join(text_parts) if text_parts else None)
        ),
    }
    if tool_calls:
        assistant_msg["tool_calls"] = tool_calls
    messages.append(assistant_msg)


def _convert_tool_choice(tc: str | dict[str, Any]) -> str | dict[str, Any]:
    if isinstance(tc, str):
        return {"auto": "auto", "any": "required", "none": "none"}.get(tc, "auto")
    tc_type = tc.get("type", "")
    if tc_type == "auto":
        return "auto"
    if tc_type == "any":
        return "required"
    if tc_type == "tool":
        return {"type": "function", "function": {"name": tc.get("name", "")}}
    return "auto"


def _convert_tool_choice_to_anthropic(tc: str | dict[str, Any]) -> str | dict[str, Any]:
    if isinstance(tc, str):
        return {
            "auto": {"type": "auto"},
            "required": {"type": "any"},
            "none": {"type": "none"},
        }.get(tc, {"type": "auto"})
    tc_type = tc.get("type", "")
    if tc_type == "function":
        return {
            "type": "tool",
            "name": tc.get("function", {}).get("name", ""),
        }
    if tc_type in {"auto", "any", "none"}:
        return {"type": tc_type}
    return {"type": "auto"}


def _openai_content_to_anthropic_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type in {"text", "input_text"}:
                block = {"type": "text", "text": item.get("text", "")}
                if "cache_control" in item:
                    block["cache_control"] = item["cache_control"]
                blocks.append(block)
            elif item_type in _PASSTHROUGH_CONTENT_BLOCK_TYPES:
                blocks.append(deepcopy(item))
            elif item_type == "tool_result":
                blocks.append(item)
        return blocks or [{"type": "text", "text": ""}]
    return [{"type": "text", "text": str(content or "")}]


# ---------------------------------------------------------------------------
# Response conversion: OpenAI → Anthropic
# ---------------------------------------------------------------------------

def anthropic_to_openai_response(
    data: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """Convert an Anthropic Messages response to OpenAI Chat Completions."""
    content = data.get("content") if isinstance(data.get("content"), list) else []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(str(block.get("text", "")))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    uncached_input = int(usage.get("input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    prompt_tokens = uncached_input + cache_read + cache_write

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(part for part in text_parts if part),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": data.get("id", f"chatcmpl_{uuid.uuid4().hex[:24]}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": _STOP_TO_FINISH.get(str(data.get("stop_reason") or "end_turn"), "stop"),
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": prompt_tokens + output_tokens,
            "prompt_tokens_details": {"cached_tokens": cache_read},
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }


def openai_to_anthropic_response(
    data: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    """Convert an OpenAI Chat Completions response to Anthropic Messages."""
    choice = data["choices"][0] if data.get("choices") else {}
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    content_blocks: list[dict[str, Any]] = []

    text = message.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    for tc in message.get("tool_calls", []):
        fn = tc.get("function", {})
        try:
            input_data = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            input_data = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "input": input_data,
        })

    usage = data.get("usage", {})

    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": _FINISH_TO_STOP.get(finish_reason, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


# ---------------------------------------------------------------------------
# Error conversion
# ---------------------------------------------------------------------------

def anthropic_error_response(status_code: int, message: str) -> dict[str, Any]:
    """Build an Anthropic-format error body."""
    return {
        "type": "error",
        "error": {
            "type": _STATUS_TO_ERROR_TYPE.get(status_code, "api_error"),
            "message": message,
        },
    }


# ---------------------------------------------------------------------------
# Streaming conversion: OpenAI SSE → Anthropic SSE
# ---------------------------------------------------------------------------

class AnthropicToOpenAIStreamConverter:
    """Stateful converter that parses Anthropic SSE chunks into OpenAI SSE."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._created = int(time.time())
        self._message_id = f"chatcmpl_{uuid.uuid4().hex[:24]}"
        self._buffer = ""
        self._finished = False
        self._emitted_role = False
        self._current_tool_id = ""
        self._current_tool_name = ""
        self._output_tokens = 0
        self._usage: dict[str, int] = {}

    def feed(self, raw: bytes) -> list[bytes]:
        events: list[bytes] = []
        self._buffer += raw.decode("utf-8", errors="replace")

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith("event:"):
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                events.extend(self.finish())
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            events.extend(self._on_event(data))
        return events

    def finish(self) -> list[bytes]:
        if self._finished:
            return []
        self._finished = True
        events: list[bytes] = []
        if self._usage:
            uncached_input = int(self._usage.get("input_tokens", 0) or 0)
            cache_read = int(self._usage.get("cache_read_input_tokens", 0) or 0)
            cache_write = int(self._usage.get("cache_creation_input_tokens", 0) or 0)
            prompt_tokens = uncached_input + cache_read + cache_write
            events.append(self._sse({
                "id": self._message_id,
                "object": "chat.completion.chunk",
                "created": self._created,
                "model": self._model,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": self._output_tokens,
                    "total_tokens": prompt_tokens + self._output_tokens,
                    "prompt_tokens_details": {"cached_tokens": cache_read},
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_write,
                },
            }))
        events.append(b"data: [DONE]\n\n")
        return events

    @staticmethod
    def _sse(data: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(data)}\n\n".encode()

    def _role_chunk(self) -> bytes:
        self._emitted_role = True
        return self._sse({
            "id": self._message_id,
            "object": "chat.completion.chunk",
            "created": self._created,
            "model": self._model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }],
        })

    def _on_event(self, data: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []
        event_type = str(data.get("type") or "")
        if event_type == "message_start":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            if isinstance(message.get("usage"), dict):
                self._usage.update(message["usage"])
            if message.get("id"):
                self._message_id = str(message["id"])
            if not self._emitted_role:
                events.append(self._role_chunk())
            return events

        if not self._emitted_role:
            events.append(self._role_chunk())

        if event_type == "content_block_start":
            block = data.get("content_block") if isinstance(data.get("content_block"), dict) else {}
            if block.get("type") == "tool_use":
                self._current_tool_id = str(block.get("id", ""))
                self._current_tool_name = str(block.get("name", ""))
                events.append(self._sse({
                    "id": self._message_id,
                    "object": "chat.completion.chunk",
                    "created": self._created,
                    "model": self._model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": self._current_tool_id,
                                "type": "function",
                                "function": {"name": self._current_tool_name, "arguments": ""},
                            }],
                        },
                        "finish_reason": None,
                    }],
                }))
            return events

        if event_type == "content_block_delta":
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
            if delta.get("type") == "text_delta":
                events.append(self._sse({
                    "id": self._message_id,
                    "object": "chat.completion.chunk",
                    "created": self._created,
                    "model": self._model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": str(delta.get("text", ""))},
                        "finish_reason": None,
                    }],
                }))
            elif delta.get("type") == "input_json_delta":
                events.append(self._sse({
                    "id": self._message_id,
                    "object": "chat.completion.chunk",
                    "created": self._created,
                    "model": self._model,
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": self._current_tool_id,
                                "type": "function",
                                "function": {
                                    "name": self._current_tool_name,
                                    "arguments": str(delta.get("partial_json", "")),
                                },
                            }],
                        },
                        "finish_reason": None,
                    }],
                }))
            return events

        if event_type == "message_delta":
            if isinstance(data.get("usage"), dict):
                self._usage.update(data["usage"])
                self._output_tokens = max(self._output_tokens, int(data["usage"].get("output_tokens", 0) or 0))
            delta = data.get("delta") if isinstance(data.get("delta"), dict) else {}
            events.append(self._sse({
                "id": self._message_id,
                "object": "chat.completion.chunk",
                "created": self._created,
                "model": self._model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": _STOP_TO_FINISH.get(str(delta.get("stop_reason") or "end_turn"), "stop"),
                }],
            }))
        return events


class OpenAIToAnthropicStreamConverter:
    """Stateful converter that parses OpenAI SSE chunks and yields Anthropic SSE events.

    Feed raw bytes from the upstream with :meth:`feed`; it returns a list of
    ready-to-send ``bytes`` chunks (each a complete SSE event).  Call
    :meth:`finish` after the upstream closes to flush any pending events.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._message_id = f"msg_{uuid.uuid4().hex[:24]}"
        self._message_started = False
        self._block_index = -1
        self._block_type: str | None = None
        self._open_tool_call_index: int | None = None
        self._tool_call_meta: dict[int, dict[str, str]] = {}
        self._output_tokens = 0
        self._buffer = ""
        self._finished = False

    # -- public API ---------------------------------------------------------

    def feed(self, raw: bytes) -> list[bytes]:
        """Process a raw byte chunk from upstream; return Anthropic SSE events."""
        events: list[bytes] = []
        self._buffer += raw.decode("utf-8", errors="replace")

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                events.extend(self._finalize())
                continue
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            events.extend(self._on_chunk(data))
        return events

    def finish(self) -> list[bytes]:
        """Flush remaining events (call after upstream stream ends)."""
        if not self._finished:
            return self._finalize()
        return []

    # -- SSE helpers --------------------------------------------------------

    @staticmethod
    def _sse(event_type: str, data: dict[str, Any]) -> bytes:
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode()

    # -- event emitters -----------------------------------------------------

    def _emit_message_start(self) -> bytes:
        self._message_started = True
        return self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self._message_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": self._model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })

    def _start_block(self, btype: str, **kw: Any) -> bytes:
        self._block_index += 1
        self._block_type = btype
        if btype == "text":
            self._open_tool_call_index = None
            block: dict[str, Any] = {"type": "text", "text": ""}
        elif btype == "tool_use":
            self._open_tool_call_index = kw.get("tool_index")
            block = {"type": "tool_use", "id": kw.get("id", ""), "name": kw.get("name", ""), "input": {}}
        else:
            self._open_tool_call_index = None
            block = {"type": btype}
        return self._sse("content_block_start", {
            "type": "content_block_start",
            "index": self._block_index,
            "content_block": block,
        })

    def _block_delta(self, delta: dict[str, Any]) -> bytes:
        return self._sse("content_block_delta", {
            "type": "content_block_delta",
            "index": self._block_index,
            "delta": delta,
        })

    def _stop_block(self) -> bytes:
        ev = self._sse("content_block_stop", {
            "type": "content_block_stop",
            "index": self._block_index,
        })
        self._block_type = None
        self._open_tool_call_index = None
        return ev

    # -- chunk processing ---------------------------------------------------

    def _on_chunk(self, data: dict[str, Any]) -> list[bytes]:
        events: list[bytes] = []

        if not self._message_started:
            events.append(self._emit_message_start())
            events.append(self._sse("ping", {"type": "ping"}))

        # Capture usage from stream (some providers include it)
        usage = data.get("usage")
        if usage:
            self._output_tokens = max(
                self._output_tokens,
                usage.get("completion_tokens", 0),
            )

        choices = data.get("choices", [])
        if not choices:
            return events

        delta = choices[0].get("delta", {})
        finish_reason = choices[0].get("finish_reason")

        # Text content
        content = delta.get("content")
        if content is not None and content != "":
            if self._block_type != "text":
                if self._block_type is not None:
                    events.append(self._stop_block())
                events.append(self._start_block("text"))
            events.append(self._block_delta({"type": "text_delta", "text": content}))

        # Tool calls
        for tc in delta.get("tool_calls", []):
            raw_index = tc.get("index", 0)
            tc_index = int(raw_index) if isinstance(raw_index, int) or str(raw_index).isdigit() else 0
            meta = self._tool_call_meta.setdefault(tc_index, {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "name": "",
            })

            tc_id = tc.get("id")
            tc_fn = tc.get("function", {})
            tc_name = tc_fn.get("name")
            tc_args = tc_fn.get("arguments", "")

            if tc_id:
                meta["id"] = tc_id
            if tc_name:
                meta["name"] = tc_name

            should_start_tool_block = (
                self._block_type != "tool_use"
                or self._open_tool_call_index != tc_index
            )
            if should_start_tool_block and (meta["id"] or meta["name"] or tc_args):
                if self._block_type is not None:
                    events.append(self._stop_block())
                events.append(self._start_block(
                    "tool_use",
                    id=meta["id"],
                    name=meta["name"],
                    tool_index=tc_index,
                ))

            if tc_args:
                events.append(self._block_delta({
                    "type": "input_json_delta",
                    "partial_json": tc_args,
                }))

        if finish_reason:
            events.extend(self._finalize(finish_reason))

        return events

    def _finalize(self, finish_reason: str | None = None) -> list[bytes]:
        if self._finished:
            return []
        self._finished = True

        events: list[bytes] = []

        if not self._message_started:
            events.append(self._emit_message_start())

        if self._block_type is not None:
            events.append(self._stop_block())

        stop = _FINISH_TO_STOP.get(finish_reason or "stop", "end_turn")
        events.append(self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": self._output_tokens},
        }))
        events.append(self._sse("message_stop", {"type": "message_stop"}))
        return events
