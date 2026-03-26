"""Helpers for OpenAI Responses API compatibility.

This adapter lets clients such as Codex talk to the proxy through
``/v1/responses`` even when the upstream only supports chat completions.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any


def responses_to_openai_chat_request(
    raw: dict[str, Any],
    *,
    previous_messages: list[dict[str, Any]] | None = None,
    default_model: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert a Responses API request into a chat completions request."""

    messages = json.loads(json.dumps(previous_messages or []))
    if previous_messages:
        input_messages = _responses_input_to_messages(raw.get("input"))
        messages.extend(input_messages)
    else:
        initial_messages = _build_initial_messages(
            instructions=raw.get("instructions"),
            input_value=raw.get("input"),
        )
        messages.extend(initial_messages)

    body: dict[str, Any] = {
        "model": str(raw.get("model") or default_model or "").strip(),
        "messages": messages,
        "stream": bool(raw.get("stream")),
    }

    if raw.get("prompt_cache_key"):
        body["prompt_cache_key"] = raw.get("prompt_cache_key")

    converted_tools = _responses_tools_to_openai(raw.get("tools"))
    if converted_tools:
        body["tools"] = converted_tools

    tool_choice = _responses_tool_choice_to_openai(raw.get("tool_choice"))
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    if isinstance(raw.get("parallel_tool_calls"), bool):
        body["parallel_tool_calls"] = raw["parallel_tool_calls"]

    return body, messages


def openai_chat_response_to_responses(
    chat_payload: dict[str, Any],
    *,
    response_id: str,
    request_body: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Convert a chat completion JSON payload into a Responses payload."""

    choices = chat_payload.get("choices") if isinstance(chat_payload.get("choices"), list) else []
    first = choices[0] if choices else {}
    message = first.get("message") if isinstance(first, dict) and isinstance(first.get("message"), dict) else {}
    assistant_message, output_items = _openai_message_to_responses_output(message)
    usage = _openai_usage_to_responses(chat_payload.get("usage"))
    response = build_responses_object(
        response_id=response_id,
        model=str(chat_payload.get("model") or request_body.get("model") or ""),
        output=output_items,
        status="completed",
        usage=usage,
        previous_response_id=request_body.get("previous_response_id"),
    )
    return response, assistant_message


def build_responses_object(
    *,
    response_id: str,
    model: str,
    output: list[dict[str, Any]],
    status: str,
    usage: dict[str, Any] | None = None,
    previous_response_id: Any = None,
) -> dict[str, Any]:
    created_at = int(time.time())
    payload: dict[str, Any] = {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": None,
        "model": model,
        "output": output,
        "parallel_tool_calls": False,
        "store": False,
    }
    if previous_response_id:
        payload["previous_response_id"] = str(previous_response_id)
    if usage is not None:
        payload["usage"] = usage
    return payload


class OpenAIChatToResponsesStreamAdapter:
    """Buffer an OpenAI chat-completions SSE stream and emit Responses SSE."""

    def __init__(self, *, request_body: dict[str, Any], response_id: str) -> None:
        self._request_body = request_body
        self._response_id = response_id
        self._buffer = ""
        self._finished = False
        self._model = str(request_body.get("model") or "")
        self._chat_response_id = ""
        self._text_parts: list[str] = []
        self._usage: dict[str, Any] | None = None
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def feed(self, raw: bytes) -> None:
        self._buffer += raw.decode("utf-8", errors="replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line or line.startswith("event:"):
                continue
            if line == "data: [DONE]":
                self._finished = True
                continue
            if not line.startswith("data: "):
                continue
            try:
                payload = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            self._consume_chunk(payload)

    def finalize(self) -> tuple[list[bytes], dict[str, Any] | None]:
        if self._buffer.strip():
            trailing = self._buffer.strip()
            if trailing.startswith("data: ") and trailing != "data: [DONE]":
                try:
                    self._consume_chunk(json.loads(trailing[6:]))
                except json.JSONDecodeError:
                    pass
        assistant_message, output_items = self._build_output_items()
        response_events = self._build_events(output_items)
        return response_events, assistant_message

    def _consume_chunk(self, payload: dict[str, Any]) -> None:
        if payload.get("id"):
            self._chat_response_id = str(payload["id"])
        if payload.get("model"):
            self._model = str(payload["model"])
        if isinstance(payload.get("usage"), dict):
            self._usage = payload["usage"]
        choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
        if not choices:
            return
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._text_parts.append(content)
        tool_calls = delta.get("tool_calls") if isinstance(delta.get("tool_calls"), list) else []
        for entry in tool_calls:
            if not isinstance(entry, dict):
                continue
            index = int(entry.get("index", 0) or 0)
            tool = self._tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if entry.get("id"):
                tool["id"] = str(entry["id"])
            function = entry.get("function") if isinstance(entry.get("function"), dict) else {}
            if function.get("name"):
                tool["name"] = str(function["name"])
            if function.get("arguments"):
                tool["arguments"] += str(function["arguments"])

    def _build_output_items(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        text = "".join(self._text_parts)
        tool_items: list[dict[str, Any]] = []
        normalized_tool_calls: list[dict[str, Any]] = []
        for index in sorted(self._tool_calls):
            call = self._tool_calls[index]
            call_id = call["id"] or f"call_{uuid.uuid4().hex[:16]}"
            name = call["name"] or "tool"
            arguments = call["arguments"] or ""
            normalized_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
            tool_items.append(
                {
                    "id": f"fc_{uuid.uuid4().hex[:24]}",
                    "type": "function_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments,
                }
            )

        assistant_message: dict[str, Any] | None = None
        output_items: list[dict[str, Any]] = []
        if text or not tool_items:
            message_item = {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
            output_items.append(message_item)
            assistant_message = {"role": "assistant", "content": text}
        elif tool_items:
            assistant_message = {"role": "assistant", "content": None}

        if assistant_message is not None and normalized_tool_calls:
            assistant_message["tool_calls"] = normalized_tool_calls
        output_items.extend(tool_items)
        return assistant_message, output_items

    def _build_events(self, output_items: list[dict[str, Any]]) -> list[bytes]:
        events: list[bytes] = []
        sequence = 1
        created_response = build_responses_object(
            response_id=self._response_id,
            model=self._model,
            output=[],
            status="in_progress",
            previous_response_id=self._request_body.get("previous_response_id"),
        )
        events.append(
            _responses_sse(
                {
                    "type": "response.created",
                    "sequence_number": sequence,
                    "response": created_response,
                }
            )
        )
        sequence += 1

        for output_index, item in enumerate(output_items):
            item_type = str(item.get("type") or "")
            if item_type == "message":
                item_id = str(item["id"])
                empty_item = {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "",
                            "annotations": [],
                        }
                    ],
                }
                text = str(((item.get("content") or [{}])[0]).get("text") or "")
                final_part = {"type": "output_text", "text": text, "annotations": []}
                events.append(
                    _responses_sse(
                        {
                            "type": "response.output_item.added",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item": empty_item,
                        }
                    )
                )
                sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.content_part.added",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item_id": item_id,
                            "content_index": 0,
                            "part": {"type": "output_text", "text": "", "annotations": []},
                        }
                    )
                )
                sequence += 1
                if text:
                    events.append(
                        _responses_sse(
                            {
                                "type": "response.output_text.delta",
                                "sequence_number": sequence,
                                "output_index": output_index,
                                "item_id": item_id,
                                "content_index": 0,
                                "delta": text,
                            }
                        )
                    )
                    sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.output_text.done",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item_id": item_id,
                            "content_index": 0,
                            "text": text,
                            "logprobs": [],
                        }
                    )
                )
                sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.content_part.done",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item_id": item_id,
                            "content_index": 0,
                            "part": final_part,
                        }
                    )
                )
                sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.output_item.done",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item": item,
                        }
                    )
                )
                sequence += 1
                continue

            if item_type == "function_call":
                item_id = str(item["id"])
                arguments = str(item.get("arguments") or "")
                in_progress_item = dict(item)
                in_progress_item["status"] = "in_progress"
                in_progress_item["arguments"] = ""
                events.append(
                    _responses_sse(
                        {
                            "type": "response.output_item.added",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item": in_progress_item,
                        }
                    )
                )
                sequence += 1
                if arguments:
                    events.append(
                        _responses_sse(
                            {
                                "type": "response.function_call_arguments.delta",
                                "sequence_number": sequence,
                                "output_index": output_index,
                                "item_id": item_id,
                                "delta": arguments,
                            }
                        )
                    )
                    sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.function_call_arguments.done",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item_id": item_id,
                            "arguments": arguments,
                            "name": str(item.get("name") or ""),
                        }
                    )
                )
                sequence += 1
                events.append(
                    _responses_sse(
                        {
                            "type": "response.output_item.done",
                            "sequence_number": sequence,
                            "output_index": output_index,
                            "item": item,
                        }
                    )
                )
                sequence += 1

        completed_response = build_responses_object(
            response_id=self._response_id,
            model=self._model,
            output=output_items,
            status="completed",
            usage=_openai_usage_to_responses(self._usage),
            previous_response_id=self._request_body.get("previous_response_id"),
        )
        events.append(
            _responses_sse(
                {
                    "type": "response.completed",
                    "sequence_number": sequence,
                    "response": completed_response,
                }
            )
        )
        events.append(b"data: [DONE]\n\n")
        return events


def _build_initial_messages(*, instructions: Any, input_value: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    instructions_text = str(instructions or "").strip()
    if instructions_text:
        system_parts.append(instructions_text)

    for message in _responses_input_to_messages(input_value):
        role = str(message.get("role") or "")
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        if role == "system" and text:
            system_parts.append(text)
            continue
        messages.append(message)

    if system_parts:
        messages.insert(0, {"role": "system", "content": "\n\n".join(part for part in system_parts if part)})
    return messages


def _responses_input_to_messages(input_value: Any) -> list[dict[str, Any]]:
    if input_value is None:
        return []
    if isinstance(input_value, str):
        return [{"role": "user", "content": input_value}]
    if isinstance(input_value, dict):
        return _responses_input_item_to_messages(input_value)

    messages: list[dict[str, Any]] = []
    if isinstance(input_value, list):
        for item in input_value:
            messages.extend(_responses_input_item_to_messages(item))
    return messages


def _responses_input_item_to_messages(item: Any) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        return []

    item_type = str(item.get("type") or "message")
    if item_type == "function_call_output":
        return [
            {
                "role": "tool",
                "tool_call_id": str(item.get("call_id") or ""),
                "content": _stringify_content(item.get("output")),
            }
        ]

    if item_type == "function_call":
        call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:16]}")
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": str(item.get("name") or "tool"),
                            "arguments": str(item.get("arguments") or ""),
                        },
                    }
                ],
            }
        ]

    role = str(item.get("role") or "")
    if item_type == "message" or role:
        chat_role = "system" if role in {"developer", "system"} else (role or "user")
        content = _responses_content_to_text(item.get("content"))
        message: dict[str, Any] = {"role": chat_role, "content": content}
        tool_calls = _responses_message_tool_calls(item)
        if tool_calls:
            message["role"] = "assistant"
            message["content"] = content or None
            message["tool_calls"] = tool_calls
        return [message]

    return []


def _responses_message_tool_calls(item: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = item.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    normalized: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        normalized.append(
            {
                "id": str(tool_call.get("id") or tool_call.get("call_id") or f"call_{uuid.uuid4().hex[:16]}"),
                "type": "function",
                "function": {
                    "name": str(function.get("name") or tool_call.get("name") or "tool"),
                    "arguments": str(function.get("arguments") or tool_call.get("arguments") or ""),
                },
            }
        )
    return normalized


def _responses_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type in {"input_text", "output_text", "text"}:
                parts.append(str(block.get("text") or ""))
            elif block_type == "input_image":
                detail = str(block.get("detail") or "image")
                parts.append(f"[image:{detail}]")
        return "\n".join(part for part in parts if part)
    return _stringify_content(content)


def _responses_tools_to_openai(tools: Any) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return converted
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = str(tool.get("type") or "")
        if tool_type != "function":
            continue
        function: dict[str, Any]
        if isinstance(tool.get("function"), dict):
            function = dict(tool["function"])
        else:
            function = {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or ""),
                "parameters": tool.get("parameters")
                if isinstance(tool.get("parameters"), dict)
                else {"type": "object"},
            }
            if isinstance(tool.get("strict"), bool):
                function["strict"] = tool["strict"]
        converted.append({"type": "function", "function": function})
    return converted


def _responses_tool_choice_to_openai(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    if tool_choice.get("type") == "function":
        name = str(tool_choice.get("name") or "")
        function = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
        if not name:
            name = str(function.get("name") or "")
        if name:
            return {"type": "function", "function": {"name": name}}
    return None


def _openai_message_to_responses_output(message: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and str(part.get("type") or "") in {"text", "output_text"}
        )
    else:
        text = ""

    tool_calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    output_items: list[dict[str, Any]] = []
    assistant_message: dict[str, Any] | None = None

    if text or not tool_calls:
        output_items.append(
            {
                "id": f"msg_{uuid.uuid4().hex[:24]}",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        )
        assistant_message = {"role": "assistant", "content": text}
    else:
        assistant_message = {"role": "assistant", "content": None}

    normalized_tool_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
        call_id = str(tool_call.get("id") or f"call_{uuid.uuid4().hex[:16]}")
        name = str(function.get("name") or "tool")
        arguments = str(function.get("arguments") or "")
        normalized_tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
        output_items.append(
            {
                "id": f"fc_{uuid.uuid4().hex[:24]}",
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            }
        )
    if assistant_message is not None and normalized_tool_calls:
        assistant_message["tool_calls"] = normalized_tool_calls
    return assistant_message, output_items


def _openai_usage_to_responses(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cached_tokens = 0
    prompt_details = usage.get("prompt_tokens_details")
    if isinstance(prompt_details, dict):
        cached_tokens = int(prompt_details.get("cached_tokens") or 0)
    cached_tokens = max(
        cached_tokens,
        int(usage.get("cache_read_input_tokens") or 0),
    )
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached_tokens},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": input_tokens + output_tokens,
    }


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _responses_sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()
