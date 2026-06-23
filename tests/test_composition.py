"""Tests for deterministic request composition."""

from __future__ import annotations

import json

import pytest

from uncommon_route.artifacts import ArtifactStore
from uncommon_route.composition import (
    CompositionPolicy,
    compose_messages,
    compose_messages_semantic,
    load_composition_policy,
)
from uncommon_route.semantic import (
    QualityFallbackPolicy,
    SemanticCallResult,
    SideChannelConfig,
    SideChannelTaskConfig,
    score_semantic_quality,
)


class FakeSemanticCompressor:
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"summary for {tool_name}: key findings only",
            model="deepseek/deepseek-chat",
            estimated_cost=0.001,
            actual_cost=0.0007,
        )

    async def summarize_history(self, transcript: str, *, latest_user_prompt: str, session_id: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"checkpoint for {session_id}: goal, files, blockers",
            model="deepseek/deepseek-chat",
            estimated_cost=0.002,
            actual_cost=0.0014,
        )

    async def rehydrate_artifact(self, query: str, *, artifact_id: str, content: str, summary: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"excerpt for {artifact_id}: the relevant section",
            model="deepseek/deepseek-chat",
            estimated_cost=0.001,
            actual_cost=0.0008,
        )


class QualityFallbackSemanticCompressor(FakeSemanticCompressor):
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"summary for {tool_name}: error path and next action",
            model="google/gemini-2.5-flash-lite",
            estimated_cost=0.001,
            actual_cost=0.0007,
            quality_fallbacks=2,
        )


def test_large_tool_result_is_offloaded(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    large_json = '{"items":[' + ",".join('{"id":1,"name":"example"}' for _ in range(900)) + ']}'
    messages = [
        {"role": "user", "content": "analyze this result"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }],
            "content": "",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": large_json},
    ]

    result = compose_messages(messages, store, CompositionPolicy(tool_offload_threshold_tokens=400))

    assert result.offloaded_messages == 1
    assert len(result.artifact_ids) == 1
    assert result.input_tokens_after < result.input_tokens_before
    tool_msg = result.messages[-1]
    assert "artifact://" in tool_msg["content"]
    artifact = store.get(result.artifact_ids[0])
    assert artifact is not None
    assert artifact["content"] == large_json
    assert artifact["tool_name"] == "search"


def test_disabled_artifact_store_keeps_tool_result_inline(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts", enabled=False)
    large_json = "{\"rows\":[" + ",".join(f'"line {i}"' for i in range(1500)) + "]}"
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "search"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": large_json},
    ]

    result = compose_messages(messages, store, CompositionPolicy(tool_offload_threshold_tokens=400))

    assert result.offloaded_messages == 0
    assert result.artifact_ids == []
    assert result.messages[1]["content"] == large_json
    assert "artifact://" not in result.messages[1]["content"]
    assert store.count() == 0


def test_multimodal_content_keeps_block_structure(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this image"},
            {"type": "image_url", "image_url": {"url": "https://example.com/test.png"}},
        ],
    }]

    result = compose_messages(messages, store)

    assert result.messages[0]["content"] == messages[0]["content"]
    assert result.input_tokens_before > 0


def test_load_composition_policy_from_json_file(tmp_path) -> None:
    config_path = tmp_path / "composition.json"
    config_path.write_text(json.dumps({
        "tool_offload_threshold_tokens": 1200,
        "sidechannel": {
            "tool_summary": {
                "primary": "openai/gpt-4o-mini",
                "fallback": ["anthropic/claude-haiku-4.5"],
                "max_tokens": 180,
                "quality": {"min_chars": 32},
            },
            "checkpoint": {
                "primary": "moonshot/kimi-k2.5",
                "fallback": ["deepseek/deepseek-chat"],
                "max_tokens": 420,
            },
        },
    }))

    policy = load_composition_policy(path=str(config_path))

    assert policy.tool_offload_threshold_tokens == 1200
    assert policy.sidechannel.tool_summary.primary == "openai/gpt-4o-mini"
    assert policy.sidechannel.tool_summary.fallback == ("anthropic/claude-haiku-4.5",)
    assert policy.sidechannel.tool_summary.max_tokens == 180
    assert policy.sidechannel.tool_summary.quality.min_chars == 32
    assert policy.sidechannel.checkpoint.primary == "moonshot/kimi-k2.5"
    assert policy.sidechannel.rehydrate.primary == "google/gemini-2.5-flash-lite"


def test_load_composition_policy_rejects_invalid_thresholds(tmp_path) -> None:
    config_path = tmp_path / "composition-invalid.json"
    config_path.write_text(json.dumps({
        "sidechannel": {
            "tool_summary": {
                "primary": "openai/gpt-4o-mini",
                "quality": {"min_source_ratio": 0.5, "max_source_ratio": 0.2},
            },
        },
    }))

    with pytest.raises(ValueError, match="max_source_ratio"):
        load_composition_policy(path=str(config_path))


def test_semantic_quality_scoring_rejects_low_overlap() -> None:
    source_text = "\n".join(
        f"migration failed because users.email was missing and idx_users_email already exists line {i}"
        for i in range(40)
    )
    ok, quality, reason = score_semantic_quality(
        "generic summary that says almost nothing about the failing migration step",
        source_text=source_text,
        query_text="users.email migration failure",
        policy=QualityFallbackPolicy(min_query_overlap_terms=2),
    )

    assert ok is False
    assert quality < 0.5
    assert reason == "low_query_overlap"


@pytest.mark.asyncio
async def test_semantic_summary_replaces_preview_stub(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    large_text = "\n".join(f"result line {i}" for i in range(2500))
    messages = [
        {"role": "user", "content": "find the important errors"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }],
            "content": "",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": large_text},
    ]

    result = await compose_messages_semantic(
        messages,
        store,
        CompositionPolicy(tool_offload_threshold_tokens=400, semantic_tool_summary_threshold_tokens=200),
        semantic_compressor=FakeSemanticCompressor(),
        session_id="sess-1",
        request=object(),
    )

    assert result.semantic_summaries == 1
    assert result.semantic_calls == 1
    assert "summarized via side-channel compression" in result.messages[-1]["content"]
    artifact_id = result.artifact_ids[0]
    artifact = store.get(artifact_id)
    assert artifact is not None
    assert artifact["summary"].startswith("summary for bash")


@pytest.mark.asyncio
async def test_semantic_quality_fallbacks_are_accumulated(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    large_text = "\n".join(f"error line {i}" for i in range(2200))
    policy = CompositionPolicy(
        tool_offload_threshold_tokens=400,
        semantic_tool_summary_threshold_tokens=200,
        sidechannel=SideChannelConfig(
            tool_summary=SideChannelTaskConfig(primary="openai/gpt-4o-mini"),
            checkpoint=SideChannelTaskConfig(primary="deepseek/deepseek-chat"),
            rehydrate=SideChannelTaskConfig(primary="google/gemini-2.5-flash-lite"),
        ),
    )
    messages = [
        {"role": "user", "content": "find the main error"},
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }],
            "content": "",
        },
        {"role": "tool", "tool_call_id": "call_1", "content": large_text},
    ]

    result = await compose_messages_semantic(
        messages,
        store,
        policy,
        semantic_compressor=QualityFallbackSemanticCompressor(),
        session_id="sess-quality",
        request=object(),
    )

    assert result.semantic_calls == 1
    assert result.semantic_quality_fallbacks == 2


@pytest.mark.asyncio
async def test_checkpoint_and_rehydrate(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    artifact = store.store_text(
        "database table users has columns id, email, created_at",
        kind="tool-result",
        role="tool",
        tool_name="read_schema",
    )
    messages = [{"role": "system", "content": "You are coding assistant."}]
    for i in range(18):
        messages.append({"role": "user", "content": f"turn {i} explain the migration status in detail"})
        messages.append({"role": "assistant", "content": f"assistant response {i} with a lot of detail " * 20})
    messages.append({"role": "user", "content": f"use {artifact.id} and artifact://{artifact.id} to answer"})

    result = await compose_messages_semantic(
        messages,
        store,
        CompositionPolicy(
            checkpoint_threshold_tokens=300,
            checkpoint_keep_last_messages=4,
            checkpoint_min_messages=6,
        ),
        semantic_compressor=FakeSemanticCompressor(),
        session_id="sess-2",
        request=object(),
    )

    assert result.checkpoint_created is True
    assert result.rehydrated_artifacts == 1
    assert result.semantic_calls >= 2
    assert any(
        isinstance(msg, dict) and msg.get("role") == "system" and "checkpoint artifact://" in str(msg.get("content"))
        for msg in result.messages
    )
    latest_user = next(msg for msg in reversed(result.messages) if msg.get("role") == "user")
    assert "Rehydrated artifact://" in latest_user["content"]


@pytest.mark.asyncio
async def test_tool_selection_skips_checkpoint_even_when_long(tmp_path) -> None:
    store = ArtifactStore(root=tmp_path / "artifacts")
    messages = [{"role": "system", "content": "You are coding assistant."}]
    for i in range(20):
        messages.append({"role": "user", "content": f"turn {i} inspect the repo and choose tools"})
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }],
        })
    messages.append({"role": "user", "content": "choose the next tool carefully"})

    result = await compose_messages_semantic(
        messages,
        store,
        CompositionPolicy(
            checkpoint_threshold_tokens=100,
            checkpoint_agentic_threshold_tokens=100,
        ),
        semantic_compressor=FakeSemanticCompressor(),
        session_id="sess-tools",
        request=object(),
        step_type="tool-selection",
        is_agentic=True,
    )

    assert result.checkpoint_created is False
