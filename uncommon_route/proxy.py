"""OpenAI-compatible proxy server for UncommonRoute.

Accepts /v1/chat/completions, runs route() for virtual model names
(uncommon-route/auto, eco, premium, free), replaces the model field,
and forwards to a configurable upstream OpenAI-compatible API.

Non-routing model names are passed through unchanged.

Integrations:
  - Session persistence: sticky model per session, three-strike escalation
  - Spend control: per-request / hourly / daily / session limits

Usage:
    from uncommon_route.proxy import create_app, serve
    serve(port=8403, upstream="http://127.0.0.1:11434/v1")
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
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
from uncommon_route.cache_support import (
    CacheRequestPlan,
    UsageMetrics,
    apply_anthropic_cache_breakpoints,
    apply_openai_cache_hints,
    estimate_usage_cost,
    parse_stream_usage_metrics,
    parse_usage_metrics,
    provider_family_for_model,
)
from uncommon_route.composition import CompositionPolicy, compose_messages_semantic, load_composition_policy
from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify, extract_features
from uncommon_route.router.config import (
    BASELINE_MODEL,
    DEFAULT_CONFIG,
    DEFAULT_MODEL_PRICING,
    VIRTUAL_MODEL_IDS,
    get_tier_configs,
    routing_profile_from_model,
    virtual_model_entries,
)
from uncommon_route.router.structural import estimate_tokens, estimate_output_budget
from uncommon_route.router.types import RequestRequirements, RoutingProfile, Tier
from uncommon_route.semantic import SemanticCallResult, SemanticCompressor
from uncommon_route.semantic import SideChannelTaskConfig, score_semantic_quality
from uncommon_route.session import (
    SessionStore,
    derive_session_id,
    get_session_id,
    hash_request_content,
)
from uncommon_route.spend_control import SpendControl
from uncommon_route.stats import RouteRecord, RouteStats
from uncommon_route.feedback import FeedbackCollector
from uncommon_route.model_experience import ModelExperienceStore
from uncommon_route.providers import ProvidersConfig, load_providers
from uncommon_route.model_map import ModelMapper
from uncommon_route.routing_config_store import RoutingConfigStore
from uncommon_route.anthropic_compat import (
    anthropic_to_openai_request,
    anthropic_to_openai_response,
    openai_to_anthropic_request,
    openai_to_anthropic_response,
    anthropic_error_response,
    AnthropicToOpenAIStreamConverter,
    OpenAIToAnthropicStreamConverter,
)

VERSION = "0.2.9"
DEFAULT_UPSTREAM = os.environ.get("UNCOMMON_ROUTE_UPSTREAM", "")
DEFAULT_PORT = int(os.environ.get("UNCOMMON_ROUTE_PORT", "8403"))
VIRTUAL_MODEL = VIRTUAL_MODEL_IDS[RoutingProfile.AUTO]

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


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute dollar cost from token counts using the model pricing table."""
    mp = DEFAULT_MODEL_PRICING.get(model)
    if mp is None:
        return 0.0
    return (input_tokens / 1_000_000) * mp.input_price + (output_tokens / 1_000_000) * mp.output_price


def _estimate_baseline_cost(input_tokens: int, output_tokens: int) -> float:
    return _estimate_cost(BASELINE_MODEL, input_tokens, output_tokens)


def _estimate_cost_from_usage(model: str, usage: UsageMetrics) -> float | None:
    pricing = DEFAULT_MODEL_PRICING.get(model)
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
    usage = parse_usage_metrics(content, model, DEFAULT_MODEL_PRICING)
    if usage is None:
        return None
    return usage.actual_cost if usage.actual_cost is not None else _estimate_cost_from_usage(model, usage)


def _parse_usage_performance(content: bytes) -> tuple[float | None, float | None]:
    usage = parse_usage_metrics(content, "", DEFAULT_MODEL_PRICING)
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


def _extract_user_prompt_text(value: Any) -> str:
    if isinstance(value, str):
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
            cleaned = _strip_wrapper_prefix(raw)
            if cleaned:
                parts.append(cleaned)
        if parts:
            return "\n".join(parts)
    return _content_text(value).strip()


def _extract_prompt(body: dict) -> tuple[str, str | None, int]:
    """Extract last user message, system prompt, and max_tokens from request body."""
    messages = body.get("messages", [])
    max_tokens: int = body.get("max_tokens", 4096)

    prompt = ""
    system_prompt: str | None = None

    for msg in reversed(messages):
        if msg.get("role") == "user" and not prompt:
            candidate = _extract_user_prompt_text(msg.get("content", ""))
            if candidate:
                prompt = candidate
        if msg.get("role") == "system" and system_prompt is None:
            text = _content_text(msg.get("content", ""))
            system_prompt = text if text else None

    return prompt, system_prompt, max_tokens


def _build_debug_response(prompt: str, system_prompt: str | None, routing_config=DEFAULT_CONFIG) -> dict:
    """Build a debug diagnostics response showing routing details."""
    result = classify(prompt, system_prompt, routing_config.scoring)
    decision = route(prompt, system_prompt, config=routing_config)

    tier_boundaries = routing_config.scoring.tier_boundaries
    lines = [
        "UncommonRoute Debug",
        "",
        f"Tier: {decision.tier.value} | Model: {decision.model}",
        f"Confidence: {decision.confidence:.2f} | Cost: ${decision.cost_estimate:.4f} | Savings: {decision.savings:.0%}",
        f"Reasoning: {decision.reasoning}",
        "",
        f"Scoring (raw: {result.score:.3f})",
        f"  Signals: {', '.join(result.signals)}",
        "",
        f"Tier Boundaries: SIMPLE <{tier_boundaries.simple_medium:.2f}"
        f" | MEDIUM <{tier_boundaries.medium_complex:.2f}"
        f" | COMPLEX <{tier_boundaries.complex_reasoning:.2f}"
        f" | REASONING >={tier_boundaries.complex_reasoning:.2f}",
    ]

    if decision.fallback_chain:
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


class UpstreamSemanticCompressor:
    """Runs semantic compression tasks through cheap upstream models."""

    def __init__(
        self,
        *,
        upstream_chat: str,
        providers_config: ProvidersConfig,
        model_mapper: ModelMapper,
        composition_policy: CompositionPolicy,
    ) -> None:
        self._upstream_chat = upstream_chat
        self._providers = providers_config
        self._mapper = model_mapper
        self._policy = composition_policy

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
            upstream_model = model_id
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
            env_key = (
                os.environ.get("UNCOMMON_ROUTE_API_KEY", "")
                or os.environ.get("COMMONSTACK_API_KEY", "")
            )
            if auth:
                headers["authorization"] = auth
            elif env_key:
                headers["authorization"] = f"Bearer {env_key}"
        return target_chat_url, headers, upstream_model


_OPENCLAW_SESSION_HEADER = "x-openclaw-session-key"


def _resolve_session(
    request: Request,
    body: dict,
    session_store: SessionStore,
) -> str | None:
    """Resolve session ID from header or message content.

    Checks (in order): configured header (x-session-id), OpenClaw's
    session header (x-openclaw-session-key), then derives from the first
    user message as a last resort.
    """
    raw_headers = {k: v for k, v in request.headers.items()}
    sid = get_session_id(raw_headers, session_store.config.header_name)
    if sid:
        return sid
    sid = get_session_id(raw_headers, _OPENCLAW_SESSION_HEADER)
    if sid:
        return sid
    messages = body.get("messages", [])
    return derive_session_id(messages)


_TIER_RANK: dict[str, int] = {"SIMPLE": 0, "MEDIUM": 1, "COMPLEX": 2, "REASONING": 3}


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
        name = fn.get("name", "")
        if name:
            tool_names.append(name)

    last_role = ""
    for msg in reversed(messages):
        if msg.get("role") != "system":
            last_role = msg.get("role", "")
            break

    if last_role == "tool":
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


def _extract_requirements(body: dict, step_type: str) -> RequestRequirements:
    messages = body.get("messages", [])
    raw_tools = body.get("tools") or body.get("customTools") or []
    has_vision = any(_has_vision_content(msg.get("content")) for msg in messages if isinstance(msg, dict))
    needs_tool_calling = bool(raw_tools)
    is_agentic = step_type != "general" or needs_tool_calling
    return RequestRequirements(
        needs_tool_calling=needs_tool_calling,
        needs_vision=has_vision,
        prefers_reasoning=False,
        is_agentic=is_agentic,
    )


def _tool_selection_tier_cap(prompt: str, step_type: str) -> Tier | None:
    if step_type != "tool-selection":
        return None
    lowered = prompt.lower()
    if any(
        marker in lowered
        for marker in ("prove", "deriv", "formal", "invariant", "theorem", "deadlock-free")
    ):
        return Tier.COMPLEX
    return Tier.MEDIUM


_MODEL_ERROR_PATTERNS = ("model", "not found", "not available", "does not exist", "unsupported", "invalid model")


def _is_model_error(content: bytes) -> bool:
    """Heuristic: does the upstream error body indicate a model-level problem?"""
    try:
        text = content.decode("utf-8", errors="replace").lower()
        return any(p in text for p in _MODEL_ERROR_PATTERNS)
    except Exception:  # noqa: BLE001
        return False


def _spend_error(result: Any, *, api_format: str = "openai") -> JSONResponse:
    """Build a 429 error response for spend control violations."""
    if api_format == "anthropic":
        return JSONResponse(
            anthropic_error_response(429, result.reason or "Spending limit exceeded"),
            status_code=429,
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
    return JSONResponse(body, status_code=429)


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
    return CacheRequestPlan(family=family)


def _anthropic_messages_url(base_url: str) -> str:
    root = str(base_url or "").rstrip("/")
    if root.endswith("/messages"):
        return root
    if root.endswith("/v1"):
        return f"{root}/messages"
    return f"{root}/v1/messages"


def _supports_native_anthropic_transport(
    *,
    selected_model: str,
    provider_entry: Any,
    upstream_provider: str,
    upstream_base: str,
) -> bool:
    if provider_family_for_model(selected_model) != "anthropic":
        return False
    target_base = getattr(provider_entry, "base_url", "") if provider_entry else upstream_base
    target_lower = str(target_base or "").lower()
    if "api.anthropic.com" in target_lower or "commonstack.ai" in target_lower:
        return True
    return upstream_provider in {"anthropic", "commonstack"}


def _transport_name(native_anthropic_transport: bool) -> str:
    return "anthropic-messages" if native_anthropic_transport else "openai-chat"


def _cache_mode_name(cache_plan: CacheRequestPlan) -> str:
    return cache_plan.mode or "none"


def _cache_family_name(cache_plan: CacheRequestPlan) -> str:
    return cache_plan.family or "generic"


def _set_route_strategy_headers(
    headers: dict[str, str],
    *,
    native_anthropic_transport: bool,
    cache_plan: CacheRequestPlan,
) -> None:
    _set_header(headers, "x-uncommon-route-transport", _transport_name(native_anthropic_transport))
    _set_header(headers, "x-uncommon-route-cache-mode", _cache_mode_name(cache_plan))
    _set_header(headers, "x-uncommon-route-cache-family", _cache_family_name(cache_plan))
    headers.pop("x-uncommon-route-cache-breakpoints", None)
    headers.pop("x-uncommon-route-cache-key", None)
    if cache_plan.cache_breakpoints:
        _set_header(headers, "x-uncommon-route-cache-breakpoints", cache_plan.cache_breakpoints)
    if cache_plan.prompt_cache_key:
        _set_header(headers, "x-uncommon-route-cache-key", cache_plan.prompt_cache_key)


def _selection_profiles_payload(config) -> dict[str, dict[str, float]]:
    return {
        profile.value: {
            "editorial": weights.editorial,
            "cost": weights.cost,
            "latency": weights.latency,
            "reliability": weights.reliability,
            "feedback": weights.feedback,
            "cache_affinity": weights.cache_affinity,
            "byok": weights.byok,
            "free_bias": weights.free_bias,
            "local_bias": weights.local_bias,
            "reasoning_bias": weights.reasoning_bias,
        }
        for profile, weights in config.selection_profiles.items()
    }


def _bandit_profiles_payload(config) -> dict[str, dict[str, object]]:
    return {
        profile.value: {
            "enabled": cfg.enabled,
            "reward_weight": cfg.reward_weight,
            "exploration_weight": cfg.exploration_weight,
            "warmup_pulls": cfg.warmup_pulls,
            "min_samples_for_guardrail": cfg.min_samples_for_guardrail,
            "min_reliability": cfg.min_reliability,
            "max_cost_ratio": cfg.max_cost_ratio,
            "enabled_tiers": [tier.value for tier in cfg.enabled_tiers],
        }
        for profile, cfg in config.bandit_profiles.items()
    }


def _serialize_candidate_scores(candidate_scores: list[Any]) -> list[dict[str, object]]:
    return [
        {
            "model": score.model,
            "total": round(score.total, 6),
            "predicted_cost": round(score.predicted_cost, 8),
            "editorial": round(score.editorial, 6),
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


def _parse_profile_value(value: str) -> RoutingProfile:
    return RoutingProfile(str(value).strip().lower())


def _parse_tier_value(value: str) -> Tier:
    return Tier(str(value).strip().upper())


def _preview_session_escalation(
    session_entry: Any,
    tier_configs: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    tier_order = ["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"]
    try:
        idx = tier_order.index(session_entry.tier)
    except ValueError:
        return None
    if idx >= len(tier_order) - 1:
        return None
    next_tier = tier_order[idx + 1]
    next_cfg = tier_configs.get(next_tier)
    if not next_cfg:
        return None
    return str(next_cfg["primary"]), next_tier


def _normalize_selector_body(body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    payload = dict(body)
    model = str(payload.get("model") or "").strip().lower()
    profile_value = payload.get("profile")
    if profile_value is not None and not model:
        try:
            model = VIRTUAL_MODEL_IDS[_parse_profile_value(str(profile_value))]
        except ValueError:
            return None, "Invalid profile"
        payload["model"] = model
    if payload.get("messages"):
        if not payload.get("model"):
            payload["model"] = VIRTUAL_MODEL_IDS[RoutingProfile.AUTO]
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
        payload["model"] = VIRTUAL_MODEL_IDS[RoutingProfile.AUTO]
    return payload, None


def create_app(
    upstream: str = DEFAULT_UPSTREAM,
    session_store: SessionStore | None = None,
    spend_control: SpendControl | None = None,
    providers_config: ProvidersConfig | None = None,
    route_stats: RouteStats | None = None,
    feedback: FeedbackCollector | None = None,
    model_mapper: ModelMapper | None = None,
    artifact_store: ArtifactStore | None = None,
    composition_policy: CompositionPolicy | None = None,
    semantic_compressor: SemanticCompressor | None = None,
    model_experience: ModelExperienceStore | None = None,
    routing_config_store: RoutingConfigStore | None = None,
) -> Starlette:
    """Create the ASGI application wired to the given upstream base URL.

    Args:
        upstream: Base URL for the upstream OpenAI-compatible API.
        session_store: Optional SessionStore for sticky sessions.
        spend_control: Optional SpendControl for spending limits.
        providers_config: Optional BYOK provider config for user-keyed models.
        route_stats: Optional RouteStats for per-request analytics.
        feedback: Optional FeedbackCollector for online learning.
        model_mapper: Optional ModelMapper for upstream model name translation.
    """
    _sessions = session_store or SessionStore()
    _spend = spend_control or SpendControl()
    _providers = providers_config or load_providers()
    _stats = route_stats or RouteStats()
    _model_experience = model_experience or ModelExperienceStore()
    _feedback = feedback or FeedbackCollector(model_experience=_model_experience)
    if getattr(_feedback, "_model_experience", None) is None:
        _feedback._model_experience = _model_experience
    _mapper = model_mapper or ModelMapper(upstream)
    _artifacts = artifact_store or ArtifactStore()
    _composition_policy = composition_policy or load_composition_policy()
    _semantic = semantic_compressor
    _routing_store = routing_config_store or RoutingConfigStore()
    _routing_config = _routing_store.config()

    upstream_chat = f"{upstream.rstrip('/')}/chat/completions"
    if _semantic is None and upstream:
        _semantic = UpstreamSemanticCompressor(
            upstream_chat=upstream_chat,
            providers_config=_providers,
            model_mapper=_mapper,
            composition_policy=_composition_policy,
        )

    async def _on_startup() -> None:
        if not upstream:
            return
        api_key = (
            os.environ.get("UNCOMMON_ROUTE_API_KEY", "")
            or os.environ.get("COMMONSTACK_API_KEY", "")
        ) or None
        count = await _mapper.discover(api_key)
        if count > 0:
            gw_tag = " (gateway)" if _mapper.is_gateway else ""
            print(f"[UncommonRoute] Discovered {count} models from {_mapper.provider}{gw_tag}")
            unresolved = _mapper.unresolved_models()
            if unresolved:
                names = ", ".join(unresolved[:5])
                extra = f" (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""
                print(f"[UncommonRoute] Warning: {len(unresolved)} internal model(s) not found upstream: {names}{extra}")
        elif _mapper.provider != "unknown":
            print(f"[UncommonRoute] Warning: could not discover models from {_mapper.provider} — using static aliases")

    def _selector_state(
        *,
        bucket_profile: RoutingProfile | None = None,
        bucket_tier: Tier | None = None,
    ) -> dict[str, Any]:
        current_config = _routing_config
        state: dict[str, Any] = {
            "selection_profiles": _selection_profiles_payload(current_config),
            "bandit_profiles": _bandit_profiles_payload(current_config),
            "experience": _model_experience.summary(),
        }
        if bucket_profile is not None and bucket_tier is not None:
            state["bucket"] = _model_experience.bucket_summary(bucket_profile, bucket_tier)
        return state

    def _build_selector_preview(body: dict[str, Any], request: Request) -> dict[str, Any]:
        model = str(body.get("model") or "").strip().lower()
        routing_profile = routing_profile_from_model(model)
        if routing_profile is None:
            return {
                "virtual": False,
                "requested_model": model,
                "served_model": model,
                "reasoning": "passthrough",
                "selector": _selector_state(),
            }

        prompt, system_prompt, max_tokens = _extract_prompt(body)
        session_id = _resolve_session(request, body, _sessions)
        cached_session = _sessions.get(session_id) if session_id else None
        step_type, tool_names = _classify_step(body)
        requirements = _extract_requirements(body, step_type)
        tier_cap = _tool_selection_tier_cap(prompt, step_type)
        is_lightweight = step_type == "tool-result-followup"
        user_keyed = _providers.keyed_models() or None
        decision = route(
            prompt,
            system_prompt,
            max_tokens,
            config=_routing_config,
            routing_profile=routing_profile,
            request_requirements=requirements,
            user_keyed_models=user_keyed,
            model_experience=_model_experience,
            tier_cap=tier_cap,
        )

        selected_model = decision.model
        served_tier = decision.tier.value
        decision_tier = decision.tier.value
        profile_value = decision.profile.value
        route_method = "cascade"
        reasoning = decision.reasoning
        if tier_cap is not None:
            reasoning = f"{reasoning} | tool-selection-cap<={tier_cap.value}"
        estimated_cost = decision.cost_estimate
        session_preview: dict[str, Any] = {
            "id": session_id,
            "applied": False,
            "action": "none",
        }

        active_tier_configs = {
            tier.value: {
                "primary": tc.primary,
                "fallback": tc.fallback,
                "hard_pin": tc.hard_pin,
                "selection_mode": "hard-pin" if tc.hard_pin else "adaptive",
            }
            for tier, tc in get_tier_configs(
                _routing_config,
                decision.profile,
                agentic=decision.profile is RoutingProfile.AGENTIC
                or (decision.profile is RoutingProfile.AUTO and requirements.is_agentic),
            ).items()
        }
        active_tier_config = get_tier_configs(
            _routing_config,
            decision.profile,
            agentic=decision.profile is RoutingProfile.AGENTIC
            or (decision.profile is RoutingProfile.AUTO and requirements.is_agentic),
        )[decision.tier]
        hard_pinned = active_tier_config.hard_pin

        if cached_session:
            session_preview["cached"] = {
                "model": cached_session.model,
                "tier": cached_session.tier,
                "profile": cached_session.profile,
                "requests": cached_session.request_count,
                "strikes": cached_session.strikes,
                "escalated": cached_session.escalated,
            }

        if cached_session and cached_session.profile == profile_value:
            session_rank = _TIER_RANK.get(cached_session.tier, 1)
            decision_rank = _TIER_RANK.get(decision.tier.value, 1)
            if hard_pinned:
                route_method = "hard-pin"
                reasoning = f"{decision.reasoning} | hard-pin"
                session_preview["applied"] = True
                session_preview["action"] = "hard-pin"
                session_preview["target"] = {"model": selected_model, "tier": served_tier}
            elif is_lightweight:
                route_method = "step-aware"
                reasoning = f"{decision.reasoning} | {step_type}"
                session_preview["action"] = "step-aware"
            elif decision_rank > session_rank:
                route_method = "session-upgrade"
                reasoning = f"{decision.reasoning} | upgrade {cached_session.tier}->{decision_tier}"
                session_preview["applied"] = True
                session_preview["action"] = "upgrade"
                session_preview["target"] = {"model": selected_model, "tier": served_tier}
            else:
                selected_model = cached_session.model
                served_tier = cached_session.tier
                route_method = "session-hold"
                reasoning = (
                    f"session-hold ({session_id[:8] if session_id else '?'}...)"
                    f" {served_tier}>={decision.tier.value}"
                )
                full_text = f"{system_prompt or ''} {prompt}".strip()
                estimated_cost = _estimate_cost(
                    selected_model,
                    estimate_tokens(full_text),
                    min(max_tokens, estimate_output_budget(prompt, served_tier)),
                )
                session_preview["applied"] = True
                session_preview["action"] = "hold"
                session_preview["target"] = {"model": selected_model, "tier": served_tier}

            if session_id and not is_lightweight and not hard_pinned:
                content_hash = hash_request_content(prompt, tool_names or None)
                previous_hash = cached_session.recent_hashes[-1] if cached_session.recent_hashes else ""
                next_strikes = (cached_session.strikes + 1) if previous_hash == content_hash else 0
                if next_strikes >= 2 and not cached_session.escalated:
                    escalation = _preview_session_escalation(cached_session, active_tier_configs)
                    if escalation is not None:
                        esc_model, esc_tier = escalation
                        selected_model = esc_model
                        served_tier = esc_tier
                        route_method = "escalated"
                        reasoning = f"escalated {cached_session.tier}->{esc_tier}"
                        estimated_cost = _estimate_cost(
                            selected_model,
                            estimate_tokens(f"{system_prompt or ''} {prompt}".strip()),
                            min(max_tokens, estimate_output_budget(prompt, served_tier)),
                        )
                        session_preview["applied"] = True
                        session_preview["action"] = "escalate"
                        session_preview["target"] = {"model": selected_model, "tier": served_tier}
                        session_preview["next_strikes"] = next_strikes
        elif cached_session and cached_session.profile != profile_value:
            session_preview["action"] = "profile-reset"

        return {
            "virtual": True,
            "requested_model": model,
            "requested_profile": routing_profile.value,
            "served_model": selected_model,
            "decision_model": decision.model,
            "served_tier": served_tier,
            "decision_tier": decision_tier,
            "profile": profile_value,
            "method": route_method,
            "reasoning": reasoning,
            "confidence": round(decision.confidence, 6),
            "estimated_cost": round(estimated_cost, 8),
            "savings": round(decision.savings, 6),
            "step_type": step_type,
            "requirements": {
                "needs_tool_calling": requirements.needs_tool_calling,
                "needs_vision": requirements.needs_vision,
                "prefers_reasoning": requirements.prefers_reasoning,
                "is_agentic": requirements.is_agentic,
            },
            "session": session_preview,
            "active_tier_configs": active_tier_configs,
            "fallback_chain": _serialize_fallback_chain(decision.fallback_chain),
            "candidate_scores": _serialize_candidate_scores(decision.candidate_scores),
            "selector": _selector_state(
                bucket_profile=decision.profile,
                bucket_tier=decision.tier,
            ),
        }

    async def handle_health(request: Request) -> JSONResponse:
        spend_status = _spend.status()
        return JSONResponse({
            "status": "ok",
            "router": "uncommon-route",
            "version": VERSION,
            "upstream": upstream,
            "sessions": _sessions.stats(),
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
            },
            "stats": {
                "total_requests": _stats.count,
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
            },
            "model_mapper": {
                "provider": _mapper.provider,
                "is_gateway": _mapper.is_gateway,
                "discovered": _mapper.discovered,
                "upstream_models": _mapper.upstream_model_count,
                "unresolved": _mapper.unresolved_models(),
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
            "mappings": _mapper.mapping_table(),
            "unresolved": _mapper.unresolved_models(),
        })

    _dashboard_mount = None
    try:
        import importlib.resources as _pkg
        _static_dir = str(_pkg.files("uncommon_route") / "static")
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

    async def handle_sessions(request: Request) -> JSONResponse:
        """GET /v1/sessions — list active sessions."""
        return JSONResponse(_sessions.stats())

    async def handle_stats(request: Request) -> JSONResponse:
        """GET /v1/stats — route analytics. POST /v1/stats — reset."""
        if request.method == "POST":
            body = await request.json()
            if body.get("action") == "reset":
                _stats.reset()
                return JSONResponse({"ok": True, "reset": True})
            return JSONResponse({"error": "Invalid action"}, status_code=400)
        s = _stats.summary()
        return JSONResponse({
            "total_requests": s.total_requests,
            "time_range_s": round(s.time_range_s, 1),
            "avg_confidence": round(s.avg_confidence, 3),
            "avg_savings": round(s.avg_savings, 3),
            "avg_latency_ms": round(s.avg_latency_us / 1000.0, 3),
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
            "by_profile": s.by_profile,
            "by_decision_tier": s.by_decision_tier,
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
            profile_param = request.query_params.get("profile")
            tier_param = request.query_params.get("tier")
            if (profile_param and not tier_param) or (tier_param and not profile_param):
                return JSONResponse(
                    {"error": "profile and tier must be provided together"},
                    status_code=400,
                )
            if profile_param and tier_param:
                try:
                    return JSONResponse(_selector_state(
                        bucket_profile=_parse_profile_value(profile_param),
                        bucket_tier=_parse_tier_value(tier_param),
                    ))
                except ValueError:
                    return JSONResponse(
                        {"error": "Invalid profile or tier"},
                        status_code=400,
                    )
            return JSONResponse(_selector_state())

        body = await request.json()
        normalized_body, error = _normalize_selector_body(body)
        if normalized_body is None:
            return JSONResponse({"error": error or "Invalid selector payload"}, status_code=400)
        return JSONResponse(_build_selector_preview(normalized_body, request))

    async def handle_routing_config(request: Request) -> JSONResponse:
        """GET /v1/routing-config — active routing profile/tier config. POST — update overrides."""
        nonlocal _routing_config
        if request.method == "GET":
            return JSONResponse(_routing_store.export())

        body = await request.json()
        action = str(body.get("action", "")).strip().lower()
        try:
            if action == "set-tier":
                profile = _parse_profile_value(str(body.get("profile", "")))
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
                    profile,
                    tier,
                    primary=primary,
                    fallback=fallback,
                    hard_pin=hard_pin,
                )
            elif action == "reset-tier":
                profile = _parse_profile_value(str(body.get("profile", "")))
                tier = _parse_tier_value(str(body.get("tier", "")))
                payload = _routing_store.reset_tier(profile, tier)
            elif action == "reset":
                payload = _routing_store.reset()
            else:
                return JSONResponse(
                    {"error": "Invalid action", "allowed": ["set-tier", "reset-tier", "reset"]},
                    status_code=400,
                )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _routing_config = _routing_store.config()
        return JSONResponse(payload)

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
            return JSONResponse(_feedback.status())
        body = await request.json()
        action = body.get("action")
        if action == "rollback":
            rolled = _feedback.rollback()
            return JSONResponse({"ok": True, "rolled_back": rolled})
        request_id = body.get("request_id", "")
        signal = body.get("signal", "")
        if not request_id or signal not in ("weak", "strong", "ok"):
            return JSONResponse(
                {"error": "Requires request_id and signal (weak|strong|ok)"},
                status_code=400,
            )
        result = _feedback.submit(request_id, signal)
        return JSONResponse({
            "ok": result.ok,
            "action": result.action,
            "from_tier": result.from_tier,
            "to_tier": result.to_tier,
            **({"reason": result.reason} if result.reason else {}),
            "total_updates": _feedback.total_updates,
        }, status_code=200 if result.ok else 404)

    async def handle_recent(request: Request) -> JSONResponse:
        """GET /v1/stats/recent — recent routed requests with feedback status."""
        limit = int(request.query_params.get("limit", "30"))
        records = _stats.recent(limit)
        for r in records:
            r["feedback_pending"] = _feedback.has_pending(r["request_id"])
        return JSONResponse(records)

    async def _handle_chat_core(
        body: dict,
        request: Request,
        *,
        api_format: str = "openai",
    ) -> Response:
        if not upstream:
            msg = _SETUP_GUIDE.strip()
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(503, msg), status_code=503)
            return JSONResponse(
                {"error": {"message": msg, "type": "configuration_error"}},
                status_code=503,
            )

        model = (body.get("model") or "").strip().lower()
        is_streaming = body.get("stream", False)

        requested_model = model
        routing_profile = routing_profile_from_model(model)
        is_virtual = routing_profile is not None
        route_start = time.perf_counter_ns()
        route_method: str = "cascade"
        confidence = 0.0
        savings = 0.0
        estimated_cost = 0.0
        baseline_cost = 0.0
        session_id: str | None = None
        request_id = ""
        prompt_preview = ""
        fallback_models: list[str] = []
        fallback_reason = ""
        step_type = "general"
        profile_value = routing_profile.value if routing_profile else ""
        decision_tier = ""
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
        prompt, system_prompt, max_tokens = _extract_prompt(body)
        _pv = " ".join(prompt[:80].split())
        prompt_preview = (_pv + "...") if len(prompt) > 80 else _pv
        session_id = _resolve_session(request, body, _sessions)
        cached_session = _sessions.get(session_id) if session_id and is_virtual else None
        step_type, tool_names = _classify_step(body)

        if is_virtual:
            if prompt.startswith("/debug"):
                debug_prompt = prompt[len("/debug"):].strip() or "hello"
                debug_body = _build_debug_response(debug_prompt, system_prompt, _routing_config)
                if api_format == "anthropic":
                    return JSONResponse(openai_to_anthropic_response(debug_body, "uncommon-route/debug"))
                return JSONResponse(debug_body)

            requirements = _extract_requirements(body, step_type)
            tier_cap = _tool_selection_tier_cap(prompt, step_type)
            is_lightweight = step_type == "tool-result-followup"

            # Always route — classifier decides tier based on current content
            user_keyed = _providers.keyed_models() or None
            decision = route(
                prompt,
                system_prompt,
                max_tokens,
                config=_routing_config,
                routing_profile=routing_profile or RoutingProfile.AUTO,
                request_requirements=requirements,
                user_keyed_models=user_keyed,
                model_experience=_model_experience,
                tier_cap=tier_cap,
            )
            selected_model = decision.model
            tier_value = decision.tier.value
            decision_tier = tier_value
            profile_value = decision.profile.value
            active_tier_configs = {
                tier.value: {
                    "primary": tc.primary,
                    "fallback": tc.fallback,
                    "hard_pin": tc.hard_pin,
                    "selection_mode": "hard-pin" if tc.hard_pin else "adaptive",
                }
                for tier, tc in get_tier_configs(
                    _routing_config,
                    decision.profile,
                    agentic=decision.profile is RoutingProfile.AGENTIC
                    or (decision.profile is RoutingProfile.AUTO and requirements.is_agentic),
                ).items()
            }
            active_tier_config = get_tier_configs(
                _routing_config,
                decision.profile,
                agentic=decision.profile is RoutingProfile.AGENTIC
                or (decision.profile is RoutingProfile.AUTO and requirements.is_agentic),
            )[decision.tier]
            hard_pinned = active_tier_config.hard_pin
            reasoning = decision.reasoning
            if tier_cap is not None:
                reasoning = f"{reasoning} | tool-selection-cap<={tier_cap.value}"
            estimated_cost = decision.cost_estimate
            baseline_cost = decision.baseline_cost
            confidence = decision.confidence
            savings = decision.savings
            route_method = "cascade"

            if cached_session and cached_session.profile == profile_value:
                session_rank = _TIER_RANK.get(cached_session.tier, 1)
                decision_rank = _TIER_RANK.get(decision.tier.value, 1)

                if hard_pinned:
                    route_method = "hard-pin"
                    reasoning = f"{decision.reasoning} | hard-pin"
                    if session_id:
                        _sessions.set(session_id, selected_model, tier_value, profile=profile_value)
                elif is_lightweight:
                    # Tool-result steps: use classifier's decision (allow downgrade)
                    route_method = "step-aware"
                    reasoning = f"{decision.reasoning} | {step_type}"
                elif decision_rank > session_rank:
                    # Higher tier needed: upgrade session
                    route_method = "session-upgrade"
                    reasoning = f"{decision.reasoning} | upgrade {cached_session.tier}->{tier_value}"
                    if session_id:
                        _sessions.set(session_id, selected_model, tier_value, profile=profile_value)
                else:
                    # Same or lower tier on non-lightweight step: hold session model
                    selected_model = cached_session.model
                    tier_value = cached_session.tier
                    route_method = "session-hold"
                    reasoning = (
                        f"session-hold ({session_id[:8] if session_id else '?'}...)"
                        f" {tier_value}>={decision.tier.value}"
                    )
                    full_text = f"{system_prompt or ''} {prompt}".strip()
                    input_toks = estimate_tokens(full_text)
                    output_budget = estimate_output_budget(prompt, tier_value)
                    estimated_cost = _estimate_cost(
                        selected_model, input_toks, min(max_tokens, output_budget),
                    )
                    baseline_cost = _estimate_baseline_cost(input_toks, min(max_tokens, output_budget))

                if session_id:
                    _sessions.touch(session_id)

                # Three-strike escalation (skip for lightweight steps — repeated
                # tool results are expected, not a signal of model inadequacy)
                if session_id and not is_lightweight and not hard_pinned:
                    content_hash = hash_request_content(prompt, tool_names or None)
                    should_escalate = _sessions.record_request_hash(session_id, content_hash)
                    if should_escalate:
                        esc = _sessions.escalate(session_id, active_tier_configs)
                        if esc:
                            original_tier = tier_value
                            selected_model, tier_value = esc
                            reasoning = f"escalated {original_tier}->{tier_value}"
                            route_method = "escalated"
                            esc_feats = extract_features(prompt, system_prompt)
                            _feedback.learn_from_escalation(
                                esc_feats, original_tier, tier_value,
                            )
                            full_text = f"{system_prompt or ''} {prompt}".strip()
                            input_toks = estimate_tokens(full_text)
                            output_budget = estimate_output_budget(prompt, tier_value)
                            estimated_cost = _estimate_cost(
                                selected_model, input_toks, min(max_tokens, output_budget),
                            )
                            baseline_cost = _estimate_baseline_cost(input_toks, min(max_tokens, output_budget))
            else:
                if session_id:
                    _sessions.set(session_id, selected_model, tier_value, profile=profile_value)
                if cached_session and cached_session.profile != profile_value:
                    reasoning = (
                        f"{decision.reasoning} | profile-reset"
                        f" {cached_session.profile}->{profile_value}"
                    )

            body["model"] = selected_model
            composition = await compose_messages_semantic(
                body.get("messages", []),
                _artifacts,
                _composition_policy,
                semantic_compressor=_semantic,
                session_id=session_id,
                request=request,
                step_type=step_type,
                is_agentic=requirements.is_agentic,
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
            estimated_cost = _estimate_cost(
                selected_model,
                input_tokens_after,
                min(max_tokens, output_budget),
            )
            baseline_cost = _estimate_baseline_cost(
                input_tokens_before if input_tokens_before > 0 else input_tokens_after,
                min(max_tokens, output_budget),
            )
            main_estimated_cost = estimated_cost
            estimated_cost += sidechannel_estimated_cost

            check = _spend.check(estimated_cost)
            if not check.allowed:
                return _spend_error(check, api_format=api_format)

            request_id = uuid.uuid4().hex[:12]
            route_feats = extract_features(prompt, system_prompt)
            _feedback.capture(
                request_id,
                route_feats,
                tier_value,
                model=selected_model,
                profile=profile_value,
            )
            fallback_models = [
                fb.model for fb in decision.fallback_chain
                if fb.model != selected_model
            ]
        else:
            selected_model = model
            tier_value = ""
            profile_value = "passthrough"
            reasoning = "passthrough"
            route_method = "passthrough"
            full_text = f"{system_prompt or ''} {prompt}".strip()
            input_tokens_before = estimate_tokens(full_text) if full_text else 0
            input_tokens_after = input_tokens_before
            estimated_cost = _estimate_cost(selected_model, input_tokens_after, max_tokens)
            baseline_cost = estimated_cost
            main_estimated_cost = estimated_cost

        route_latency_us = (time.perf_counter_ns() - route_start) / 1000

        # BYOK: if user has a key for this model, route to their provider directly
        provider_entry = _providers.get_for_model(selected_model)
        upstream_body = json.loads(json.dumps(body))

        fwd_headers: dict[str, str] = {}
        for key in ("authorization", "content-type", "accept", "user-agent"):
            val = request.headers.get(key)
            if val:
                fwd_headers[key] = val
        if api_format == "anthropic" and "authorization" not in fwd_headers:
            x_api_key = request.headers.get("x-api-key")
            if x_api_key:
                fwd_headers["authorization"] = f"Bearer {x_api_key}"
        if "content-type" not in fwd_headers:
            fwd_headers["content-type"] = "application/json"
        fwd_headers["user-agent"] = f"uncommon-route/{VERSION}"

        # Resolve model name for the target upstream
        if not provider_entry:
            upstream_body["model"] = _mapper.resolve(selected_model)

        native_anthropic_transport = _supports_native_anthropic_transport(
            selected_model=selected_model,
            provider_entry=provider_entry,
            upstream_provider=_mapper.provider,
            upstream_base=upstream,
        )
        if native_anthropic_transport:
            target_chat_url = _anthropic_messages_url(
                provider_entry.base_url if provider_entry and provider_entry.base_url else upstream,
            )
            transport_body = openai_to_anthropic_request(upstream_body)
            cache_plan = apply_anthropic_cache_breakpoints(
                transport_body,
                session_id=session_id,
                step_type=step_type,
            )
        else:
            if provider_entry and provider_entry.base_url:
                target_chat_url = f"{provider_entry.base_url.rstrip('/')}/chat/completions"
            else:
                target_chat_url = upstream_chat
            transport_body = json.loads(json.dumps(upstream_body))
            cache_plan = _apply_provider_cache_plan(
                transport_body,
                selected_model=selected_model,
                provider_entry=provider_entry,
                session_id=session_id,
                step_type=step_type,
                upstream_provider=_mapper.provider,
            )

        def _current_route_strategy() -> tuple[str, str, str, int]:
            return (
                _transport_name(native_anthropic_transport),
                _cache_mode_name(cache_plan),
                _cache_family_name(cache_plan),
                cache_plan.cache_breakpoints,
            )

        # Auth: BYOK key > env key > request header
        env_key = (
            os.environ.get("UNCOMMON_ROUTE_API_KEY", "")
            or os.environ.get("COMMONSTACK_API_KEY", "")
        )
        if provider_entry:
            if native_anthropic_transport:
                fwd_headers.pop("authorization", None)
                fwd_headers["x-api-key"] = provider_entry.api_key
            else:
                fwd_headers["authorization"] = f"Bearer {provider_entry.api_key}"
        elif env_key:
            if native_anthropic_transport:
                fwd_headers.pop("authorization", None)
                fwd_headers["x-api-key"] = env_key
            else:
                fwd_headers["authorization"] = f"Bearer {env_key}"
        if native_anthropic_transport:
            if "x-api-key" not in fwd_headers and "authorization" in fwd_headers:
                bearer = fwd_headers["authorization"]
                if bearer.lower().startswith("bearer "):
                    fwd_headers["x-api-key"] = bearer[7:].strip()
            if "x-api-key" in fwd_headers:
                fwd_headers.pop("authorization", None)
            fwd_headers.setdefault("anthropic-version", request.headers.get("anthropic-version", "2023-06-01"))
            anthropic_beta = request.headers.get("anthropic-beta")
            if anthropic_beta:
                fwd_headers["anthropic-beta"] = anthropic_beta

        debug_headers: dict[str, str] = {}
        if is_virtual:
            _set_header(debug_headers, "x-uncommon-route-profile", profile_value)
            _set_header(debug_headers, "x-uncommon-route-request-id", request_id)
            _set_header(debug_headers, "x-uncommon-route-model", selected_model)
            _set_header(debug_headers, "x-uncommon-route-tier", tier_value)
            _set_header(debug_headers, "x-uncommon-route-decision-tier", decision_tier or tier_value)
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
                native_anthropic_transport=native_anthropic_transport,
                cache_plan=cache_plan,
            )
            _set_header(debug_headers, "x-uncommon-route-reasoning", reasoning)
            stream_tag = " stream" if is_streaming else ""
            session_tag = f"  session:{session_id[:8]}" if session_id else ""
            fmt_tag = f"  [{api_format}]" if api_format != "openai" else ""
            transport_name, cache_mode_name, _cache_family, _cache_breakpoints = _current_route_strategy()
            print(
                f"[route] {profile_value}:{tier_value} → {selected_model}"
                f"  ${estimated_cost:.4f}  (in {input_tokens_before}->{input_tokens_after}"
                f"  transport:{transport_name}"
                f"  cache:{cache_mode_name}"
                f"  sem:{semantic_calls}"
                f"  {route_latency_us:.0f}µs"
                f"  {route_method}{stream_tag}{session_tag}{fmt_tag})"
            )

        try:
            if is_streaming:
                def _record_stream_success(stream_usage: UsageMetrics | None) -> None:
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

                    if is_virtual:
                        _model_experience.observe(
                            selected_model,
                            profile_value,
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
                                profile=profile_value,
                            )
                        combined_cost = (
                            (stream_actual_cost if stream_actual_cost is not None else main_estimated_cost)
                            + (sidechannel_actual_cost if sidechannel_actual_cost is not None else sidechannel_estimated_cost)
                        )
                        _spend.record(
                            combined_cost,
                            model=selected_model,
                            action="chat",
                        )
                        _stats.record(RouteRecord(
                            timestamp=time.time(),
                            requested_model=requested_model,
                            profile=profile_value,
                            model=selected_model,
                            tier=tier_value,
                            decision_tier=decision_tier or tier_value,
                            confidence=confidence, method=route_method,  # type: ignore[arg-type]
                            estimated_cost=estimated_cost, baseline_cost=baseline_cost, actual_cost=stream_actual_cost,
                            savings=savings, latency_us=route_latency_us,
                            usage_input_tokens=stream_usage.input_tokens_total if stream_usage else 0,
                            usage_output_tokens=stream_usage.output_tokens if stream_usage else 0,
                            cache_read_input_tokens=stream_usage.cache_read_input_tokens if stream_usage else 0,
                            cache_write_input_tokens=stream_usage.cache_write_input_tokens if stream_usage else 0,
                            cache_hit_ratio=stream_usage.cache_hit_ratio if stream_usage else 0.0,
                            transport=_transport_name(native_anthropic_transport),
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
                            step_type=step_type, fallback_reason=fallback_reason,
                            streaming=True,
                            request_id=request_id, prompt_preview=prompt_preview,
                        ))
                    else:
                        _stats.record(RouteRecord(
                            timestamp=time.time(),
                            requested_model=requested_model,
                            profile=profile_value,
                            model=selected_model,
                            tier=tier_value,
                            decision_tier=decision_tier or tier_value,
                            confidence=1.0,
                            method="passthrough",  # type: ignore[arg-type]
                            estimated_cost=estimated_cost,
                            baseline_cost=baseline_cost,
                            actual_cost=stream_actual_cost,
                            savings=0.0,
                            latency_us=route_latency_us,
                            usage_input_tokens=stream_usage.input_tokens_total if stream_usage else 0,
                            usage_output_tokens=stream_usage.output_tokens if stream_usage else 0,
                            cache_read_input_tokens=stream_usage.cache_read_input_tokens if stream_usage else 0,
                            cache_write_input_tokens=stream_usage.cache_write_input_tokens if stream_usage else 0,
                            cache_hit_ratio=stream_usage.cache_hit_ratio if stream_usage else 0.0,
                            transport=_transport_name(native_anthropic_transport),
                            cache_mode=_cache_mode_name(cache_plan),
                            cache_family=_cache_family_name(cache_plan),
                            cache_breakpoints=cache_plan.cache_breakpoints,
                            input_tokens_before=input_tokens_before,
                            input_tokens_after=input_tokens_after,
                            session_id=session_id,
                            streaming=True,
                            step_type=step_type,
                            request_id=request_id,
                            prompt_preview=prompt_preview,
                        ))

                def _record_stream_failure() -> None:
                    if is_virtual:
                        _model_experience.observe(
                            selected_model,
                            profile_value,
                            tier_value,
                            success=False,
                        )
                        _stats.record(RouteRecord(
                            timestamp=time.time(),
                            requested_model=requested_model,
                            profile=profile_value,
                            model=selected_model,
                            tier=tier_value,
                            decision_tier=decision_tier or tier_value,
                            confidence=confidence, method=route_method,  # type: ignore[arg-type]
                            estimated_cost=estimated_cost, baseline_cost=baseline_cost, savings=savings,
                            latency_us=route_latency_us,
                            transport=_transport_name(native_anthropic_transport),
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
                            step_type=step_type, fallback_reason=fallback_reason,
                            streaming=True,
                            request_id=request_id, prompt_preview=prompt_preview,
                        ))
                    else:
                        _stats.record(RouteRecord(
                            timestamp=time.time(),
                            requested_model=requested_model,
                            profile=profile_value,
                            model=selected_model,
                            tier=tier_value,
                            decision_tier=decision_tier or tier_value,
                            confidence=1.0,
                            method="passthrough",  # type: ignore[arg-type]
                            estimated_cost=estimated_cost,
                            baseline_cost=baseline_cost,
                            savings=0.0,
                            latency_us=route_latency_us,
                            input_tokens_before=input_tokens_before,
                            input_tokens_after=input_tokens_after,
                            session_id=session_id,
                            streaming=True,
                            step_type=step_type,
                            request_id=request_id,
                            prompt_preview=prompt_preview,
                        ))

                if native_anthropic_transport:
                    async def anthropic_native_sse() -> AsyncGenerator[bytes, None]:
                        stream_chunks: list[bytes] = []
                        converter = None if api_format == "anthropic" else AnthropicToOpenAIStreamConverter(model=selected_model)
                        try:
                            async for chunk in _stream_upstream(target_chat_url, transport_body, fwd_headers):
                                stream_chunks.append(chunk)
                                if converter is None:
                                    yield chunk
                                else:
                                    for ev in converter.feed(chunk):
                                        yield ev
                            if converter is not None:
                                for ev in converter.finish():
                                    yield ev
                            _record_stream_success(
                                parse_stream_usage_metrics(stream_chunks, selected_model, DEFAULT_MODEL_PRICING)
                            )
                        except Exception:
                            _record_stream_failure()
                            raise

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
                    converter = OpenAIToAnthropicStreamConverter(model=selected_model)

                    async def anthropic_sse() -> AsyncGenerator[bytes, None]:
                        stream_chunks: list[bytes] = []
                        try:
                            async for chunk in _stream_upstream(target_chat_url, transport_body, fwd_headers):
                                stream_chunks.append(chunk)
                                for ev in converter.feed(chunk):
                                    yield ev
                            for ev in converter.finish():
                                yield ev
                            _record_stream_success(
                                parse_stream_usage_metrics(stream_chunks, selected_model, DEFAULT_MODEL_PRICING)
                            )
                        except Exception:
                            _record_stream_failure()
                            raise

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
                        async for chunk in _stream_upstream(target_chat_url, transport_body, fwd_headers):
                            stream_chunks.append(chunk)
                            yield chunk
                        _record_stream_success(
                            parse_stream_usage_metrics(stream_chunks, selected_model, DEFAULT_MODEL_PRICING)
                        )
                    except Exception:
                        _record_stream_failure()
                        raise

                return StreamingResponse(
                    sse_passthrough(),
                    media_type="text/event-stream",
                    headers={
                        "cache-control": "no-cache",
                        "connection": "keep-alive",
                        **debug_headers,
                    },
                )

            client = _get_client()
            resp = await client.post(target_chat_url, json=transport_body, headers=fwd_headers)

            # Fallback: if upstream rejects the model, try alternatives
            if (
                is_virtual
                and resp.status_code in (400, 404, 422)
                and fallback_models
                and not provider_entry
                and _is_model_error(resp.content)
            ):
                original_model = transport_body["model"]
                for fb_model in fallback_models:
                    fb_openai_body = json.loads(json.dumps(upstream_body))
                    fb_resolved = _mapper.resolve(fb_model)
                    fb_openai_body["model"] = fb_resolved
                    fb_native_anthropic = _supports_native_anthropic_transport(
                        selected_model=fb_model,
                        provider_entry=None,
                        upstream_provider=_mapper.provider,
                        upstream_base=upstream,
                    )
                    if fb_native_anthropic:
                        fb_target_chat_url = _anthropic_messages_url(upstream)
                        fb_transport_body = openai_to_anthropic_request(fb_openai_body)
                        fb_cache_plan = apply_anthropic_cache_breakpoints(
                            fb_transport_body,
                            session_id=session_id,
                            step_type=step_type,
                        )
                    else:
                        fb_target_chat_url = upstream_chat
                        fb_transport_body = fb_openai_body
                        fb_cache_plan = _apply_provider_cache_plan(
                            fb_transport_body,
                            selected_model=fb_model,
                            provider_entry=None,
                            session_id=session_id,
                            step_type=step_type,
                            upstream_provider=_mapper.provider,
                        )
                    fb_headers = dict(fwd_headers)
                    if fb_native_anthropic:
                        if env_key:
                            fb_headers.pop("authorization", None)
                            fb_headers["x-api-key"] = env_key
                        elif "x-api-key" not in fb_headers and "authorization" in fb_headers:
                            bearer = fb_headers["authorization"]
                            if bearer.lower().startswith("bearer "):
                                fb_headers["x-api-key"] = bearer[7:].strip()
                        if "x-api-key" in fb_headers:
                            fb_headers.pop("authorization", None)
                        fb_headers.setdefault("anthropic-version", request.headers.get("anthropic-version", "2023-06-01"))
                        anthropic_beta = request.headers.get("anthropic-beta")
                        if anthropic_beta:
                            fb_headers["anthropic-beta"] = anthropic_beta
                    retry = await client.post(fb_target_chat_url, json=fb_transport_body, headers=fb_headers)
                    if retry.status_code < 400:
                        selected_model = fb_model
                        resp = retry
                        target_chat_url = fb_target_chat_url
                        transport_body = fb_transport_body
                        fwd_headers = fb_headers
                        native_anthropic_transport = fb_native_anthropic
                        route_method = "fallback"
                        fallback_reason = f"{original_model} unavailable -> {fb_resolved}"
                        reasoning = f"fallback: {fallback_reason}"
                        cache_plan = fb_cache_plan
                        _set_header(debug_headers, "x-uncommon-route-model", selected_model)
                        _set_route_strategy_headers(
                            debug_headers,
                            native_anthropic_transport=native_anthropic_transport,
                            cache_plan=cache_plan,
                        )
                        _set_header(debug_headers, "x-uncommon-route-reasoning", reasoning)
                        if request_id:
                            _feedback.rebind_request(
                                request_id,
                                model=selected_model,
                                tier=tier_value,
                                profile=profile_value,
                            )
                        print(f"[route] fallback → {fb_resolved}  ({original_model} unavailable)")
                        break

            actual_cost: float | None = None
            ttft_ms: float | None = None
            tps: float | None = None
            usage_metrics: UsageMetrics | None = None
            if resp.status_code == 200:
                usage_metrics = parse_usage_metrics(resp.content, selected_model, DEFAULT_MODEL_PRICING)
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

            if is_virtual:
                if resp.status_code == 200:
                    _model_experience.observe(
                        selected_model,
                        profile_value,
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
                    if request_id:
                        _feedback.rebind_request(
                            request_id,
                            model=selected_model,
                            tier=tier_value,
                            profile=profile_value,
                        )
                else:
                    _model_experience.observe(
                        selected_model,
                        profile_value,
                        tier_value,
                        success=False,
                    )
                combined_cost = (
                    (actual_cost if actual_cost is not None else main_estimated_cost)
                    + (sidechannel_actual_cost if sidechannel_actual_cost is not None else sidechannel_estimated_cost)
                )
                _spend.record(
                    combined_cost,
                    model=selected_model,
                    action="chat",
                )
                _stats.record(RouteRecord(
                    timestamp=time.time(),
                    requested_model=requested_model,
                    profile=profile_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, baseline_cost=baseline_cost, actual_cost=actual_cost,
                    savings=savings, latency_us=route_latency_us,
                    usage_input_tokens=usage_metrics.input_tokens_total if usage_metrics else 0,
                    usage_output_tokens=usage_metrics.output_tokens if usage_metrics else 0,
                    cache_read_input_tokens=usage_metrics.cache_read_input_tokens if usage_metrics else 0,
                    cache_write_input_tokens=usage_metrics.cache_write_input_tokens if usage_metrics else 0,
                    cache_hit_ratio=usage_metrics.cache_hit_ratio if usage_metrics else 0.0,
                    transport=_transport_name(native_anthropic_transport),
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
                    session_id=session_id, streaming=False,
                    step_type=step_type, fallback_reason=fallback_reason,
                    request_id=request_id, prompt_preview=prompt_preview,
                ))
            else:
                _stats.record(RouteRecord(
                    timestamp=time.time(),
                    requested_model=requested_model,
                    profile=profile_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    confidence=1.0,
                    method="passthrough",  # type: ignore[arg-type]
                    estimated_cost=estimated_cost,
                    baseline_cost=baseline_cost,
                    actual_cost=actual_cost,
                    savings=0.0,
                    latency_us=route_latency_us,
                    usage_input_tokens=usage_metrics.input_tokens_total if usage_metrics else 0,
                    usage_output_tokens=usage_metrics.output_tokens if usage_metrics else 0,
                    cache_read_input_tokens=usage_metrics.cache_read_input_tokens if usage_metrics else 0,
                    cache_write_input_tokens=usage_metrics.cache_write_input_tokens if usage_metrics else 0,
                    cache_hit_ratio=usage_metrics.cache_hit_ratio if usage_metrics else 0.0,
                    transport=_transport_name(native_anthropic_transport),
                    cache_mode=_cache_mode_name(cache_plan),
                    cache_family=_cache_family_name(cache_plan),
                    cache_breakpoints=cache_plan.cache_breakpoints,
                    input_tokens_before=input_tokens_before,
                    input_tokens_after=input_tokens_after,
                    session_id=session_id,
                    streaming=False,
                    step_type=step_type,
                    request_id=request_id,
                    prompt_preview=prompt_preview,
                ))

            if api_format == "anthropic":
                if native_anthropic_transport:
                    return Response(
                        content=resp.content,
                        status_code=resp.status_code,
                        headers={
                            "content-type": resp.headers.get("content-type", "application/json"),
                            **debug_headers,
                        },
                    )
                if resp.status_code == 200:
                    try:
                        oai_data = json.loads(resp.content)
                        anth_data = openai_to_anthropic_response(oai_data, selected_model)
                        return JSONResponse(anth_data, headers=debug_headers)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
                try:
                    err_body = json.loads(resp.content)
                    err_msg = err_body.get("error", {}).get("message", "Upstream error")
                except (json.JSONDecodeError, TypeError):
                    err_msg = "Upstream error"
                return JSONResponse(
                    anthropic_error_response(resp.status_code, err_msg),
                    status_code=resp.status_code,
                    headers=debug_headers,
                )

            if native_anthropic_transport:
                if resp.status_code == 200:
                    try:
                        anth_data = json.loads(resp.content)
                        oai_data = anthropic_to_openai_response(anth_data, selected_model)
                        return JSONResponse(oai_data, headers=debug_headers)
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        pass
                try:
                    err_body = json.loads(resp.content)
                    err_msg = err_body.get("error", {}).get("message", "Upstream error")
                except (json.JSONDecodeError, TypeError):
                    err_msg = "Upstream error"
                return JSONResponse(
                    {"error": {"message": err_msg, "type": "proxy_error"}},
                    status_code=resp.status_code,
                    headers=debug_headers,
                )

            resp_headers = {
                "content-type": resp.headers.get("content-type", "application/json"),
                **debug_headers,
            }
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
            )
        except httpx.ConnectError:
            if is_virtual:
                _model_experience.observe(
                    selected_model,
                    profile_value,
                    tier_value,
                    success=False,
                )
                _stats.record(RouteRecord(
                    timestamp=time.time(),
                    requested_model=requested_model,
                    profile=profile_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, baseline_cost=baseline_cost, savings=savings,
                    latency_us=route_latency_us,
                    transport=_transport_name(native_anthropic_transport),
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
                    step_type=step_type, fallback_reason=fallback_reason,
                    streaming=is_streaming,
                    request_id=request_id, prompt_preview=prompt_preview,
                ))
            msg = f"Upstream unreachable: {upstream_chat}"
            if api_format == "anthropic":
                return JSONResponse(anthropic_error_response(502, msg), status_code=502, headers=debug_headers)
            return JSONResponse(
                {"error": {"message": msg, "type": "proxy_error"}},
                status_code=502,
                headers=debug_headers,
            )
        except httpx.TimeoutException:
            if is_virtual:
                _model_experience.observe(
                    selected_model,
                    profile_value,
                    tier_value,
                    success=False,
                )
                _stats.record(RouteRecord(
                    timestamp=time.time(),
                    requested_model=requested_model,
                    profile=profile_value,
                    model=selected_model,
                    tier=tier_value,
                    decision_tier=decision_tier or tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, baseline_cost=baseline_cost, savings=savings,
                    latency_us=route_latency_us,
                    transport=_transport_name(native_anthropic_transport),
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
                    step_type=step_type, fallback_reason=fallback_reason,
                    streaming=is_streaming,
                    request_id=request_id, prompt_preview=prompt_preview,
                ))
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
        return await _handle_chat_core(body, request)

    async def handle_messages(request: Request) -> Response:
        raw = await request.json()
        preview_body = anthropic_to_openai_request(raw)
        body = preview_body
        requested_model = str(raw.get("model") or "").strip()
        if requested_model and (routing_profile_from_model(requested_model) is not None or "/" in requested_model):
            body["model"] = requested_model
        else:
            body["model"] = VIRTUAL_MODEL
        return await _handle_chat_core(body, request, api_format="anthropic")

    @asynccontextmanager
    async def _lifespan(app: Starlette) -> _LifespanGen[None, None]:
        await _on_startup()
        yield

    routes = [
        Route("/health", handle_health, methods=["GET"]),
        Route("/v1/models", handle_models, methods=["GET"]),
        Route("/v1/models/mapping", handle_models_mapping, methods=["GET"]),
        Route("/v1/chat/completions", handle_chat_completions, methods=["POST"]),
        Route("/v1/messages", handle_messages, methods=["POST"]),
        Route("/v1/spend", handle_spend, methods=["GET", "POST"]),
        Route("/v1/sessions", handle_sessions, methods=["GET"]),
        Route("/v1/stats", handle_stats, methods=["GET", "POST"]),
        Route("/v1/selector", handle_selector, methods=["GET", "POST"]),
        Route("/v1/routing-config", handle_routing_config, methods=["GET", "POST"]),
        Route("/v1/artifacts", handle_artifacts, methods=["GET"]),
        Route("/v1/artifacts/{artifact_id:str}", handle_artifact, methods=["GET"]),
        Route("/v1/feedback", handle_feedback, methods=["GET", "POST"]),
        Route("/v1/stats/recent", handle_recent, methods=["GET"]),
    ]
    if _dashboard_mount is not None:
        routes.append(Mount("/dashboard", app=_dashboard_mount))

    return Starlette(routes=routes, lifespan=_lifespan)


def serve(
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    upstream: str = DEFAULT_UPSTREAM,
    session_store: SessionStore | None = None,
    spend_control: SpendControl | None = None,
    route_stats: RouteStats | None = None,
) -> None:
    """Start the proxy server (blocking)."""
    import uvicorn

    app = create_app(
        upstream=upstream,
        session_store=session_store,
        spend_control=spend_control,
        route_stats=route_stats,
    )
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
    if upstream:
        short = upstream.replace("https://", "").replace("http://", "").rstrip("/v1").rstrip("/")
        print(f"  Upstream:    {short}")
        print(f"  Proxy:       {base}")
        if has_dashboard:
            print(f"  Dashboard:   {base}/dashboard/")
        print()
        print(f"  Quick test:")
        print(f"    curl {base}/health")
    else:
        print(f"  Upstream:    (not configured)")
        print()
        print(f"  Get started:")
        print(f'    export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"')
        print(f'    export UNCOMMON_ROUTE_API_KEY="your-key"')
        print(f"    uncommon-route serve")
    print(f"  {bar}")
    print(flush=True)

    uvicorn.run(app, host=host, port=port, log_level="info")
