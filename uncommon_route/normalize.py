"""Message normalization for stable hashing.

Used to derive content-block-aware fingerprints of conversation prefixes,
independent of multimodal payload bytes (images), tool argument key
ordering, and whitespace variation.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_WHITESPACE_RUN = re.compile(r"\s+")


def hash16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _flatten_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    btype = block.get("type", "")
    if btype == "text":
        return str(block.get("text", ""))
    if btype == "tool_use":
        name = block.get("name", "")
        args = block.get("input", block.get("arguments", {}))
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return f"[tool_use:{name}({canonical})]"
    if btype == "tool_result":
        inner = block.get("content", "")
        if not inner:
            return ""
        if isinstance(inner, str):
            return inner
        if isinstance(inner, list):
            return " ".join(_flatten_block(b) for b in inner)
        return ""
    if btype in ("image", "input_image"):
        return "[image]"
    return ""


def normalize_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(p for p in (_flatten_block(b) for b in content) if p)
    else:
        text = "" if content is None else str(content)
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    return text


def normalize_messages_to_hashes(messages: list[dict[str, Any]]) -> list[str]:
    return [hash16(normalize_message_text(m)) for m in messages]
