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
import time
import uuid
from typing import Any, AsyncGenerator

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify, extract_features
from uncommon_route.router.config import DEFAULT_CONFIG, DEFAULT_MODEL_PRICING
from uncommon_route.router.structural import estimate_tokens, estimate_output_budget
from uncommon_route.router.types import Tier
from uncommon_route.session import (
    SessionStore,
    derive_session_id,
    get_session_id,
    hash_request_content,
)
from uncommon_route.spend_control import SpendControl
from uncommon_route.stats import RouteRecord, RouteStats
from uncommon_route.feedback import FeedbackCollector
from uncommon_route.providers import ProvidersConfig, load_providers

VERSION = "0.1.2"
DEFAULT_UPSTREAM = os.environ.get("UNCOMMON_ROUTE_UPSTREAM", "")
DEFAULT_PORT = int(os.environ.get("UNCOMMON_ROUTE_PORT", "8403"))
VIRTUAL_MODEL = "uncommon-route/auto"

_SETUP_GUIDE = """\
No upstream API configured. UncommonRoute is a routing layer — it needs an upstream LLM API to forward requests to.

Set one of the following:

  # Option 1: Any OpenAI-compatible API
  export UNCOMMON_ROUTE_UPSTREAM="https://api.openai.com/v1"
  export UNCOMMON_ROUTE_API_KEY="sk-..."

  # Option 2: OpenRouter (100+ models, single key)
  export UNCOMMON_ROUTE_UPSTREAM="https://openrouter.ai/api/v1"
  export UNCOMMON_ROUTE_API_KEY="sk-or-..."

  # Option 3: Commonstack (multi-provider gateway)
  export UNCOMMON_ROUTE_UPSTREAM="https://api.commonstack.ai/v1"
  export UNCOMMON_ROUTE_API_KEY="csk-..."

  # Option 4: Local (Ollama, vLLM, etc.)
  export UNCOMMON_ROUTE_UPSTREAM="http://127.0.0.1:11434/v1"

Then restart:  uncommon-route serve
"""

VIRTUAL_MODELS = [
    {"id": VIRTUAL_MODEL, "object": "model", "owned_by": "uncommon-route"},
]

_http_client: httpx.AsyncClient | None = None


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute dollar cost from token counts using the model pricing table."""
    mp = DEFAULT_MODEL_PRICING.get(model)
    if mp is None:
        return 0.0
    return (input_tokens / 1_000_000) * mp.input_price + (output_tokens / 1_000_000) * mp.output_price


def _parse_usage_cost(content: bytes, model: str) -> float | None:
    """Extract actual cost from an upstream response's ``usage`` field.

    Returns None when the response is unparseable or lacks token counts.
    """
    try:
        data = json.loads(content)
        usage = data.get("usage")
        if not usage:
            return None
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        if prompt_tokens == 0 and completion_tokens == 0:
            return None
        return _estimate_cost(model, prompt_tokens, completion_tokens)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
    return _http_client


def _extract_prompt(body: dict) -> tuple[str, str | None, int]:
    """Extract last user message, system prompt, and max_tokens from request body."""
    messages = body.get("messages", [])
    max_tokens: int = body.get("max_tokens", 4096)

    prompt = ""
    system_prompt: str | None = None

    for msg in reversed(messages):
        if msg.get("role") == "user" and not prompt:
            content = msg.get("content", "")
            prompt = content if isinstance(content, str) else str(content)
        if msg.get("role") == "system" and system_prompt is None:
            content = msg.get("content", "")
            system_prompt = content if isinstance(content, str) else str(content)

    return prompt, system_prompt, max_tokens


def _build_debug_response(prompt: str, system_prompt: str | None) -> dict:
    """Build a debug diagnostics response showing routing details."""
    result = classify(prompt, system_prompt)
    decision = route(prompt, system_prompt)

    tier_boundaries = DEFAULT_CONFIG.scoring.tier_boundaries
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
        lines.append("Fallback Chain (cost-sorted):")
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


def _spend_error(result: Any) -> JSONResponse:
    """Build a 429 error response for spend control violations."""
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


def create_app(
    upstream: str = DEFAULT_UPSTREAM,
    session_store: SessionStore | None = None,
    spend_control: SpendControl | None = None,
    providers_config: ProvidersConfig | None = None,
    route_stats: RouteStats | None = None,
    feedback: FeedbackCollector | None = None,
) -> Starlette:
    """Create the ASGI application wired to the given upstream base URL.

    Args:
        upstream: Base URL for the upstream OpenAI-compatible API.
        session_store: Optional SessionStore for sticky sessions.
        spend_control: Optional SpendControl for spending limits.
        providers_config: Optional BYOK provider config for user-keyed models.
        route_stats: Optional RouteStats for per-request analytics.
        feedback: Optional FeedbackCollector for online learning.
    """
    _sessions = session_store or SessionStore()
    _spend = spend_control or SpendControl()
    _providers = providers_config or load_providers()
    _stats = route_stats or RouteStats()
    _feedback = feedback or FeedbackCollector()

    upstream_chat = f"{upstream.rstrip('/')}/chat/completions"

    tier_configs_dict = {
        tier.value: {"primary": tc.primary, "fallback": tc.fallback}
        for tier, tc in DEFAULT_CONFIG.tiers.items()
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
            "stats": {
                "total_requests": _stats.count,
            },
            "feedback": {
                "pending": _feedback.pending_count,
                "total_updates": _feedback.total_updates,
                "online_model": _feedback.online_model_active,
            },
        })

    async def handle_models(request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": VIRTUAL_MODELS})

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
            "avg_latency_us": round(s.avg_latency_us, 1),
            "total_estimated_cost": round(s.total_estimated_cost, 6),
            "total_actual_cost": round(s.total_actual_cost, 6),
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
            "by_method": s.by_method,
        })

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

    async def handle_chat_completions(request: Request) -> Response:
        if not upstream:
            return JSONResponse(
                {"error": {"message": _SETUP_GUIDE.strip(), "type": "configuration_error"}},
                status_code=503,
            )

        body = await request.json()
        model = (body.get("model") or "").strip().lower()
        is_streaming = body.get("stream", False)

        is_virtual = model == VIRTUAL_MODEL
        route_start = time.perf_counter_ns()
        route_method: str = "cascade"
        confidence = 0.0
        savings = 0.0
        estimated_cost = 0.0
        session_id: str | None = None
        request_id = ""

        if is_virtual:
            prompt, system_prompt, max_tokens = _extract_prompt(body)

            if prompt.startswith("/debug"):
                debug_prompt = prompt[len("/debug"):].strip() or "hello"
                debug_body = _build_debug_response(debug_prompt, system_prompt)
                return JSONResponse(debug_body)

            session_id = _resolve_session(request, body, _sessions)
            cached_session = _sessions.get(session_id) if session_id else None
            step_type, tool_names = _classify_step(body)
            is_lightweight = step_type == "tool-result-followup"

            # Always route — classifier decides tier based on current content
            user_keyed = _providers.keyed_models() or None
            decision = route(prompt, system_prompt, max_tokens, user_keyed_models=user_keyed)
            selected_model = decision.model
            tier_value = decision.tier.value
            reasoning = decision.reasoning
            estimated_cost = decision.cost_estimate
            confidence = decision.confidence
            savings = decision.savings
            route_method = "cascade"

            if cached_session:
                session_rank = _TIER_RANK.get(cached_session.tier, 1)
                decision_rank = _TIER_RANK.get(decision.tier.value, 1)

                if is_lightweight:
                    # Tool-result steps: use classifier's decision (allow downgrade)
                    route_method = "step-aware"
                    reasoning = f"{decision.reasoning} | {step_type}"
                elif decision_rank > session_rank:
                    # Higher tier needed: upgrade session
                    route_method = "session-upgrade"
                    reasoning = f"{decision.reasoning} | upgrade {cached_session.tier}->{tier_value}"
                    if session_id:
                        _sessions.set(session_id, selected_model, tier_value)
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

                if session_id:
                    _sessions.touch(session_id)

                # Three-strike escalation (skip for lightweight steps — repeated
                # tool results are expected, not a signal of model inadequacy)
                if session_id and not is_lightweight:
                    content_hash = hash_request_content(prompt, tool_names or None)
                    should_escalate = _sessions.record_request_hash(session_id, content_hash)
                    if should_escalate:
                        esc = _sessions.escalate(session_id, tier_configs_dict)
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
            else:
                if session_id:
                    _sessions.set(session_id, selected_model, tier_value)

            check = _spend.check(estimated_cost)
            if not check.allowed:
                return _spend_error(check)

            request_id = uuid.uuid4().hex[:12]
            route_feats = extract_features(prompt, system_prompt)
            _feedback.capture(request_id, route_feats, tier_value)

            body["model"] = selected_model
        else:
            selected_model = model
            tier_value = ""
            reasoning = "passthrough"

        route_latency_us = (time.perf_counter_ns() - route_start) / 1000

        # BYOK: if user has a key for this model, route to their provider directly
        provider_entry = _providers.get_for_model(selected_model)
        if provider_entry and provider_entry.base_url:
            target_chat_url = f"{provider_entry.base_url.rstrip('/')}/chat/completions"
        else:
            target_chat_url = upstream_chat

        fwd_headers: dict[str, str] = {}
        for key in ("authorization", "content-type", "accept", "user-agent"):
            val = request.headers.get(key)
            if val:
                fwd_headers[key] = val
        if "content-type" not in fwd_headers:
            fwd_headers["content-type"] = "application/json"
        fwd_headers["user-agent"] = f"uncommon-route/{VERSION}"

        # Auth: BYOK key > request header > UNCOMMON_ROUTE_API_KEY > COMMONSTACK_API_KEY (legacy)
        if provider_entry:
            fwd_headers["authorization"] = f"Bearer {provider_entry.api_key}"
        elif "authorization" not in fwd_headers:
            api_key = (
                os.environ.get("UNCOMMON_ROUTE_API_KEY", "")
                or os.environ.get("COMMONSTACK_API_KEY", "")
            )
            if api_key:
                fwd_headers["authorization"] = f"Bearer {api_key}"
            raw_model = selected_model.split("/", 1)[-1] if "/" in selected_model else selected_model
            body["model"] = raw_model

        debug_headers: dict[str, str] = {}
        if is_virtual:
            debug_headers["x-uncommon-route-request-id"] = request_id
            debug_headers["x-uncommon-route-model"] = selected_model
            debug_headers["x-uncommon-route-tier"] = tier_value
            debug_headers["x-uncommon-route-step"] = step_type
            debug_headers["x-uncommon-route-reasoning"] = reasoning

        try:
            if is_streaming:
                if is_virtual:
                    _spend.record(estimated_cost, model=selected_model, action="chat")
                    _stats.record(RouteRecord(
                        timestamp=time.time(), model=selected_model, tier=tier_value,
                        confidence=confidence, method=route_method,  # type: ignore[arg-type]
                        estimated_cost=estimated_cost, savings=savings,
                        latency_us=route_latency_us, session_id=session_id,
                        streaming=True,
                    ))

                async def sse_passthrough() -> AsyncGenerator[bytes, None]:
                    async for chunk in _stream_upstream(target_chat_url, body, fwd_headers):
                        yield chunk

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
            resp = await client.post(target_chat_url, json=body, headers=fwd_headers)

            if is_virtual:
                actual_cost: float | None = None
                if resp.status_code == 200:
                    actual_cost = _parse_usage_cost(resp.content, selected_model)
                _spend.record(
                    actual_cost if actual_cost is not None else estimated_cost,
                    model=selected_model,
                    action="chat",
                )
                _stats.record(RouteRecord(
                    timestamp=time.time(), model=selected_model, tier=tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, actual_cost=actual_cost,
                    savings=savings, latency_us=route_latency_us,
                    session_id=session_id, streaming=False,
                ))

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
                _stats.record(RouteRecord(
                    timestamp=time.time(), model=selected_model, tier=tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, savings=savings,
                    latency_us=route_latency_us, session_id=session_id,
                    streaming=is_streaming,
                ))
            return JSONResponse(
                {"error": {"message": f"Upstream unreachable: {upstream_chat}", "type": "proxy_error"}},
                status_code=502,
                headers=debug_headers,
            )
        except httpx.TimeoutException:
            if is_virtual:
                _stats.record(RouteRecord(
                    timestamp=time.time(), model=selected_model, tier=tier_value,
                    confidence=confidence, method=route_method,  # type: ignore[arg-type]
                    estimated_cost=estimated_cost, savings=savings,
                    latency_us=route_latency_us, session_id=session_id,
                    streaming=is_streaming,
                ))
            return JSONResponse(
                {"error": {"message": "Upstream request timed out", "type": "proxy_error"}},
                status_code=504,
                headers=debug_headers,
            )

    return Starlette(
        routes=[
            Route("/health", handle_health, methods=["GET"]),
            Route("/v1/models", handle_models, methods=["GET"]),
            Route("/v1/chat/completions", handle_chat_completions, methods=["POST"]),
            Route("/v1/spend", handle_spend, methods=["GET", "POST"]),
            Route("/v1/sessions", handle_sessions, methods=["GET"]),
            Route("/v1/stats", handle_stats, methods=["GET", "POST"]),
            Route("/v1/feedback", handle_feedback, methods=["GET", "POST"]),
        ],
    )


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
    print(f"[UncommonRoute] Proxy listening on http://{host}:{port}")
    if upstream:
        print(f"[UncommonRoute] Upstream: {upstream}")
    else:
        print(f"[UncommonRoute] WARNING: No upstream configured — requests will return 503")
        print(f"[UncommonRoute] Set UNCOMMON_ROUTE_UPSTREAM and UNCOMMON_ROUTE_API_KEY to enable forwarding")
    print(f"[UncommonRoute] Virtual model: {VIRTUAL_MODEL}")
    print(f"[UncommonRoute] Session persistence: enabled")
    print(f"[UncommonRoute] Spend control: enabled")
    uvicorn.run(app, host=host, port=port, log_level="info")
