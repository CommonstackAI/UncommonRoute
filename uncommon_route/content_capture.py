"""Per-transport content extraction.

Three transports are supported: anthropic-messages, openai-chat, openai-responses.
Each has both a non-streaming extractor (parse a JSON body) and a streaming
SSE reducer (assemble chunks).
"""

from __future__ import annotations

import json
from typing import Any


# --- Non-streaming extractors -------------------------------------------------

def extract_assistant_blocks_anthropic(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    blocks = data.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(str(b.get("text", "")))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""),
                "name": b.get("name", ""),
                "input": b.get("input", {}),
            })
    finish = str(data.get("stop_reason", ""))
    return "".join(text_parts), tool_calls, finish


def extract_assistant_blocks_openai_chat(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    choices = data.get("choices") or []
    if not choices:
        return "", [], ""
    msg = choices[0].get("message") or {}
    text_field = msg.get("content", "")
    if isinstance(text_field, list):
        text = "\n".join(
            p.get("text", "") for p in text_field
            if isinstance(p, dict) and p.get("type") == "text"
        )
    else:
        text = str(text_field) if text_field is not None else ""
    raw_calls = msg.get("tool_calls") or []
    calls: list[dict[str, Any]] = []
    for tc in raw_calls:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments", {})
        calls.append({
            "id": tc.get("id", "") if isinstance(tc, dict) else "",
            "name": fn.get("name", ""),
            "input": args,
        })
    finish = str(choices[0].get("finish_reason", ""))
    return text, calls, finish


def extract_assistant_blocks_openai_responses(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    output = data.get("output") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    text_parts.append(str(c.get("text", "")))
        elif item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments", "{}"))
            except Exception:
                args = item.get("arguments", {})
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "name": item.get("name", ""),
                "input": args,
            })
    finish = str(data.get("status", ""))
    return "".join(text_parts), tool_calls, finish


# --- Streaming reducers -------------------------------------------------------

def _iter_sse_events(chunks: list[bytes]) -> list[tuple[str | None, str]]:
    """Yield (event_name, data_json_str) for each SSE event in chunks."""
    buf = b"".join(chunks).decode("utf-8", errors="replace")
    out: list[tuple[str | None, str]] = []
    for raw in buf.split("\n\n"):
        ev = None
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):].strip()
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if data_lines:
            out.append((ev, "\n".join(data_lines)))
    return out


def _stream_anthropic(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    blocks: dict[int, dict[str, Any]] = {}
    finish = ""
    for ev, data in _iter_sse_events(chunks):
        if not data or data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        ptype = payload.get("type", ev or "")
        if ptype == "content_block_start":
            idx = payload.get("index", 0)
            blocks[idx] = dict(payload.get("content_block") or {})
            blocks[idx].setdefault("text", "")
            blocks[idx].setdefault("input", {})
        elif ptype == "content_block_delta":
            idx = payload.get("index", 0)
            delta = payload.get("delta") or {}
            block = blocks.setdefault(idx, {"type": "text", "text": ""})
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                block.setdefault("_partial", "")
                block["_partial"] += str(delta.get("partial_json", ""))
        elif ptype == "content_block_stop":
            idx = payload.get("index", 0)
            block = blocks.get(idx)
            if block and "_partial" in block:
                try:
                    block["input"] = json.loads(block["_partial"])
                except Exception:
                    block["input"] = block.get("_partial")
                block.pop("_partial", None)
        elif ptype == "message_delta":
            sr = (payload.get("delta") or {}).get("stop_reason")
            if sr:
                finish = str(sr)
    text = ""
    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(blocks):
        b = blocks[idx]
        if b.get("type") == "text":
            text += str(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""),
                "name": b.get("name", ""),
                "input": b.get("input", {}),
            })
    return text, tool_calls, finish


def _stream_openai_chat(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    text_parts: list[str] = []
    finish = ""
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    for _ev, data in _iter_sse_events(chunks):
        if data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        choices = payload.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        delta = ch.get("delta") or {}
        if "content" in delta and delta["content"]:
            text_parts.append(str(delta["content"]))
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_calls_by_index.setdefault(
                idx, {"id": "", "name": "", "_args": ""}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["_args"] += fn["arguments"]
        if ch.get("finish_reason"):
            finish = str(ch["finish_reason"])
    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(tool_calls_by_index):
        slot = tool_calls_by_index[idx]
        try:
            args = json.loads(slot["_args"]) if slot["_args"] else {}
        except Exception:
            args = slot["_args"]
        tool_calls.append({"id": slot["id"], "name": slot["name"], "input": args})
    return "".join(text_parts), tool_calls, finish


def _stream_openai_responses(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    # Responses API streams `response.output_text.delta` / `response.output_item.added` events.
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish = ""
    for _ev, data in _iter_sse_events(chunks):
        if data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        ptype = payload.get("type", "")
        if ptype == "response.output_text.delta":
            text_parts.append(str(payload.get("delta", "")))
        elif ptype == "response.output_item.added":
            item = payload.get("item") or {}
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments", "{}"))
                except Exception:
                    args = item.get("arguments", {})
                tool_calls.append({
                    "id": item.get("call_id", item.get("id", "")),
                    "name": item.get("name", ""),
                    "input": args,
                })
        elif ptype == "response.completed":
            resp = payload.get("response") or {}
            finish = str(resp.get("status", "") or finish)
    return "".join(text_parts), tool_calls, finish


def parse_stream_assistant_content(
    chunks: list[bytes], transport: str
) -> tuple[str, list[dict[str, Any]], str]:
    if transport == "anthropic-messages":
        return _stream_anthropic(chunks)
    if transport == "openai-chat":
        return _stream_openai_chat(chunks)
    if transport == "openai-responses":
        return _stream_openai_responses(chunks)
    return "", [], ""


# --- Truncation ---------------------------------------------------------------

def truncate_content_payload(
    row: dict[str, Any], *, cap_bytes: int = 64 * 1024
) -> tuple[dict[str, Any], bool]:
    """Return a reduced copy of the row whose JSON-encoded size <= cap_bytes.

    The input row is not mutated. Drop order:
    response_text -> response_tool_calls -> request_messages (oldest first).
    """
    out = dict(row)

    def _size() -> int:
        return len(json.dumps(out, default=str, ensure_ascii=False).encode("utf-8"))

    if _size() <= cap_bytes:
        return out, False

    if out.get("response_text"):
        out["response_text"] = ""
    if _size() <= cap_bytes:
        return out, True

    if out.get("response_tool_calls"):
        out["response_tool_calls"] = []
    if _size() <= cap_bytes:
        return out, True

    msgs = list(out.get("request_messages") or [])
    while msgs and _size() > cap_bytes:
        msgs.pop(0)
        out["request_messages"] = msgs
    return out, True
