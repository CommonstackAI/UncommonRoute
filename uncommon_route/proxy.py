"""OpenAI-compatible proxy server for UncommonRoute.

Accepts /v1/chat/completions, runs route() for virtual model names
(`uncommon-route/auto`, `uncommon-route/fast`, `uncommon-route/best`),
replaces the model field, and forwards to a configurable upstream
OpenAI-compatible API.

Non-routing model names are passed through unchanged.

Integrations:
  - Session persistence: sticky model per session, three-strike escalation
  - Spend control: per-request / hourly / daily / session limits

Usage:
    from uncommon_route.proxy import create_app, serve
    serve(port=8403, upstream="http://127.0.0.1:11434/v1")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, AsyncGenerator

from collections.abc import AsyncGenerator as _LifespanGen
from contextlib import asynccontextmanager

import httpx

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from uncommon_route.artifacts import ArtifactStore
from uncommon_route.calibration import get_active_route_confidence_calibrator
from uncommon_route.cache_support import (
    CacheRequestPlan,
    UsageMetrics,
    apply_anthropic_cache_breakpoints,
    apply_openai_cache_hints,
    estimate_usage_cost,
    parse_stream_usage_metrics,
    parse_usage_metrics,
    provider_family_for_model,
    strip_anthropic_cache_controls,
)
from uncommon_route.composition import CompositionPolicy, compose_messages_semantic, load_composition_policy
from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify, extract_features
from uncommon_route.router.config import (
    BASELINE_MODEL,
    DEFAULT_CONFIG,
    DEFAULT_MODEL_PRICING,
    VIRTUAL_MODEL_IDS,
    routing_mode_from_model,
    virtual_model_entries,
)
from uncommon_route.router.quality import (
    model_served_quality,
    normalize_served_quality,
    request_capability_lane,
)
from uncommon_route.router.signal_tuning import (
    DEFAULT_SIGNAL_TUNING,
    contextual_followup_floor_from_text,
    system_prompt_is_title_generation_sidechannel,
    system_prompt_has_structured_output_constraint,
    text_substance_score,
    vision_prompt_needs_medium_floor,
)
from uncommon_route.router.structural import estimate_tokens, estimate_output_budget
from uncommon_route.router.types import (
    ModelPricing,
    RequestRequirements,
    RoutingFeatures,
    RoutingInfeasibleError,
    RoutingMode,
    ServedQuality,
    Tier,
    WorkloadHints,
)
from uncommon_route.semantic import SemanticCallResult, SemanticCompressor
from uncommon_route.semantic import SideChannelTaskConfig, score_semantic_quality
from uncommon_route.session import RecentSessions, derive_session_id, derive_session_id_v2
from uncommon_route.normalize import (
    hash16,
    normalize_message_text,
    normalize_messages_to_hashes,
)
from uncommon_route.spend_control import SpendControl
from uncommon_route.stats import RouteRecord, RouteStats, record_to_recent_dict
from uncommon_route.traces import RequestTrace, TraceStore, prompt_hash as trace_prompt_hash
from uncommon_route.events import get_bus
from uncommon_route.feedback import FeedbackCollector
from uncommon_route.model_experience import ModelExperienceStore
from uncommon_route.paths import data_dir
from uncommon_route.providers import (
    ProvidersConfig,
    add_provider,
    load_providers,
    resolve_upstream_model,
    remove_provider,
    verify_key,
)
from uncommon_route.model_map import ModelMapper
from uncommon_route.routing_config_store import RoutingConfigStore
from uncommon_route.scene_store import SceneConfig, SceneStore, _serialize_scene
from uncommon_route.connections_store import ConnectionsStore, mask_api_key, resolve_primary_connection
from uncommon_route.anthropic_compat import (
    anthropic_to_openai_request,
    anthropic_to_openai_response,
    openai_to_anthropic_request,
    openai_to_anthropic_response,
    anthropic_error_response,
    AnthropicToOpenAIStreamConverter,
    OpenAIToAnthropicStreamConverter,
)
from uncommon_route.responses_compat import (
    OpenAIChatToResponsesStreamAdapter,
    openai_chat_response_to_responses,
    responses_to_openai_chat_request,
)
from uncommon_route.version import VERSION
from uncommon_route.content_capture import (
    extract_assistant_blocks_anthropic,
    extract_assistant_blocks_openai_chat,
    extract_assistant_blocks_openai_responses,
    parse_stream_assistant_content,
    truncate_content_payload,
)

logger = logging.getLogger("uncommon-route")
_debug_log = logging.getLogger("uncommon_route.debug_routing")

DEFAULT_UPSTREAM = ""
DEFAULT_PORT = int(os.environ.get("UNCOMMON_ROUTE_PORT", "8403"))
_RECURSION_GUARD_HEADER = "x-uncommon-route-recursion-guard"
_ORIGINAL_MODEL_HEADER = "x-uncommon-route-original-model"

# Cross-provider safe ceiling for outgoing max_tokens. Upstream gateways cap
# output below the model's native limit (e.g. GLM-4.6 via some gateways rejects
# >32768 even though the model supports 131k). Clients like Claude Code default
# to 64k assuming Anthropic; we re-route to cheaper models that reject it.
UPSTREAM_MAX_OUTPUT_TOKENS = 32_768

_LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost"}
_ADMIN_ENV_VAR = "UNCOMMON_ROUTE_ADMIN_TOKEN"


@dataclass(frozen=True, slots=True)
class TransportDecision:
    requested_transport: str
    selected_transport: str
    reason: str
    preference_source: str
    native_anthropic_transport: bool = False


def _admin_auth_failure(request: Request) -> JSONResponse | None:
    """Return a 401/403 response when admin endpoints should be blocked.

    Admin endpoints (connections, providers, spend write, routing-config write, selector write)
    can mutate API keys and routing behavior. Two policies:

      * ``UNCOMMON_ROUTE_ADMIN_TOKEN`` is set: require ``Authorization: Bearer <token>``.
      * Not set: only accept requests from a local client.
    """
    token = os.environ.get(_ADMIN_ENV_VAR, "").strip()
    if token:
        hdr = request.headers.get("authorization", "")
        if hdr.startswith("Bearer ") and hdr[7:].strip() == token:
            return None
        return JSONResponse({"error": f"admin token required (set {_ADMIN_ENV_VAR})"}, status_code=401)
    client_host = getattr(request.client, "host", "") if request.client else ""
    if client_host in _LOCAL_CLIENT_HOSTS:
        return None
    return JSONResponse(
        {
            "error": (
                "admin endpoints require a local client; "
                f"set {_ADMIN_ENV_VAR} to allow remote access"
            )
        },
        status_code=403,
    )


class _SpendReservation:
    """Serialize spend check+commit so concurrent requests cannot overrun limits.

    ``SpendControl.check`` reads the historical ledger; between check and the
    post-upstream ``record`` call there is an ``await`` where the event loop can
    run other coroutines, so two concurrent requests may both pass ``check`` on
    the same remaining budget and then both record. The reservation ring holds
    estimated costs of in-flight requests and adds them to the ``check`` denominator.
    """

    _MAX_AGE_S = 600.0  # drop stale reservations whose settle/release never fired

    def __init__(self, spend: SpendControl) -> None:
        self._spend = spend
        self._lock = asyncio.Lock()
        self._pending: dict[str, tuple[float, float]] = {}

    def _gc(self, now: float) -> float:
        live: dict[str, tuple[float, float]] = {}
        total = 0.0
        for rid, (amount, ts) in self._pending.items():
            if now - ts <= self._MAX_AGE_S:
                live[rid] = (amount, ts)
                total += amount
        self._pending = live
        return total

    async def reserve(self, reservation_id: str, estimated_cost: float):
        async with self._lock:
            pending_total = self._gc(time.time())
            result = self._spend.check(estimated_cost, additional_committed=pending_total)
            if result.allowed:
                self._pending[reservation_id] = (estimated_cost, time.time())
            return result

    async def update_reserve(self, reservation_id: str, estimated_cost: float):
        """Re-reserve a request under a new estimated cost (e.g. fallback model)."""
        async with self._lock:
            self._pending.pop(reservation_id, None)
            pending_total = self._gc(time.time())
            result = self._spend.check(estimated_cost, additional_committed=pending_total)
            if result.allowed:
                self._pending[reservation_id] = (estimated_cost, time.time())
            return result

    async def settle(
        self,
        reservation_id: str,
        actual_cost: float,
        *,
        model: str | None = None,
        action: str | None = None,
    ) -> None:
        async with self._lock:
            self._pending.pop(reservation_id, None)
            self._spend.record(actual_cost, model=model, action=action)

    async def release(self, reservation_id: str) -> None:
        async with self._lock:
            self._pending.pop(reservation_id, None)

_SETUP_GUIDE = """\
No upstream API configured. UncommonRoute is a routing layer — it needs an upstream LLM API to forward requests to.

Set one of the following:

  # Option 1: Any OpenAI-compatible API
  export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"
  export UNCOMMON_ROUTE_API_KEY="sk-..."

  # Option 2: Commonstack (multi-provider gateway)
  export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
  export UNCOMMON_ROUTE_API_KEY="csk-..."

  # Option 3: Local (Ollama, vLLM, etc.)
  export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"

Then restart:  uncommon-route serve
"""

VIRTUAL_MODELS = virtual_model_entries()

_http_client: httpx.AsyncClient | None = None

_WRAPPER_BLOCK_RE = re.compile(
    r"^<(?P<tag>[a-z0-9_-]+)>\s*(?P<body>.*?)\s*</(?P=tag)>\s*",
    re.IGNORECASE | re.DOTALL,
)
_WRAPPER_TAGS = {"system-reminder", "assistant-reminder", "user-prompt-submit-hook"}

_CURRENT_MSG_MARKER_RE = re.compile(
    r"\[Current\s+message\s*[-–—]\s*respond\s+to\s+this\]",
    re.IGNORECASE,
)
_HISTORY_CONTEXT_MARKER_RE = re.compile(
    r"^\[(?:Chat\s+messages\s+since\s+your\s+last\s+reply|Previous\s+conversation|Conversation\s+history)\s*[-–—]\s*for\s+context\]",
    re.IGNORECASE,
)
_SENDER_PREFIX_RE = re.compile(r"^(?:User|Human|Assistant|Tool(?::[^\n]*)?):\s*", re.IGNORECASE)

_WRAPPER_MARKERS = (
    "the following skills are available for use with the skill tool",
    "as you answer the user's questions, you can use the following context",
    "codebase and user instructions are shown below",
    "these instructions override any default behavior",
    "tags contain information from the system",
    "the system will automatically compress prior messages",
    "the user will primarily request you to perform software engineering tasks",
    "contents of /",
    "# claudemd",
    "# important-instruction-reminders",
    "# currentdate",
)


_active_pricing: dict[str, ModelPricing] = {}


def _get_pricing() -> dict[str, ModelPricing]:
    """Return live pricing when available, otherwise static fallback."""
    return _active_pricing or DEFAULT_MODEL_PRICING


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute dollar cost from token counts using the model pricing table."""
    mp = _get_pricing().get(model)
    if mp is None:
        return 0.0
    return (input_tokens / 1_000_000) * mp.input_price + (output_tokens / 1_000_000) * mp.output_price


def _estimate_baseline_cost(input_tokens: int, output_tokens: int) -> float:
    return _estimate_cost(BASELINE_MODEL, input_tokens, output_tokens)


def _estimate_cost_from_usage(model: str, usage: UsageMetrics) -> float | None:
    pricing = _get_pricing().get(model)
    if pricing is None:
        return None
    return estimate_usage_cost(
        input_tokens_uncached=usage.input_tokens_uncached,
        output_tokens=usage.output_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        cache_write_input_tokens=usage.cache_write_input_tokens,
        pricing=pricing,
    )


def _parse_usage_cost(content: bytes, model: str) -> float | None:
    usage = parse_usage_metrics(content, model, _get_pricing())
    if usage is None:
        return None
    return usage.actual_cost if usage.actual_cost is not None else _estimate_cost_from_usage(model, usage)


def _parse_usage_performance(content: bytes) -> tuple[float | None, float | None]:
    usage = parse_usage_metrics(content, "", _get_pricing())
    if usage is None:
        return None, None
    return usage.ttft_ms, usage.tps


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
    return _http_client


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _looks_like_wrapper_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    lower = " ".join(stripped.lower().split())
    if any(marker in lower for marker in _WRAPPER_MARKERS):
        return True
    match = _WRAPPER_BLOCK_RE.fullmatch(stripped)
    if match and match.group("tag").lower() in _WRAPPER_TAGS:
        return True
    return False


def _strip_wrapper_prefix(text: str) -> str:
    remaining = text.strip()
    while remaining:
        match = _WRAPPER_BLOCK_RE.match(remaining)
        if not match:
            break
        block = match.group(0).strip()
        if not _looks_like_wrapper_text(block):
            break
        remaining = remaining[match.end():].lstrip()
    if _looks_like_wrapper_text(remaining):
        return ""
    return remaining.strip()


def _extract_current_message(text: str) -> str | None:
    """Extract the actual user message from bracket-marker history context.

    Frameworks like OpenClaw wrap conversation history and the current message
    into a single user content string:

        [Chat messages since your last reply - for context]
        User: previous message
        Assistant: previous reply

        [Current message - respond to this]
        User: hi

    This function returns the text after the current-message marker, stripped
    of any sender prefix like "User: ".  Returns None if no marker is found.
    """
    match = _CURRENT_MSG_MARKER_RE.search(text)
    if not match:
        return None
    after = text[match.end():].strip()
    after = _SENDER_PREFIX_RE.sub("", after).strip()
    return after if after else None


def _extract_user_prompt_text(value: Any) -> str:
    if isinstance(value, str):
        current = _extract_current_message(value)
        if current:
            return current
        cleaned = _strip_wrapper_prefix(value)
        if cleaned:
            return cleaned
        return "" if _looks_like_wrapper_text(value) else value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict) or item.get("type") not in {"text", "input_text"}:
                continue
            raw = str(item.get("text", ""))
            current = _extract_current_message(raw)
            if current:
                parts.append(current)
                continue
            cleaned = _strip_wrapper_prefix(raw)
            if cleaned:
                parts.append(cleaned)
        if parts:
            return "\n".join(parts)
    return _content_text(value).strip()


def _extract_prompt(body: dict) -> tuple[str, str | None, int]:
    """Extract last user message, system prompt, and max_tokens from request body."""
    messages = body.get("messages", [])
    raw_max_tokens = body.get("max_tokens", body.get("max_completion_tokens", 4096))
    if raw_max_tokens is None:
        raw_max_tokens = body.get("max_completion_tokens", 4096)
    try:
        max_tokens = max(1, int(raw_max_tokens)) if raw_max_tokens is not None else 4096
    except (TypeError, ValueError):
        max_tokens = 4096

    prompt = ""
    system_prompt: str | None = None

    if _debug_log.isEnabledFor(logging.DEBUG):
        _debug_log.debug(
            "=== REQUEST STRUCTURE === messages=%d roles=%s",
            len(messages),
            [m.get("role") for m in messages],
        )
        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            raw_text = _content_text(content) if not isinstance(content, str) else content
            _debug_log.debug(
                "  msg[%d] role=%s content_type=%s len=%d preview=%.200s",
                i, role, type(content).__name__, len(raw_text), raw_text[:200],
            )

    for msg in reversed(messages):
        if msg.get("role") == "user" and not prompt:
            raw_content = msg.get("content", "")
            candidate = _extract_user_prompt_text(raw_content)
            if _debug_log.isEnabledFor(logging.DEBUG):
                raw_text = _content_text(raw_content) if not isinstance(raw_content, str) else raw_content
                _debug_log.debug(
                    "  LAST USER MSG: raw_len=%d extracted_len=%d raw_preview=%.300s extracted=%.300s",
                    len(raw_text), len(candidate), raw_text[:300], candidate[:300],
                )
            if candidate:
                prompt = candidate
        if msg.get("role") == "system" and system_prompt is None:
            text = _content_text(msg.get("content", ""))
            system_prompt = text if text else None

    if _debug_log.isEnabledFor(logging.DEBUG):
        _debug_log.debug(
            "  EXTRACTED: prompt_len=%d prompt=%.200s system_len=%d",
            len(prompt), prompt[:200],
            len(system_prompt) if system_prompt else 0,
        )

    return prompt, system_prompt, max_tokens


def _build_debug_response(prompt: str, system_prompt: str | None, routing_config=DEFAULT_CONFIG) -> dict:
    """Build a debug diagnostics response showing routing details."""
    result = classify(prompt, system_prompt, routing_config.scoring)
    decision = None
    routing_error = None
    try:
        decision = route(prompt, system_prompt, config=routing_config)
    except RoutingInfeasibleError as exc:
        routing_error = exc.infeasibility

    tier_boundaries = routing_config.scoring.tier_boundaries
    lines = [
        "UncommonRoute Debug",
        "",
    ]
    if decision is not None:
        lines.extend([
            f"Tier: {decision.tier.value} | Model: {decision.model}",
            f"Confidence: {decision.confidence:.2f} | Cost: ${decision.cost_estimate:.4f} | Savings: {decision.savings:.0%}",
            f"Reasoning: {decision.reasoning}",
            "",
        ])
    elif routing_error is not None:
        lines.append(f"Routing Error: {routing_error.message}")
        if routing_error.failed_constraints:
            lines.append(f"Failed Constraints: {', '.join(routing_error.failed_constraints)}")
        if routing_error.missing_capabilities:
            lines.append(f"Missing Capabilities: {', '.join(routing_error.missing_capabilities)}")
        if routing_error.max_cost is not None:
            lines.append(f"Max Cost: ${routing_error.max_cost:.6f}")
        if routing_error.cheapest_cost is not None:
            lines.append(f"Cheapest Feasible Cost: ${routing_error.cheapest_cost:.6f}")
        lines.append("")

    lines.extend([
        "Scoring",
        f"  Signals: {', '.join(result.signals)}",
        "",
        f"Tier Boundaries: SIMPLE <{tier_boundaries.simple_medium:.2f}"
        f" | MEDIUM <{tier_boundaries.medium_complex:.2f}"
        f" | COMPLEX >={tier_boundaries.medium_complex:.2f}",
    ])

    if decision is not None and decision.fallback_chain:
        lines.append("")
        lines.append("Fallback Chain (configured order):")
        for fb in decision.fallback_chain:
            lines.append(f"  {fb.model}: ${fb.cost_estimate:.4f} (budget: {fb.suggested_output_budget})")

    return {
        "id": f"chatcmpl-debug-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "uncommon-route/debug",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "\n".join(lines)},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def _stream_upstream(
    upstream_url: str,
    body: dict,
    headers: dict[str, str],
) -> AsyncGenerator[bytes, None]:
    """Stream response from upstream, yielding raw bytes."""
    client = _get_client()
    async with client.stream(
        "POST",
        upstream_url,
        json=body,
        headers=headers,
    ) as resp:
        async for chunk in resp.aiter_bytes():
            yield chunk


def _extract_assistant_text(content: bytes) -> str:
    try:
        data = json.loads(content)
    except Exception:
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    text = message.get("content", "")
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        parts: list[str] = []
        for item in text:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(text)


def _normalize_reasoning_content_chunk(raw: bytes) -> bytes:
    """Mirror reasoning_content into content for clients that only read content."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw

    lines = text.split("\n")
    changed = False
    for idx, line in enumerate(lines):
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            continue
        delta = first_choice.get("delta")
        if not isinstance(delta, dict):
            continue
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content and not delta.get("content"):
            delta["content"] = reasoning_content
            lines[idx] = f"data: {json.dumps(data, ensure_ascii=False)}"
            changed = True
    if not changed:
        return raw
    return "\n".join(lines).encode("utf-8")


def _recursion_guard_enabled(request: Request) -> bool:
    value = str(request.headers.get(_RECURSION_GUARD_HEADER, "")).strip().lower()
    return value in {"1", "true", "yes", "on", "internal"}


def _is_virtual_model_name(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return routing_mode_from_model(normalized) is not None


def _capture_enabled() -> bool:
    return os.environ.get("UNCOMMON_ROUTE_CAPTURE_CONTENT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def _build_capture_dict(
    body: dict, text: str, calls: list[dict[str, Any]], finish: str
) -> dict[str, Any]:
    sys_field = body.get("system", "")
    if isinstance(sys_field, list):
        system_text = " ".join(
            (b.get("text") or "")
            for b in sys_field
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        system_text = str(sys_field) if sys_field else ""
    raw_tools = body.get("tools") or body.get("customTools") or []
    raw = {
        "request_messages": list(body.get("messages") or []),
        "request_system": system_text,
        "request_tools_count": len(raw_tools) if isinstance(raw_tools, list) else 0,
        "response_text": text,
        "response_tool_calls": calls,
        "response_finish_reason": finish,
        "content_truncated": False,
    }
    cap_bytes = _content_cap_bytes()
    if cap_bytes <= 0:
        raw["content_truncated"] = False
        return raw
    truncated, was_trunc = truncate_content_payload(raw, cap_bytes=cap_bytes)
    truncated["content_truncated"] = was_trunc
    return truncated


def _content_cap_bytes() -> int:
    """Per-row size cap for captured content. 0 disables truncation entirely."""
    raw = os.environ.get("UNCOMMON_ROUTE_CONTENT_CAP_BYTES", "").strip()
    if not raw:
        return 256 * 1024
    try:
        return int(raw)
    except ValueError:
        return 256 * 1024


def _capture_non_streaming(
    body: dict, response_content: bytes, transport: str
) -> dict[str, Any]:
    """Return cold-field dict to merge into a RequestTrace, or {} if disabled."""
    if not _capture_enabled():
        return {}
    if transport == "anthropic-messages":
        text, calls, finish = extract_assistant_blocks_anthropic(response_content)
    elif transport == "openai-chat":
        text, calls, finish = extract_assistant_blocks_openai_chat(response_content)
    elif transport == "openai-responses":
        text, calls, finish = extract_assistant_blocks_openai_responses(response_content)
    else:
        return {}
    return _build_capture_dict(body, text, calls, finish)


def _capture_streaming(
    body: dict, stream_chunks: list[bytes], transport: str
) -> dict[str, Any]:
    if not _capture_enabled():
        return {}
    text, calls, finish = parse_stream_assistant_content(stream_chunks, transport)
    return _build_capture_dict(body, text, calls, finish)


class UpstreamSemanticCompressor:
    """Runs semantic compression tasks through cheap upstream models."""

    def __init__(
        self,
        *,
        upstream_chat: str,
        primary_api_key: str,
        providers_config: ProvidersConfig,
        model_mapper: ModelMapper,
        composition_policy: CompositionPolicy,
    ) -> None:
        self._upstream_chat = upstream_chat
        self._primary_api_key = primary_api_key
        self._providers = providers_config
        self._mapper = model_mapper
        self._policy = composition_policy

    def rebind_primary(
        self,
        *,
        upstream_chat: str,
        primary_api_key: str,
        model_mapper: ModelMapper,
    ) -> None:
        self._upstream_chat = upstream_chat
        self._primary_api_key = primary_api_key
        self._mapper = model_mapper

    def rebind_providers(self, providers_config: ProvidersConfig) -> None:
        self._providers = providers_config

    async def summarize_tool_result(
        self,
        content: str,
        *,
        tool_name: str,
        latest_user_prompt: str,
        request: Request,
    ) -> SemanticCallResult | None:
        system = (
            "You compress tool outputs for another model. Preserve facts, paths, errors, "
            "identifiers, counts, and anything actionable. Output plain text only."
        )
        user = (
            f"Latest user goal:\n{latest_user_prompt}\n\n"
            f"Tool: {tool_name or 'unknown'}\n"
            "Summarize the following tool result for continuation in under 220 words.\n\n"
            f"{content}"
        )
        return await self._run_task(
            request,
            self._policy.sidechannel.tool_summary,
            system,
            user,
            source_text=content,
            query_text=f"{latest_user_prompt} {tool_name}".strip(),
        )

    async def summarize_history(
        self,
        transcript: str,
        *,
        latest_user_prompt: str,
        session_id: str,
        request: Request,
    ) -> SemanticCallResult | None:
        system = (
            "You compress earlier conversation turns into durable working memory. Preserve goal, "
            "decisions, files, commands, errors, unresolved issues, and next steps. Plain text only."
        )
        user = (
            f"Session: {session_id or '-'}\n"
            f"Latest user goal:\n{latest_user_prompt}\n\n"
            "Summarize the earlier transcript for future continuation in under 300 words.\n\n"
            f"{transcript}"
        )
        return await self._run_task(
            request,
            self._policy.sidechannel.checkpoint,
            system,
            user,
            source_text=transcript,
            query_text=latest_user_prompt,
        )

    async def rehydrate_artifact(
        self,
        query: str,
        *,
        artifact_id: str,
        content: str,
        summary: str,
        request: Request,
    ) -> SemanticCallResult | None:
        system = (
            "Extract only the minimum artifact context needed for the current user request. "
            "Prefer raw facts and snippets over explanation. Plain text only."
        )
        seed = f"Existing summary:\n{summary}\n\n" if summary else ""
        user = (
            f"Current user query:\n{query}\n\n"
            f"Artifact: artifact://{artifact_id}\n\n"
            f"{seed}"
            "Return the most relevant excerpt in under 180 words.\n\n"
            f"{content}"
        )
        return await self._run_task(
            request,
            self._policy.sidechannel.rehydrate,
            system,
            user,
            source_text=content,
            query_text=query,
        )

    async def _run_task(
        self,
        request: Request,
        task: SideChannelTaskConfig,
        system_prompt: str,
        user_prompt: str,
        *,
        source_text: str,
        query_text: str,
    ) -> SemanticCallResult | None:
        input_tokens = estimate_tokens(system_prompt) + estimate_tokens(user_prompt)
        client = _get_client()
        quality_fallbacks = 0
        attempts = 0
        for model_id in task.candidates():
            attempts += 1
            resolved = self._resolve_request(model_id, request)
            if resolved is None:
                continue
            target_chat_url, headers, upstream_model = resolved
            payload = {
                "model": upstream_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": task.max_tokens,
                "stream": False,
            }
            try:
                resp = await client.post(target_chat_url, json=payload, headers=headers)
            except (httpx.ConnectError, httpx.TimeoutException):
                continue
            if resp.status_code >= 400:
                if resp.status_code in (400, 404, 422) and _is_model_error(resp.content):
                    continue
                continue
            text = _extract_assistant_text(resp.content).strip()
            if not text:
                continue
            ok, quality_score, _reason = score_semantic_quality(
                text,
                source_text=source_text,
                query_text=query_text,
                policy=task.quality,
            )
            if not ok:
                quality_fallbacks += 1
                continue
            actual_cost = _parse_usage_cost(resp.content, model_id)
            estimated_cost = _estimate_cost(model_id, input_tokens, task.max_tokens)
            return SemanticCallResult(
                text=text,
                model=model_id,
                estimated_cost=estimated_cost,
                actual_cost=actual_cost,
                quality_score=quality_score,
                attempts=attempts,
                quality_fallbacks=quality_fallbacks,
            )
        return None

    def _resolve_request(self, model_id: str, request: Request) -> tuple[str, dict[str, str], str] | None:
        provider_entry = self._providers.get_for_model(model_id)
        if provider_entry and provider_entry.base_url:
            target_chat_url = f"{provider_entry.base_url.rstrip('/')}/chat/completions"
            upstream_model = resolve_upstream_model(provider_entry.name, model_id)
        elif self._upstream_chat:
            target_chat_url = self._upstream_chat
            upstream_model = self._mapper.resolve(model_id)
        else:
            return None

        headers: dict[str, str] = {
            "content-type": "application/json",
            "user-agent": f"uncommon-route/{VERSION} semantic",
        }
        if provider_entry:
            headers["authorization"] = f"Bearer {provider_entry.api_key}"
        else:
            auth = request.headers.get("authorization")
            if auth:
                headers["authorization"] = auth
            elif self._primary_api_key:
                headers["authorization"] = f"Bearer {self._primary_api_key}"
        return target_chat_url, headers, upstream_model


_OPENCLAW_SESSION_HEADER = "x-openclaw-session-key"


def _resolve_session_id(request: Request, body: dict) -> str | None:
    """Derive a session ID for cache keys and composition (not routing)."""
    raw_headers = {k: v for k, v in request.headers.items()}
    sid = raw_headers.get("x-session-id") or raw_headers.get(_OPENCLAW_SESSION_HEADER)
    if sid:
        return sid
    messages = body.get("messages", [])
    return derive_session_id(messages)


# Process-wide registry for derive_session_id_v2 (shadow mode).
_SESSION_V2_REGISTRY = RecentSessions(
    capacity=int(os.environ.get("UNCOMMON_ROUTE_SESSION_TABLE_SIZE", "5000")),
    ttl_seconds=float(os.environ.get("UNCOMMON_ROUTE_SESSION_TTL_S", "21600")),
)


def _extract_session_v2_inputs(
    request: Request, body: dict
) -> dict[str, Any]:
    """Compute session_id_v2 inputs and shadow output for this request.

    Returns a dict suitable for **-splatting into RequestTrace(...).
    """
    messages = body.get("messages") or []
    msg_hashes = normalize_messages_to_hashes(messages)
    first_user_v2 = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            first_user_v2 = hash16(normalize_message_text(m))
            break
    system_text = ""
    sys_field = body.get("system")
    if isinstance(sys_field, str):
        system_text = sys_field
    elif isinstance(sys_field, list):
        system_text = " ".join(
            (b.get("text") or "")
            for b in sys_field
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                system_text = normalize_message_text(m)
                break
    system_h = hash16(system_text) if system_text else ""

    metadata_uid = ""
    md = body.get("metadata") or {}
    if isinstance(md, dict):
        metadata_uid = str(md.get("user_id", "") or "")

    prev_response = str(body.get("previous_response_id", "") or "")
    headers = {k.lower(): v for k, v in request.headers.items()}
    user_agent = headers.get("user-agent", "")

    sid_v2 = derive_session_id_v2(
        msg_hashes=msg_hashes,
        first_user_v2=first_user_v2,
        system_hash=system_h,
        metadata_uid=metadata_uid,
        prev_response=prev_response,
        registry=_SESSION_V2_REGISTRY,
        now=time.time(),
    )

    return {
        "messages_count": len(messages),
        "msg_hashes": msg_hashes,
        "first_user_hash_v2": first_user_v2,
        "system_hash": system_h,
        "metadata_user_id": metadata_uid,
        "previous_response_id": prev_response,
        "user_agent": user_agent,
        "session_id_v2": sid_v2,
    }


def _classify_step(body: dict) -> tuple[str, list[str]]:
    """Classify the current agentic step from the request body.

    Returns (step_type, tool_names) where step_type is one of:
      - "tool-result-followup": last message is a tool result
      - "tool-selection": tools available, last message is from user
      - "general": no agentic signals

    tool_names: function names from the tools array (for hash differentiation).

    Checks both ``tools`` (standard OpenAI) and ``customTools`` (OpenClaw's
    internal format when ``compat.openaiCompletionsTools`` is not enabled).
    """
    messages = body.get("messages", [])
    raw_tools: list[dict[str, Any]] = body.get("tools") or body.get("customTools") or []
    has_tools = bool(raw_tools)

    tool_names: list[str] = []
    for t in raw_tools:
        fn = t.get("function") or t.get("definition") or {}
        name = fn.get("name") or t.get("name") or ""
        if name:
            tool_names.append(name)

    last_message: dict[str, Any] | None = None
    for msg in reversed(messages):
        if msg.get("role") != "system":
            last_message = msg
            break

    last_role = last_message.get("role", "") if isinstance(last_message, dict) else ""
    last_content = last_message.get("content") if isinstance(last_message, dict) else None

    if last_role == "tool":
        return "tool-result-followup", tool_names

    if isinstance(last_content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in last_content
    ):
        return "tool-result-followup", tool_names

    if has_tools and last_role == "user":
        return "tool-selection", tool_names

    return "general", tool_names


def _has_vision_content(value: Any) -> bool:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                if item.get("type") in ("image_url", "input_image"):
                    return True
                if _has_vision_content(item.get("content")):
                    return True
            elif _has_vision_content(item):
                return True
    elif isinstance(value, dict):
        if value.get("type") in ("image_url", "input_image"):
            return True
        if "image_url" in value:
            return True
        return _has_vision_content(value.get("content"))
    return False


def _body_has_structured_output_directive(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        if system_prompt_has_structured_output_constraint(_content_text(message.get("content", ""))):
            return True
    return False


def _body_has_title_generation_sidechannel(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        if system_prompt_is_title_generation_sidechannel(_content_text(message.get("content", ""))):
            return True
    return False


def _contextual_followup_floor(messages: list[Any], prompt: str) -> Tier | None:
    user_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    if len(user_indexes) < 2:
        return None

    latest_index = user_indexes[-1]
    latest = prompt or _extract_user_prompt_text(messages[latest_index].get("content", ""))
    prior_parts: list[str] = []
    for message in messages[:latest_index]:
        if not isinstance(message, dict) or message.get("role") == "system":
            continue
        if message.get("role") == "user":
            text = _extract_user_prompt_text(message.get("content", ""))
        else:
            text = _content_text(message.get("content", ""))
        if text.strip():
            prior_parts.append(text)
    prior_context = "\n".join(prior_parts)
    return contextual_followup_floor_from_text(
        prior_text=prior_context,
        latest_text=latest,
    )


def _vision_analysis_floor(has_vision: bool, prompt: str) -> Tier | None:
    if vision_prompt_needs_medium_floor(has_vision=has_vision, prompt=prompt):
        return Tier.MEDIUM
    return None


_HIGH_RISK_TOOL_MARKERS = (
    "traceback",
    "exception",
    "stack trace",
    "assertionerror",
    "syntaxerror",
    "typeerror",
    "valueerror",
    "invalid_request_error",
    "connectionrefused",
    "timed out",
    "timeout",
    "permission denied",
    "no such file",
    "failed",
    "failure",
    "fatal",
    "panic",
    "segmentation fault",
    "400 bad request",
    "500 internal",
    "api error",
    "error:",
    "error trace_id",
    "报错",
    "错误",
    "异常",
    "失败",
    "堆栈",
    "超时",
    "拒绝连接",
)

_RETRY_PROMPT_MARKERS = (
    "again",
    "retry",
    "rerun",
    "re-run",
    "try again",
    "continue",
    "继续",
    "重试",
    "再试",
    "再来",
    "重新",
)

_SUCCESSFUL_FAILURE_SUMMARY_RE = re.compile(
    r"\b(?:0\s+(?:failed|failures?|errors?)|(?:failed|failures?|errors?)\s*[:=]\s*0|no\s+(?:failures?|errors?))\b",
    re.IGNORECASE,
)
_NONZERO_FAILURE_SUMMARY_RE = re.compile(
    r"\b(?:[1-9]\d*\s+(?:failed|failures?|errors?)|(?:failed|failures?|errors?)\s*[:=]\s*[1-9]\d*)\b",
    re.IGNORECASE,
)
_EXPLICIT_FAIL_STATUS_RE = re.compile(
    r"(?:^|\n)\s*(?:[\w./:= -]+\s*[:=]\s*)?FAIL(?:\s|$)",
    re.IGNORECASE,
)
_VERIFICATION_CONTEXT_MARKERS = (
    "final verification",
    "verification",
    "verify",
    "test from",
    "tests:",
    "pytest",
    "unittest",
    "runtests",
    "failures",
    "assertionerror",
    "expected",
    "actual",
    "lint",
    "typecheck",
    "type-check",
    "mypy",
    "tsc",
    "eslint",
    "ruff",
)
_NONZERO_EXIT_STATUS_RE = re.compile(
    r"\b(?:exit(?:ed)?(?:\s+with)?\s+(?:status|code)|exit\s+status|return\s+code)\s*[:=]?\s*[1-9]\d*\b",
    re.IGNORECASE,
)
_XML_RETURN_CODE_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>", re.IGNORECASE)
_DEPENDENCY_RECOVERY_RE = re.compile(
    r"\b(?:modulenotfounderror|importerror)\b[^\n]*(?:no module named|module named|cannot import)",
    re.IGNORECASE,
)

_ENVIRONMENT_RECOVERY_MARKERS = (
    "no module named",
    "module not found",
    "could not find a version",
    "no matching distribution",
    "successfully installed",
    "successfully uninstalled",
    "editable installation",
    "source checkout",
    "build_ext",
    "site-packages/numpy",
    "module 'numpy' has no attribute",
)

_ENVIRONMENT_COMMAND_MARKERS = (
    "pip install",
    "uv pip",
    "poetry install",
    "pip-sync",
    "python setup.py",
    "build_ext",
)
_INVOCATION_FAILURE_MARKERS = (
    "unittest.loader._failedtest",
    "failedtest",
    "failed to import test module",
    "error importing test module",
    "no tests ran",
    "not found:",
    "module has no attribute",
)
_ROUTINE_SUCCESS_COMMAND_MARKERS = (
    "git status",
    "git diff --stat",
    "git rev-parse",
    "pwd",
    "mkdir",
    "touch ",
    "cat >",
    "tee ",
)
_SUCCESSFUL_TEST_SUMMARY_RE = re.compile(
    r"\b(?:\d+\s+passed|ran\s+\d+\s+tests?.*\bok\b|test suites?:.*passed|tests?:.*passed|ok\s+[\w./-]+)\b",
    re.IGNORECASE | re.DOTALL,
)
_GENERIC_ROUTINE_SUCCESS_RE = re.compile(
    r"^\s*(?:done|ok|success|successful|completed)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_READ_ONLY_COMMAND_RE = re.compile(
    r"^\s*(?:"
    r"cat|sed\b|grep\b|rg\b|find\b|ls\b|head\b|tail\b|"
    r"git\s+(?:diff|status|show|log|rev-parse)\b"
    r")",
    re.IGNORECASE,
)


def _contains_risk_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _has_nonzero_xml_returncode(text: str) -> bool:
    match = _XML_RETURN_CODE_RE.search(text or "")
    if match is None:
        return False
    try:
        return int(match.group(1)) != 0
    except ValueError:
        return False


def _has_zero_xml_returncode(text: str) -> bool:
    match = _XML_RETURN_CODE_RE.search(text or "")
    if match is None:
        return False
    try:
        return int(match.group(1)) == 0
    except ValueError:
        return False


def _command_is_read_only_observation(command: str) -> bool:
    return bool(_READ_ONLY_COMMAND_RE.search(command or ""))


def _has_verification_context(text: str, command: str = "") -> bool:
    haystack = f"{command}\n{text}".lower()
    return any(marker in haystack for marker in _VERIFICATION_CONTEXT_MARKERS)


def _contains_tool_failure_signal(text: str, command: str = "") -> bool:
    if (
        _has_zero_xml_returncode(text)
        and _command_is_read_only_observation(command)
        and not _NONZERO_FAILURE_SUMMARY_RE.search(text)
    ):
        summary_stripped = _SUCCESSFUL_FAILURE_SUMMARY_RE.sub(" ", text)
        if not (
            _has_verification_context(text, command)
            and _EXPLICIT_FAIL_STATUS_RE.search(summary_stripped)
        ):
            return False

    if (
        _has_nonzero_xml_returncode(text)
        or _NONZERO_FAILURE_SUMMARY_RE.search(text)
        or _NONZERO_EXIT_STATUS_RE.search(text)
    ):
        return True
    summary_stripped = _SUCCESSFUL_FAILURE_SUMMARY_RE.sub(" ", text)
    if _has_verification_context(text, command) and _EXPLICIT_FAIL_STATUS_RE.search(summary_stripped):
        return True
    if not _contains_risk_marker(text, _HIGH_RISK_TOOL_MARKERS):
        return False
    lowered = text.lower()
    if "traceback" in lowered or "exception" in lowered or "error:" in lowered:
        return True
    failure_words = ("failed", "failure", "失败")
    summary_stripped = summary_stripped.lower()
    has_remaining_failure = any(word in summary_stripped for word in failure_words)
    if not has_remaining_failure and _SUCCESSFUL_FAILURE_SUMMARY_RE.search(text) and not any(
        marker in lowered
        for marker in (
            "assertionerror",
            "syntaxerror",
            "typeerror",
            "valueerror",
            "invalid_request_error",
            "fatal",
            "panic",
            "400 bad request",
            "500 internal",
            "报错",
            "错误",
            "异常",
        )
    ):
        return False
    return True


def _contains_environment_recovery_signal(text: str, command: str = "") -> bool:
    haystack = f"{command}\n{text}".lower()
    return (
        bool(_DEPENDENCY_RECOVERY_RE.search(text or ""))
        or _contains_risk_marker(haystack, _ENVIRONMENT_COMMAND_MARKERS)
        or _contains_risk_marker(haystack, _ENVIRONMENT_RECOVERY_MARKERS)
    )


def _tool_result_failure_kind(text: str, command: str = "") -> str:
    """Classify a tool failure by what the next routing step should optimize.

    Semantic failures are evidence about the patch or answer quality.
    Environment/invocation failures are operational noise: keep them visible as
    high-risk, but do not let them masquerade as complex reasoning failures.
    """
    if not text:
        return ""
    haystack = f"{command}\n{text}".lower()
    if _contains_environment_recovery_signal(text, command):
        return "environment"
    if any(marker in haystack for marker in _INVOCATION_FAILURE_MARKERS):
        return "invocation"
    if _contains_tool_failure_signal(text, command):
        if _has_verification_context(text, command) or _NONZERO_FAILURE_SUMMARY_RE.search(text):
            return "semantic"
        return "unknown"
    return ""


def _tool_result_is_routine_success(text: str, is_error: bool, command: str) -> bool:
    if is_error or _contains_tool_failure_signal(text, command):
        return False
    return (
        bool(_SUCCESSFUL_TEST_SUMMARY_RE.search(text or ""))
        or bool(_GENERIC_ROUTINE_SUCCESS_RE.search(text or ""))
        or _contains_risk_marker(command, _ROUTINE_SUCCESS_COMMAND_MARKERS)
    )


def _tool_result_is_short_success_observation(text: str, is_error: bool, command: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 800:
        return False
    if _contains_environment_recovery_signal(text, command):
        return False
    if _tool_result_is_routine_success(text, is_error, command):
        return False
    return not is_error and not _contains_tool_failure_signal(text, command)


def _risk_text(value: Any) -> str:
    """Flatten text-bearing content, including Anthropic tool_result blocks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_risk_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "message", "error", "stderr", "stdout"):
            if key in value:
                parts.append(_risk_text(value.get(key)))
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _tool_call_command(tool_call: dict[str, Any]) -> str:
    fn = tool_call.get("function") or {}
    if not isinstance(fn, dict):
        return ""
    raw_args = fn.get("arguments")
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError:
            return raw_args
        if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
            return str(parsed["command"])
    return ""


def _command_for_tool_result(
    messages: list[Any],
    *,
    before_index: int,
    tool_call_id: str,
) -> str:
    if not tool_call_id:
        return ""
    for prior in reversed(messages[:before_index]):
        if not isinstance(prior, dict):
            continue
        extra = prior.get("extra") or {}
        if isinstance(extra, dict):
            for action in extra.get("actions") or ():
                if (
                    isinstance(action, dict)
                    and action.get("tool_call_id") == tool_call_id
                    and isinstance(action.get("command"), str)
                ):
                    return str(action["command"])
        for tc in prior.get("tool_calls") or ():
            if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                command = _tool_call_command(tc)
                if command:
                    return command
    return ""


def _latest_tool_result_context(messages: list[Any]) -> tuple[str, bool, str]:
    for index in range(len(messages) - 1, -1, -1):
        msg = messages[index]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            text = _risk_text(msg.get("content"))
            command = _command_for_tool_result(
                messages,
                before_index=index,
                tool_call_id=str(msg.get("tool_call_id") or ""),
            )
            return text, bool(msg.get("is_error")) or _has_nonzero_xml_returncode(text), command
        content = msg.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    text = _risk_text(block.get("content"))
                    command = _command_for_tool_result(
                        messages,
                        before_index=index,
                        tool_call_id=str(block.get("tool_use_id") or ""),
                    )
                    return (
                        text,
                        bool(block.get("is_error")) or _has_nonzero_xml_returncode(text),
                        command,
                    )
    return "", False, ""


def _latest_tool_result_signal(messages: list[Any]) -> tuple[str, bool]:
    text, is_error, _command = _latest_tool_result_context(messages)
    return text, is_error


def _last_non_system_message(messages: list[Any]) -> dict[str, Any] | None:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") != "system":
            return msg
    return None


def _current_step_tool_result_signal(messages: list[Any], step_type: str) -> tuple[str, bool]:
    if step_type != "tool-result-followup":
        return "", False
    text, is_error, _command = _latest_tool_result_context(messages)
    return text, is_error


def _current_step_tool_result_context(
    messages: list[Any],
    step_type: str,
) -> tuple[str, bool, str]:
    if step_type != "tool-result-followup":
        return "", False, ""
    return _latest_tool_result_context(messages)


def _tool_result_is_environment_recovery(text: str, is_error: bool, command: str) -> bool:
    if _contains_environment_recovery_signal(text, command):
        return True
    return False


_REASONING_DISABLED_VALUES = {"", "none", "off", "false", "disabled", "disable"}
_REASONING_FLOOR_VALUES = {"medium", "high", "xhigh", "x-high", "max"}
_TIER_RANK = {Tier.SIMPLE: 0, Tier.MEDIUM: 1, Tier.COMPLEX: 2}
_SUGGESTION_MODE_RE = re.compile(r"^\s*\[SUGGESTION MODE:", re.IGNORECASE)


def _max_tier(left: Tier | None, right: Tier | None) -> Tier | None:
    if left is None:
        return right
    if right is None:
        return left
    return left if _TIER_RANK[left] >= _TIER_RANK[right] else right


def _reasoning_preference(body: dict[str, Any]) -> tuple[bool, Tier | None]:
    """Detect explicit reasoning/thinking controls from OpenAI and Anthropic shaped requests."""
    prefers_reasoning = False
    tier_floor: Tier | None = None

    def mark(value: Any, *, floor_medium: bool = False) -> None:
        nonlocal prefers_reasoning, tier_floor
        normalized = str(value or "").strip().lower()
        if normalized in _REASONING_DISABLED_VALUES:
            return
        prefers_reasoning = True
        if floor_medium or normalized in _REASONING_FLOOR_VALUES:
            tier_floor = _max_tier(tier_floor, Tier.MEDIUM)

    if "reasoning_effort" in body:
        mark(body.get("reasoning_effort"))

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        if effort is not None:
            mark(effort)
        elif reasoning:
            mark("medium", floor_medium=True)
    elif reasoning is not None:
        mark(reasoning)

    thinking = body.get("thinking")
    if isinstance(thinking, dict):
        thinking_type = str(thinking.get("type") or "").strip().lower()
        budget = thinking.get("budget_tokens")
        budget_enabled = False
        try:
            budget_enabled = budget is not None and int(budget) > 0
        except (TypeError, ValueError):
            budget_enabled = bool(budget)
        if thinking_type not in _REASONING_DISABLED_VALUES and (thinking_type or budget_enabled):
            mark("medium", floor_medium=True)
    elif thinking is not None:
        mark(thinking, floor_medium=True)

    if _contains_anthropic_thinking_blocks(body):
        # Prior signed thinking blocks are a transport/model-continuity
        # constraint, not evidence that the latest user ask is complex.
        pass

    return prefers_reasoning, tier_floor


def _is_suggestion_mode_prompt(prompt: str) -> bool:
    """Detect Claude Code's autocomplete prompt wrapper."""
    return bool(_SUGGESTION_MODE_RE.match(str(prompt or "")))


def _estimate_step_risk(
    *,
    messages: list[Any],
    step_type: str,
    tool_names: tuple[str, ...],
    prompt: str,
    needs_tool_calling: bool,
    wants_structured_output: bool,
) -> str:
    tool_result_text, tool_result_is_error, tool_command = _current_step_tool_result_context(messages, step_type)
    previous_tool_result_text, previous_tool_result_is_error = _latest_tool_result_signal(messages)
    prompt_text = str(prompt or "")
    if _is_suggestion_mode_prompt(prompt_text):
        return "low"
    prompt_has_high_risk_shape = (
        needs_tool_calling
        and text_substance_score(prompt_text)
        >= DEFAULT_SIGNAL_TUNING.tool_prompt_high_risk_substance_score
    )

    if step_type == "tool-result-followup":
        if tool_result_is_error:
            return "high"
        if _contains_tool_failure_signal(tool_result_text, tool_command):
            return "high"
        if _tool_result_is_routine_success(tool_result_text, tool_result_is_error, tool_command):
            return "low"
        if _tool_result_is_short_success_observation(tool_result_text, tool_result_is_error, tool_command):
            return "normal"
        return "normal"

    retrying_previous_tool = (
        _contains_risk_marker(prompt_text, _RETRY_PROMPT_MARKERS)
        and (
            previous_tool_result_is_error
            or _contains_tool_failure_signal(previous_tool_result_text)
        )
    )
    if retrying_previous_tool or prompt_has_high_risk_shape:
        return "high"

    if step_type == "tool-selection" and len(prompt_text) <= 40 and len(tool_names) <= 6:
        return "low"
    if not needs_tool_calling and len(prompt_text) <= 80:
        return "low"
    return "normal"


def _agent_state_pressure(messages: list[Any], step_risk: str) -> tuple[int, float]:
    """Continuous pressure signal for long or failure-heavy agent trajectories."""
    tool_steps = 0
    failure_steps = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("tool_calls"):
            tool_steps += 1
        tool_text, tool_is_error = _latest_tool_result_signal([msg])
        if msg.get("role") == "tool" or tool_text:
            tool_steps += 1
            command = ""
            if tool_is_error or _contains_tool_failure_signal(tool_text, command):
                failure_steps += 1

    step_component = min(0.45, max(0, tool_steps) / 24.0)
    failure_component = min(0.35, failure_steps / 6.0)
    risk_component = 0.20 if str(step_risk or "").lower() == "high" else 0.0
    return tool_steps, min(1.0, step_component + failure_component + risk_component)


def _extract_routing_features(
    body: dict,
    *,
    step_type: str,
    tool_names: list[str] | tuple[str, ...] | None = None,
    prompt: str = "",
    max_output_tokens: int = 4096,
    session_id: str | None = None,
) -> RoutingFeatures:
    """Extract routing features from the current request step."""
    messages = body.get("messages", [])
    raw_tools = body.get("tools") or body.get("customTools") or []
    normalized_tool_names = tuple(tool_names or ())
    if not normalized_tool_names:
        derived_tool_names: list[str] = []
        for tool in raw_tools:
            fn = tool.get("function") or tool.get("definition") or {}
            name = str(fn.get("name") or tool.get("name") or "").strip()
            if name:
                derived_tool_names.append(name)
        normalized_tool_names = tuple(derived_tool_names)

    has_vision = any(_has_vision_content(msg.get("content")) for msg in messages if isinstance(msg, dict))
    needs_tool_calling = bool(raw_tools)
    has_tool_results = step_type == "tool-result-followup"
    suggestion_mode = _is_suggestion_mode_prompt(prompt)
    title_generation_sidechannel = _body_has_title_generation_sidechannel(messages)

    response_format = body.get("response_format")
    response_format_name: str | None = None
    wants_structured_output = False
    if isinstance(response_format, dict):
        response_format_name = str(response_format.get("type") or "json_schema").strip().lower() or "json_schema"
        wants_structured_output = True
    elif isinstance(response_format, str):
        response_format_name = response_format.strip().lower() or None
        wants_structured_output = response_format_name in {"json", "json_schema"}
    if (
        not title_generation_sidechannel
        and not wants_structured_output
        and _body_has_structured_output_directive(messages)
    ):
        response_format_name = "system"
        wants_structured_output = True

    step_risk = (
        "low"
        if title_generation_sidechannel
        else _estimate_step_risk(
            messages=messages,
            step_type=step_type,
            tool_names=normalized_tool_names,
            prompt=prompt,
            needs_tool_calling=needs_tool_calling,
            wants_structured_output=wants_structured_output,
        )
    )
    contextual_floor = (
        None
        if suggestion_mode or title_generation_sidechannel
        else _contextual_followup_floor(messages, prompt)
    )
    if contextual_floor is not None and step_risk == "low":
        step_risk = "normal"
    tier_floor = Tier.MEDIUM if step_risk == "high" or wants_structured_output else None
    tier_floor = _max_tier(tier_floor, contextual_floor)
    tier_floor = _max_tier(tier_floor, _vision_analysis_floor(has_vision, prompt))
    tool_result_text, tool_result_is_error, tool_command = _current_step_tool_result_context(
        messages,
        step_type,
    )
    failure_kind = (
        _tool_result_failure_kind(tool_result_text, tool_command)
        if step_type == "tool-result-followup"
        else ""
    )
    environment_recovery = (
        step_type == "tool-result-followup"
        and failure_kind == "environment"
    )
    invocation_recovery = (
        step_type == "tool-result-followup"
        and failure_kind == "invocation"
    )
    routine_success = (
        step_type == "tool-result-followup"
        and _tool_result_is_routine_success(tool_result_text, tool_result_is_error, tool_command)
    )
    short_success_observation = (
        step_type == "tool-result-followup"
        and _tool_result_is_short_success_observation(tool_result_text, tool_result_is_error, tool_command)
    )
    tier_cap = (
        Tier.SIMPLE
        if suggestion_mode or title_generation_sidechannel
        else (
            Tier.MEDIUM
            if step_risk == "low"
            or environment_recovery
            or invocation_recovery
            or routine_success
            or short_success_observation
            else None
        )
    )
    tier_cap_reason = ""
    if tier_cap is not None:
        if suggestion_mode:
            tier_cap_reason = "suggestion-mode"
        elif title_generation_sidechannel:
            tier_cap_reason = "title-generation"
        elif environment_recovery:
            tier_cap_reason = "environment-recovery"
        elif invocation_recovery:
            tier_cap_reason = "invocation-recovery"
        elif routine_success:
            tier_cap_reason = "routine-success"
        elif short_success_observation:
            tier_cap_reason = "short-observation"
        else:
            tier_cap_reason = "low-risk"
    prefers_reasoning, reasoning_tier_floor = _reasoning_preference(body)
    tier_floor = _max_tier(tier_floor, reasoning_tier_floor)
    agent_step_count, agent_pressure = _agent_state_pressure(messages, step_risk)

    return RoutingFeatures(
        step_type=step_type,
        tool_names=normalized_tool_names,
        has_tool_results=has_tool_results,
        streaming=bool(body.get("stream", False)),
        needs_tool_calling=needs_tool_calling,
        needs_vision=has_vision,
        needs_structured_output=wants_structured_output,
        response_format=response_format_name,
        step_risk=step_risk,
        is_agentic=has_tool_results or (needs_tool_calling and step_type != "general"),
        is_coding=False,
        prefers_reasoning=prefers_reasoning,
        requested_max_output_tokens=max(1, int(max_output_tokens)),
        tier_floor=tier_floor,
        tier_cap=tier_cap,
        tier_cap_reason=tier_cap_reason,
        session_present=bool(session_id),
        agent_step_count=agent_step_count,
        agent_pressure=agent_pressure,
        capability_lane=None,
        verification_failed=failure_kind == "semantic",
        failure_kind=failure_kind,
    )


def extract_context_features(body: dict, step_type: str, prompt: str = "") -> dict[str, float]:
    """Extract numerical context features for the classifier.

    These encode agentic context as numerical signals that the classifier
    can learn from — no keyword matching, no binary overrides.
    """
    messages = body.get("messages", [])
    raw_tools = body.get("tools") or body.get("customTools") or []

    tool_count = len(raw_tools)
    tools_present = 1.0 if tool_count > 0 else 0.0
    last_role_tool = 1.0 if step_type == "tool-result-followup" else 0.0
    conversation_depth = min(1.0, len(messages) / 30.0)

    tool_result_length = 0.0
    prior_tool_calls = 0
    for msg in messages:
        role = msg.get("role", "")
        tool_result_text, _ = _latest_tool_result_signal([msg])
        if tool_result_text:
            tool_result_length = max(tool_result_length, len(tool_result_text))
        if role == "tool":
            prior_tool_calls += 1
        elif role == "assistant" and msg.get("tool_calls"):
            prior_tool_calls += 1

    user_after_tools = 0.0
    saw_tool = False
    for msg in messages:
        if msg.get("role") == "tool" or _latest_tool_result_signal([msg])[0]:
            saw_tool = True
        elif msg.get("role") == "user" and saw_tool:
            user_after_tools = 1.0

    prompt_ratio = 0.5
    if messages:
        total_len = sum(len(str(msg.get("content", ""))) for msg in messages)
        if total_len > 0:
            prompt_ratio = min(1.0, len(prompt) / total_len)

    return {
        "ctx_tools_present": tools_present,
        "ctx_tool_count": min(1.0, tool_count / 20.0),
        "ctx_last_role_tool": last_role_tool,
        "ctx_tool_result_length": min(1.0, tool_result_length / 3000.0),
        "ctx_conversation_depth": conversation_depth,
        "ctx_prior_tool_calls": min(1.0, prior_tool_calls / 10.0),
        "ctx_user_after_tools": user_after_tools,
        "ctx_prompt_ratio": prompt_ratio,
    }


def _extract_requirements(body: dict, step_type: str, prompt: str = "") -> tuple[RequestRequirements, WorkloadHints]:
    features = _extract_routing_features(
        body,
        step_type=step_type,
        prompt=prompt,
    )
    return features.request_requirements(), features.workload_hints()


_MODEL_ERROR_PATTERNS = ("model", "not found", "not available", "does not exist", "unsupported", "invalid model")


def _is_model_error(content: bytes) -> bool:
    """Heuristic: does the upstream error body indicate a model-level problem?"""
    try:
        text = content.decode("utf-8", errors="replace").lower()
        return any(p in text for p in _MODEL_ERROR_PATTERNS)
    except Exception:  # noqa: BLE001
        return False


def _truncate_diagnostic_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _extract_error_details(status_code: int, content: bytes) -> tuple[str, str]:
    fallback_code = f"http_{status_code}"
    fallback_message = f"HTTP {status_code}"
    if not content:
        return fallback_code, fallback_message
    try:
        payload = json.loads(content.decode("utf-8", errors="replace"))
    except Exception:
        text = content.decode("utf-8", errors="replace")
        return fallback_code, _truncate_diagnostic_text(text)

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            code = str(error_payload.get("code") or error_payload.get("type") or "").strip()
            message = str(
                error_payload.get("message")
                or error_payload.get("detail")
                or error_payload.get("error")
                or ""
            ).strip()
            return code or fallback_code, _truncate_diagnostic_text(message or fallback_message)

        code = str(payload.get("code") or payload.get("type") or "").strip()
        message = str(payload.get("message") or payload.get("detail") or "").strip()
        if code or message:
            return code or fallback_code, _truncate_diagnostic_text(message or fallback_message)

    return fallback_code, _truncate_diagnostic_text(str(payload) or fallback_message)


def _spend_error(
    result: Any,
    *,
    api_format: str = "openai",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a 429 error response for spend control violations."""
    if api_format == "anthropic":
        return JSONResponse(
            anthropic_error_response(429, result.reason or "Spending limit exceeded"),
            status_code=429,
            headers=headers,
        )
    body: dict[str, Any] = {
        "error": {
            "message": result.reason or "Spending limit exceeded",
            "type": "spend_limit_exceeded",
            "code": "spend_limit_exceeded",
        }
    }
    if result.reset_in_s is not None:
        body["error"]["reset_in_seconds"] = result.reset_in_s
    return JSONResponse(body, status_code=429, headers=headers)


def _routing_infeasible_payload(
    error: RoutingInfeasibleError,
    *,
    api_format: str = "openai",
) -> dict[str, Any]:
    detail = error.infeasibility.as_dict()
    code = str(detail.pop("code"))
    message = str(detail.pop("message"))
    if api_format == "anthropic":
        body = anthropic_error_response(400, message)
        body["error"]["code"] = code
        if detail:
            body["error"]["details"] = detail
        return body
    payload: dict[str, Any] = {
        "error": {
            "message": message,
            "type": "routing_infeasible",
            "code": code,
        },
    }
    if detail:
        payload["error"]["details"] = detail
    return payload


def _routing_infeasible_response(
    error: RoutingInfeasibleError,
    *,
    api_format: str = "openai",
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        _routing_infeasible_payload(error, api_format=api_format),
        status_code=400,
        headers=headers,
    )


def _safe_header_value(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("→", "->").replace("—", "-")
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _set_header(headers: dict[str, str], key: str, value: object) -> None:
    headers[key] = _safe_header_value(value)


def _apply_provider_cache_plan(
    body: dict[str, Any],
    *,
    selected_model: str,
    provider_entry: Any,
    session_id: str | None,
    step_type: str,
    upstream_provider: str,
) -> CacheRequestPlan:
    family = provider_family_for_model(
        selected_model,
        provider_name=getattr(provider_entry, "name", None),
        upstream_provider=upstream_provider,
    )
    if family == "openai":
        return apply_openai_cache_hints(
            body,
            model=selected_model,
            session_id=session_id,
            step_type=step_type,
        )
    if family == "anthropic":
        return CacheRequestPlan(family="anthropic", mode="stable-prefix")
    if family == "deepseek":
        return CacheRequestPlan(family="deepseek", mode="stable-prefix")
    if family == "google":
        _strip_openai_cache_hints_for_google(body)
        return CacheRequestPlan(family="google", mode="cache-bypass")
    return CacheRequestPlan(family=family)


def _strip_openai_cache_hints_for_google(body: dict[str, Any]) -> None:
    """Remove Anthropic-derived cache hints that Gemini/OpenAI transport cannot accept."""
    for tool in body.get("tools", []) or []:
        if isinstance(tool, dict):
            tool.pop("cache_control", None)

    for message in body.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        flattened_text_parts: list[str] = []
        normalized_parts: list[dict[str, Any]] = []
        only_text_parts = True

        for item in content:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized.pop("cache_control", None)
            item_type = normalized.get("type")
            if item_type in {"text", "input_text"}:
                flattened_text_parts.append(str(normalized.get("text", "")))
            else:
                only_text_parts = False
                normalized_parts.append(normalized)

        if only_text_parts:
            message["content"] = "\n".join(part for part in flattened_text_parts if part)
            continue

        if flattened_text_parts:
            normalized_parts.insert(0, {
                "type": "text",
                "text": "\n".join(part for part in flattened_text_parts if part),
            })
        message["content"] = normalized_parts


def _anthropic_messages_url(base_url: str) -> str:
    root = str(base_url or "").rstrip("/")
    if root.endswith("/messages"):
        return root
    if root.endswith("/v1"):
        return f"{root}/messages"
    return f"{root}/v1/messages"


def _anthropic_transport_base(base_url: str, family: str) -> str:
    root = str(base_url or "").rstrip("/")
    if not root:
        return root
    if str(family or "").strip().lower() != "minimax":
        return root
    lower = root.lower()
    if "commonstack.ai" in lower:
        return root
    if "openrouter.ai" in lower:
        return root
    if "/anthropic" in lower:
        return root
    if lower.endswith("/v1"):
        return f"{root[:-3]}/anthropic"
    return f"{root}/anthropic"


def _anthropic_response_model_name(model: str) -> str:
    value = str(model or "").strip()
    if not value:
        return value
    if value.startswith("anthropic/"):
        value = value.split("/", 1)[1]
    return re.sub(r"(\d)\.(\d)", r"\1-\2", value)


def _requested_transport_name(*, api_format: str, endpoint_name: str) -> str:
    if str(api_format or "").strip().lower() == "anthropic" or str(endpoint_name or "").strip().lower() == "messages":
        return "anthropic-messages"
    if str(endpoint_name or "").strip().lower() == "responses":
        return "openai-responses"
    return "openai-chat"


def _supports_native_anthropic_transport(
    *,
    selected_model: str,
    provider_entry: Any,
    upstream_provider: str,
    upstream_base: str,
) -> bool:
    family = provider_family_for_model(
        selected_model,
        provider_name=getattr(provider_entry, "name", None),
        upstream_provider=upstream_provider,
    )
    if family not in {"anthropic", "minimax"}:
        return False
    target_base = getattr(provider_entry, "base_url", "") if provider_entry else upstream_base
    target_base = _anthropic_transport_base(target_base, family)
    target_lower = str(target_base or "").lower()
    if "commonstack.ai" in target_lower:
        return True
    if family == "anthropic":
        if "api.anthropic.com" in target_lower:
            return True
        return upstream_provider in {"anthropic", "commonstack"}
    if "/anthropic" in target_lower:
        return True
    if "api.minimax.io" in target_lower or "api.minimaxi.com" in target_lower:
        return True
    return upstream_provider in {"minimax", "commonstack", "openrouter"}


def _choose_transport(
    *,
    api_format: str,
    endpoint_name: str,
    selected_model: str,
    provider_entry: Any,
    upstream_provider: str,
    upstream_base: str,
    step_type: str,
    has_tools: bool,
    has_tool_results: bool,
    anthropic_beta_present: bool,
) -> TransportDecision:
    requested_transport = _requested_transport_name(
        api_format=api_format,
        endpoint_name=endpoint_name,
    )
    family = provider_family_for_model(
        selected_model,
        provider_name=getattr(provider_entry, "name", None),
        upstream_provider=upstream_provider,
    )
    native_available = _supports_native_anthropic_transport(
        selected_model=selected_model,
        provider_entry=provider_entry,
        upstream_provider=upstream_provider,
        upstream_base=upstream_base,
    )
    if requested_transport == "openai-responses":
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="openai-chat",
            reason="responses ingress currently normalizes to openai chat upstream",
            preference_source="responses-compat",
            native_anthropic_transport=False,
        )
    if family == "anthropic" and native_available:
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="anthropic-messages",
            reason=(
                "anthropic ingress preserved for anthropic-native provider"
                if requested_transport == "anthropic-messages"
                else "provider-native anthropic transport preferred"
            ),
            preference_source=(
                "ingress+provider"
                if requested_transport == "anthropic-messages"
                else "provider-family"
            ),
            native_anthropic_transport=True,
        )
    if family == "minimax" and requested_transport == "anthropic-messages" and native_available:
        if step_type == "tool-result-followup":
            reason = "anthropic tool-result follow-up preserved for minimax anthropic transport"
            source = "agentic-ingress"
        elif has_tools or has_tool_results or anthropic_beta_present:
            reason = "anthropic tool semantics preserved for minimax anthropic-compatible provider"
            source = "tool-compat"
        else:
            reason = "anthropic ingress preserved for minimax anthropic-compatible provider"
            source = "ingress-policy"
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="anthropic-messages",
            reason=reason,
            preference_source=source,
            native_anthropic_transport=True,
        )
    if family == "minimax" and requested_transport == "anthropic-messages" and not native_available:
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="openai-chat",
            reason="minimax anthropic transport unavailable for current upstream",
            preference_source="capability-guard",
            native_anthropic_transport=False,
        )
    if requested_transport == "anthropic-messages":
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="openai-chat",
            reason="provider does not advertise stable anthropic-native transport here",
            preference_source="default-openai",
            native_anthropic_transport=False,
        )
    if family == "minimax":
        return TransportDecision(
            requested_transport=requested_transport,
            selected_transport="openai-chat",
            reason="openai ingress retained for minimax in v1 transport policy",
            preference_source="ingress-policy",
            native_anthropic_transport=False,
        )
    return TransportDecision(
        requested_transport=requested_transport,
        selected_transport="openai-chat",
        reason="openai-compatible transport retained",
        preference_source="default-openai",
        native_anthropic_transport=False,
    )


def _can_reuse_native_anthropic_body(
    *,
    upstream_body: dict[str, Any],
    source_preview_body: dict[str, Any] | None,
) -> bool:
    if source_preview_body is None:
        return False
    for key in ("messages", "tools", "tool_choice", "temperature", "top_p", "stream", "stop"):
        if upstream_body.get(key) != source_preview_body.get(key):
            return False
    return True


def _contains_anthropic_thinking_blocks(body: dict[str, Any] | None) -> bool:
    if not isinstance(body, dict):
        return False
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in {"thinking", "redacted_thinking"}:
                return True
    return False


def _merge_available_models(
    available_models: list[str],
    extra_models: list[str] | tuple[str, ...] | set[str] | None,
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for model in [*available_models, *(extra_models or ())]:
        model_id = str(model or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        merged.append(model_id)
    return merged


def _reuse_anthropic_source_body(
    *,
    source_body: dict[str, Any],
    upstream_body: dict[str, Any],
) -> dict[str, Any]:
    transport_body = json.loads(json.dumps(source_body))
    for key in ("model", "max_tokens", "stream", "temperature", "top_p"):
        if key in upstream_body:
            transport_body[key] = json.loads(json.dumps(upstream_body[key]))
    stop_value = upstream_body.get("stop")
    if isinstance(stop_value, list):
        transport_body["stop_sequences"] = list(stop_value)
    elif isinstance(stop_value, str) and stop_value.strip():
        transport_body["stop_sequences"] = [stop_value]
    return transport_body


def _transport_name(native_anthropic_transport: bool) -> str:
    return "anthropic-messages" if native_anthropic_transport else "openai-chat"


def _cache_mode_name(cache_plan: CacheRequestPlan) -> str:
    return cache_plan.mode or "none"


def _cache_family_name(cache_plan: CacheRequestPlan) -> str:
    return cache_plan.family or "generic"


def _set_route_strategy_headers(
    headers: dict[str, str],
    *,
    transport_decision: TransportDecision,
    cache_plan: CacheRequestPlan,
) -> None:
    _set_header(headers, "x-uncommon-route-requested-transport", transport_decision.requested_transport)
    _set_header(headers, "x-uncommon-route-transport", transport_decision.selected_transport)
    _set_header(headers, "x-uncommon-route-transport-reason", transport_decision.reason)
    _set_header(headers, "x-uncommon-route-transport-source", transport_decision.preference_source)
    _set_header(headers, "x-uncommon-route-cache-mode", _cache_mode_name(cache_plan))
    _set_header(headers, "x-uncommon-route-cache-family", _cache_family_name(cache_plan))
    headers.pop("x-uncommon-route-cache-breakpoints", None)
    headers.pop("x-uncommon-route-cache-key", None)
    if cache_plan.cache_breakpoints:
        _set_header(headers, "x-uncommon-route-cache-breakpoints", cache_plan.cache_breakpoints)
    if cache_plan.prompt_cache_key:
        _set_header(headers, "x-uncommon-route-cache-key", cache_plan.prompt_cache_key)


def _selection_modes_payload(config) -> dict[str, dict[str, float]]:
    return {
        mode.value: {
            "editorial": mode_config.selection.editorial,
            "cost": mode_config.selection.cost,
            "latency": mode_config.selection.latency,
            "reliability": mode_config.selection.reliability,
            "feedback": mode_config.selection.feedback,
            "cache_affinity": mode_config.selection.cache_affinity,
            "byok": mode_config.selection.byok,
            "free_bias": mode_config.selection.free_bias,
            "local_bias": mode_config.selection.local_bias,
            "reasoning_bias": mode_config.selection.reasoning_bias,
            "quality_alignment": mode_config.selection.quality_alignment,
            "continuity": mode_config.selection.continuity,
        }
        for mode, mode_config in config.modes.items()
    }


def _selection_weights_payload(config, mode_value: str) -> dict[str, float]:
    return dict(_selection_modes_payload(config).get(str(mode_value or "").strip().lower(), {}))


def _bandit_modes_payload(config) -> dict[str, dict[str, object]]:
    return {
        mode.value: {
            "enabled": mode_config.bandit.enabled,
            "reward_weight": mode_config.bandit.reward_weight,
            "exploration_weight": mode_config.bandit.exploration_weight,
            "warmup_pulls": mode_config.bandit.warmup_pulls,
            "min_samples_for_guardrail": mode_config.bandit.min_samples_for_guardrail,
            "min_reliability": mode_config.bandit.min_reliability,
            "max_cost_ratio": mode_config.bandit.max_cost_ratio,
            "enabled_tiers": [tier.value for tier in mode_config.bandit.enabled_tiers],
        }
        for mode, mode_config in config.modes.items()
    }


def _serialize_candidate_scores(candidate_scores: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "model": score.model,
            "total": round(score.total, 6),
            "predicted_cost": round(score.predicted_cost, 8),
            "editorial": round(score.editorial, 6),
            "quality_prior_raw": round(score.quality_prior_raw, 6),
            "quality_prior_source": score.quality_prior_source,
            "quality_prior_match_type": score.quality_prior_match_type,
            "quality_prior_matched_model": score.quality_prior_matched_model,
            "quality_prior_confidence": round(score.quality_prior_confidence, 6),
            "quality_prior_samples": score.quality_prior_samples,
            "cost": round(score.cost, 6),
            "latency": round(score.latency, 6),
            "reliability": round(score.reliability, 6),
            "feedback": round(score.feedback, 6),
            "cache_affinity": round(score.cache_affinity, 6),
            "effective_cost_multiplier": round(score.effective_cost_multiplier, 6),
            "byok": round(score.byok, 6),
            "free_bias": round(score.free_bias, 6),
            "local_bias": round(score.local_bias, 6),
            "reasoning_bias": round(score.reasoning_bias, 6),
            "quality_alignment": round(score.quality_alignment, 6),
            "continuity_bias": round(score.continuity_bias, 6),
            "served_quality": score.served_quality,
            "bandit_mean": round(score.bandit_mean, 6),
            "exploration_bonus": round(score.exploration_bonus, 6),
            "samples": score.samples,
        }
        for score in candidate_scores
    ]


def _serialize_fallback_chain(fallback_chain: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "model": option.model,
            "cost_estimate": round(option.cost_estimate, 8),
            "suggested_output_budget": option.suggested_output_budget,
        }
        for option in fallback_chain
    ]


def _serialize_routing_features(features: RoutingFeatures) -> dict[str, object]:
    return {
        "step_type": features.step_type,
        "tool_names": list(features.tool_names),
        "tool_count": features.tool_count,
        "has_tool_results": features.has_tool_results,
        "streaming": features.streaming,
        "needs_tool_calling": features.needs_tool_calling,
        "needs_vision": features.needs_vision,
        "needs_structured_output": features.needs_structured_output,
        "response_format": features.response_format,
        "step_risk": features.step_risk,
        "is_agentic": features.is_agentic,
        "is_coding": features.is_coding,
        "prefers_reasoning": features.prefers_reasoning,
        "requested_max_output_tokens": features.requested_max_output_tokens,
        "tier_floor": features.tier_floor.value if features.tier_floor is not None else None,
        "tier_cap": features.tier_cap.value if features.tier_cap is not None else None,
        "tier_cap_reason": features.tier_cap_reason,
        "session_present": features.session_present,
        "agent_step_count": features.agent_step_count,
        "agent_pressure": round(features.agent_pressure, 6),
        "capability_lane": features.capability_lane.value if features.capability_lane is not None else None,
        "previous_served_quality": features.previous_served_quality.value if features.previous_served_quality is not None else None,
        "continuity_quality_floor": features.continuity_quality_floor.value if features.continuity_quality_floor is not None else None,
        "verification_failed": features.verification_failed,
        "failure_kind": features.failure_kind,
        "tags": list(features.tags()),
    }


def _parse_mode_value(value: str) -> RoutingMode:
    return RoutingMode(str(value).strip().lower())


def _parse_tier_value(value: str) -> Tier:
    return Tier(str(value).strip().upper())


def _normalize_selector_body(
    body: dict[str, Any],
    *,
    default_mode: RoutingMode = RoutingMode.AUTO,
) -> tuple[dict[str, Any] | None, str | None]:
    payload = dict(body)
    model = str(payload.get("model") or "").strip().lower()
    mode_value = payload.get("mode")
    if mode_value is not None and not model:
        try:
            model = VIRTUAL_MODEL_IDS[_parse_mode_value(str(mode_value))]
        except ValueError:
            return None, "Invalid mode"
        payload["model"] = model
    if payload.get("messages"):
        if not payload.get("model"):
            payload["model"] = VIRTUAL_MODEL_IDS[default_mode]
        return payload, None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None, "Requires either messages or prompt"
    system_prompt = payload.get("system_prompt")
    messages: list[dict[str, Any]] = []
    if isinstance(system_prompt, str) and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    payload["messages"] = messages
    if not payload.get("model"):
        payload["model"] = VIRTUAL_MODEL_IDS[default_mode]
    return payload, None


def _flatten_tool_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
        return " ".join(parts)
    return str(content) if content else ""


def _trace_decision_card(trace: "RequestTrace") -> dict[str, Any]:
    """Subset of the trace useful for the UI as a decision card."""
    return {
        "model": trace.model,
        "decision_tier": trace.decision_tier or trace.tier,
        "served_quality": trace.served_quality,
        "capability_lane": trace.capability_lane,
        "raw_confidence": trace.raw_confidence,
        "latency_us": trace.latency_us,
        "route_latency_ms": trace.route_latency_ms if trace.route_latency_ms > 0 else trace.latency_us / 1000.0,
        "upstream_elapsed_ms": trace.upstream_elapsed_ms,
        "first_token_ms": trace.first_token_ms,
        "estimated_cost": trace.estimated_cost,
        "route_reasoning": trace.route_reasoning,
        "feature_tags": list(trace.feature_tags or []),
        "constraint_tags": list(trace.constraint_tags or []),
        "hint_tags": list(trace.hint_tags or []),
        "transport": trace.transport,
        "transport_reason": trace.transport_reason,
        "attempts_payload": list(trace.attempts_payload or []),
        "fallback_reason": trace.fallback_reason,
    }


def _assemble_conversation(
    traces: "TraceStore", session_id: str
) -> dict[str, Any] | None:
    # 1. Pull all hot rows for this session_id.
    matching = [r for r in traces._records if r.session_id == session_id]
    if not matching:
        return None
    matching.sort(key=lambda r: r.timestamp)

    # 2. Pull cold fields per turn.
    cold_by_id: dict[str, dict[str, Any]] = {}
    for t in matching:
        cold = traces.load_content(t.request_id)
        if cold is not None:
            cold_by_id[t.request_id] = cold

    has_any_content = any(
        (c.get("request_messages") or c.get("response_text"))
        for c in cold_by_id.values()
    )

    # 3. Compact-break detection from msg_hashes.
    breaks: list[int] = []
    for k in range(1, len(matching)):
        prev = list(matching[k - 1].msg_hashes or [])
        curr = list(matching[k].msg_hashes or [])
        if not prev or not curr:
            continue
        if curr[: len(prev)] != prev:
            breaks.append(k)

    if not has_any_content:
        # Surface turn-level decisions only (no message bodies).
        decisions = [
            {
                "role": "assistant",
                "text": "",
                "tool_calls": [],
                "ts": t.timestamp,
                "request_id": t.request_id,
                "decision": _trace_decision_card(t),
            }
            for t in matching
        ]
        return {
            "session_id": session_id,
            "turn_count": len(matching),
            "content_available": False,
            "compact_breaks": breaks,
            "messages": decisions,
        }

    # 4. Build backbone from the LAST turn's request_messages.
    last_turn = matching[-1]
    last_cold = cold_by_id.get(last_turn.request_id, {})
    backbone = list(last_cold.get("request_messages") or [])

    # 5. Walk backbone, expand into chat messages with decisions.
    #
    # Alignment: each captured turn's response goes into the NEXT turn's
    # backbone as an assistant message — except the LAST captured turn's
    # response, which is standalone (appended in step 6). So:
    #   backbone_assistants_count + 1 captured-or-skipped turns total
    #   the LAST (matching_count - 1) backbone assistants align with
    #     matching[0..matching_count-2]
    #   any earlier backbone assistants are PRE-CAPTURE (no decision)
    backbone_assistants_count = sum(
        1 for m in backbone
        if isinstance(m, dict) and m.get("role") == "assistant"
    )
    align_offset = max(0, backbone_assistants_count - (len(matching) - 1))

    out_messages: list[dict[str, Any]] = []
    assistant_idx = 0
    for m in backbone:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            # May carry tool_results inside content blocks.
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        out_messages.append({
                            "role": "tool_result",
                            "tool_use_id": b.get("tool_use_id", ""),
                            "text": _flatten_tool_result(b.get("content", "")),
                            "from_request_id": last_turn.request_id,
                        })
                # Surface any plain user text alongside tool_results.
                text = " ".join(
                    str(b.get("text", ""))
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    out_messages.append({
                        "role": "user",
                        "text": text,
                        "ts": None,
                        "from_request_id": last_turn.request_id,
                    })
            else:
                out_messages.append({
                    "role": "user",
                    "text": str(content),
                    "ts": None,
                    "from_request_id": last_turn.request_id,
                })
        elif role == "assistant":
            text = ""
            calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            text += str(b.get("text", ""))
                        elif b.get("type") == "tool_use":
                            calls.append({
                                "id": b.get("id", ""),
                                "name": b.get("name", ""),
                                "input": b.get("input", {}),
                            })
            else:
                text = str(content)

            mapped_idx = assistant_idx - align_offset
            if 0 <= mapped_idx < len(matching) - 1:
                decision_trace = matching[mapped_idx]
                entry = {
                    "role": "assistant",
                    "text": text,
                    "tool_calls": calls,
                    "ts": decision_trace.timestamp,
                    "request_id": decision_trace.request_id,
                    "decision": _trace_decision_card(decision_trace),
                }
            else:
                # Pre-capture assistant — no decision card available.
                entry = {
                    "role": "assistant",
                    "text": text,
                    "tool_calls": calls,
                    "ts": None,
                    "request_id": None,
                    "decision": None,
                }
            out_messages.append(entry)
            assistant_idx += 1
        elif role == "tool":
            out_messages.append({
                "role": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "text": str(content),
                "from_request_id": last_turn.request_id,
            })
        # role == "system": skip (system prompt is not a conversation turn for UI)

    # 6. Append the LAST turn's response (assistant_N — not yet in the backbone).
    final = matching[-1]
    out_messages.append({
        "role": "assistant",
        "text": last_cold.get("response_text", "") or "",
        "tool_calls": list(last_cold.get("response_tool_calls") or []),
        "ts": final.timestamp,
        "request_id": final.request_id,
        "decision": _trace_decision_card(final),
    })

    return {
        "session_id": session_id,
        "turn_count": len(matching),
        "content_available": True,
        "compact_breaks": breaks,
        "messages": out_messages,
    }


def create_app(
    upstream: str | None = DEFAULT_UPSTREAM,
    spend_control: SpendControl | None = None,
    providers_config: ProvidersConfig | None = None,
    route_stats: RouteStats | None = None,
    trace_store: TraceStore | None = None,
    feedback: FeedbackCollector | None = None,
    model_mapper: ModelMapper | None = None,
    artifact_store: ArtifactStore | None = None,
    composition_policy: CompositionPolicy | None = None,
    semantic_compressor: SemanticCompressor | None = None,
    model_experience: ModelExperienceStore | None = None,
    routing_config_store: RoutingConfigStore | None = None,
    connections_store: ConnectionsStore | None = None,
    route_confidence_calibrator: object | None = None,
) -> Starlette:
    """Create the ASGI application wired to the given upstream base URL."""
    _cli_upstream_override = str(upstream or "").strip() or None
    _connections_store = connections_store or ConnectionsStore()
    _effective_connection = resolve_primary_connection(
        cli_upstream=_cli_upstream_override,
        store=_connections_store,
    )
    upstream = _effective_connection.upstream
    _primary_api_key = _effective_connection.api_key
    _spend = spend_control or SpendControl()
    _spend_reservation = _SpendReservation(_spend)
    _providers = providers_config or load_providers()
    _stats = route_stats or RouteStats()
    _traces = trace_store or TraceStore()
    _route_confidence = route_confidence_calibrator or get_active_route_confidence_calibrator()
    if any(record.feedback_signal for record in _stats.history()):
        _route_confidence.fit_from_route_records(_stats.history())
    _model_experience = model_experience or ModelExperienceStore()
    _feedback = feedback or FeedbackCollector(
        model_experience=_model_experience,
        buffer_path=data_dir() / "feedback_buffer.json",
    )
    if getattr(_feedback, "_model_experience", None) is None:
        _feedback._model_experience = _model_experience
    _mapper = model_mapper or ModelMapper(upstream)
    _artifacts = artifact_store or ArtifactStore()
    _composition_policy = composition_policy or load_composition_policy()
    _semantic = semantic_compressor
    _routing_store = routing_config_store or RoutingConfigStore()
    _routing_config = _routing_store.config()
    _scene_store = SceneStore()
    _responses_history: dict[str, list[dict[str, Any]]] = {}
    forced_messages_mode_raw = str(os.environ.get("UNCOMMON_ROUTE_FORCE_MESSAGES_DEFAULT_MODE", "")).strip()
    forced_messages_upstream_model = str(
        os.environ.get("UNCOMMON_ROUTE_FORCE_MESSAGES_UPSTREAM_MODEL", "")
    ).strip()
    _forced_messages_default_mode: RoutingMode | None = None
    disable_anthropic_cache = str(os.environ.get("UNCOMMON_ROUTE_DISABLE_ANTHROPIC_CACHE", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if forced_messages_mode_raw:
        try:
            _forced_messages_default_mode = _parse_mode_value(forced_messages_mode_raw)
        except ValueError:
            if forced_messages_mode_raw.lower() in {"1", "true", "yes", "on"}:
                _forced_messages_default_mode = _routing_store.default_mode()

    def _upstream_chat_url(base_url: str) -> str:
        return f"{str(base_url or '').rstrip('/')}/chat/completions"

    def _build_semantic_compressor() -> SemanticCompressor | None:
        if not upstream:
            return semantic_compressor if semantic_compressor is not None and not isinstance(semantic_compressor, UpstreamSemanticCompressor) else None
        if semantic_compressor is not None and not isinstance(semantic_compressor, UpstreamSemanticCompressor):
            return semantic_compressor
        compressor = _semantic if isinstance(_semantic, UpstreamSemanticCompressor) else None
        if compressor is None:
            compressor = UpstreamSemanticCompressor(
                upstream_chat=_upstream_chat_url(upstream),
                primary_api_key=_primary_api_key,
                providers_config=_providers,
                model_mapper=_mapper,
                composition_policy=_composition_policy,
            )
        else:
            compressor.rebind_primary(
                upstream_chat=_upstream_chat_url(upstream),
                primary_api_key=_primary_api_key,
                model_mapper=_mapper,
            )
            compressor.rebind_providers(_providers)
        return compressor

    _semantic = _build_semantic_compressor()

    from uncommon_route.implicit_feedback import (
        RetrialDetector,
        analyze_logprobs,
        compute_implicit_quality,
    )
    from uncommon_route.circuit_breaker import CircuitBreakerRegistry
    _retrial_detector = RetrialDetector()
    _circuit_breaker = CircuitBreakerRegistry()

    def _refresh_active_pricing() -> None:
        """Merge dynamic pricing (from discovery) with static fallback."""
        nonlocal _routing_config
        global _active_pricing
        merged = dict(DEFAULT_MODEL_PRICING)
        dynamic = _mapper.dynamic_pricing
        if dynamic:
            merged.update(dynamic)
        _active_pricing = merged

        import copy
        updated = copy.deepcopy(_routing_store.config())
        dynamic_caps = _mapper.dynamic_capabilities
        if dynamic_caps:
            merged_caps = dict(updated.model_capabilities)
            merged_caps.update(dynamic_caps)
            updated.model_capabilities = merged_caps
        _routing_config = updated

    def _reload_providers() -> ProvidersConfig:
        nonlocal _providers, _semantic
        _providers = load_providers()
        if isinstance(_semantic, UpstreamSemanticCompressor):
            _semantic.rebind_providers(_providers)
        return _providers

    def _current_connection_payload() -> dict[str, Any]:
        effective = resolve_primary_connection(
            cli_upstream=_cli_upstream_override,
            store=_connections_store,
        )
        return {
            "source": effective.source,
            "upstream_source": effective.upstream_source,
            "api_key_source": effective.api_key_source,
            "editable": effective.editable,
            "upstream": upstream,
            "has_api_key": bool(_primary_api_key),
            "api_key_preview": mask_api_key(_primary_api_key),
            "provider": _mapper.provider,
            "is_gateway": _mapper.is_gateway,
            "discovered": _mapper.discovered,
            "upstream_model_count": _mapper.upstream_model_count,
            "pool_size": _mapper.pool_size,
            "unresolved": _mapper.unresolved_models(),
            "pricing_source": "dynamic" if _mapper.discovered else "static",
        }

    async def _reload_primary_connection(
        *,
        next_upstream: str,
        next_api_key: str,
        persist: bool,
    ) -> tuple[bool, dict[str, Any]]:
        nonlocal upstream, _primary_api_key, _mapper, _semantic
        candidate_upstream = str(next_upstream or "").strip()
        candidate_api_key = str(next_api_key or "").strip()
        candidate_mapper = ModelMapper(candidate_upstream)

        if candidate_upstream:
            count = await candidate_mapper.discover(candidate_api_key or None)
            if count <= 0:
                detail = candidate_mapper.provider
                if detail != "unknown":
                    detail = f"{detail} (discovery failed)"
                return False, {
                    "error": "Unable to validate upstream connection",
                    "detail": detail or "discovery failed",
                }

        previous_upstream = upstream
        previous_api_key = _primary_api_key
        previous_mapper = _mapper
        previous_semantic = _semantic

        try:
            upstream = candidate_upstream
            _primary_api_key = candidate_api_key
            _mapper = candidate_mapper
            _semantic = _build_semantic_compressor()
            _refresh_active_pricing()
            if persist:
                _connections_store.set_primary(
                    upstream=candidate_upstream,
                    api_key=candidate_api_key,
                )
            return True, _current_connection_payload()
        except Exception as exc:  # noqa: BLE001
            upstream = previous_upstream
            _primary_api_key = previous_api_key
            _mapper = previous_mapper
            _semantic = previous_semantic
            _refresh_active_pricing()
            return False, {"error": "Failed to reload primary connection", "detail": str(exc)}

    async def _on_startup() -> None:
        # ─── v2 lifecycle startup ───
        try:
            from uncommon_route.v2_lifecycle import on_startup as v2_startup
            v2_startup()
        except Exception as e:
            logger.warning("v2 lifecycle startup failed: %s", e)

        if not upstream:
            return
        count = await _mapper.discover(_primary_api_key or None)
        if count > 0:
            gw_tag = " (gateway)" if _mapper.is_gateway else ""
            print(f"[UncommonRoute] Discovered {count} models from {_mapper.provider}{gw_tag}")
            print(f"[UncommonRoute] Model pool: {count} models with live pricing + inferred capabilities")
            _refresh_active_pricing()
            unresolved = _mapper.unresolved_models()
            if unresolved:
                names = ", ".join(unresolved[:5])
                extra = f" (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""
                print(f"[UncommonRoute] Note: {len(unresolved)} legacy model(s) not matched upstream: {names}{extra}")
        elif _mapper.provider != "unknown":
            print(f"[UncommonRoute] Warning: could not discover models from {_mapper.provider} — using static config")

        try:
            from uncommon_route.benchmark import get_benchmark_cache
            bm_cache = get_benchmark_cache()
            bm_count = await bm_cache.refresh()
            if bm_count > 0:
                print(f"[UncommonRoute] Benchmark quality: {bm_count} models from {bm_cache.source_summary()}")
            else:
                seed_count = bm_cache.model_count()
                if seed_count > 0:
                    print(f"[UncommonRoute] Benchmark quality: {seed_count} models from seed data (API fetch pending)")
        except Exception as exc:
            logger.warning("Benchmark quality fetch failed: %s", exc)

    _rediscovery_task = None
    _benchmark_refresh_task = None

    async def _rediscovery_loop() -> None:
        """Periodically re-discover upstream models to track changes."""
        import asyncio
        interval = float(os.environ.get("UNCOMMON_ROUTE_REDISCOVERY_INTERVAL", "300"))
        while True:
            await asyncio.sleep(interval)
            try:
                count = await _mapper.discover(_primary_api_key or None)
                if count > 0:
                    _refresh_active_pricing()
                    logger.info("Rediscovery: %d models from %s", count, _mapper.provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Rediscovery failed: %s", exc)

    async def _benchmark_refresh_loop() -> None:
        """Refresh external benchmark priors off the request path.

        Providers still enforce their own TTLs, so this loop is cheap when
        cached data is fresh and avoids blocking live routing on network calls.
        """
        import asyncio
        interval = float(os.environ.get("UNCOMMON_ROUTE_BENCHMARK_REFRESH_INTERVAL", "3600"))
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                from uncommon_route.benchmark import get_benchmark_cache
                count = await get_benchmark_cache().refresh()
                if count > 0:
                    logger.info("Benchmark refresh: %d models updated", count)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Benchmark refresh failed: %s", exc)

    def _selector_state(
        *,
        bucket_mode: RoutingMode | None = None,
        bucket_tier: Tier | None = None,
    ) -> dict[str, Any]:
        current_config = _routing_config
        state: dict[str, Any] = {
            "default_mode": _routing_store.default_mode().value,
            "selection_modes": _selection_modes_payload(current_config),
            "bandit_modes": _bandit_modes_payload(current_config),
            "experience": _model_experience.summary(),
        }
        if bucket_mode is not None and bucket_tier is not None:
            state["bucket"] = _model_experience.bucket_summary(bucket_mode, bucket_tier)
        return state

    def _routing_features_with_quality_context(
        features: RoutingFeatures,
        *,
        api_format: str,
        endpoint_name: str,
        session_id: str | None,
        has_tools: bool,
    ) -> RoutingFeatures:
        capability_lane = features.capability_lane
        if capability_lane is None:
            capability_lane = request_capability_lane(features)

        previous_served_quality: ServedQuality | None = None
        continuity_quality_floor: ServedQuality | None = None
        protocol_side_channel = features.tier_cap_reason == "suggestion-mode"
        if session_id and not protocol_side_channel:
            latest_session_trace = _traces.latest_for_session(session_id)
            if latest_session_trace is not None:
                previous_served_quality = normalize_served_quality(latest_session_trace.served_quality)
            if features.step_type == "tool-result-followup":
                continuity_trace = _traces.latest_for_session(
                    session_id,
                    step_types=("tool-selection", "tool-result-followup"),
                )
                if continuity_trace is not None:
                    continuity_quality_floor = normalize_served_quality(continuity_trace.served_quality)

        return replace(
            features,
            capability_lane=capability_lane,
            previous_served_quality=previous_served_quality,
            continuity_quality_floor=continuity_quality_floor,
        )

    def _build_selector_preview(body: dict[str, Any], request: Request) -> dict[str, Any]:
        model = str(body.get("model") or "").strip().lower()
        routing_mode = routing_mode_from_model(model)
        if routing_mode is None:
            return {
                "virtual": False,
                "requested_model": model,
                "served_model": model,
                "reasoning": "passthrough",
                "selector": _selector_state(),
            }

        prompt, system_prompt, max_tokens = _extract_prompt(body)
        step_type, tool_names = _classify_step(body)
        routing_features = _extract_routing_features(
            body,
            step_type=step_type,
            tool_names=tool_names,
            prompt=prompt,
            max_output_tokens=max_tokens,
            session_id=_resolve_session_id(request, body),
        )
        routing_features = _routing_features_with_quality_context(
            routing_features,
            api_format="openai",
            endpoint_name="chat_completions",
            session_id=_resolve_session_id(request, body),
            has_tools=bool(body.get("tools") or body.get("customTools")),
        )
        ctx_features = extract_context_features(body, step_type, prompt)
        user_keyed = _providers.keyed_models() or None
        base_available_models = _mapper.routable_models if _mapper.discovered else list(DEFAULT_MODEL_PRICING.keys())
        if user_keyed:
            base_available_models = _merge_available_models(base_available_models, sorted(user_keyed))
        available_models = _circuit_breaker.filter_available(base_available_models)
        decision = route(
            prompt,
            system_prompt,
            max_tokens,
            config=_routing_config,
            routing_mode=routing_mode,
            routing_features=routing_features,
            user_keyed_models=user_keyed,
            model_experience=_model_experience,
            route_confidence_calibrator=_route_confidence,
            context_features=ctx_features,
            pricing=_get_pricing(),
            available_models=available_models or None,
            model_capabilities=_routing_config.model_capabilities,
            messages=body.get("messages"),
            record_lifecycle=False,
        )
        reasoning = decision.reasoning

        effective_requirements = decision.routing_features.request_requirements()
        effective_hints = decision.routing_features.workload_hints()

        return {
            "virtual": True,
            "requested_model": model,
            "requested_mode": routing_mode.value,
            "served_model": decision.model,
            "served_tier": decision.tier.value,
            "served_quality": decision.served_quality.value,
            "served_quality_target": decision.served_quality_target.value,
            "served_quality_floor": decision.served_quality_floor.value if decision.served_quality_floor is not None else "",
            "capability_lane": decision.capability_lane.value,
            "mode": decision.mode.value,
            "method": decision.method,
            "reasoning": reasoning,
            "confidence": round(decision.confidence, 6),
            "raw_confidence": round(decision.raw_confidence, 6),
            "confidence_source": decision.confidence_source,
            "confidence_calibration": {
                "version": decision.calibration_version,
                "sample_count": decision.calibration_sample_count,
                "temperature": round(decision.calibration_temperature, 6),
                "applied_tags": list(decision.calibration_applied_tags),
            },
            "estimated_cost": round(decision.cost_estimate, 8),
            "baseline_cost": round(decision.baseline_cost, 8),
            "savings": round(decision.savings, 6),
            "step_type": decision.routing_features.step_type,
            "requirements": {
                "needs_tool_calling": effective_requirements.needs_tool_calling,
                "needs_vision": effective_requirements.needs_vision,
                "prefers_reasoning": effective_requirements.prefers_reasoning,
                "is_agentic": effective_hints.is_agentic,
            },
            "routing_features": _serialize_routing_features(decision.routing_features),
            "constraint_tags": list(decision.constraints.tags()),
            "hint_tags": list(decision.workload_hints.tags()),
            "answer_depth": decision.answer_depth.value,
            "fallback_chain": _serialize_fallback_chain(decision.fallback_chain),
            "candidate_scores": _serialize_candidate_scores(decision.candidate_scores),
            "selector": _selector_state(
                bucket_mode=decision.mode,
                bucket_tier=decision.tier,
            ),
        }

    async def handle_health(request: Request) -> JSONResponse:
        spend_status = _spend.status()
        trace_summary = _traces.summary()
        return JSONResponse({
            "status": "ok",
            "router": "uncommon-route",
            "version": VERSION,
            "upstream": upstream,
            "connections": _current_connection_payload(),
            "spending": {
                "limits": {k: v for k, v in vars(spend_status.limits).items() if v is not None},
                "spent": spend_status.spent,
                "remaining": {k: v for k, v in spend_status.remaining.items() if v is not None},
                "calls": spend_status.calls,
            },
            "providers": {
                "count": len(_providers.providers),
                "names": _providers.provider_names(),
                "keyed_models": sorted(_providers.keyed_models()),
            },
            "selector": _selector_state(),
            "routing_config": {
                "source": _routing_store.export().get("source", "local-file"),
                "editable": _routing_store.export().get("editable", True),
                "default_mode": _routing_store.default_mode().value,
            },
            "stats": {
                "total_requests": _stats.count,
            },
            "traces": {
                "total_requests": _traces.count,
                "error_count": trace_summary["error_count"],
                "virtual_requests": trace_summary["virtual_requests"],
                "passthrough_requests": trace_summary["passthrough_requests"],
            },
            "composition": {
                "artifacts": _artifacts.count(),
                "semantic_enabled": _semantic is not None,
                "policy": _composition_policy.to_dict(),
                "sidechannel_models": {
                    "tool_summary": _composition_policy.sidechannel.tool_summary.candidates(),
                    "checkpoint": _composition_policy.sidechannel.checkpoint.candidates(),
                    "rehydrate": _composition_policy.sidechannel.rehydrate.candidates(),
                },
            },
            "feedback": {
                "pending": _feedback.pending_count,
                "total_updates": _feedback.total_updates,
                "online_model": _feedback.online_model_active,
                "route_confidence_calibration": _route_confidence.status(),
            },
            "model_mapper": {
                "provider": _mapper.provider,
                "is_gateway": _mapper.is_gateway,
                "discovered": _mapper.discovered,
                "upstream_models": _mapper.upstream_model_count,
                "pool_size": _mapper.pool_size,
                "unresolved": _mapper.unresolved_models(),
                "pricing_source": "dynamic" if _mapper.discovered else "static",
            },
        })

    async def handle_models(request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": VIRTUAL_MODELS})

    async def handle_models_mapping(request: Request) -> JSONResponse:
        return JSONResponse({
            "provider": _mapper.provider,
            "is_gateway": _mapper.is_gateway,
            "discovered": _mapper.discovered,
            "upstream_model_count": _mapper.upstream_model_count,
            "pool_size": _mapper.pool_size,
            "mappings": _mapper.mapping_table(),
            "pool": _mapper.pool_table(),
            "unresolved": _mapper.unresolved_models(),
            "pricing_source": "dynamic" if _mapper.discovered else "static",
        })

    def _providers_payload() -> dict[str, Any]:
        rows = []
        for name in sorted(_providers.providers):
            entry = _providers.providers[name]
            rows.append({
                "name": entry.name,
                "base_url": entry.base_url,
                "models": list(entry.models),
                "model_count": len(entry.models),
                "plan": entry.plan,
                "has_api_key": bool(entry.api_key),
                "api_key_preview": mask_api_key(entry.api_key),
            })
        return {
            "count": len(rows),
            "providers": rows,
        }

    async def handle_connections(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        if request.method == "GET":
            return JSONResponse(_current_connection_payload())

        effective = resolve_primary_connection(
            cli_upstream=_cli_upstream_override,
            store=_connections_store,
        )
        if not effective.editable:
            return JSONResponse({
                "error": "Primary upstream is externally managed",
                "source": effective.source,
                "upstream_source": effective.upstream_source,
                "api_key_source": effective.api_key_source,
            }, status_code=409)

        body = await request.json()
        next_upstream = str(body.get("upstream", upstream)).strip()
        next_api_key = str(body.get("api_key", _primary_api_key)).strip()
        ok, payload = await _reload_primary_connection(
            next_upstream=next_upstream,
            next_api_key=next_api_key,
            persist=True,
        )
        return JSONResponse(payload, status_code=200 if ok else 502)

    async def handle_providers(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        if request.method == "GET":
            return JSONResponse(_providers_payload())

        body = await request.json()
        name = str(body.get("name", "")).strip().lower()
        api_key = str(body.get("api_key", "")).strip()
        if not name or not api_key:
            return JSONResponse({"error": "Requires name and api_key"}, status_code=400)
        base_url_raw = body.get("base_url")
        plan = str(body.get("plan", "")).strip()
        models_raw = body.get("models")
        models = None
        if isinstance(models_raw, list):
            models = [str(item).strip() for item in models_raw if str(item).strip()]
        base_url = str(base_url_raw).strip() if base_url_raw is not None else None

        verification: dict[str, Any] | None = None
        verify_requested = bool(body.get("verify", False))
        if verify_requested and base_url:
            verified, detail = verify_key(base_url, api_key)
            verification = {"ok": verified, "detail": detail}

        add_provider(
            name,
            api_key,
            base_url=base_url,
            models=models,
            plan=plan,
        )
        _reload_providers()
        payload = {"ok": True, **_providers_payload()}
        if verification is not None:
            payload["verification"] = verification
        return JSONResponse(payload)

    async def handle_provider_detail(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        name = str(request.path_params["name"]).strip().lower()
        if request.method == "DELETE":
            removed = remove_provider(name)
            _reload_providers()
            return JSONResponse({"ok": True, "removed": removed, **_providers_payload()})

        entry = _providers.providers.get(name)
        if entry is None:
            return JSONResponse({"error": "Provider not found"}, status_code=404)
        verified, detail = verify_key(entry.base_url, entry.api_key)
        return JSONResponse({
            "ok": verified,
            "detail": detail,
            "provider": {
                "name": entry.name,
                "base_url": entry.base_url,
                "model_count": len(entry.models),
                "api_key_preview": mask_api_key(entry.api_key),
            },
        }, status_code=200 if verified else 502)

    _dashboard_mount = None
    try:
        import importlib.resources as _pkg
        from pathlib import Path as _P
        _static_dir = str(_pkg.files("uncommon_route") / "static")
        # Fallback: if importlib points to stale site-packages, use local package dir
        if not (_P(_static_dir) / "index.html").exists():
            _local_static = _P(__file__).resolve().parent / "static"
            if (_local_static / "index.html").exists():
                _static_dir = str(_local_static)
        _dashboard_mount = StaticFiles(directory=_static_dir, html=True)
    except Exception:  # noqa: BLE001
        pass

    async def handle_spend(request: Request) -> JSONResponse:
        """GET /v1/spend — current spend status. POST /v1/spend — set limits."""
        if request.method == "GET":
            s = _spend.status()
            return JSONResponse({
                "limits": {k: v for k, v in vars(s.limits).items() if v is not None},
                "spent": s.spent,
                "remaining": {k: v for k, v in s.remaining.items() if v is not None},
                "calls": s.calls,
            })
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        body = await request.json()
        action = body.get("action", "set")
        window = body.get("window")
        amount = body.get("amount")
        if action == "set" and window and amount is not None:
            _spend.set_limit(window, float(amount))
            return JSONResponse({"ok": True, "window": window, "amount": amount})
        if action == "clear" and window:
            _spend.clear_limit(window)
            return JSONResponse({"ok": True, "window": window, "cleared": True})
        if action == "reset_session":
            _spend.reset_session()
            return JSONResponse({"ok": True, "session_reset": True})
        return JSONResponse({"error": "Invalid action"}, status_code=400)

    async def handle_stats(request: Request) -> JSONResponse:
        """GET /v1/stats — route analytics. POST /v1/stats — reset."""
        if request.method == "POST":
            body = await request.json()
            if body.get("action") == "reset":
                traces_cleared = _traces.count
                _stats.reset()
                _traces.reset()
                _route_confidence.reset()
                cleared_feedback = _feedback.clear_pending()
                return JSONResponse({
                    "ok": True,
                    "reset": True,
                    "traces_cleared": traces_cleared,
                    "feedback_cleared": cleared_feedback,
                    "route_confidence_calibration_reset": True,
                })
            return JSONResponse({"error": "Invalid action"}, status_code=400)
        s = _stats.summary()
        return JSONResponse({
            "total_requests": s.total_requests,
            "time_range_s": round(s.time_range_s, 1),
            "avg_confidence": round(s.avg_confidence, 3),
            "avg_savings": round(s.avg_savings, 3),
            "avg_latency_ms": round(s.avg_latency_us / 1000.0, 3),
            "avg_route_latency_ms": round(s.avg_route_latency_ms, 3),
            "avg_upstream_elapsed_ms": round(s.avg_upstream_elapsed_ms, 3),
            "avg_first_token_ms": round(s.avg_first_token_ms, 3),
            "avg_input_reduction_ratio": round(s.avg_input_reduction_ratio, 3),
            "avg_cache_hit_ratio": round(s.avg_cache_hit_ratio, 3),
            "total_estimated_cost": round(s.total_estimated_cost, 6),
            "total_baseline_cost": round(s.total_baseline_cost, 6),
            "total_actual_cost": round(s.total_actual_cost, 6),
            "total_savings_absolute": round(s.total_savings_absolute, 6),
            "total_savings_ratio": round(s.total_savings_ratio, 6),
            "total_cache_savings": round(s.total_cache_savings, 6),
            "total_compaction_savings": round(s.total_compaction_savings, 6),
            "total_usage_input_tokens": s.total_usage_input_tokens,
            "total_usage_output_tokens": s.total_usage_output_tokens,
            "total_cache_read_input_tokens": s.total_cache_read_input_tokens,
            "total_cache_write_input_tokens": s.total_cache_write_input_tokens,
            "total_cache_breakpoints": s.total_cache_breakpoints,
            "total_input_tokens_before": s.total_input_tokens_before,
            "total_input_tokens_after": s.total_input_tokens_after,
            "total_artifacts_created": s.total_artifacts_created,
            "total_compacted_messages": s.total_compacted_messages,
            "total_semantic_summaries": s.total_semantic_summaries,
            "total_semantic_calls": s.total_semantic_calls,
            "total_semantic_failures": s.total_semantic_failures,
            "total_semantic_quality_fallbacks": s.total_semantic_quality_fallbacks,
            "total_checkpoints_created": s.total_checkpoints_created,
            "total_rehydrated_artifacts": s.total_rehydrated_artifacts,
            "route_confidence_calibration": _route_confidence.status(),
            "by_mode": s.by_mode,
            "by_decision_tier": s.by_decision_tier,
            "by_served_quality": s.by_served_quality,
            "by_capability_lane": s.by_capability_lane,
            "by_tier": {
                tier: {
                    "count": ts.count,
                    "avg_confidence": round(ts.avg_confidence, 3),
                    "avg_savings": round(ts.avg_savings, 3),
                    "total_cost": round(ts.total_cost, 6),
                }
                for tier, ts in s.by_tier.items()
            },
            "by_model": {
                model: {"count": ms.count, "total_cost": round(ms.total_cost, 6)}
                for model, ms in s.by_model.items()
            },
            "by_transport": {
                transport: {"count": ms.count, "total_cost": round(ms.total_cost, 6)}
                for transport, ms in s.by_transport.items()
            },
            "by_cache_mode": {
                mode: {"count": ms.count, "total_cost": round(ms.total_cost, 6)}
                for mode, ms in s.by_cache_mode.items()
            },
            "by_cache_family": {
                family: {"count": ms.count, "total_cost": round(ms.total_cost, 6)}
                for family, ms in s.by_cache_family.items()
            },
            "by_method": s.by_method,
            "selector": _selector_state(),
        })

    async def handle_selector(request: Request) -> JSONResponse:
        """GET /v1/selector — selector state. POST /v1/selector — preview candidate choice."""
        if request.method == "GET":
            mode_param = request.query_params.get("mode")
            tier_param = request.query_params.get("tier")
            if (mode_param and not tier_param) or (tier_param and not mode_param):
                return JSONResponse(
                    {"error": "mode and tier must be provided together"},
                    status_code=400,
                )
            if mode_param and tier_param:
                try:
                    return JSONResponse(_selector_state(
                        bucket_mode=_parse_mode_value(mode_param),
                        bucket_tier=_parse_tier_value(tier_param),
                    ))
                except ValueError:
                    return JSONResponse(
                        {"error": "Invalid mode or tier"},
                        status_code=400,
                    )
            return JSONResponse(_selector_state())

        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        body = await request.json()
        normalized_body, error = _normalize_selector_body(
            body,
            default_mode=_routing_store.default_mode(),
        )
        if normalized_body is None:
            return JSONResponse({"error": error or "Invalid selector payload"}, status_code=400)
        try:
            return JSONResponse(_build_selector_preview(normalized_body, request))
        except RoutingInfeasibleError as exc:
            payload = _routing_infeasible_payload(exc)
            payload["selector"] = _selector_state()
            return JSONResponse(payload, status_code=400)

    async def handle_routing_config(request: Request) -> JSONResponse:
        """GET /v1/routing-config — active routing mode/tier config. POST — update overrides."""
        nonlocal _routing_config
        if request.method == "GET":
            return JSONResponse(_routing_store.export())

        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        body = await request.json()
        action = str(body.get("action", "")).strip().lower()
        try:
            if action == "set-default-mode":
                mode = _parse_mode_value(str(body.get("mode", "")))
                payload = _routing_store.set_default_mode(mode)
            elif action == "set-tier":
                mode = _parse_mode_value(str(body.get("mode", "")))
                tier = _parse_tier_value(str(body.get("tier", "")))
                primary = str(body.get("primary", "")).strip()
                fallback_raw = body.get("fallback", [])
                selection_mode = str(body.get("selection_mode", "")).strip().lower()
                hard_pin = bool(body.get("hard_pin", False))
                if selection_mode:
                    hard_pin = selection_mode in {"hard-pin", "hard_pin", "pinned"}
                if isinstance(fallback_raw, str):
                    fallback = [part.strip() for part in fallback_raw.split(",") if part.strip()]
                elif isinstance(fallback_raw, list):
                    fallback = [str(item).strip() for item in fallback_raw if str(item).strip()]
                else:
                    return JSONResponse({"error": "fallback must be a list or comma-separated string"}, status_code=400)
                payload = _routing_store.set_tier(
                    mode,
                    tier,
                    primary=primary,
                    fallback=fallback,
                    hard_pin=hard_pin,
                )
            elif action == "reset-tier":
                mode = _parse_mode_value(str(body.get("mode", "")))
                tier = _parse_tier_value(str(body.get("tier", "")))
                payload = _routing_store.reset_tier(mode, tier)
            elif action == "reset-default-mode":
                payload = _routing_store.reset_default_mode()
            elif action == "reset":
                payload = _routing_store.reset()
            else:
                return JSONResponse(
                    {
                        "error": "Invalid action",
                        "allowed": [
                            "set-default-mode",
                            "set-tier",
                            "reset-tier",
                            "reset-default-mode",
                            "reset",
                        ],
                    },
                    status_code=400,
                )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _refresh_active_pricing()
        return JSONResponse(payload)

    async def handle_scenes(request: Request) -> JSONResponse:
        """GET /v1/scenes — list all scenes.  POST /v1/scenes — add/remove/import."""
        if request.method == "GET":
            return JSONResponse(_scene_store.export())

        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        body = await request.json()
        action = str(body.get("action", "")).strip().lower()
        try:
            if action == "add":
                name = str(body.get("name", "")).strip()
                primary = str(body.get("primary", "")).strip()
                if not name or not primary:
                    return JSONResponse({"error": "name and primary are required"}, status_code=400)
                fallback_raw = body.get("fallback", [])
                if isinstance(fallback_raw, str):
                    fallback = [p.strip() for p in fallback_raw.split(",") if p.strip()]
                elif isinstance(fallback_raw, list):
                    fallback = [str(f).strip() for f in fallback_raw if str(f).strip()]
                else:
                    fallback = []
                tier_floor_raw = body.get("tier_floor")
                tier_floor = Tier(str(tier_floor_raw).upper()) if tier_floor_raw else None
                tier_cap_raw = body.get("tier_cap")
                tier_cap = Tier(str(tier_cap_raw).upper()) if tier_cap_raw else None
                allowed_providers_raw = body.get("allowed_providers", [])
                allowed_providers = (
                    [str(p).strip() for p in allowed_providers_raw if str(p).strip()]
                    if isinstance(allowed_providers_raw, list) else []
                )
                max_cost_raw = body.get("max_cost_per_request")
                max_cost = float(max_cost_raw) if max_cost_raw is not None else None
                if max_cost is not None and max_cost <= 0:
                    return JSONResponse({"error": "max_cost_per_request must be positive"}, status_code=400)
                scene = SceneConfig(
                    name=name,
                    primary=primary,
                    fallback=fallback,
                    hard_pin=bool(body.get("hard_pin", False)),
                    description=str(body.get("description", "")),
                    tier_floor=tier_floor,
                    tier_cap=tier_cap,
                    allowed_providers=allowed_providers,
                    max_cost_per_request=max_cost,
                )
                stored = _scene_store.add(scene)
                return JSONResponse({"ok": True, "scene": _serialize_scene_response(stored)})
            elif action == "remove":
                name = str(body.get("name", "")).strip()
                if not name:
                    return JSONResponse({"error": "name is required"}, status_code=400)
                removed = _scene_store.remove(name)
                return JSONResponse({"ok": removed, "name": name})
            elif action == "import":
                data = body.get("data", {})
                count = _scene_store.import_scenes(data)
                return JSONResponse({"ok": True, "imported": count})
            else:
                return JSONResponse(
                    {"error": "Invalid action", "allowed": ["add", "remove", "import"]},
                    status_code=400,
                )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    async def handle_scene_detail(request: Request) -> JSONResponse:
        """GET /v1/scenes/<name> — get one scene."""
        name = request.path_params["name"]
        scene = _scene_store.get(name)
        if scene is None:
            return JSONResponse({"error": f"Scene '{name}' not found"}, status_code=404)
        return JSONResponse(_serialize_scene_response(scene))

    def _serialize_scene_response(scene: SceneConfig) -> dict:
        data = _serialize_scene(scene)
        data["model_pool"] = scene.model_pool()
        return data

    async def handle_artifacts(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", "50"))
        return JSONResponse({
            "count": _artifacts.count(),
            "items": _artifacts.list(limit=max(1, min(limit, 200))),
        })

    async def handle_artifact(request: Request) -> JSONResponse:
        artifact_id = request.path_params["artifact_id"]
        artifact = _artifacts.get(artifact_id)
        if artifact is None:
            return JSONResponse({"error": "Artifact not found"}, status_code=404)
        return JSONResponse(artifact)

    async def handle_feedback(request: Request) -> JSONResponse:
        """GET /v1/feedback — status. POST /v1/feedback — submit signal or rollback."""
        if request.method == "GET":
            return JSONResponse({
                **_feedback.status(),
                "route_confidence_calibration": _route_confidence.status(),
            })
        body = await request.json()
        action = body.get("action")
        if action == "rollback":
            denied = _admin_auth_failure(request)
            if denied is not None:
                return denied
            rolled = _feedback.rollback()
            return JSONResponse({
                "ok": True,
                "rolled_back": rolled,
                "route_confidence_calibration": _route_confidence.status(),
            })
        request_id = body.get("request_id", "")
        signal = body.get("signal", "")
        if not request_id or signal not in ("weak", "strong", "ok"):
            return JSONResponse(
                {"error": "Requires request_id and signal (weak|strong|ok)"},
                status_code=400,
            )
        result = _feedback.submit(request_id, signal)
        if result.action != "expired":
            _stats.record_feedback(
                request_id,
                signal=signal,
                ok=result.ok,
                action=result.action,
                from_tier=result.from_tier,
                to_tier=result.to_tier,
                reason=result.reason,
            )
            get_bus().publish({
                "type": "feedback_updated",
                "request_id": request_id,
                "feedback_signal": signal,
                "feedback_ok": result.ok,
                "feedback_action": result.action,
                "feedback_from_tier": result.from_tier,
                "feedback_to_tier": result.to_tier,
                "feedback_reason": result.reason,
                "feedback_submitted_at": time.time(),
            })
            _traces.record_feedback(
                request_id,
                signal=signal,
                ok=result.ok,
                action=result.action,
                from_tier=result.from_tier,
                to_tier=result.to_tier,
                reason=result.reason,
            )
            _route_confidence.fit_from_route_records(_stats.history())
            # ─── v2: update signal weights + shadow labels from feedback ───
            try:
                from uncommon_route.v2_lifecycle import on_feedback as v2_feedback
                v2_feedback(
                    request_id=request_id,
                    signal=signal,
                    routed_tier_v1=result.from_tier or "",
                )
            except Exception:
                pass
        return JSONResponse({
            "ok": result.ok,
            "action": result.action,
            "from_tier": result.from_tier,
            "to_tier": result.to_tier,
            **({"reason": result.reason} if result.reason else {}),
            "total_updates": _feedback.total_updates,
            "route_confidence_calibration": _route_confidence.status(),
        }, status_code=200 if result.ok else 404)

    async def handle_recent(request: Request) -> JSONResponse:
        """GET /v1/stats/recent — recent routed requests with feedback status."""
        limit = int(request.query_params.get("limit", "30"))
        records = _stats.recent(max(limit * 3, limit))
        visible_records: list[dict[str, Any]] = []
        for r in records:
            has_result = bool(r.get("feedback_action"))
            r["feedback_pending"] = (not has_result) and _feedback.has_pending(r["request_id"])
            if has_result or r["feedback_pending"]:
                visible_records.append(r)
            if len(visible_records) >= limit:
                break
        return JSONResponse(visible_records)

    async def handle_events_stream(request: Request) -> Response:
        """GET /v1/events/stream — Server-Sent Events for live dashboard updates."""
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied

        bus = get_bus()
        queue = await bus.subscribe()

        async def _gen() -> AsyncGenerator[bytes, None]:
            yield b": connected\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        payload = json.dumps(event, default=str)
                        yield f"data: {payload}\n\n".encode("utf-8")
                    except asyncio.TimeoutError:
                        yield b": keepalive\n\n"
            finally:
                await bus.unsubscribe(queue)

        return StreamingResponse(
            _gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    async def handle_traces(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        limit = max(1, min(int(request.query_params.get("limit", "50")), 200))
        errors_only = request.query_params.get("errors_only", "").strip().lower() in {"1", "true", "yes"}
        return JSONResponse({
            "total_requests": _traces.count,
            "summary": _traces.summary(),
            "items": _traces.recent(limit=limit, errors_only=errors_only),
        })

    async def handle_trace_detail(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        request_id = str(request.path_params["request_id"]).strip()
        trace = _traces.find(request_id)
        if trace is None:
            return JSONResponse({"error": "Trace not found", "request_id": request_id}, status_code=404)
        return JSONResponse(trace)

    async def handle_session_conversation(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        session_id = str(request.path_params["session_id"]).strip()
        out = _assemble_conversation(_traces, session_id)
        if out is None:
            return JSONResponse(
                {"error": "Session not found", "session_id": session_id},
                status_code=404,
            )
        return JSONResponse(out)

    async def handle_v2_metrics(request: Request) -> JSONResponse:
        """GET /v1/v2-metrics — v2 routing metrics snapshot."""
        from uncommon_route.v2_lifecycle import get_metrics, is_signal_b_promoted
        metrics = get_metrics()
        if metrics is None:
            return JSONResponse({"error": "v2 lifecycle not initialized"}, status_code=503)
        metrics["signal_b_promoted"] = is_signal_b_promoted()
        return JSONResponse(metrics)

    async def handle_route_preview(request: Request) -> JSONResponse:
        """POST /v1/route-preview — preview routing decision without sending request."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        preview_body = dict(body)
        if not preview_body.get("model") and not preview_body.get("mode"):
            try:
                risk_tolerance = float(preview_body.get("risk_tolerance", 0.5))
            except (TypeError, ValueError):
                risk_tolerance = 0.5
            if risk_tolerance <= 0.25:
                preview_body["mode"] = RoutingMode.BEST.value
            elif risk_tolerance >= 0.75:
                preview_body["mode"] = RoutingMode.FAST.value

        normalized_body, error = _normalize_selector_body(
            preview_body,
            default_mode=_routing_store.default_mode(),
        )
        if normalized_body is None:
            return JSONResponse({"error": error or "Invalid route preview payload"}, status_code=400)
        try:
            preview = _build_selector_preview(normalized_body, request)
        except RoutingInfeasibleError as exc:
            payload = _routing_infeasible_payload(exc)
            payload["selector"] = _selector_state()
            return JSONResponse(payload, status_code=400)

        tier_name = str(preview.get("served_tier") or "MEDIUM").upper()
        tier_index = {
            "SIMPLE": 0,
            "MEDIUM": 1,
            "COMPLEX": 3,
        }.get(tier_name, 1)
        legacy_tier_name = {
            "SIMPLE": "low",
            "MEDIUM": "mid",
            "COMPLEX": "high",
        }.get(tier_name, "mid")
        routing_features = preview.get("routing_features")
        step_risk = ""
        if isinstance(routing_features, dict):
            step_risk = str(routing_features.get("step_risk") or "")
        risk_tier = {"low": 0, "normal": 1, "high": 3}.get(step_risk, tier_index)

        result = {
            **preview,
            # Compatibility fields consumed by the existing dashboard Playground.
            "tier": tier_index,
            "tier_name": legacy_tier_name,
            "cost_estimate": preview.get("estimated_cost", 0.0),
            "cost_baseline": preview.get("baseline_cost", preview.get("estimated_cost", 0.0)),
            "signals": [
                {
                    "name": "router",
                    "tier": tier_index,
                    "confidence": preview.get("confidence", 0.0),
                },
                {
                    "name": "step-risk",
                    "tier": risk_tier,
                    "confidence": 1.0 if step_risk else 0.0,
                    "shadow": True,
                },
            ],
        }
        return JSONResponse(result)

    async def _handle_chat_core(
        body: dict,
        request: Request,
        *,
        api_format: str = "openai",
        endpoint_name: str = "chat_completions",
        source_body: dict[str, Any] | None = None,
        source_preview_body: dict[str, Any] | None = None,
    ) -> Response:
        if not upstream:
            msg = _SETUP_GUIDE.strip()
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(503, msg), status_code=503)
            return JSONResponse(
                {"error": {"message": msg, "type": "configuration_error"}},
                status_code=503,
            )
        upstream_chat = _upstream_chat_url(upstream)

        model = (body.get("model") or "").strip().lower()
        is_streaming = body.get("stream", False)
        response_model = str(body.pop("_client_requested_model", "") or model).strip()
        recursion_guarded = _recursion_guard_enabled(request)

        if not model:
            default_mode = _routing_store.default_mode()
            model = VIRTUAL_MODEL_IDS[default_mode]
            body["model"] = model

        requested_model = model
        routing_mode = routing_mode_from_model(model)
        if recursion_guarded and routing_mode is not None:
            msg = "Virtual UncommonRoute models cannot be routed recursively"
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(400, msg), status_code=400)
            return JSONResponse(
                {"error": {"message": msg, "type": "invalid_request_error"}},
                status_code=400,
            )
        is_virtual = routing_mode is not None
        route_start = time.perf_counter_ns()
        route_method: str = "pool"
        confidence = 0.0
        savings = 0.0
        estimated_cost = 0.0
        baseline_cost = 0.0
        selected_model = model
        tier_value = ""
        reasoning = "passthrough"
        route_reasoning = ""
        raw_confidence = 0.0
        confidence_source = ""
        calibration_version = ""
        calibration_sample_count = 0
        calibration_temperature = 1.0
        calibration_applied_tags: list[str] = []
        feature_tags: list[str] = []
        constraint_tags: list[str] = []
        hint_tags: list[str] = []
        answer_depth_value = "standard"
        routing_features_payload: dict[str, object] = {}
        fallback_chain_payload: list[dict[str, object]] = []
        candidate_scores_payload: list[dict[str, object]] = []
        selection_weights_payload: dict[str, float] = {}
        attempts_payload: list[dict[str, Any]] = []
        session_id: str | None = None
        request_id = uuid.uuid4().hex[:12]
        get_bus().publish({
            "type": "request_started",
            "request_id": request_id,
            "timestamp": time.time(),
        })
        debug_headers: dict[str, str] = {}
        prompt_preview = ""
        prompt_hash_value = ""
        fallback_models: list[str] = []
        fallback_reason = ""
        step_type = "general"
        mode_value = routing_mode.value if routing_mode else ""
        decision_tier = ""
        served_quality_value = ""
        served_quality_target_value = ""
        served_quality_floor_value = ""
        capability_lane_value = ""
        complexity_value = 0.33
        input_tokens_before = 0
        input_tokens_after = 0
        artifacts_created = 0
        compacted_messages = 0
        semantic_summaries = 0
        semantic_calls = 0
        semantic_failures = 0
        semantic_quality_fallbacks = 0
        checkpoint_created = False
        rehydrated_artifacts = 0
        sidechannel_estimated_cost = 0.0
        sidechannel_actual_cost: float | None = None
        main_estimated_cost = 0.0
        requested_transport = _requested_transport_name(
            api_format=api_format,
            endpoint_name=endpoint_name,
        )
        transport_decision = TransportDecision(
            requested_transport=requested_transport,
            selected_transport="openai-chat" if requested_transport == "openai-responses" else requested_transport,
            reason="transport not evaluated yet",
            preference_source="pending",
            native_anthropic_transport=requested_transport == "anthropic-messages",
        )
        prompt, system_prompt, max_tokens = _extract_prompt(body)
        effective_output_tokens = max_tokens
        _pv = " ".join(prompt[:80].split())
        prompt_preview = (_pv + "...") if len(prompt) > 80 else _pv
        prompt_hash_value = trace_prompt_hash(prompt)
        session_id = _resolve_session_id(request, body)
        turn_id = f"{session_id or '_'}:{prompt_hash_value}" if prompt_hash_value else ""
        step_type, tool_names = _classify_step(body)
        _set_header(debug_headers, "x-uncommon-route-request-id", request_id)

        retrial_previous = None
        if is_virtual and prompt:
            retrial_previous = _retrial_detector.record_request(
                prompt,
                model="pending",
                mode=routing_mode.value if routing_mode else "auto",
                tier="",
                request_id=request_id,
            )
            if retrial_previous and retrial_previous.model != "pending":
                _model_experience.record_feedback(
                    retrial_previous.model,
                    retrial_previous.mode,
                    retrial_previous.tier or "MEDIUM",
                    "weak",
                )
                logger.info(
                    "Retrial detected: prompt_hash=%s previous_model=%s → recording weak feedback",
                    retrial_previous.prompt_hash,
                    retrial_previous.model,
                )

        # ── Scene resolution ───────────────────────────────────────────
        # Trigger precedence:
        #   1. x-uncommon-route-scene header
        #   2. Virtual model ID: uncommon-route/scene/<name>
        #   3. (Future: OpenClaw session → scene mapping via plugin)
        _scene_name: str | None = None
        _active_scene: SceneConfig | None = None

        # Check header first (highest priority)
        _scene_header = request.headers.get("x-uncommon-route-scene", "").strip()
        if _scene_header:
            _scene_name = _scene_header

        # Check virtual model ID: uncommon-route/scene/<name>
        if not _scene_name and model.startswith("uncommon-route/scene/"):
            _scene_name = model[len("uncommon-route/scene/"):].strip()
            # Treat scene requests as virtual (need routing)
            if not is_virtual:
                is_virtual = True
                routing_mode = RoutingMode.AUTO
                mode_value = routing_mode.value

        if _scene_name:
            _active_scene = _scene_store.resolve(_scene_name)
            if _active_scene:
                if not is_virtual:
                    is_virtual = True
                    routing_mode = RoutingMode.AUTO
                mode_value = routing_mode.value if routing_mode else ""
                logger.info(
                    "Scene '%s' active: primary=%s hard_pin=%s",
                    _active_scene.name, _active_scene.primary, _active_scene.hard_pin,
                )
                route_method = f"scene:{_active_scene.name}"

        if is_virtual:
            _set_header(debug_headers, "x-uncommon-route-mode", mode_value)
            if prompt.startswith("/debug"):
                debug_prompt = prompt[len("/debug"):].strip() or "hello"
                debug_body = _build_debug_response(debug_prompt, system_prompt, _routing_config)
                if api_format == "anthropic":
                    return JSONResponse(
                        openai_to_anthropic_response(debug_body, "uncommon-route/debug"),
                        headers=debug_headers,
                    )
                return JSONResponse(debug_body, headers=debug_headers)

            routing_features = _extract_routing_features(
                body,
                step_type=step_type,
                tool_names=tool_names,
                prompt=prompt,
                max_output_tokens=max_tokens,
                session_id=session_id,
            )
            routing_features = _routing_features_with_quality_context(
                routing_features,
                api_format=api_format,
                endpoint_name=endpoint_name,
                session_id=session_id,
                has_tools=bool(body.get("tools") or body.get("customTools")),
            )
            ctx_features = extract_context_features(body, step_type, prompt)
            hints = routing_features.workload_hints()
            step_type = routing_features.step_type
            user_keyed = _providers.keyed_models() or None
            try:
                base_available_models = _mapper.routable_models if _mapper.discovered else list(DEFAULT_MODEL_PRICING.keys())
                if user_keyed:
                    base_available_models = _merge_available_models(base_available_models, sorted(user_keyed))
                route_available_models = _circuit_breaker.filter_available(base_available_models)
                if _active_scene and not _active_scene.hard_pin:
                    scene_pool = _active_scene.model_pool()
                    available_scene_models = [m for m in scene_pool if m in route_available_models]
                    route_available_models = available_scene_models or scene_pool

                if _active_scene and _active_scene.hard_pin:
                    from uncommon_route.router.types import (
                        AnswerDepth,
                        FallbackOption,
                        RoutingDecision,
                    )

                    scene_pool = _active_scene.model_pool()
                    scene_tier = _active_scene.tier_floor or Tier.COMPLEX
                    scene_budget = estimate_output_budget(prompt, scene_tier.value)
                    scene_output_budget = min(max_tokens, scene_budget)
                    input_token_estimate = estimate_tokens(prompt)
                    scene_cost = _estimate_cost(
                        _active_scene.primary,
                        input_token_estimate,
                        scene_output_budget,
                    )
                    scene_baseline = _estimate_baseline_cost(
                        input_token_estimate,
                        scene_output_budget,
                    )
                    scene_lane = request_capability_lane(routing_features)
                    scene_quality = model_served_quality(
                        _active_scene.primary,
                        scene_lane,
                        _routing_config.model_capabilities.get(_active_scene.primary),
                    )
                    decision = RoutingDecision(
                        model=_active_scene.primary,
                        tier=scene_tier,
                        capability_lane=scene_lane,
                        served_quality=scene_quality,
                        served_quality_target=scene_quality,
                        served_quality_floor=routing_features.continuity_quality_floor,
                        continuity_quality_floor=routing_features.continuity_quality_floor,
                        mode=routing_mode or RoutingMode.AUTO,
                        confidence=1.0,
                        method=f"scene:{_active_scene.name}:hard-pin",
                        reasoning=f"scene={_active_scene.name} hard-pin -> {_active_scene.primary}",
                        cost_estimate=scene_cost,
                        baseline_cost=scene_baseline,
                        savings=(
                            max(0.0, (scene_baseline - scene_cost) / scene_baseline)
                            if scene_baseline > 0
                            else 0.0
                        ),
                        raw_confidence=1.0,
                        confidence_source="scene",
                        complexity=1.0,
                        constraints=_active_scene.as_routing_constraints(),
                        workload_hints=hints,
                        routing_features=routing_features,
                        answer_depth=AnswerDepth.STANDARD,
                        suggested_output_budget=scene_output_budget,
                        fallback_chain=[
                            FallbackOption(model=m, cost_estimate=0.0, suggested_output_budget=scene_output_budget)
                            for m in scene_pool[1:]
                        ],
                    )
                else:
                    scene_constraints = _active_scene.as_routing_constraints() if _active_scene else None
                    decision = route(
                        prompt,
                        system_prompt,
                        max_tokens,
                        config=_routing_config,
                        routing_mode=routing_mode or RoutingMode.AUTO,
                        routing_features=routing_features,
                        routing_constraints=scene_constraints,
                        user_keyed_models=user_keyed,
                        model_experience=_model_experience,
                        route_confidence_calibrator=_route_confidence,
                        context_features=ctx_features,
                        pricing=_get_pricing(),
                        available_models=route_available_models or None,
                        model_capabilities=_routing_config.model_capabilities,
                        messages=body.get("messages"),
                        tier_floor=_active_scene.tier_floor if _active_scene else None,
                        tier_cap=_active_scene.tier_cap if _active_scene else None,
                    )
            except RoutingInfeasibleError as exc:
                route_latency_us = (time.perf_counter_ns() - route_start) / 1000
                route_reasoning = exc.infeasibility.message
                capability_lane_value = (
                    routing_features.capability_lane.value
                    if routing_features.capability_lane is not None
                    else capability_lane_value
                )
                timestamp_value = time.time()
                infeasible_record = RouteRecord(
                    timestamp=timestamp_value,
                    requested_model=requested_model,
                    mode=mode_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier,
                    served_quality=served_quality_value,
                    served_quality_target=served_quality_target_value,
                    served_quality_floor=served_quality_floor_value,
                    capability_lane=capability_lane_value,
                    confidence=confidence,
                    method=route_method,  # type: ignore[arg-type]
                    raw_confidence=raw_confidence,
                    confidence_source=confidence_source,
                    calibration_version=calibration_version,
                    calibration_sample_count=calibration_sample_count,
                    calibration_temperature=calibration_temperature,
                    calibration_applied_tags=calibration_applied_tags,
                    estimated_cost=estimated_cost,
                    baseline_cost=baseline_cost,
                    savings=savings,
                    latency_us=route_latency_us,
                    transport=transport_decision.selected_transport,
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                    prompt_preview=prompt_preview,
                    route_reasoning=route_reasoning,
                    routing_features_payload=_serialize_routing_features(routing_features),
                    status_code=400,
                    error_code=exc.infeasibility.code.value,
                    error_stage="routing",
                    error_message=exc.infeasibility.message,
                )
                _stats.record(infeasible_record)
                get_bus().publish({
                    "type": "request_completed",
                    "record": record_to_recent_dict(infeasible_record),
                })
                _traces.record(RequestTrace(
                    timestamp=timestamp_value,
                    request_id=request_id,
                    requested_model=requested_model,
                    model=selected_model,
                    status_code=400,
                    mode=mode_value,
                    tier=tier_value,
                    decision_tier=decision_tier,
                    served_quality=served_quality_value,
                    served_quality_target=served_quality_target_value,
                    served_quality_floor=served_quality_floor_value,
                    capability_lane=capability_lane_value,
                    method=route_method,
                    api_format=api_format,
                    endpoint=endpoint_name,
                    is_virtual=True,
                    session_id=session_id,
                    streaming=is_streaming,
                    prompt_preview=prompt_preview,
                    prompt_hash=prompt_hash_value,
                    step_type=step_type,
                    route_reasoning=route_reasoning,
                    latency_us=route_latency_us,
                    transport=transport_decision.selected_transport,
                    requested_transport=transport_decision.requested_transport,
                    transport_reason=transport_decision.reason,
                    transport_preference_source=transport_decision.preference_source,
                    routing_features_payload=_serialize_routing_features(routing_features),
                    error_code=exc.infeasibility.code.value,
                    error_stage="routing",
                    error_message=exc.infeasibility.message,
                    **_extract_session_v2_inputs(request, source_body or body),
                ))
                return _routing_infeasible_response(
                    exc,
                    api_format=api_format,
                    headers=debug_headers,
                )
            try:
                from uncommon_route.v2_lifecycle import associate_request_id
                associate_request_id(request_id)
            except Exception:
                pass
            selected_model = decision.model
            if _is_virtual_model_name(selected_model):
                msg = f"Router selected a virtual model recursively: {selected_model}"
                logger.error(msg)
                if api_format == "anthropic":
                    return JSONResponse(anthropic_error_response(500, msg), status_code=500)
                return JSONResponse(
                    {"error": {"message": msg, "type": "routing_error"}},
                    status_code=500,
                )
            tier_value = decision.tier.value
            decision_tier = tier_value
            route_method = decision.method
            get_bus().publish({
                "type": "request_routed",
                "request_id": request_id,
                "turn_id": turn_id,
                "tier": tier_value,
                "model": selected_model,
                "method": route_method,
                "transport": transport_decision.selected_transport,
                "prompt_preview": prompt_preview,
            })
            served_quality_value = decision.served_quality.value
            served_quality_target_value = decision.served_quality_target.value
            served_quality_floor_value = (
                decision.served_quality_floor.value
                if decision.served_quality_floor is not None
                else ""
            )
            capability_lane_value = decision.capability_lane.value
            mode_value = decision.mode.value
            if _debug_log.isEnabledFor(logging.DEBUG):
                _debug_log.debug(
                    "=== ROUTING DECISION === tier=%s model=%s confidence=%.2f raw=%.2f source=%s "
                    "prompt_tokens=%d prompt=%.100s reasoning=%s",
                    tier_value, selected_model, decision.confidence, decision.raw_confidence, decision.confidence_source,
                    estimate_tokens(prompt), prompt[:100], decision.reasoning,
                )
            reasoning = decision.reasoning
            route_reasoning = decision.reasoning
            estimated_cost = decision.cost_estimate
            baseline_cost = decision.baseline_cost
            confidence = decision.confidence
            savings = decision.savings
            mode_value = decision.mode.value
            raw_confidence = decision.raw_confidence
            confidence_source = decision.confidence_source
            calibration_version = decision.calibration_version
            calibration_sample_count = decision.calibration_sample_count
            calibration_temperature = decision.calibration_temperature
            calibration_applied_tags = list(decision.calibration_applied_tags)
            feature_tags = list(decision.routing_features.tags())
            constraint_tags = list(decision.constraints.tags())
            hint_tags = list(decision.workload_hints.tags())
            answer_depth_value = decision.answer_depth.value
            selection_weights_payload = _selection_weights_payload(_routing_config, mode_value)
            complexity_value = decision.complexity
            routing_features_payload = _serialize_routing_features(decision.routing_features)
            fallback_chain_payload = _serialize_fallback_chain(decision.fallback_chain)
            candidate_scores_payload = _serialize_candidate_scores(decision.candidate_scores[:5])

            if _retrial_detector.history_size > 0:
                _retrial_detector._history[-1].model = selected_model
                _retrial_detector._history[-1].tier = tier_value

            body["model"] = selected_model
            if not body.get("stream") and "logprobs" not in body:
                body["logprobs"] = True
                body["top_logprobs"] = 3

            composition = await compose_messages_semantic(
                body.get("messages", []),
                _artifacts,
                _composition_policy,
                semantic_compressor=_semantic,
                session_id=session_id,
                request=request,
                step_type=step_type,
                is_agentic=hints.is_agentic,
            )
            body["messages"] = composition.messages
            input_tokens_before = composition.input_tokens_before
            input_tokens_after = composition.input_tokens_after
            artifacts_created = len(composition.artifact_ids)
            compacted_messages = composition.compacted_messages + composition.offloaded_messages
            semantic_summaries = composition.semantic_summaries
            semantic_calls = composition.semantic_calls
            semantic_failures = composition.semantic_failures
            semantic_quality_fallbacks = composition.semantic_quality_fallbacks
            checkpoint_created = composition.checkpoint_created
            rehydrated_artifacts = composition.rehydrated_artifacts
            sidechannel_estimated_cost = composition.semantic_estimated_cost
            sidechannel_actual_cost = composition.semantic_actual_cost
            output_budget = estimate_output_budget(prompt, tier_value)
            effective_output_tokens = min(max_tokens, output_budget)
            estimated_cost = _estimate_cost(
                selected_model,
                input_tokens_after,
                effective_output_tokens,
            )
            baseline_cost = _estimate_baseline_cost(
                input_tokens_before if input_tokens_before > 0 else input_tokens_after,
                effective_output_tokens,
            )
            main_estimated_cost = estimated_cost
            estimated_cost += sidechannel_estimated_cost
            transport_decision = _choose_transport(
                api_format=api_format,
                endpoint_name=endpoint_name,
                selected_model=selected_model,
                provider_entry=_providers.get_for_model(selected_model),
                upstream_provider=_mapper.provider,
                upstream_base=upstream,
                step_type=step_type,
                has_tools=bool(body.get("tools") or body.get("customTools")),
                has_tool_results=any(
                    isinstance(message, dict) and str(message.get("role", "")).strip().lower() == "tool"
                    for message in body.get("messages", [])
                    if isinstance(message, dict)
                ),
                anthropic_beta_present=bool(request.headers.get("anthropic-beta")),
            )

            check = await _spend_reservation.reserve(request_id, estimated_cost)
            if not check.allowed:
                timestamp_value = time.time()
                spend_blocked_record = RouteRecord(
                    timestamp=timestamp_value,
                    requested_model=requested_model,
                    mode=mode_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    served_quality=served_quality_value,
                    served_quality_target=served_quality_target_value,
                    served_quality_floor=served_quality_floor_value,
                    capability_lane=capability_lane_value,
                    confidence=confidence,
                    method=route_method,  # type: ignore[arg-type]
                    raw_confidence=raw_confidence,
                    confidence_source=confidence_source,
                    calibration_version=calibration_version,
                    calibration_sample_count=calibration_sample_count,
                    calibration_temperature=calibration_temperature,
                    calibration_applied_tags=calibration_applied_tags,
                    estimated_cost=estimated_cost,
                    baseline_cost=baseline_cost,
                    savings=savings,
                    latency_us=(time.perf_counter_ns() - route_start) / 1000,
                    transport=transport_decision.selected_transport,
                    session_id=session_id,
                    turn_id=turn_id,
                    step_type=step_type,
                    request_id=request_id,
                    prompt_preview=prompt_preview,
                    route_reasoning=route_reasoning,
                    constraint_tags=constraint_tags,
                    hint_tags=hint_tags,
                    feature_tags=feature_tags,
                    answer_depth=answer_depth_value,
                    routing_features_payload=routing_features_payload,
                    fallback_chain_payload=fallback_chain_payload,
                    candidate_scores_payload=candidate_scores_payload,
                    status_code=429,
                    error_code="spend_limit_exceeded",
                    error_stage="guardrail",
                    error_message=check.reason or "Spending limit exceeded",
                )
                _stats.record(spend_blocked_record)
                get_bus().publish({
                    "type": "request_completed",
                    "record": record_to_recent_dict(spend_blocked_record),
                })
                _traces.record(RequestTrace(
                    timestamp=timestamp_value,
                    request_id=request_id,
                    requested_model=requested_model,
                    model=selected_model,
                    status_code=429,
                    mode=mode_value,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    served_quality=served_quality_value,
                    served_quality_target=served_quality_target_value,
                    served_quality_floor=served_quality_floor_value,
                    capability_lane=capability_lane_value,
                    method=route_method,
                    api_format=api_format,
                    endpoint=endpoint_name,
                    is_virtual=True,
                    session_id=session_id,
                    streaming=is_streaming,
                    prompt_preview=prompt_preview,
                    prompt_hash=prompt_hash_value,
                    step_type=step_type,
                    route_reasoning=route_reasoning,
                    confidence=confidence,
                    raw_confidence=raw_confidence,
                    confidence_source=confidence_source,
                    calibration_version=calibration_version,
                    calibration_sample_count=calibration_sample_count,
                    calibration_temperature=calibration_temperature,
                    calibration_applied_tags=calibration_applied_tags,
                    complexity=complexity_value,
                    estimated_cost=estimated_cost,
                    baseline_cost=baseline_cost,
                    savings=savings,
                    latency_us=(time.perf_counter_ns() - route_start) / 1000,
                    transport=transport_decision.selected_transport,
                    requested_transport=transport_decision.requested_transport,
                    transport_reason=transport_decision.reason,
                    transport_preference_source=transport_decision.preference_source,
                    fallback_reason=fallback_reason,
                    answer_depth=answer_depth_value,
                    constraint_tags=constraint_tags,
                    hint_tags=hint_tags,
                    feature_tags=feature_tags,
                    routing_features_payload=routing_features_payload,
                    fallback_chain_payload=fallback_chain_payload,
                    candidate_scores_payload=candidate_scores_payload,
                    selection_weights_payload=selection_weights_payload,
                    attempts_payload=attempts_payload,
                    error_code="spend_limit_exceeded",
                    error_stage="guardrail",
                    error_message=check.reason or "Spending limit exceeded",
                    **_extract_session_v2_inputs(request, source_body or body),
                ))
                return _spend_error(check, api_format=api_format, headers=debug_headers)

            route_feats = extract_features(prompt, system_prompt)
            _feedback.capture(
                request_id,
                route_feats,
                tier_value,
                model=selected_model,
                mode=mode_value,
            )
            fallback_models = []
            for fb in decision.fallback_chain:
                fb_model = fb.model
                if fb_model == selected_model or _is_virtual_model_name(fb_model):
                    continue
                if _mapper.discovered and _mapper.is_available(fb_model) is False:
                    logger.info("Skipping unavailable fallback model: %s", fb_model)
                    continue
                fallback_models.append(fb_model)
        else:
            selected_model = model
            tier_value = ""
            mode_value = "passthrough"
            reasoning = "passthrough"
            route_reasoning = reasoning
            route_method = "passthrough"
            full_text = f"{system_prompt or ''} {prompt}".strip()
            input_tokens_before = estimate_tokens(full_text) if full_text else 0
            input_tokens_after = input_tokens_before
            estimated_cost = _estimate_cost(selected_model, input_tokens_after, max_tokens)
            baseline_cost = estimated_cost
            main_estimated_cost = estimated_cost
            effective_output_tokens = max_tokens
            raw_confidence = 1.0
            confidence_source = "passthrough"
            calibration_version = ""
            calibration_sample_count = 0
            calibration_temperature = 1.0
            calibration_applied_tags = []
            feature_tags = []
            constraint_tags = []
            hint_tags = []
            answer_depth_value = "standard"
            _set_header(debug_headers, "x-uncommon-route-mode", mode_value)
            _set_header(debug_headers, "x-uncommon-route-model", selected_model)
            _set_header(debug_headers, "x-uncommon-route-reasoning", reasoning)
            transport_decision = _choose_transport(
                api_format=api_format,
                endpoint_name=endpoint_name,
                selected_model=selected_model,
                provider_entry=_providers.get_for_model(selected_model),
                upstream_provider=_mapper.provider,
                upstream_base=upstream,
                step_type=step_type,
                has_tools=bool(body.get("tools") or body.get("customTools")),
                has_tool_results=any(
                    isinstance(message, dict) and str(message.get("role", "")).strip().lower() == "tool"
                    for message in body.get("messages", [])
                    if isinstance(message, dict)
                ),
                anthropic_beta_present=bool(request.headers.get("anthropic-beta")),
            )

        route_latency_us = (time.perf_counter_ns() - route_start) / 1000

        primary_key = _primary_api_key

        def _estimated_total_cost_for(model_name: str) -> tuple[float, float]:
            token_input = input_tokens_after if input_tokens_after > 0 else input_tokens_before
            main_cost = _estimate_cost(model_name, token_input, effective_output_tokens)
            total_cost = main_cost + (sidechannel_estimated_cost if is_virtual else 0.0)
            return main_cost, total_cost

        def _prepare_attempt(model_name: str) -> dict[str, Any]:
            attempt_provider_entry = _providers.get_for_model(model_name)
            attempt_upstream_body = json.loads(json.dumps(body))
            _requested_max = attempt_upstream_body.get("max_tokens")
            if isinstance(_requested_max, int) and _requested_max > UPSTREAM_MAX_OUTPUT_TOKENS:
                attempt_upstream_body["max_tokens"] = UPSTREAM_MAX_OUTPUT_TOKENS
            attempt_headers: dict[str, str] = {}
            for key in (
                "authorization",
                "content-type",
                "accept",
                "user-agent",
                _RECURSION_GUARD_HEADER,
                _ORIGINAL_MODEL_HEADER,
            ):
                val = request.headers.get(key)
                if val:
                    attempt_headers[key] = val
            if api_format == "anthropic" and "authorization" not in attempt_headers:
                x_api_key = request.headers.get("x-api-key")
                if x_api_key:
                    attempt_headers["authorization"] = f"Bearer {x_api_key}"
            if "content-type" not in attempt_headers:
                attempt_headers["content-type"] = "application/json"
            attempt_headers["user-agent"] = f"uncommon-route/{VERSION}"
            if is_virtual and not attempt_provider_entry:
                attempt_headers[_RECURSION_GUARD_HEADER] = "1"
                if requested_model:
                    attempt_headers[_ORIGINAL_MODEL_HEADER] = requested_model

            resolved_model = model_name
            if attempt_provider_entry:
                resolved_model = resolve_upstream_model(attempt_provider_entry.name, model_name)
            else:
                resolved_model = _mapper.resolve(model_name)
            attempt_upstream_body["model"] = resolved_model

            attempt_has_tools = bool(
                attempt_upstream_body.get("tools")
                or attempt_upstream_body.get("customTools")
            )
            attempt_has_tool_results = any(
                isinstance(message, dict) and str(message.get("role", "")).strip().lower() == "tool"
                for message in attempt_upstream_body.get("messages", [])
                if isinstance(message, dict)
            )
            attempt_transport_decision = _choose_transport(
                api_format=api_format,
                endpoint_name=endpoint_name,
                selected_model=model_name,
                provider_entry=attempt_provider_entry,
                upstream_provider=_mapper.provider,
                upstream_base=upstream,
                step_type=step_type,
                has_tools=attempt_has_tools,
                has_tool_results=attempt_has_tool_results,
                anthropic_beta_present=bool(request.headers.get("anthropic-beta")),
            )
            attempt_native_anthropic_transport = attempt_transport_decision.native_anthropic_transport
            if attempt_native_anthropic_transport:
                source_has_thinking = _contains_anthropic_thinking_blocks(source_body)
                target_base = attempt_provider_entry.base_url if attempt_provider_entry and attempt_provider_entry.base_url else upstream
                target_base = _anthropic_transport_base(
                    target_base,
                    provider_family_for_model(
                        model_name,
                        provider_name=getattr(attempt_provider_entry, "name", None),
                        upstream_provider=_mapper.provider,
                    ),
                )
                attempt_target_chat_url = _anthropic_messages_url(
                    target_base,
                )
                if (
                    api_format == "anthropic"
                    and source_body is not None
                    and (
                        source_has_thinking
                        or _can_reuse_native_anthropic_body(
                            upstream_body=attempt_upstream_body,
                            source_preview_body=source_preview_body,
                        )
                    )
                ):
                    attempt_transport_body = _reuse_anthropic_source_body(
                        source_body=source_body,
                        upstream_body=attempt_upstream_body,
                    )
                else:
                    attempt_transport_body = openai_to_anthropic_request(attempt_upstream_body)
                if disable_anthropic_cache:
                    attempt_cache_plan = strip_anthropic_cache_controls(attempt_transport_body)
                else:
                    attempt_cache_plan = apply_anthropic_cache_breakpoints(
                        attempt_transport_body,
                        session_id=session_id,
                        step_type=step_type,
                    )
            else:
                if attempt_provider_entry and attempt_provider_entry.base_url:
                    attempt_target_chat_url = f"{attempt_provider_entry.base_url.rstrip('/')}/chat/completions"
                else:
                    attempt_target_chat_url = upstream_chat
                attempt_transport_body = json.loads(json.dumps(attempt_upstream_body))
                attempt_cache_plan = _apply_provider_cache_plan(
                    attempt_transport_body,
                    selected_model=model_name,
                    provider_entry=attempt_provider_entry,
                    session_id=session_id,
                    step_type=step_type,
                    upstream_provider=_mapper.provider,
                )

            if attempt_provider_entry:
                if attempt_native_anthropic_transport:
                    attempt_headers.pop("authorization", None)
                    attempt_headers["x-api-key"] = attempt_provider_entry.api_key
                else:
                    attempt_headers["authorization"] = f"Bearer {attempt_provider_entry.api_key}"
            elif primary_key:
                if attempt_native_anthropic_transport:
                    attempt_headers.pop("authorization", None)
                    attempt_headers["x-api-key"] = primary_key
                else:
                    attempt_headers["authorization"] = f"Bearer {primary_key}"
            if attempt_native_anthropic_transport:
                if "x-api-key" not in attempt_headers and "authorization" in attempt_headers:
                    bearer = attempt_headers["authorization"]
                    if bearer.lower().startswith("bearer "):
                        attempt_headers["x-api-key"] = bearer[7:].strip()
                if "x-api-key" in attempt_headers:
                    attempt_headers.pop("authorization", None)
                attempt_headers.setdefault("anthropic-version", request.headers.get("anthropic-version", "2023-06-01"))
                anthropic_beta = request.headers.get("anthropic-beta")
                if anthropic_beta:
                    attempt_headers["anthropic-beta"] = anthropic_beta

            return {
                "selected_model": model_name,
                "provider_entry": attempt_provider_entry,
                "upstream_body": attempt_upstream_body,
                "target_chat_url": attempt_target_chat_url,
                "transport_body": attempt_transport_body,
                "headers": attempt_headers,
                "transport_decision": attempt_transport_decision,
                "native_anthropic_transport": attempt_native_anthropic_transport,
                "cache_plan": attempt_cache_plan,
                "resolved_model": resolved_model,
            }

        attempt = _prepare_attempt(selected_model)
        provider_entry = attempt["provider_entry"]
        upstream_body = attempt["upstream_body"]
        target_chat_url = attempt["target_chat_url"]
        transport_body = attempt["transport_body"]
        fwd_headers = attempt["headers"]
        transport_decision = attempt["transport_decision"]
        native_anthropic_transport = attempt["native_anthropic_transport"]
        cache_plan = attempt["cache_plan"]
        resolved_model = attempt["resolved_model"]
        _set_route_strategy_headers(
            debug_headers,
            transport_decision=transport_decision,
            cache_plan=cache_plan,
        )

        def _current_route_strategy() -> tuple[str, str, str, int]:
            return (
                transport_decision.selected_transport,
                _cache_mode_name(cache_plan),
                _cache_family_name(cache_plan),
                cache_plan.cache_breakpoints,
            )

        current_attempt_trace: dict[str, Any] | None = None
        attempt_start_ns: dict[int, int] = {}

        def _elapsed_ms_since(start_ns: int | None) -> float:
            if start_ns is None:
                return 0.0
            return max(0.0, (time.perf_counter_ns() - start_ns) / 1_000_000.0)

        def _current_attempt_elapsed_ms() -> float:
            if current_attempt_trace is None:
                return 0.0
            attempt_index = int(current_attempt_trace.get("attempt_index") or 0)
            return _elapsed_ms_since(attempt_start_ns.get(attempt_index))

        def _attempt_total_upstream_elapsed_ms() -> float:
            values = []
            for attempt_trace in attempts_payload:
                try:
                    elapsed = float(attempt_trace.get("upstream_elapsed_ms") or 0.0)
                except (TypeError, ValueError):
                    elapsed = 0.0
                if elapsed > 0:
                    values.append(elapsed)
            return sum(values)

        def _attempt_first_token_ms() -> float:
            for attempt_trace in reversed(attempts_payload):
                if not attempt_trace.get("success"):
                    continue
                for key in ("first_token_ms", "provider_ttft_ms"):
                    try:
                        value = float(attempt_trace.get(key) or 0.0)
                    except (TypeError, ValueError):
                        value = 0.0
                    if value > 0:
                        return value
            return 0.0

        def _begin_attempt_trace(
            attempt_payload: dict[str, Any],
            *,
            fallback_from: str | None = None,
        ) -> None:
            nonlocal current_attempt_trace
            attempt_index = len(attempts_payload) + 1
            attempt_start_ns[attempt_index] = time.perf_counter_ns()
            provider_name = ""
            if attempt_payload.get("provider_entry") is not None:
                provider_name = str(getattr(attempt_payload["provider_entry"], "name", "") or "")
            selected_attempt_model = str(attempt_payload["selected_model"])
            resolved_attempt_model = str(attempt_payload["resolved_model"])
            current_attempt_trace = {
                "attempt_index": attempt_index,
                "selected_model": selected_attempt_model,
                "resolved_model": resolved_attempt_model,
                "provider_name": provider_name,
                "target_url": attempt_payload["target_chat_url"],
                "requested_transport": attempt_payload["transport_decision"].requested_transport,
                "transport": attempt_payload["transport_decision"].selected_transport,
                "transport_reason": attempt_payload["transport_decision"].reason,
                "transport_preference_source": attempt_payload["transport_decision"].preference_source,
                "cache_mode": _cache_mode_name(attempt_payload["cache_plan"]),
                "cache_family": _cache_family_name(attempt_payload["cache_plan"]),
                "cache_breakpoints": attempt_payload["cache_plan"].cache_breakpoints,
                "fallback_from": fallback_from or "",
                "fallback_reason": (
                    f"{fallback_from} unavailable -> {resolved_attempt_model}"
                    if fallback_from else ""
                ),
                "started_at": time.time(),
                "response_headers_ms": 0.0,
                "upstream_elapsed_ms": 0.0,
                "first_token_ms": 0.0,
                "provider_ttft_ms": 0.0,
                "tokens_per_second": 0.0,
                "status_code": 0,
                "success": False,
                "error_code": "",
                "error_message": "",
            }
            attempts_payload.append(current_attempt_trace)

        def _complete_attempt_trace(
            *,
            status_code: int | None = None,
            success: bool = False,
            error_code: str = "",
            error_message: str = "",
            response_headers: bool = False,
        ) -> None:
            if current_attempt_trace is None:
                return
            elapsed_ms = _current_attempt_elapsed_ms()
            if status_code is not None:
                current_attempt_trace["status_code"] = status_code
            current_attempt_trace["success"] = success
            if response_headers and not current_attempt_trace.get("response_headers_ms"):
                current_attempt_trace["response_headers_ms"] = elapsed_ms
            current_attempt_trace["upstream_elapsed_ms"] = elapsed_ms
            if error_code:
                current_attempt_trace["error_code"] = error_code
            if error_message:
                current_attempt_trace["error_message"] = error_message

        def _mark_current_attempt_first_token() -> None:
            if current_attempt_trace is None:
                return
            try:
                current = float(current_attempt_trace.get("first_token_ms") or 0.0)
            except (TypeError, ValueError):
                current = 0.0
            if current <= 0:
                current_attempt_trace["first_token_ms"] = _current_attempt_elapsed_ms()

        def _apply_current_attempt_usage_timings(usage_metrics: UsageMetrics | None) -> None:
            if current_attempt_trace is None or usage_metrics is None:
                return
            if usage_metrics.ttft_ms is not None and usage_metrics.ttft_ms > 0:
                current_attempt_trace["provider_ttft_ms"] = float(usage_metrics.ttft_ms)
                try:
                    first_token_ms = float(current_attempt_trace.get("first_token_ms") or 0.0)
                except (TypeError, ValueError):
                    first_token_ms = 0.0
                if first_token_ms <= 0:
                    current_attempt_trace["first_token_ms"] = float(usage_metrics.ttft_ms)
            if usage_metrics.tps is not None and usage_metrics.tps > 0:
                current_attempt_trace["tokens_per_second"] = float(usage_metrics.tps)

        def _append_blocked_attempt(
            model_name: str,
            *,
            error_code: str,
            error_message: str,
        ) -> None:
            attempts_payload.append({
                "attempt_index": len(attempts_payload) + 1,
                "selected_model": model_name,
                "resolved_model": model_name,
                "provider_name": "",
                "target_url": "",
                "requested_transport": transport_decision.requested_transport,
                "transport": transport_decision.selected_transport,
                "transport_reason": transport_decision.reason,
                "transport_preference_source": transport_decision.preference_source,
                "cache_mode": "",
                "cache_family": "",
                "cache_breakpoints": 0,
                "fallback_from": "",
                "fallback_reason": "",
                "started_at": time.time(),
                "response_headers_ms": 0.0,
                "upstream_elapsed_ms": 0.0,
                "first_token_ms": 0.0,
                "provider_ttft_ms": 0.0,
                "tokens_per_second": 0.0,
                "status_code": 0,
                "success": False,
                "error_code": error_code,
                "error_message": error_message,
                "blocked": True,
            })

        if is_virtual:
            _set_header(debug_headers, "x-uncommon-route-mode", mode_value)
            _set_header(debug_headers, "x-uncommon-route-request-id", request_id)
            _set_header(debug_headers, "x-uncommon-route-model", selected_model)
            _set_header(debug_headers, "x-uncommon-route-tier", tier_value)
            _set_header(debug_headers, "x-uncommon-route-decision-tier", decision_tier or tier_value)
            _set_header(debug_headers, "x-uncommon-route-method", route_method)
            if served_quality_value:
                _set_header(debug_headers, "x-uncommon-route-served-quality", served_quality_value)
            if capability_lane_value:
                _set_header(debug_headers, "x-uncommon-route-capability-lane", capability_lane_value)
                _set_header(debug_headers, "x-uncommon-route-lane", capability_lane_value)
            _set_header(debug_headers, "x-uncommon-route-step", step_type)
            _set_header(debug_headers, "x-uncommon-route-input-before", input_tokens_before)
            _set_header(debug_headers, "x-uncommon-route-input-after", input_tokens_after)
            _set_header(debug_headers, "x-uncommon-route-artifacts", artifacts_created)
            _set_header(debug_headers, "x-uncommon-route-semantic-calls", semantic_calls)
            _set_header(debug_headers, "x-uncommon-route-semantic-fallbacks", semantic_quality_fallbacks)
            _set_header(debug_headers, "x-uncommon-route-checkpoints", 1 if checkpoint_created else 0)
            _set_header(debug_headers, "x-uncommon-route-rehydrated", rehydrated_artifacts)
            _set_route_strategy_headers(
                debug_headers,
                transport_decision=transport_decision,
                cache_plan=cache_plan,
            )
            _set_header(debug_headers, "x-uncommon-route-reasoning", reasoning)
            stream_tag = " stream" if is_streaming else ""
            session_tag = f"  session:{session_id[:8]}" if session_id else ""
            fmt_tag = f"  [{api_format}]" if api_format != "openai" else ""
            transport_name, cache_mode_name, _cache_family, _cache_breakpoints = _current_route_strategy()
            print(
                f"[route] {mode_value}:{tier_value} → {selected_model}"
                f"  ${estimated_cost:.4f}  (in {input_tokens_before}->{input_tokens_after}"
                f"  transport:{transport_name}"
                f"  cache:{cache_mode_name}"
                f"  sem:{semantic_calls}"
                f"  {route_latency_us:.0f}µs"
                f"  {route_method}{stream_tag}{session_tag}{fmt_tag})"
            )

        def _sync_virtual_debug_headers() -> None:
            if not is_virtual:
                return
            _set_header(debug_headers, "x-uncommon-route-model", selected_model)
            _set_header(debug_headers, "x-uncommon-route-method", route_method)
            if served_quality_value:
                _set_header(debug_headers, "x-uncommon-route-served-quality", served_quality_value)
            if capability_lane_value:
                _set_header(debug_headers, "x-uncommon-route-capability-lane", capability_lane_value)
                _set_header(debug_headers, "x-uncommon-route-lane", capability_lane_value)
            _set_route_strategy_headers(
                debug_headers,
                transport_decision=transport_decision,
                cache_plan=cache_plan,
            )
            _set_header(debug_headers, "x-uncommon-route-reasoning", reasoning)

        def _apply_attempt(
            attempt_payload: dict[str, Any],
            *,
            fallback_from: str | None = None,
            record_successful_fallback: bool = True,
        ) -> None:
            nonlocal selected_model, provider_entry, upstream_body, target_chat_url
            nonlocal transport_body, fwd_headers, transport_decision, native_anthropic_transport
            nonlocal cache_plan, resolved_model, route_method, fallback_reason
            nonlocal reasoning, route_reasoning, main_estimated_cost, estimated_cost
            nonlocal served_quality_value, served_quality_floor_value, served_quality_target_value, capability_lane_value

            selected_model = attempt_payload["selected_model"]
            provider_entry = attempt_payload["provider_entry"]
            upstream_body = attempt_payload["upstream_body"]
            target_chat_url = attempt_payload["target_chat_url"]
            transport_body = attempt_payload["transport_body"]
            fwd_headers = attempt_payload["headers"]
            transport_decision = attempt_payload["transport_decision"]
            native_anthropic_transport = attempt_payload["native_anthropic_transport"]
            cache_plan = attempt_payload["cache_plan"]
            resolved_model = attempt_payload["resolved_model"]
            main_estimated_cost, estimated_cost = _estimated_total_cost_for(selected_model)
            lane = request_capability_lane(routing_features)
            capability_lane_value = capability_lane_value or lane.value
            served_quality_value = model_served_quality(
                selected_model,
                lane,
                _routing_config.model_capabilities.get(selected_model),
            ).value

            if fallback_from is not None:
                route_method = "fallback"
                fallback_reason = f"{fallback_from} unavailable -> {resolved_model}"
                reasoning = f"fallback: {fallback_reason}"
                route_reasoning = reasoning
                if record_successful_fallback:
                    _mapper.record_alias(fallback_from, resolved_model)
                if record_successful_fallback and request_id:
                    _feedback.rebind_request(
                        request_id,
                        model=selected_model,
                        tier=tier_value,
                        mode=mode_value,
                    )
                if record_successful_fallback:
                    print(f"[route] fallback → {resolved_model}  ({fallback_from} unavailable)")

            _sync_virtual_debug_headers()

        async def _spend_error_for_model(model_name: str) -> JSONResponse | None:
            if not is_virtual:
                return None
            _next_main_cost, total_cost = _estimated_total_cost_for(model_name)
            check = await _spend_reservation.update_reserve(request_id, total_cost)
            if check.allowed:
                return None
            _append_blocked_attempt(
                model_name,
                error_code="spend_limit_exceeded",
                error_message=check.reason or "Spending limit exceeded",
            )
            return _spend_error(check, api_format=api_format, headers=debug_headers)

        def _should_try_fallback(status_code: int, content: bytes) -> bool:
            if not is_virtual or not fallback_models:
                return False
            if status_code in (500, 502, 503, 504):
                return True
            return status_code in (400, 404, 422) and _is_model_error(content)

        def _transport_error_response(exc: httpx.TransportError) -> httpx.Response:
            status_code = 504 if isinstance(exc, httpx.TimeoutException) else 502
            error_type = "timeout" if status_code == 504 else "proxy_error"
            if status_code == 504:
                message = "Upstream request timed out"
            elif isinstance(exc, httpx.ConnectError):
                message = f"Upstream unreachable: {upstream_chat}"
            else:
                message = "Upstream disconnected before sending a response"
            detail = str(exc).strip()
            if detail:
                message = f"{message}: {detail}"
            return httpx.Response(
                status_code,
                json={"error": {"message": message, "type": error_type}},
            )

        async def _post_non_stream_attempt(attempt_payload: dict[str, Any]) -> httpx.Response:
            try:
                return await _get_client().post(
                    attempt_payload["target_chat_url"],
                    json=attempt_payload["transport_body"],
                    headers=attempt_payload["headers"],
                )
            except httpx.TransportError as exc:
                return _transport_error_response(exc)

        def _build_proxy_response(
            *,
            status_code: int,
            content: bytes,
            content_type: str,
            native_transport: bool,
        ) -> Response:
            if api_format == "anthropic":
                if native_transport:
                    return Response(
                        content=content,
                        status_code=status_code,
                        headers={
                            "content-type": content_type,
                            **debug_headers,
                        },
                    )
                if status_code == 200:
                    try:
                        oai_data = json.loads(content)
                        anth_data = openai_to_anthropic_response(
                            oai_data,
                            _anthropic_response_model_name(
                                response_model or requested_model or selected_model
                            ),
                        )
                        return JSONResponse(anth_data, headers=debug_headers)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
                try:
                    err_body = json.loads(content)
                    err_msg = err_body.get("error", {}).get("message", "Upstream error")
                except (json.JSONDecodeError, TypeError):
                    err_msg = "Upstream error"
                return JSONResponse(
                    anthropic_error_response(status_code, err_msg),
                    status_code=status_code,
                    headers=debug_headers,
                )

            if native_transport:
                if status_code == 200:
                    try:
                        anth_data = json.loads(content)
                        oai_data = anthropic_to_openai_response(anth_data, selected_model)
                        return JSONResponse(oai_data, headers=debug_headers)
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        pass
                try:
                    err_body = json.loads(content)
                    err_msg = err_body.get("error", {}).get("message", "Upstream error")
                except (json.JSONDecodeError, TypeError):
                    err_msg = "Upstream error"
                return JSONResponse(
                    {"error": {"message": err_msg, "type": "proxy_error"}},
                    status_code=status_code,
                    headers=debug_headers,
                )

            return Response(
                content=content,
                status_code=status_code,
                headers={
                    "content-type": content_type,
                    **debug_headers,
                },
            )

        def _record_route_trace(
            *,
            status_code: int,
            actual_cost: float | None = None,
            usage_metrics: UsageMetrics | None = None,
            streaming: bool,
            error_code: str = "",
            error_stage: str = "",
            error_message: str = "",
            response_content: bytes | None = None,
            stream_chunks: list[bytes] | None = None,
        ) -> None:
            method_value = route_method if is_virtual else "passthrough"
            confidence_value = confidence if is_virtual else 1.0
            savings_value = savings if is_virtual else 0.0
            timestamp_value = time.time()
            route_latency_ms = route_latency_us / 1000.0
            upstream_elapsed_ms = _attempt_total_upstream_elapsed_ms()
            first_token_ms = _attempt_first_token_ms()

            if is_virtual or status_code == 200:
                completed_record = RouteRecord(
                    timestamp=timestamp_value,
                    requested_model=requested_model,
                    mode=mode_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    served_quality=served_quality_value,
                    served_quality_target=served_quality_target_value,
                    served_quality_floor=served_quality_floor_value,
                    capability_lane=capability_lane_value,
                    confidence=confidence_value,
                    method=method_value,  # type: ignore[arg-type]
                    raw_confidence=raw_confidence,
                    confidence_source=confidence_source,
                    calibration_version=calibration_version,
                    calibration_sample_count=calibration_sample_count,
                    calibration_temperature=calibration_temperature,
                    calibration_applied_tags=calibration_applied_tags,
                    estimated_cost=estimated_cost,
                    baseline_cost=baseline_cost,
                    actual_cost=actual_cost,
                    savings=savings_value,
                    latency_us=route_latency_us,
                    route_latency_ms=route_latency_ms,
                    upstream_elapsed_ms=upstream_elapsed_ms,
                    first_token_ms=first_token_ms,
                    usage_input_tokens=usage_metrics.input_tokens_total if usage_metrics else 0,
                    usage_output_tokens=usage_metrics.output_tokens if usage_metrics else 0,
                    cache_read_input_tokens=usage_metrics.cache_read_input_tokens if usage_metrics else 0,
                    cache_write_input_tokens=usage_metrics.cache_write_input_tokens if usage_metrics else 0,
                    cache_hit_ratio=usage_metrics.cache_hit_ratio if usage_metrics else 0.0,
                    transport=transport_decision.selected_transport,
                    cache_mode=_cache_mode_name(cache_plan),
                    cache_family=_cache_family_name(cache_plan),
                    cache_breakpoints=cache_plan.cache_breakpoints,
                    input_tokens_before=input_tokens_before,
                    input_tokens_after=input_tokens_after,
                    artifacts_created=artifacts_created,
                    compacted_messages=compacted_messages,
                    semantic_summaries=semantic_summaries,
                    semantic_calls=semantic_calls,
                    semantic_failures=semantic_failures,
                    semantic_quality_fallbacks=semantic_quality_fallbacks,
                    checkpoint_created=checkpoint_created,
                    rehydrated_artifacts=rehydrated_artifacts,
                    sidechannel_estimated_cost=sidechannel_estimated_cost,
                    sidechannel_actual_cost=sidechannel_actual_cost,
                    session_id=session_id,
                    turn_id=turn_id,
                    step_type=step_type,
                    fallback_reason=fallback_reason,
                    streaming=streaming,
                    request_id=request_id,
                    prompt_preview=prompt_preview,
                    complexity=complexity_value,
                    route_reasoning=route_reasoning or reasoning,
                    constraint_tags=constraint_tags,
                    hint_tags=hint_tags,
                    feature_tags=feature_tags,
                    answer_depth=answer_depth_value,
                    routing_features_payload=routing_features_payload,
                    fallback_chain_payload=fallback_chain_payload,
                    candidate_scores_payload=candidate_scores_payload,
                    status_code=status_code,
                    error_code=error_code,
                    error_stage=error_stage,
                    error_message=error_message,
                )
                _stats.record(completed_record)
                get_bus().publish({
                    "type": "request_completed",
                    "record": record_to_recent_dict(completed_record),
                })

            transport_for_capture = transport_decision.selected_transport
            # The Responses API raw body has no `messages` field (it uses
            # `input`/`previous_response_id`); use the converted chat-shape
            # body so request_messages captures the full backbone. Other
            # wire formats keep their original source_body to preserve shape
            # (Anthropic blocks etc.). Decide by endpoint, not upstream
            # transport — Responses ingress is normalized to openai-chat
            # before reaching the upstream.
            if endpoint_name == "responses":
                capture_body = body
            else:
                capture_body = source_body or body

            def _capture_for_record(content, chunks):
                if content is not None:
                    return _capture_non_streaming(capture_body, content, transport_for_capture)
                if chunks is not None:
                    return _capture_streaming(capture_body, chunks, transport_for_capture)
                return {}

            _traces.record(RequestTrace(
                timestamp=timestamp_value,
                request_id=request_id,
                requested_model=requested_model,
                model=selected_model,
                status_code=status_code,
                mode=mode_value,
                tier=tier_value,
                decision_tier=decision_tier or tier_value,
                served_quality=served_quality_value,
                served_quality_target=served_quality_target_value,
                served_quality_floor=served_quality_floor_value,
                capability_lane=capability_lane_value,
                method=method_value,
                api_format=api_format,
                endpoint=endpoint_name,
                is_virtual=is_virtual,
                session_id=session_id,
                streaming=streaming,
                prompt_preview=prompt_preview,
                prompt_hash=prompt_hash_value,
                step_type=step_type,
                route_reasoning=route_reasoning or reasoning,
                confidence=confidence_value,
                raw_confidence=raw_confidence,
                confidence_source=confidence_source,
                calibration_version=calibration_version,
                calibration_sample_count=calibration_sample_count,
                calibration_temperature=calibration_temperature,
                calibration_applied_tags=calibration_applied_tags,
                complexity=complexity_value,
                estimated_cost=estimated_cost,
                baseline_cost=baseline_cost,
                actual_cost=actual_cost,
                savings=savings_value,
                latency_us=route_latency_us,
                route_latency_ms=route_latency_ms,
                upstream_elapsed_ms=upstream_elapsed_ms,
                first_token_ms=first_token_ms,
                usage_input_tokens=usage_metrics.input_tokens_total if usage_metrics else 0,
                usage_output_tokens=usage_metrics.output_tokens if usage_metrics else 0,
                cache_read_input_tokens=usage_metrics.cache_read_input_tokens if usage_metrics else 0,
                cache_write_input_tokens=usage_metrics.cache_write_input_tokens if usage_metrics else 0,
                cache_hit_ratio=usage_metrics.cache_hit_ratio if usage_metrics else 0.0,
                transport=transport_decision.selected_transport,
                requested_transport=transport_decision.requested_transport,
                transport_reason=transport_decision.reason,
                transport_preference_source=transport_decision.preference_source,
                cache_mode=_cache_mode_name(cache_plan),
                cache_family=_cache_family_name(cache_plan),
                cache_breakpoints=cache_plan.cache_breakpoints,
                input_tokens_before=input_tokens_before,
                input_tokens_after=input_tokens_after,
                artifacts_created=artifacts_created,
                compacted_messages=compacted_messages,
                semantic_summaries=semantic_summaries,
                semantic_calls=semantic_calls,
                semantic_failures=semantic_failures,
                semantic_quality_fallbacks=semantic_quality_fallbacks,
                checkpoint_created=checkpoint_created,
                rehydrated_artifacts=rehydrated_artifacts,
                sidechannel_estimated_cost=sidechannel_estimated_cost,
                sidechannel_actual_cost=sidechannel_actual_cost,
                fallback_reason=fallback_reason,
                answer_depth=answer_depth_value,
                constraint_tags=constraint_tags,
                hint_tags=hint_tags,
                feature_tags=feature_tags,
                routing_features_payload=routing_features_payload,
                fallback_chain_payload=fallback_chain_payload,
                candidate_scores_payload=candidate_scores_payload,
                selection_weights_payload=selection_weights_payload,
                attempts_payload=attempts_payload,
                error_code=error_code,
                error_stage=error_stage,
                error_message=error_message,
                **_extract_session_v2_inputs(request, source_body or body),
                **_capture_for_record(response_content, stream_chunks),
            ))

        def _record_response_error(response: Response, *, streaming: bool) -> None:
            body = getattr(response, "body", b"") or b""
            if not isinstance(body, (bytes, bytearray)):
                body = bytes(body)
            derived_error_code, derived_error_message = _extract_error_details(response.status_code, body)
            if derived_error_code == "spend_limit_exceeded":
                error_stage_value = "guardrail"
            else:
                error_stage_value = "upstream_response"
            _record_route_trace(
                status_code=response.status_code,
                streaming=streaming,
                error_code=derived_error_code,
                error_stage=error_stage_value,
                error_message=derived_error_message,
            )

        # Tracks whether the streaming path has completed a record() call.
        # Needed because Starlette cancels the response generator when the
        # client disconnects, raising CancelledError that bypasses
        # `except Exception` and would otherwise leave the row stuck in
        # routed state on the dashboard.
        stream_record_state = {"done": False}

        try:
            if is_streaming:
                async def _record_stream_success(
                    stream_usage: UsageMetrics | None,
                    *,
                    stream_chunks: list[bytes] | None = None,
                ) -> None:
                    stream_actual_cost: float | None = None
                    stream_ttft_ms: float | None = None
                    stream_tps: float | None = None
                    if stream_usage is not None:
                        stream_actual_cost = (
                            stream_usage.actual_cost
                            if stream_usage.actual_cost is not None
                            else _estimate_cost_from_usage(selected_model, stream_usage)
                        )
                        stream_ttft_ms = stream_usage.ttft_ms
                        stream_tps = stream_usage.tps
                    _apply_current_attempt_usage_timings(stream_usage)
                    _complete_attempt_trace(status_code=200, success=True)

                    if is_virtual:
                        _model_experience.observe(
                            selected_model,
                            mode_value,
                            tier_value,
                            success=True,
                            ttft_ms=stream_ttft_ms,
                            tps=stream_tps,
                            total_input_tokens=stream_usage.input_tokens_total if stream_usage else None,
                            uncached_input_tokens=stream_usage.input_tokens_uncached if stream_usage else None,
                            cache_read_tokens=stream_usage.cache_read_input_tokens if stream_usage else 0,
                            cache_write_tokens=stream_usage.cache_write_input_tokens if stream_usage else 0,
                            input_cost_multiplier=stream_usage.input_cost_multiplier if stream_usage else None,
                        )
                        if request_id:
                            _feedback.rebind_request(
                                request_id,
                                model=selected_model,
                                tier=tier_value,
                                mode=mode_value,
                            )
                        combined_cost = (
                            (stream_actual_cost if stream_actual_cost is not None else main_estimated_cost)
                            + (sidechannel_actual_cost if sidechannel_actual_cost is not None else sidechannel_estimated_cost)
                        )
                        await _spend_reservation.settle(
                            request_id,
                            combined_cost,
                            model=selected_model,
                            action="chat",
                        )
                        _record_route_trace(
                            status_code=200,
                            actual_cost=stream_actual_cost,
                            usage_metrics=stream_usage,
                            streaming=True,
                            stream_chunks=stream_chunks,
                        )
                    else:
                        _record_route_trace(
                            status_code=200,
                            actual_cost=stream_actual_cost,
                            usage_metrics=stream_usage,
                            streaming=True,
                            stream_chunks=stream_chunks,
                        )
                    stream_record_state["done"] = True

                async def _record_stream_failure() -> None:
                    await _spend_reservation.release(request_id)
                    _complete_attempt_trace(
                        status_code=502,
                        success=False,
                        error_code="stream_failure",
                        error_message="Streaming response interrupted",
                    )
                    if is_virtual:
                        _model_experience.observe(
                            selected_model,
                            mode_value,
                            tier_value,
                            success=False,
                        )
                        _circuit_breaker.record_failure(selected_model)
                        _record_route_trace(
                            status_code=502,
                            streaming=True,
                            error_code="stream_failure",
                            error_stage="stream",
                            error_message="Streaming response interrupted",
                        )
                    else:
                        _record_route_trace(
                            status_code=502,
                            streaming=True,
                            error_code="stream_failure",
                            error_stage="stream",
                            error_message="Streaming response interrupted",
                        )
                    stream_record_state["done"] = True

                async def _record_stream_aborted(
                    stream_chunks: list[bytes] | None = None,
                ) -> None:
                    if stream_record_state["done"]:
                        return
                    try:
                        await _spend_reservation.release(request_id)
                    except Exception:
                        pass
                    _complete_attempt_trace(
                        status_code=499,
                        success=False,
                        error_code="client_disconnected",
                        error_message="Client closed connection before stream finalized",
                    )
                    _record_route_trace(
                        status_code=499,
                        streaming=True,
                        error_code="client_disconnected",
                        error_stage="stream",
                        error_message="Client closed connection before stream finalized",
                        stream_chunks=stream_chunks,
                    )
                    stream_record_state["done"] = True

                async def _open_stream_attempt(attempt_payload: dict[str, Any]) -> httpx.Response:
                    client = _get_client()
                    req = client.build_request(
                        "POST",
                        attempt_payload["target_chat_url"],
                        json=attempt_payload["transport_body"],
                        headers=attempt_payload["headers"],
                    )
                    return await client.send(req, stream=True)

                async def _select_stream_response() -> tuple[httpx.Response | None, Response | None]:
                    fallback_source_model: str | None = None
                    attempt_queue = [attempt, *(_prepare_attempt(model_name) for model_name in fallback_models)]

                    for index, attempt_payload in enumerate(attempt_queue):
                        if index > 0:
                            spend_error = await _spend_error_for_model(attempt_payload["selected_model"])
                            if spend_error is not None:
                                return None, spend_error

                        _begin_attempt_trace(
                            attempt_payload,
                            fallback_from=fallback_source_model if index > 0 else None,
                        )
                        resp = await _open_stream_attempt(attempt_payload)
                        if resp.status_code < 400:
                            _complete_attempt_trace(
                                status_code=resp.status_code,
                                success=True,
                                response_headers=True,
                            )
                            if index > 0 and fallback_source_model is not None:
                                _apply_attempt(attempt_payload, fallback_from=fallback_source_model)
                            return resp, None

                        content = await resp.aread()
                        content_type = resp.headers.get("content-type", "application/json")
                        await resp.aclose()
                        attempt_error_code, attempt_error_message = _extract_error_details(resp.status_code, content)
                        _complete_attempt_trace(
                            status_code=resp.status_code,
                            error_code=attempt_error_code,
                            error_message=attempt_error_message,
                        )
                        if _should_try_fallback(resp.status_code, content):
                            if fallback_source_model is None:
                                fallback_source_model = str(
                                    attempt_payload["transport_body"].get("model")
                                    or attempt_payload["resolved_model"]
                                )
                            if index > 0:
                                _apply_attempt(
                                    attempt_payload,
                                    fallback_from=fallback_source_model,
                                    record_successful_fallback=False,
                                )
                            continue
                        if index > 0 and fallback_source_model is not None:
                            _apply_attempt(
                                attempt_payload,
                                fallback_from=fallback_source_model,
                                record_successful_fallback=False,
                            )
                        return None, _build_proxy_response(
                            status_code=resp.status_code,
                            content=content,
                            content_type=content_type,
                            native_transport=attempt_payload["native_anthropic_transport"],
                        )

                    return None, _build_proxy_response(
                        status_code=502,
                        content=b'{"error":{"message":"No fallback model succeeded","type":"proxy_error"}}',
                        content_type="application/json",
                        native_transport=native_anthropic_transport,
                    )

                stream_resp, early_response = await _select_stream_response()
                if early_response is not None:
                    await _spend_reservation.release(request_id)
                    _record_response_error(early_response, streaming=True)
                    return early_response

                assert stream_resp is not None

                if native_anthropic_transport:
                    async def anthropic_native_sse() -> AsyncGenerator[bytes, None]:
                        stream_chunks: list[bytes] = []
                        converter = None if api_format == "anthropic" else AnthropicToOpenAIStreamConverter(model=selected_model)
                        try:
                            async for chunk in stream_resp.aiter_bytes():
                                stream_chunks.append(chunk)
                                _mark_current_attempt_first_token()
                                if converter is None:
                                    yield chunk
                                else:
                                    for ev in converter.feed(chunk):
                                        yield ev
                            if converter is not None:
                                for ev in converter.finish():
                                    yield ev
                            await _record_stream_success(
                                parse_stream_usage_metrics(stream_chunks, selected_model, _get_pricing()),
                                stream_chunks=stream_chunks,
                            )
                        except Exception:
                            await _record_stream_failure()
                            raise
                        finally:
                            await _record_stream_aborted(stream_chunks=stream_chunks)
                            await stream_resp.aclose()

                    return StreamingResponse(
                        anthropic_native_sse(),
                        media_type="text/event-stream",
                        headers={
                            "cache-control": "no-cache",
                            "connection": "keep-alive",
                            **debug_headers,
                        },
                    )

                if api_format == "anthropic":
                    anthropic_response_model = _anthropic_response_model_name(
                        response_model or requested_model or selected_model
                    )
                    converter = OpenAIToAnthropicStreamConverter(
                        model=anthropic_response_model
                    )

                    async def anthropic_sse() -> AsyncGenerator[bytes, None]:
                        stream_chunks: list[bytes] = []
                        try:
                            async for chunk in stream_resp.aiter_bytes():
                                stream_chunks.append(chunk)
                                _mark_current_attempt_first_token()
                                for ev in converter.feed(chunk):
                                    yield ev
                            for ev in converter.finish():
                                yield ev
                            await _record_stream_success(
                                parse_stream_usage_metrics(stream_chunks, selected_model, _get_pricing()),
                                stream_chunks=stream_chunks,
                            )
                        except Exception:
                            await _record_stream_failure()
                            raise
                        finally:
                            await _record_stream_aborted(stream_chunks=stream_chunks)
                            await stream_resp.aclose()

                    return StreamingResponse(
                        anthropic_sse(),
                        media_type="text/event-stream",
                        headers={
                            "cache-control": "no-cache",
                            "connection": "keep-alive",
                            **debug_headers,
                        },
                    )

                async def sse_passthrough() -> AsyncGenerator[bytes, None]:
                    stream_chunks: list[bytes] = []
                    try:
                        async for chunk in stream_resp.aiter_bytes():
                            stream_chunks.append(chunk)
                            _mark_current_attempt_first_token()
                            yield _normalize_reasoning_content_chunk(chunk)
                        await _record_stream_success(
                            parse_stream_usage_metrics(stream_chunks, selected_model, _get_pricing()),
                            stream_chunks=stream_chunks,
                        )
                    except Exception:
                        await _record_stream_failure()
                        raise
                    finally:
                        await _record_stream_aborted(stream_chunks=stream_chunks)
                        await stream_resp.aclose()

                return StreamingResponse(
                    sse_passthrough(),
                    media_type="text/event-stream",
                    headers={
                        "cache-control": "no-cache",
                        "connection": "keep-alive",
                        **debug_headers,
                    },
                )

            _begin_attempt_trace(attempt)
            resp = await _post_non_stream_attempt(attempt)
            initial_error_code = ""
            initial_error_message = ""
            if resp.status_code < 400:
                _complete_attempt_trace(status_code=resp.status_code, success=True)
            else:
                initial_error_code, initial_error_message = _extract_error_details(resp.status_code, resp.content)
                _complete_attempt_trace(
                    status_code=resp.status_code,
                    error_code=initial_error_code,
                    error_message=initial_error_message,
                )

            # Fallback: if upstream rejects the model, try alternatives
            if _should_try_fallback(resp.status_code, resp.content):
                fallback_source_model = str(transport_body.get("model") or resolved_model)
                for fb_model in fallback_models:
                    spend_error = await _spend_error_for_model(fb_model)
                    if spend_error is not None:
                        _record_response_error(spend_error, streaming=False)
                        return spend_error
                    fb_attempt = _prepare_attempt(fb_model)
                    _begin_attempt_trace(fb_attempt, fallback_from=fallback_source_model)
                    retry = await _post_non_stream_attempt(fb_attempt)
                    retry_error_code = ""
                    retry_error_message = ""
                    if retry.status_code < 400:
                        _complete_attempt_trace(status_code=retry.status_code, success=True)
                    else:
                        retry_error_code, retry_error_message = _extract_error_details(retry.status_code, retry.content)
                        _complete_attempt_trace(
                            status_code=retry.status_code,
                            error_code=retry_error_code,
                            error_message=retry_error_message,
                        )
                    if retry.status_code < 400:
                        resp = retry
                        _apply_attempt(fb_attempt, fallback_from=fallback_source_model)
                        break
                    resp = retry
                    _apply_attempt(
                        fb_attempt,
                        fallback_from=fallback_source_model,
                        record_successful_fallback=False,
                    )
                    if not _should_try_fallback(retry.status_code, retry.content):
                        break

            actual_cost: float | None = None
            ttft_ms: float | None = None
            tps: float | None = None
            usage_metrics: UsageMetrics | None = None
            if resp.status_code == 200:
                usage_metrics = parse_usage_metrics(resp.content, selected_model, _get_pricing())
                if usage_metrics is not None:
                    actual_cost = (
                        usage_metrics.actual_cost
                        if usage_metrics.actual_cost is not None
                        else _estimate_cost_from_usage(selected_model, usage_metrics)
                    )
                    ttft_ms = usage_metrics.ttft_ms
                    tps = usage_metrics.tps
                    if is_virtual:
                        _set_header(debug_headers, "x-uncommon-route-cache-hit-ratio", round(usage_metrics.cache_hit_ratio, 4))
                        _set_header(debug_headers, "x-uncommon-route-cache-read", usage_metrics.cache_read_input_tokens)
                        _set_header(debug_headers, "x-uncommon-route-cache-write", usage_metrics.cache_write_input_tokens)
                    _apply_current_attempt_usage_timings(usage_metrics)

            if is_virtual:
                if resp.status_code == 200:
                    logprob_conf = None
                    try:
                        resp_json = json.loads(resp.content)
                        logprob_conf = analyze_logprobs(resp_json)
                        if logprob_conf and logprob_conf.confidence_score < 0.3:
                            implicit_signal = compute_implicit_quality(
                                is_retrial=bool(retrial_previous),
                                logprob_confidence=logprob_conf,
                            )
                            if implicit_signal.should_penalize:
                                _model_experience.observe(
                                    selected_model, mode_value, tier_value, success=False,
                                )
                                logger.info(
                                    "Low logprob confidence for %s: score=%.2f → recording failure",
                                    selected_model, logprob_conf.confidence_score,
                                )
                    except Exception:
                        pass

                    _model_experience.observe(
                        selected_model,
                        mode_value,
                        tier_value,
                        success=True,
                        ttft_ms=ttft_ms,
                        tps=tps,
                        total_input_tokens=usage_metrics.input_tokens_total if usage_metrics else None,
                        uncached_input_tokens=usage_metrics.input_tokens_uncached if usage_metrics else None,
                        cache_read_tokens=usage_metrics.cache_read_input_tokens if usage_metrics else 0,
                        cache_write_tokens=usage_metrics.cache_write_input_tokens if usage_metrics else 0,
                        input_cost_multiplier=usage_metrics.input_cost_multiplier if usage_metrics else None,
                    )
                    _circuit_breaker.record_success(selected_model)
                    if request_id:
                        _feedback.rebind_request(
                            request_id,
                            model=selected_model,
                            tier=tier_value,
                            mode=mode_value,
                        )
                else:
                    _model_experience.observe(
                        selected_model,
                        mode_value,
                        tier_value,
                        success=False,
                    )
                    _circuit_breaker.record_failure(selected_model)
                combined_cost = (
                    (actual_cost if actual_cost is not None else main_estimated_cost)
                    + (sidechannel_actual_cost if sidechannel_actual_cost is not None else sidechannel_estimated_cost)
                )
                await _spend_reservation.settle(
                    request_id,
                    combined_cost,
                    model=selected_model,
                    action="chat",
                )
                upstream_error_code = ""
                upstream_error_stage = ""
                upstream_error_message = ""
                if resp.status_code >= 400:
                    upstream_error_code, upstream_error_message = _extract_error_details(resp.status_code, resp.content)
                    upstream_error_stage = "upstream_response"
                _record_route_trace(
                    status_code=resp.status_code,
                    actual_cost=actual_cost,
                    usage_metrics=usage_metrics,
                    streaming=False,
                    error_code=upstream_error_code,
                    error_stage=upstream_error_stage,
                    error_message=upstream_error_message,
                    response_content=resp.content,
                )
                # ─── v2 telemetry Stage 2: complete record with outcome ───
                try:
                    from uncommon_route.v2_lifecycle import complete_telemetry
                    complete_telemetry(
                        request_id=request_id,
                        outcome="success" if resp.status_code == 200 else "failure",
                        final_tier=int(decision.tier.value == "COMPLEX") * 3 if is_virtual else -1,
                        final_model=selected_model,
                    )
                except Exception:
                    pass
            else:
                passthrough_error_code = ""
                passthrough_error_stage = ""
                passthrough_error_message = ""
                if resp.status_code >= 400:
                    passthrough_error_code, passthrough_error_message = _extract_error_details(resp.status_code, resp.content)
                    passthrough_error_stage = "upstream_response"
                _record_route_trace(
                    status_code=resp.status_code,
                    actual_cost=actual_cost,
                    usage_metrics=usage_metrics,
                    streaming=False,
                    error_code=passthrough_error_code,
                    error_stage=passthrough_error_stage,
                    error_message=passthrough_error_message,
                    response_content=resp.content,
                )

            return _build_proxy_response(
                status_code=resp.status_code,
                content=resp.content,
                content_type=resp.headers.get("content-type", "application/json"),
                native_transport=native_anthropic_transport,
            )
        except httpx.ConnectError:
            _complete_attempt_trace(
                status_code=502,
                error_code="connect_error",
                error_message=f"Upstream unreachable: {upstream_chat}",
            )
            if is_virtual:
                await _spend_reservation.release(request_id)
                _model_experience.observe(
                    selected_model,
                    mode_value,
                    tier_value,
                    success=False,
                )
                _circuit_breaker.record_failure(selected_model)
            _record_route_trace(
                status_code=502,
                streaming=is_streaming,
                error_code="connect_error",
                error_stage="upstream_request",
                error_message=f"Upstream unreachable: {upstream_chat}",
            )
            msg = f"Upstream unreachable: {upstream_chat}"
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(502, msg), status_code=502, headers=debug_headers)
            return JSONResponse(
                {"error": {"message": msg, "type": "proxy_error"}},
                status_code=502,
                headers=debug_headers,
            )
        except httpx.TimeoutException:
            _complete_attempt_trace(
                status_code=504,
                error_code="timeout",
                error_message="Upstream request timed out",
            )
            if is_virtual:
                await _spend_reservation.release(request_id)
                _model_experience.observe(
                    selected_model,
                    mode_value,
                    tier_value,
                    success=False,
                )
                _circuit_breaker.record_failure(selected_model)
            _record_route_trace(
                status_code=504,
                streaming=is_streaming,
                error_code="timeout",
                error_stage="upstream_request",
                error_message="Upstream request timed out",
            )
            msg = "Upstream request timed out"
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(504, msg), status_code=504, headers=debug_headers)
            return JSONResponse(
                {"error": {"message": msg, "type": "proxy_error"}},
                status_code=504,
                headers=debug_headers,
            )

    async def handle_chat_completions(request: Request) -> Response:
        body = await request.json()
        return await _handle_chat_core(body, request, endpoint_name="chat_completions")

    async def handle_messages(request: Request) -> Response:
        raw = await request.json()
        preview_body = anthropic_to_openai_request(raw)
        body = preview_body
        requested_model = str(raw.get("model") or "").strip()
        body["_client_requested_model"] = requested_model
        if forced_messages_upstream_model:
            body["model"] = forced_messages_upstream_model
        elif routing_mode_from_model(requested_model) is not None:
            body["model"] = requested_model
        elif requested_model and "/" in requested_model and _forced_messages_default_mode is None:
            body["model"] = requested_model
        else:
            mode = _forced_messages_default_mode or _routing_store.default_mode()
            body["model"] = VIRTUAL_MODEL_IDS[mode]
        return await _handle_chat_core(
            body,
            request,
            api_format="anthropic",
            endpoint_name="messages",
            source_body=raw,
            source_preview_body=preview_body,
        )

    async def handle_responses(request: Request) -> Response:
        raw = await request.json()
        previous_response_id = str(raw.get("previous_response_id") or "").strip()
        previous_messages = None
        if previous_response_id:
            previous_messages = _responses_history.get(previous_response_id)
            if previous_messages is None:
                return JSONResponse(
                    {
                        "error": {
                            "message": f"Unknown previous_response_id: {previous_response_id}",
                            "type": "invalid_request_error",
                        },
                    },
                    status_code=400,
                )

        body, chat_messages = responses_to_openai_chat_request(
            raw,
            previous_messages=previous_messages,
            default_model=VIRTUAL_MODEL_IDS[_routing_store.default_mode()],
        )
        response_id = f"resp_{uuid.uuid4().hex[:24]}"
        upstream_resp = await _handle_chat_core(
            body, request, endpoint_name="responses", source_body=raw,
        )

        if upstream_resp.status_code != 200:
            return upstream_resp

        if isinstance(upstream_resp, StreamingResponse):
            adapter = OpenAIChatToResponsesStreamAdapter(
                request_body=raw,
                response_id=response_id,
            )

            async def responses_sse() -> AsyncGenerator[bytes, None]:
                async for chunk in upstream_resp.body_iterator:
                    adapter.feed(chunk)
                events, assistant_message = adapter.finalize()
                if assistant_message is not None:
                    _responses_history[response_id] = json.loads(
                        json.dumps(chat_messages + [assistant_message])
                    )
                for event in events:
                    yield event

            headers = {
                key: value
                for key, value in upstream_resp.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            return StreamingResponse(
                responses_sse(),
                media_type="text/event-stream",
                headers=headers,
            )

        try:
            chat_payload = json.loads(upstream_resp.body)
        except (AttributeError, TypeError, json.JSONDecodeError):
            return upstream_resp

        responses_payload, assistant_message = openai_chat_response_to_responses(
            chat_payload,
            response_id=response_id,
            request_body=raw,
        )
        if assistant_message is not None:
            _responses_history[response_id] = json.loads(
                json.dumps(chat_messages + [assistant_message])
            )
        headers = {
            key: value
            for key, value in upstream_resp.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        return JSONResponse(responses_payload, headers=headers)

    @asynccontextmanager
    async def _lifespan(app: Starlette) -> _LifespanGen[None, None]:
        import asyncio
        await _on_startup()
        nonlocal _rediscovery_task, _benchmark_refresh_task
        if upstream:
            _rediscovery_task = asyncio.create_task(_rediscovery_loop())
            _benchmark_refresh_task = asyncio.create_task(_benchmark_refresh_loop())
        try:
            yield
        finally:
            if _rediscovery_task is not None:
                _rediscovery_task.cancel()
            if _benchmark_refresh_task is not None:
                _benchmark_refresh_task.cancel()
            # ─── v2 lifecycle shutdown ───
            try:
                from uncommon_route.v2_lifecycle import on_shutdown as v2_shutdown
                v2_shutdown()
            except Exception as e:
                logger.warning("v2 lifecycle shutdown failed: %s", e)

    routes = [
        Route("/health", handle_health, methods=["GET"]),
        Route("/v1/connections", handle_connections, methods=["GET", "PUT"]),
        Route("/v1/providers", handle_providers, methods=["GET", "POST"]),
        Route("/v1/providers/{name:str}", handle_provider_detail, methods=["DELETE"]),
        Route("/v1/providers/{name:str}/verify", handle_provider_detail, methods=["POST"]),
        Route("/v1/models", handle_models, methods=["GET"]),
        Route("/v1/models/mapping", handle_models_mapping, methods=["GET"]),
        Route("/v1/chat/completions", handle_chat_completions, methods=["POST"]),
        Route("/v1/messages", handle_messages, methods=["POST"]),
        Route("/v1/v1/messages", handle_messages, methods=["POST"]),
        Route("/v1/responses", handle_responses, methods=["POST"]),
        Route("/v1/spend", handle_spend, methods=["GET", "POST"]),
        Route("/v1/stats", handle_stats, methods=["GET", "POST"]),
        Route("/v1/selector", handle_selector, methods=["GET", "POST"]),
        Route("/v1/routing-config", handle_routing_config, methods=["GET", "POST"]),
        Route("/v1/scenes", handle_scenes, methods=["GET", "POST"]),
        Route("/v1/scenes/{name:str}", handle_scene_detail, methods=["GET"]),
        Route("/v1/artifacts", handle_artifacts, methods=["GET"]),
        Route("/v1/artifacts/{artifact_id:str}", handle_artifact, methods=["GET"]),
        Route("/v1/feedback", handle_feedback, methods=["GET", "POST"]),
        Route("/v1/stats/recent", handle_recent, methods=["GET"]),
        Route("/v1/events/stream", handle_events_stream, methods=["GET"]),
        Route("/v1/traces", handle_traces, methods=["GET"]),
        Route("/v1/traces/{request_id:str}", handle_trace_detail, methods=["GET"]),
        Route(
            "/v1/sessions/{session_id:str}/conversation",
            handle_session_conversation,
            methods=["GET"],
        ),
        Route("/v1/route-preview", handle_route_preview, methods=["POST"]),
        Route("/v1/v2-metrics", handle_v2_metrics, methods=["GET"]),
    ]
    if _dashboard_mount is not None:
        routes.append(Mount("/dashboard", app=_dashboard_mount))

    return Starlette(routes=routes, lifespan=_lifespan)


def serve(
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    upstream: str | None = DEFAULT_UPSTREAM,
    spend_control: SpendControl | None = None,
    route_stats: RouteStats | None = None,
) -> None:
    """Start the proxy server (blocking)."""
    import uvicorn

    if os.environ.get("UNCOMMON_ROUTE_DEBUG_ROUTING"):
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
        _debug_log.setLevel(logging.DEBUG)

    app = create_app(
        upstream=upstream,
        spend_control=spend_control,
        route_stats=route_stats,
    )
    effective = resolve_primary_connection(
        cli_upstream=str(upstream or "").strip() or None,
        store=ConnectionsStore(),
    )
    providers = load_providers()
    base = f"http://{host}:{port}"
    bar = "─" * 45

    has_dashboard = False
    try:
        import importlib.resources as _pr
        has_dashboard = (_pr.files("uncommon_route") / "static" / "index.html").is_file()
    except Exception:  # noqa: BLE001
        pass

    print()
    print(f"  UncommonRoute v{VERSION}")
    print(f"  {bar}")
    if effective.upstream:
        short = effective.upstream.replace("https://", "").replace("http://", "").rstrip("/v1").rstrip("/")
        print(f"  Upstream:    {short}")
        print(f"  Proxy:       {base}")
        if has_dashboard:
            print(f"  Dashboard:   {base}/dashboard/")
        print()
        print("  Quick test:")
        print(f"    curl {base}/health")
    elif providers.providers:
        names = ", ".join(sorted(providers.providers.keys()))
        print("  Upstream:    (not configured)")
        print(f"  BYOK:        {names}")
        print(f"  Proxy:       {base}")
        if has_dashboard:
            print(f"  Dashboard:   {base}/dashboard/")
        print()
        print("  Ready in BYOK mode:")
        print("    Requests will use your configured provider keys.")
        print("    Run `uncommon-route doctor` to review the active setup.")
    else:
        print("  Upstream:    (not configured)")
        print()
        print("  Get started:")
        print("    uncommon-route init")
        print()
        print("  Manual setup:")
        print('    export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"')
        print('    export UNCOMMON_ROUTE_API_KEY="your-key"')
        print("    # or: uncommon-route provider add openai sk-...")
        print("    uncommon-route serve")
    print(f"  {bar}")
    print(flush=True)

    uvicorn.run(app, host=host, port=port, log_level="info")
