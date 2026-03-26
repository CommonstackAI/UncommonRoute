"""Provider-aware prompt-cache hints and usage accounting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from uncommon_route.router.types import ModelPricing


@dataclass(frozen=True, slots=True)
class CacheRequestPlan:
    family: str
    mode: str = "none"
    prompt_cache_key: str = ""
    retention: str = ""
    anthropic_ttl: str = ""
    cache_breakpoints: int = 0


@dataclass(frozen=True, slots=True)
class UsageMetrics:
    input_tokens_total: int
    input_tokens_uncached: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    total_tokens: int = 0
    ttft_ms: float | None = None
    tps: float | None = None
    actual_cost: float | None = None
    input_cost_multiplier: float = 1.0
    cache_hit_ratio: float = 0.0


def provider_family_for_model(
    model: str,
    *,
    provider_name: str | None = None,
    upstream_provider: str | None = None,
) -> str:
    if provider_name:
        return str(provider_name).strip().lower()
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    if upstream_provider:
        return str(upstream_provider).strip().lower()
    return "generic"


def apply_openai_cache_hints(
    body: dict[str, Any],
    *,
    model: str,
    session_id: str | None,
    step_type: str,
) -> CacheRequestPlan:
    tools = body.get("tools") or body.get("customTools") or []
    if not session_id and not tools and step_type == "general":
        return CacheRequestPlan(family="openai")

    key = _stable_prompt_cache_key(body, model=model, session_id=session_id, step_type=step_type)
    if key:
        body["prompt_cache_key"] = key

    retention = ""
    if _is_openai_cache_retention_model(model):
        retention = "24h" if (session_id and (step_type != "general" or tools)) else "in-memory"
        body["prompt_cache_retention"] = retention

    return CacheRequestPlan(
        family="openai",
        mode="prompt_cache_key",
        prompt_cache_key=key,
        retention=retention,
    )


def apply_anthropic_cache_breakpoints(
    body: dict[str, Any],
    *,
    session_id: str | None,
    step_type: str,
) -> CacheRequestPlan:
    tools = body.get("tools") or []
    messages = body.get("messages") or []
    breakpoints = 0

    # Anthropic requires cache_control TTLs to be non-increasing across
    # tools → system → messages.  Determine the effective TTL by taking the
    # longest of (a) our session-based preference and (b) any TTL already
    # present in the request so breakpoints we add never violate ordering.
    computed_ttl = "1h" if session_id and step_type != "general" else None
    existing_max = _max_existing_cache_ttl(body)
    use_1h = computed_ttl == "1h" or existing_max == "1h"
    cc: dict[str, Any] = {"type": "ephemeral", "ttl": "1h"} if use_1h else {"type": "ephemeral"}

    if tools:
        tools[-1]["cache_control"] = dict(cc)
        breakpoints += 1

    system = body.get("system")
    if isinstance(system, list) and system:
        system[-1]["cache_control"] = dict(cc)
        breakpoints += 1
    elif isinstance(system, str) and system.strip():
        body["system"] = [{"type": "text", "text": system, "cache_control": dict(cc)}]
        breakpoints += 1
    elif messages:
        for idx, message in enumerate(messages[:-1]):
            if message.get("role") not in {"system", "user", "assistant"}:
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                body_messages = list(messages)
                body_messages[idx] = {
                    **message,
                    "content": [{"type": "text", "text": content, "cache_control": dict(cc)}],
                }
                body["messages"] = body_messages
                breakpoints += 1
                break

    ttl = "1h" if use_1h else "5m"
    return CacheRequestPlan(
        family="anthropic",
        mode="cache_control",
        anthropic_ttl=ttl,
        cache_breakpoints=breakpoints,
    )


def strip_anthropic_cache_controls(body: dict[str, Any]) -> CacheRequestPlan:
    """Remove Anthropic-specific cache_control blocks from a request body."""
    for holder in _iter_anthropic_cache_control_holders(body):
        holder.pop("cache_control", None)
    return CacheRequestPlan(
        family="anthropic",
        mode="none",
    )


def _iter_anthropic_cache_control_holders(body: dict[str, Any]) -> list[dict[str, Any]]:
    holders: list[dict[str, Any]] = []

    tools = body.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict):
                holders.append(tool)

    system = body.get("system")
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                holders.append(block)

    messages = body.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            holders.append(message)
            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        holders.append(block)

    return holders


def _max_existing_cache_ttl(body: dict[str, Any]) -> str | None:
    """Return ``"1h"`` if any cache_control block in *body* uses that TTL."""
    for key in ("tools", "system", "messages"):
        section = body.get(key)
        if not isinstance(section, list):
            continue
        for item in section:
            if not isinstance(item, dict):
                continue
            if _cache_control_has_1h(item.get("cache_control")):
                return "1h"
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and _cache_control_has_1h(block.get("cache_control")):
                        return "1h"
    return None


def _cache_control_has_1h(cc: Any) -> bool:
    return isinstance(cc, dict) and cc.get("ttl") == "1h"


def parse_usage_metrics(
    content: bytes,
    model: str,
    pricing: dict[str, ModelPricing],
) -> UsageMetrics | None:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return None

    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}

    raw_prompt_tokens = _as_int(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
    output_tokens = _as_int(usage.get("completion_tokens", usage.get("output_tokens", 0)))
    total_tokens = _as_int(usage.get("total_tokens", 0))

    prompt_cache_hit = _as_int(
        usage.get("prompt_cache_hit_tokens", prompt_details.get("prompt_cache_hit_tokens", 0)),
    )
    prompt_cache_miss = _as_int(
        usage.get("prompt_cache_miss_tokens", prompt_details.get("prompt_cache_miss_tokens", 0)),
    )
    cache_read_input_tokens = _first_positive_int(
        usage.get("cache_read_input_tokens"),
        prompt_details.get("cache_read_input_tokens"),
        usage.get("cached_tokens"),
        prompt_details.get("cached_tokens"),
        prompt_cache_hit,
    )
    cache_write_input_tokens = _first_positive_int(
        usage.get("cache_creation_input_tokens"),
        prompt_details.get("cache_creation_input_tokens"),
        usage.get("cache_write_tokens"),
        prompt_details.get("cache_write_tokens"),
    )

    if prompt_cache_hit or prompt_cache_miss:
        input_tokens_uncached = prompt_cache_miss
        input_tokens_total = prompt_cache_hit + prompt_cache_miss
    elif "input_tokens" in usage and "prompt_tokens" not in usage:
        input_tokens_uncached = raw_prompt_tokens
        input_tokens_total = raw_prompt_tokens + cache_read_input_tokens + cache_write_input_tokens
    else:
        input_tokens_total = max(raw_prompt_tokens, raw_prompt_tokens + cache_write_input_tokens)
        input_tokens_uncached = max(raw_prompt_tokens - cache_read_input_tokens, 0)

    if total_tokens <= 0:
        total_tokens = input_tokens_total + output_tokens

    if (
        input_tokens_total <= 0
        and input_tokens_uncached <= 0
        and output_tokens <= 0
        and cache_read_input_tokens <= 0
        and cache_write_input_tokens <= 0
    ):
        return None

    mp = pricing.get(model)
    actual_cost: float | None = None
    input_cost_multiplier = 1.0
    if mp is not None:
        actual_cost = estimate_usage_cost(
            input_tokens_uncached=input_tokens_uncached,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            pricing=mp,
        )
        baseline_input_cost = (input_tokens_total / 1_000_000) * mp.input_price
        effective_input_cost = estimate_input_cost(
            input_tokens_uncached=input_tokens_uncached,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            pricing=mp,
        )
        if baseline_input_cost > 0:
            input_cost_multiplier = max(0.05, min(2.0, effective_input_cost / baseline_input_cost))

    ttft = usage.get("ttft", output_details.get("ttft"))
    tps = usage.get("tps", output_details.get("tps"))
    ttft_ms: float | None = None
    if isinstance(ttft, (int, float)) and ttft > 0:
        ttft_ms = float(ttft * 1000.0) if ttft < 100 else float(ttft)
    tokens_per_second: float | None = None
    if isinstance(tps, (int, float)) and tps > 0:
        tokens_per_second = float(tps)

    cache_hit_ratio = 0.0
    if input_tokens_total > 0:
        cache_hit_ratio = max(0.0, min(1.0, cache_read_input_tokens / float(input_tokens_total)))

    return UsageMetrics(
        input_tokens_total=input_tokens_total,
        input_tokens_uncached=input_tokens_uncached,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        total_tokens=total_tokens,
        ttft_ms=ttft_ms,
        tps=tokens_per_second,
        actual_cost=actual_cost,
        input_cost_multiplier=input_cost_multiplier,
        cache_hit_ratio=cache_hit_ratio,
    )


def parse_stream_usage_metrics(
    chunks: list[bytes],
    model: str,
    pricing: dict[str, ModelPricing],
) -> UsageMetrics | None:
    if not chunks:
        return None
    try:
        text = b"".join(chunks).decode("utf-8", errors="replace")
    except Exception:
        return None

    anthropic_usage: dict[str, Any] = {}
    latest: UsageMetrics | None = None

    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("usage"), dict):
            anthropic_usage.update(data["usage"])
            parsed = parse_usage_metrics(json.dumps({"usage": anthropic_usage}).encode("utf-8"), model, pricing)
            if parsed is not None:
                latest = parsed
            continue
        if not isinstance(data, dict):
            continue
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            anthropic_usage.update(message["usage"])
        if data.get("type") == "message_delta" and isinstance(data.get("usage"), dict):
            anthropic_usage.update(data["usage"])
        if anthropic_usage:
            parsed = parse_usage_metrics(
                json.dumps({"usage": anthropic_usage}).encode("utf-8"),
                model,
                pricing,
            )
            if parsed is not None:
                latest = parsed
    return latest


def estimate_input_cost(
    *,
    input_tokens_uncached: int,
    cache_read_input_tokens: int,
    cache_write_input_tokens: int,
    pricing: ModelPricing,
) -> float:
    cached_read_price = (
        pricing.cached_input_price
        if pricing.cached_input_price is not None
        else pricing.input_price
    )
    cache_write_price = (
        pricing.cache_write_price
        if pricing.cache_write_price is not None
        else pricing.input_price
    )
    return (
        (input_tokens_uncached / 1_000_000) * pricing.input_price
        + (cache_read_input_tokens / 1_000_000) * cached_read_price
        + (cache_write_input_tokens / 1_000_000) * cache_write_price
    )


def estimate_usage_cost(
    *,
    input_tokens_uncached: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_write_input_tokens: int,
    pricing: ModelPricing,
) -> float:
    return estimate_input_cost(
        input_tokens_uncached=input_tokens_uncached,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
        pricing=pricing,
    ) + ((output_tokens / 1_000_000) * pricing.output_price)


def _stable_prompt_cache_key(
    body: dict[str, Any],
    *,
    model: str,
    session_id: str | None,
    step_type: str,
) -> str:
    seed_parts = [model, session_id or "stateless", step_type]
    tools = body.get("tools") or body.get("customTools") or []
    if tools:
        try:
            seed_parts.append(json.dumps(tools, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        except TypeError:
            seed_parts.append(str(tools))
    system_parts: list[str] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system":
            system_parts.append(_message_text(msg))
    if system_parts:
        seed_parts.append("\n".join(system_parts)[:4096])
    digest = hashlib.sha256("\n".join(seed_parts).encode("utf-8")).hexdigest()[:24]
    return f"ur:{digest}"


def _is_openai_cache_retention_model(model: str) -> bool:
    normalized = (model or "").strip().lower()
    return normalized.startswith("openai/")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _first_positive_int(*values: Any) -> int:
    for value in values:
        parsed = _as_int(value)
        if parsed > 0:
            return parsed
    return 0
