"""Request composition: deterministic compaction plus optional semantic side-channel passes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from uncommon_route.artifacts import ArtifactStore
from uncommon_route.router.structural import estimate_tokens
from uncommon_route.semantic import (
    DEFAULT_SIDECHANNEL_CONFIG,
    SemanticCallResult,
    SemanticCompressor,
    SideChannelConfig,
)

_ARTIFACT_REF_RE = re.compile(r"artifact://([a-z0-9]{6,32})")


@dataclass(frozen=True, slots=True)
class CompositionPolicy:
    tool_offload_threshold_tokens: int = 2400
    semantic_tool_summary_threshold_tokens: int = 1400
    inline_compact_threshold_chars: int = 800
    preview_chars: int = 320
    tail_chars: int = 160
    checkpoint_threshold_tokens: int = 16_000
    checkpoint_agentic_threshold_tokens: int = 32_000
    checkpoint_keep_last_messages: int = 8
    checkpoint_agentic_keep_last_messages: int = 14
    checkpoint_min_messages: int = 14
    checkpoint_skip_recent_tool_window: int = 8
    checkpoint_skip_tool_selection: bool = True
    rehydrate_max_artifacts: int = 2
    rehydrate_append_chars: int = 900
    sidechannel: SideChannelConfig = field(default_factory=lambda: DEFAULT_SIDECHANNEL_CONFIG)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_offload_threshold_tokens": self.tool_offload_threshold_tokens,
            "semantic_tool_summary_threshold_tokens": self.semantic_tool_summary_threshold_tokens,
            "inline_compact_threshold_chars": self.inline_compact_threshold_chars,
            "preview_chars": self.preview_chars,
            "tail_chars": self.tail_chars,
            "checkpoint_threshold_tokens": self.checkpoint_threshold_tokens,
            "checkpoint_agentic_threshold_tokens": self.checkpoint_agentic_threshold_tokens,
            "checkpoint_keep_last_messages": self.checkpoint_keep_last_messages,
            "checkpoint_agentic_keep_last_messages": self.checkpoint_agentic_keep_last_messages,
            "checkpoint_min_messages": self.checkpoint_min_messages,
            "checkpoint_skip_recent_tool_window": self.checkpoint_skip_recent_tool_window,
            "checkpoint_skip_tool_selection": self.checkpoint_skip_tool_selection,
            "rehydrate_max_artifacts": self.rehydrate_max_artifacts,
            "rehydrate_append_chars": self.rehydrate_append_chars,
            "sidechannel": self.sidechannel.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        base: CompositionPolicy | None = None,
    ) -> CompositionPolicy:
        base = base or cls()
        if not data:
            return base
        policy = cls(
            tool_offload_threshold_tokens=int(
                data.get("tool_offload_threshold_tokens", base.tool_offload_threshold_tokens),
            ),
            semantic_tool_summary_threshold_tokens=int(
                data.get(
                    "semantic_tool_summary_threshold_tokens",
                    base.semantic_tool_summary_threshold_tokens,
                ),
            ),
            inline_compact_threshold_chars=int(
                data.get("inline_compact_threshold_chars", base.inline_compact_threshold_chars),
            ),
            preview_chars=int(data.get("preview_chars", base.preview_chars)),
            tail_chars=int(data.get("tail_chars", base.tail_chars)),
            checkpoint_threshold_tokens=int(
                data.get("checkpoint_threshold_tokens", base.checkpoint_threshold_tokens),
            ),
            checkpoint_agentic_threshold_tokens=int(
                data.get(
                    "checkpoint_agentic_threshold_tokens",
                    base.checkpoint_agentic_threshold_tokens,
                ),
            ),
            checkpoint_keep_last_messages=int(
                data.get("checkpoint_keep_last_messages", base.checkpoint_keep_last_messages),
            ),
            checkpoint_agentic_keep_last_messages=int(
                data.get(
                    "checkpoint_agentic_keep_last_messages",
                    base.checkpoint_agentic_keep_last_messages,
                ),
            ),
            checkpoint_min_messages=int(
                data.get("checkpoint_min_messages", base.checkpoint_min_messages),
            ),
            checkpoint_skip_recent_tool_window=int(
                data.get(
                    "checkpoint_skip_recent_tool_window",
                    base.checkpoint_skip_recent_tool_window,
                ),
            ),
            checkpoint_skip_tool_selection=bool(
                data.get("checkpoint_skip_tool_selection", base.checkpoint_skip_tool_selection),
            ),
            rehydrate_max_artifacts=int(
                data.get("rehydrate_max_artifacts", base.rehydrate_max_artifacts),
            ),
            rehydrate_append_chars=int(
                data.get("rehydrate_append_chars", base.rehydrate_append_chars),
            ),
            sidechannel=SideChannelConfig.from_dict(
                data.get("sidechannel"),
                base=base.sidechannel,
            ),
        )
        if policy.tool_offload_threshold_tokens <= 0:
            raise ValueError("tool_offload_threshold_tokens must be > 0")
        if policy.semantic_tool_summary_threshold_tokens <= 0:
            raise ValueError("semantic_tool_summary_threshold_tokens must be > 0")
        if policy.inline_compact_threshold_chars <= 0:
            raise ValueError("inline_compact_threshold_chars must be > 0")
        if policy.preview_chars <= 0 or policy.tail_chars < 0:
            raise ValueError("preview/tail char thresholds must be non-negative and preview > 0")
        if policy.checkpoint_threshold_tokens <= 0:
            raise ValueError("checkpoint_threshold_tokens must be > 0")
        if policy.checkpoint_agentic_threshold_tokens <= 0:
            raise ValueError("checkpoint_agentic_threshold_tokens must be > 0")
        if policy.checkpoint_keep_last_messages <= 0 or policy.checkpoint_min_messages <= 0:
            raise ValueError("checkpoint message thresholds must be > 0")
        if policy.checkpoint_agentic_keep_last_messages <= 0 or policy.checkpoint_skip_recent_tool_window < 0:
            raise ValueError("agentic checkpoint thresholds must be valid")
        if policy.rehydrate_max_artifacts <= 0 or policy.rehydrate_append_chars <= 0:
            raise ValueError("rehydrate thresholds must be > 0")
        return policy


@dataclass(frozen=True, slots=True)
class CompositionResult:
    messages: list[dict[str, Any]]
    input_tokens_before: int
    input_tokens_after: int
    artifact_ids: list[str] = field(default_factory=list)
    compacted_messages: int = 0
    offloaded_messages: int = 0
    semantic_summaries: int = 0
    checkpoint_created: bool = False
    rehydrated_artifacts: int = 0
    semantic_calls: int = 0
    semantic_failures: int = 0
    semantic_quality_fallbacks: int = 0
    semantic_estimated_cost: float = 0.0
    semantic_actual_cost: float = 0.0

    @property
    def changed(self) -> bool:
        return (
            self.input_tokens_after < self.input_tokens_before
            or self.compacted_messages > 0
            or self.offloaded_messages > 0
            or self.semantic_summaries > 0
            or self.checkpoint_created
            or self.rehydrated_artifacts > 0
        )


DEFAULT_COMPOSITION_POLICY = CompositionPolicy()


def load_composition_policy(
    *,
    path: str | None = None,
    env: dict[str, str] | None = None,
) -> CompositionPolicy:
    env = env or dict(os.environ)
    inline = env.get("UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON", "").strip()
    config_path = path or env.get("UNCOMMON_ROUTE_COMPOSITION_CONFIG", "").strip()
    if config_path:
        raw = Path(config_path).read_text()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("composition config file must contain a JSON object")
        return CompositionPolicy.from_dict(data, base=DEFAULT_COMPOSITION_POLICY)
    if inline:
        data = json.loads(inline)
        if not isinstance(data, dict):
            raise ValueError("UNCOMMON_ROUTE_COMPOSITION_CONFIG_JSON must be a JSON object")
        return CompositionPolicy.from_dict(data, base=DEFAULT_COMPOSITION_POLICY)
    return DEFAULT_COMPOSITION_POLICY


def compose_messages(
    messages: list[dict[str, Any]],
    artifact_store: ArtifactStore,
    policy: CompositionPolicy | None = None,
) -> CompositionResult:
    policy = policy or CompositionPolicy()
    return _compose_deterministic(messages, artifact_store, policy)


async def compose_messages_semantic(
    messages: list[dict[str, Any]],
    artifact_store: ArtifactStore,
    policy: CompositionPolicy | None = None,
    *,
    semantic_compressor: SemanticCompressor | None = None,
    session_id: str | None = None,
    request: Any = None,
    step_type: str = "general",
    is_agentic: bool = False,
) -> CompositionResult:
    policy = policy or CompositionPolicy()
    base = _compose_deterministic(messages, artifact_store, policy)
    rewritten = [dict(msg) if isinstance(msg, dict) else msg for msg in base.messages]
    artifact_ids = list(base.artifact_ids)
    compacted_messages = base.compacted_messages
    offloaded_messages = base.offloaded_messages
    semantic_summaries = 0
    checkpoint_created = False
    rehydrated_artifacts = 0
    semantic_calls = 0
    semantic_failures = 0
    semantic_quality_fallbacks = 0
    semantic_estimated_cost = 0.0
    semantic_actual_cost = 0.0

    latest_user_prompt = _latest_user_text(rewritten)

    if semantic_compressor:
        for idx, msg in enumerate(rewritten):
            if not isinstance(msg, dict) or msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or "artifact://" not in content:
                continue
            artifact_id = _extract_artifact_id(content)
            if not artifact_id:
                continue
            artifact = artifact_store.get(artifact_id)
            if artifact is None:
                continue
            if artifact.get("token_estimate", 0) < policy.semantic_tool_summary_threshold_tokens:
                continue
            summary = artifact.get("summary", "")
            if not summary:
                tool_name = str(artifact.get("tool_name", ""))
                call = await semantic_compressor.summarize_tool_result(
                    artifact["content"],
                    tool_name=tool_name,
                    latest_user_prompt=latest_user_prompt,
                    request=request,
                )
                semantic_calls += 1
                if call is None:
                    semantic_failures += 1
                else:
                    summary = call.text.strip()
                    semantic_estimated_cost += call.estimated_cost
                    semantic_actual_cost += call.actual_cost or 0.0
                    semantic_quality_fallbacks += call.quality_fallbacks
                    artifact_store.update_summary(artifact_id, summary)
            if summary:
                rewritten[idx]["content"] = _build_semantic_artifact_stub(
                    artifact,
                    summary,
                    policy,
                )
                semantic_summaries += 1

        (
            rewritten,
            rehydrated_delta,
            calls_delta,
            failures_delta,
            quality_fallbacks_delta,
            est_delta,
            act_delta,
        ) = await _rehydrate_artifacts(
            rewritten,
            artifact_store,
            semantic_compressor,
            policy,
            request=request,
        )
        rehydrated_artifacts += rehydrated_delta
        semantic_calls += calls_delta
        semantic_failures += failures_delta
        semantic_quality_fallbacks += quality_fallbacks_delta
        semantic_estimated_cost += est_delta
        semantic_actual_cost += act_delta

        maybe_checkpoint = await _checkpoint_history(
            rewritten,
            artifact_store,
            semantic_compressor,
            policy,
            session_id=session_id or "",
            request=request,
            step_type=step_type,
            is_agentic=is_agentic,
        )
        if maybe_checkpoint is not None:
            rewritten, checkpoint_summary, checkpoint_artifact_id, call = maybe_checkpoint
            checkpoint_created = True
            if checkpoint_artifact_id:
                artifact_ids.append(checkpoint_artifact_id)
            if checkpoint_summary:
                semantic_summaries += 1
            if call is not None:
                semantic_calls += 1
                semantic_estimated_cost += call.estimated_cost
                semantic_actual_cost += call.actual_cost or 0.0
                semantic_quality_fallbacks += call.quality_fallbacks

    input_before = base.input_tokens_before
    input_after = _estimate_messages_tokens(rewritten)

    return CompositionResult(
        messages=rewritten,
        input_tokens_before=input_before,
        input_tokens_after=input_after,
        artifact_ids=artifact_ids,
        compacted_messages=compacted_messages,
        offloaded_messages=offloaded_messages,
        semantic_summaries=semantic_summaries,
        checkpoint_created=checkpoint_created,
        rehydrated_artifacts=rehydrated_artifacts,
        semantic_calls=semantic_calls,
        semantic_failures=semantic_failures,
        semantic_quality_fallbacks=semantic_quality_fallbacks,
        semantic_estimated_cost=semantic_estimated_cost,
        semantic_actual_cost=semantic_actual_cost,
    )


def _compose_deterministic(
    messages: list[dict[str, Any]],
    artifact_store: ArtifactStore,
    policy: CompositionPolicy,
) -> CompositionResult:
    rewritten: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    compacted_messages = 0
    offloaded_messages = 0
    input_before = 0

    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            rewritten.append(msg)
            continue

        content = msg.get("content")
        text = _content_to_text(content)
        if text is None:
            rewritten.append(dict(msg))
            continue

        input_before += estimate_tokens(text)
        new_msg = dict(msg)
        compacted = _safe_compact_text(text)

        if (
            artifact_store.enabled
            and msg.get("role") == "tool"
            and estimate_tokens(compacted) >= policy.tool_offload_threshold_tokens
        ):
            tool_call_id = str(msg.get("tool_call_id", ""))
            tool_name = _infer_tool_name(messages, idx, tool_call_id)
            content_type = "application/json" if _looks_like_json(compacted) else "text/plain"
            record = artifact_store.store_text(
                text,
                kind="tool-result",
                role="tool",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                content_type=content_type,
            )
            new_msg["content"] = _build_artifact_stub(compacted, record, policy)
            artifact_ids.append(record.id)
            offloaded_messages += 1
        elif msg.get("role") == "tool" and compacted != text:
            new_msg["content"] = compacted
            compacted_messages += 1
        elif isinstance(content, str):
            if len(content) >= policy.inline_compact_threshold_chars and compacted != content:
                new_msg["content"] = compacted
                compacted_messages += 1

        rewritten.append(new_msg)

    input_after = _estimate_messages_tokens(rewritten)
    return CompositionResult(
        messages=rewritten,
        input_tokens_before=input_before,
        input_tokens_after=input_after,
        artifact_ids=artifact_ids,
        compacted_messages=compacted_messages,
        offloaded_messages=offloaded_messages,
    )


async def _rehydrate_artifacts(
    messages: list[dict[str, Any]],
    artifact_store: ArtifactStore,
    semantic_compressor: SemanticCompressor,
    policy: CompositionPolicy,
    *,
    request: Any,
) -> tuple[list[dict[str, Any]], int, int, int, int, float, float]:
    if not artifact_store.enabled:
        return messages, 0, 0, 0, 0, 0.0, 0.0
    latest_idx = _latest_user_index(messages)
    if latest_idx is None:
        return messages, 0, 0, 0, 0, 0.0, 0.0
    msg = dict(messages[latest_idx])
    content = msg.get("content")
    if not isinstance(content, str):
        return messages, 0, 0, 0, 0, 0.0, 0.0

    refs = _ARTIFACT_REF_RE.findall(content)
    if not refs:
        return messages, 0, 0, 0, 0, 0.0, 0.0

    appended: list[str] = []
    used = 0
    calls = 0
    failures = 0
    quality_fallbacks = 0
    est = 0.0
    act = 0.0
    for artifact_id in refs[:policy.rehydrate_max_artifacts]:
        artifact = artifact_store.get(artifact_id)
        if artifact is None:
            continue
        query = content
        summary = str(artifact.get("summary", ""))
        excerpt = summary
        if not excerpt:
            call = await semantic_compressor.rehydrate_artifact(
                query,
                artifact_id=artifact_id,
                content=artifact.get("content", ""),
                summary=summary,
                request=request,
            )
            calls += 1
            if call is None:
                failures += 1
                excerpt = _truncate_excerpt(artifact.get("content", ""), policy.rehydrate_append_chars)
            else:
                excerpt = call.text.strip()
                est += call.estimated_cost
                act += call.actual_cost or 0.0
                quality_fallbacks += call.quality_fallbacks
        else:
            excerpt = excerpt[:policy.rehydrate_append_chars]
        if excerpt:
            appended.append(f"[Rehydrated artifact://{artifact_id}]\n{excerpt}")
            used += 1

    if appended:
        msg["content"] = content + "\n\n" + "\n\n".join(appended)
        messages = list(messages)
        messages[latest_idx] = msg
    return messages, used, calls, failures, quality_fallbacks, est, act


async def _checkpoint_history(
    messages: list[dict[str, Any]],
    artifact_store: ArtifactStore,
    semantic_compressor: SemanticCompressor,
    policy: CompositionPolicy,
    *,
    session_id: str,
    request: Any,
    step_type: str,
    is_agentic: bool,
) -> tuple[list[dict[str, Any]], str, str, SemanticCallResult | None] | None:
    if not artifact_store.enabled:
        return None
    if step_type == "tool-selection" and policy.checkpoint_skip_tool_selection:
        return None

    keep_last_messages = (
        policy.checkpoint_agentic_keep_last_messages
        if is_agentic
        else policy.checkpoint_keep_last_messages
    )
    threshold_tokens = (
        policy.checkpoint_agentic_threshold_tokens
        if is_agentic
        else policy.checkpoint_threshold_tokens
    )

    if len(messages) < max(policy.checkpoint_min_messages, keep_last_messages + 2):
        return None
    if _estimate_messages_tokens(messages) < threshold_tokens:
        return None

    leading_system, remainder = _split_leading_system(messages)
    if len(remainder) <= keep_last_messages:
        return None
    if (
        is_agentic
        and policy.checkpoint_skip_recent_tool_window > 0
        and _has_recent_tool_activity(remainder[-policy.checkpoint_skip_recent_tool_window:])
    ):
        return None

    head = remainder[:-keep_last_messages]
    tail = remainder[-keep_last_messages:]
    transcript = _messages_to_transcript(head)
    if not transcript.strip():
        return None

    history_record = artifact_store.store_text(
        transcript,
        kind="history-block",
        role="system",
        session_id=session_id,
        content_type="text/plain",
    )
    summary = history_record.summary
    call: SemanticCallResult | None = None
    if not summary:
        call = await semantic_compressor.summarize_history(
            transcript,
            latest_user_prompt=_latest_user_text(messages),
            session_id=session_id,
            request=request,
        )
        if call is None:
            return None
        summary = call.text.strip()
        artifact_store.update_summary(history_record.id, summary)

    checkpoint_msg = {
        "role": "system",
        "content": (
            f"[UncommonRoute checkpoint artifact://{history_record.id}]\n"
            "Earlier conversation compressed for context continuity.\n"
            f"{summary}"
        ),
    }
    return [*leading_system, checkpoint_msg, *tail], summary, history_record.id, call


def _has_recent_tool_activity(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            return True
        if msg.get("tool_calls"):
            return True
    return False


def _content_to_text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return str(content)
    return str(content)


def _estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = _content_to_text(msg.get("content"))
        if text:
            total += estimate_tokens(text)
    return total


def _safe_compact_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    try:
        parsed = json.loads(normalized)
    except Exception:
        parsed = None
    if isinstance(parsed, (dict, list)):
        try:
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass

    lines = [line.rstrip() for line in normalized.split("\n")]
    squashed = "\n".join(lines)
    squashed = re.sub(r"\n{3,}", "\n\n", squashed)
    return squashed.strip()


def _infer_tool_name(messages: list[dict[str, Any]], idx: int, tool_call_id: str) -> str:
    if not tool_call_id:
        return ""
    for prev in reversed(messages[:idx]):
        if not isinstance(prev, dict):
            continue
        tool_calls = prev.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            if call.get("id") != tool_call_id:
                continue
            fn = call.get("function") or {}
            name = fn.get("name", "")
            return str(name)
    return ""


def _build_artifact_stub(text: str, record: Any, policy: CompositionPolicy) -> str:
    preview = text[:policy.preview_chars].strip()
    tail = text[-policy.tail_chars:].strip() if len(text) > policy.preview_chars else ""
    lines = [
        f"[UncommonRoute artifact://{record.id}]",
        "Large tool result offloaded for token efficiency.",
        (
            f"tool={record.tool_name or 'unknown'}"
            f" tool_call_id={record.tool_call_id or '-'}"
            f" tokens~{record.token_estimate}"
            f" chars={record.char_count}"
            f" sha256={record.sha256[:12]}"
        ),
    ]
    shape = _describe_shape(text)
    if shape:
        lines.append(shape)
    if preview:
        lines.extend(["preview:", preview])
    if tail and tail != preview:
        lines.extend(["tail:", tail])
    return "\n".join(lines)


def _build_semantic_artifact_stub(artifact: dict[str, Any], summary: str, policy: CompositionPolicy) -> str:
    preview = summary[: policy.preview_chars].strip()
    lines = [
        f"[UncommonRoute artifact://{artifact['id']}]",
        "Large tool result summarized via side-channel compression.",
        (
            f"tool={artifact.get('tool_name') or 'unknown'}"
            f" tool_call_id={artifact.get('tool_call_id') or '-'}"
            f" tokens~{artifact.get('token_estimate', 0)}"
            f" sha256={str(artifact.get('sha256', ''))[:12]}"
        ),
        "summary:",
        preview,
    ]
    return "\n".join(lines)


def _messages_to_transcript(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "unknown")).upper()
        text = _content_to_text(msg.get("content"))
        if not text:
            continue
        parts.append(f"[{role}]\n{text}")
    return "\n\n".join(parts)


def _split_leading_system(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    leading: list[dict[str, Any]] = []
    idx = 0
    while idx < len(messages):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "system":
            leading.append(msg)
            idx += 1
            continue
        break
    return leading, messages[idx:]


def _latest_user_text(messages: list[dict[str, Any]]) -> str:
    idx = _latest_user_index(messages)
    if idx is None:
        return ""
    content = messages[idx].get("content")
    return content if isinstance(content, str) else (_content_to_text(content) or "")


def _latest_user_index(messages: list[dict[str, Any]]) -> int | None:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "user":
            return idx
    return None


def _extract_artifact_id(text: str) -> str:
    m = _ARTIFACT_REF_RE.search(text)
    return m.group(1) if m else ""


def _describe_shape(text: str) -> str:
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        keys = ", ".join(list(parsed.keys())[:8])
        return f"shape=json object keys=[{keys}]"
    if isinstance(parsed, list):
        return f"shape=json array items={len(parsed)}"
    lines = text.count("\n") + 1
    return f"shape=text lines={lines}"


def _truncate_excerpt(text: str, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    head = stripped[: max(1, limit // 2)].strip()
    tail = stripped[-max(1, limit // 3):].strip()
    return f"{head}\n...\n{tail}"


def _looks_like_json(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")
