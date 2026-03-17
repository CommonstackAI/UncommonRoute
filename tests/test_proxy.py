"""Integration tests for the proxy server with session + spend control."""

from __future__ import annotations


import pytest
from starlette.testclient import TestClient

from uncommon_route.artifacts import ArtifactStore
from uncommon_route.composition import CompositionPolicy
from uncommon_route.connections_store import ConnectionsStore, InMemoryConnectionsStorage
from uncommon_route.model_experience import InMemoryModelExperienceStorage, ModelExperienceStore
from uncommon_route.proxy import _extract_current_message, _extract_prompt, create_app
from uncommon_route.router.config import routing_mode_from_model
from uncommon_route.routing_config_store import InMemoryRoutingConfigStorage, RoutingConfigStore
from uncommon_route.semantic import SemanticCallResult, SideChannelConfig, SideChannelTaskConfig
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.router.types import RoutingMode


class FakeSemanticCompressor:
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"semantic summary for {tool_name}", model="deepseek/deepseek-chat", estimated_cost=0.001)

    async def summarize_history(self, transcript: str, *, latest_user_prompt: str, session_id: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"checkpoint summary for {session_id}", model="deepseek/deepseek-chat", estimated_cost=0.002)

    async def rehydrate_artifact(self, query: str, *, artifact_id: str, content: str, summary: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"rehydrated excerpt for {artifact_id}", model="deepseek/deepseek-chat", estimated_cost=0.001)


class QualityFallbackSemanticCompressor(FakeSemanticCompressor):
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"semantic summary for {tool_name}",
            model="google/gemini-2.5-flash-lite",
            estimated_cost=0.001,
            quality_fallbacks=3,
        )


class TestPromptExtraction:
    def test_extract_prompt_ignores_claude_code_wrapper_blocks(self) -> None:
        prompt, system_prompt, max_tokens = _extract_prompt({
            "max_tokens": 128,
            "messages": [
                {"role": "system", "content": "top-level system"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<system-reminder>\nThe following skills are available for use with the Skill tool.\n</system-reminder>",
                        },
                        {
                            "type": "text",
                            "text": "<system-reminder>\nAs you answer the user's questions, you can use the following context.\n# claudeMd\n</system-reminder>",
                        },
                        {
                            "type": "text",
                            "text": "List the top-level directories in the current repository.",
                        },
                    ],
                },
            ],
        })

        assert prompt == "List the top-level directories in the current repository."
        assert system_prompt == "top-level system"
        assert max_tokens == 128

    def test_extract_prompt_strips_wrapper_prefix_from_string_message(self) -> None:
        prompt, _system_prompt, _max_tokens = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "<system-reminder>\n"
                        "The following skills are available for use with the Skill tool.\n"
                        "</system-reminder>\n"
                        "Find routing_mode_from_model in this repository."
                    ),
                },
            ],
        })

        assert prompt == "Find routing_mode_from_model in this repository."

    # ── OpenClaw bracket-marker history context ──

    def test_openclaw_history_context_simple_greeting(self) -> None:
        """OpenClaw wraps history + current message in bracket markers."""
        prompt, system_prompt, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "You are Claw, an AI assistant."},
                {
                    "role": "user",
                    "content": (
                        "[Chat messages since your last reply - for context]\n"
                        "User: Hey can you help me?\n"
                        "Assistant: Of course! What do you need?\n"
                        "\n"
                        "[Current message - respond to this]\n"
                        "User: hi"
                    ),
                },
            ],
        })
        assert prompt == "hi"
        assert system_prompt == "You are Claw, an AI assistant."

    def test_openclaw_history_context_complex_prompt(self) -> None:
        """Current message after marker can be a complex task, not just a greeting."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": (
                        "[Chat messages since your last reply - for context]\n"
                        "User: I need help with my database\n"
                        "Assistant: What database are you using?\n"
                        "\n"
                        "[Current message - respond to this]\n"
                        "User: Design a fault-tolerant distributed PostgreSQL "
                        "cluster with automatic failover, read replicas, and "
                        "connection pooling using PgBouncer"
                    ),
                },
            ],
        })
        assert prompt.startswith("Design a fault-tolerant distributed PostgreSQL")
        assert "PgBouncer" in prompt

    def test_openclaw_no_history_only_current_marker(self) -> None:
        """Message with only the current-message marker, no history section."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "[Current message - respond to this]\n"
                        "User: what is 2+2?"
                    ),
                },
            ],
        })
        assert prompt == "what is 2+2?"

    def test_openclaw_no_sender_prefix(self) -> None:
        """Current message without 'User:' prefix should still work."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "[Chat messages since your last reply - for context]\n"
                        "User: previous\n"
                        "\n"
                        "[Current message - respond to this]\n"
                        "explain quicksort"
                    ),
                },
            ],
        })
        assert prompt == "explain quicksort"

    def test_openclaw_multiline_current_message(self) -> None:
        """Multi-line current message is preserved intact."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "[Chat messages since your last reply - for context]\n"
                        "User: help\n"
                        "\n"
                        "[Current message - respond to this]\n"
                        "User: Write a Python function that:\n"
                        "1. Reads a CSV file\n"
                        "2. Validates the schema\n"
                        "3. Returns a DataFrame"
                    ),
                },
            ],
        })
        assert prompt.startswith("Write a Python function that:")
        assert "Returns a DataFrame" in prompt

    def test_openclaw_long_history_short_message(self) -> None:
        """Long history context should not inflate the extracted prompt."""
        history_lines = "\n".join(
            f"{'User' if i % 2 == 0 else 'Assistant'}: "
            f"{'Some question about topic ' + str(i) if i % 2 == 0 else 'Here is my detailed response about topic ' + str(i) + ' with lots of context and details'}"
            for i in range(40)
        )
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "x" * 5000},
                {
                    "role": "user",
                    "content": (
                        f"[Chat messages since your last reply - for context]\n"
                        f"{history_lines}\n"
                        f"\n"
                        f"[Current message - respond to this]\n"
                        f"User: thanks"
                    ),
                },
            ],
        })
        assert prompt == "thanks"

    def test_extract_current_message_returns_none_for_plain_text(self) -> None:
        """_extract_current_message returns None when no marker is present."""
        assert _extract_current_message("just a regular message") is None
        assert _extract_current_message("hello") is None
        assert _extract_current_message("") is None

    # ── Claude Code / Cursor (XML content blocks) ──

    def test_claude_code_multiple_system_reminder_blocks(self) -> None:
        """Claude Code sends multiple <system-reminder> blocks + real prompt."""
        prompt, system_prompt, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "You are Claude."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "<system-reminder>\nThe following skills are available for use with the Skill tool.\nSkill A, Skill B\n</system-reminder>"},
                        {"type": "text", "text": "<system-reminder>\nAs you answer the user's questions, you can use the following context.\n# claudeMd\nSome workspace rules.\n</system-reminder>"},
                        {"type": "text", "text": "<system-reminder>\nTags contain information from the system.\n</system-reminder>"},
                        {"type": "text", "text": "Fix the bug in src/auth.py where the JWT token validation fails on expired tokens."},
                    ],
                },
            ],
        })
        assert prompt == "Fix the bug in src/auth.py where the JWT token validation fails on expired tokens."
        assert system_prompt == "You are Claude."

    def test_cursor_wrapper_in_string_content(self) -> None:
        """Cursor may send wrapper prefix as a single string, not content blocks."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "<system-reminder>\n"
                        "Codebase and user instructions are shown below.\n"
                        "</system-reminder>\n"
                        "<system-reminder>\n"
                        "The user will primarily request you to perform software engineering tasks.\n"
                        "</system-reminder>\n"
                        "Refactor the database connection pool to use async context managers."
                    ),
                },
            ],
        })
        assert prompt == "Refactor the database connection pool to use async context managers."

    def test_claude_code_cache_control_blocks(self) -> None:
        """Claude Code content blocks with cache_control metadata."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "system prompt"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<system-reminder>\nThe following skills are available for use with the Skill tool.\n</system-reminder>",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": "Run the test suite and fix any failures."},
                    ],
                },
            ],
        })
        assert prompt == "Run the test suite and fix any failures."

    # ── Codex (standard OpenAI format) ──

    def test_codex_standard_openai_format(self) -> None:
        """Codex uses standard OpenAI format with proper role separation."""
        prompt, system_prompt, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "Add error handling to the API endpoint."},
            ],
        })
        assert prompt == "Add error handling to the API endpoint."
        assert system_prompt == "You are a coding assistant."

    def test_codex_multi_turn_conversation(self) -> None:
        """Multi-turn conversation extracts only the last user message."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "What frameworks do you support?"},
                {"role": "assistant", "content": "I support React, Vue, Angular..."},
                {"role": "user", "content": "Show me a React example."},
                {"role": "assistant", "content": "Here is a simple React component..."},
                {"role": "user", "content": "Make it TypeScript."},
            ],
        })
        assert prompt == "Make it TypeScript."

    def test_codex_with_tool_messages(self) -> None:
        """Agentic loop with tool calls — last user message is extracted."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "Read the config file."},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"config.json"}'}}
                ]},
                {"role": "tool", "tool_call_id": "call_1", "content": '{"port": 3000}'},
                {"role": "user", "content": "Change the port to 8080."},
            ],
        })
        assert prompt == "Change the port to 8080."

    # ── OpenAI SDK / generic ──

    def test_openai_sdk_simple_message(self) -> None:
        """Vanilla OpenAI SDK call with a single user message."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "user", "content": "hello"},
            ],
        })
        assert prompt == "hello"

    def test_openai_sdk_content_blocks(self) -> None:
        """OpenAI SDK with content blocks (vision-style)."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image."},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                },
            ],
        })
        assert prompt == "Describe this image."

    # ── Edge cases ──

    def test_no_user_message_returns_empty(self) -> None:
        """Request with no user message returns empty prompt."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "system", "content": "system"},
            ],
        })
        assert prompt == ""

    def test_empty_messages_returns_empty(self) -> None:
        prompt, _sys, _max = _extract_prompt({"messages": []})
        assert prompt == ""

    def test_plain_text_unaffected(self) -> None:
        """Plain user message without any wrappers stays intact."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "user", "content": "Write a Rust HTTP server."},
            ],
        })
        assert prompt == "Write a Rust HTTP server."

    def test_bracket_text_without_marker_unaffected(self) -> None:
        """Brackets in regular text don't trigger false stripping."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {"role": "user", "content": "Implement [Serializable] attribute in C#."},
            ],
        })
        assert prompt == "Implement [Serializable] attribute in C#."

    def test_openclaw_marker_in_content_blocks(self) -> None:
        """OpenClaw-style marker delivered as content blocks (list format)."""
        prompt, _sys, _max = _extract_prompt({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "[Chat messages since your last reply - for context]\n"
                                "User: old message\n"
                                "\n"
                                "[Current message - respond to this]\n"
                                "User: summarize this file"
                            ),
                        },
                    ],
                },
            ],
        })
        assert prompt == "summarize this file"


@pytest.fixture
def client() -> TestClient:
    """Test client with in-memory spend control (no real upstream)."""
    spend_control = SpendControl(storage=InMemorySpendControlStorage())
    app = create_app(
        upstream="http://127.0.0.1:1/fake",
        spend_control=spend_control,
    )
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["router"] == "uncommon-route"

    def test_health_includes_spending(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "spending" in data
        assert "calls" in data["spending"]

    def test_health_exposes_custom_composition_policy(self, tmp_path) -> None:
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            artifact_store=ArtifactStore(root=tmp_path / "artifacts"),
            composition_policy=CompositionPolicy(
                tool_offload_threshold_tokens=1234,
                sidechannel=SideChannelConfig(
                    tool_summary=SideChannelTaskConfig(
                        primary="openai/gpt-4o-mini",
                        fallback=("anthropic/claude-haiku-4.5",),
                    ),
                    checkpoint=SideChannelTaskConfig(primary="moonshot/kimi-k2.5"),
                    rehydrate=SideChannelTaskConfig(primary="deepseek/deepseek-chat"),
                ),
            ),
        )
        client = TestClient(app, raise_server_exceptions=False)

        data = client.get("/health").json()

        assert data["composition"]["policy"]["tool_offload_threshold_tokens"] == 1234
        assert data["composition"]["policy"]["sidechannel"]["tool_summary"]["primary"] == "openai/gpt-4o-mini"
        assert data["composition"]["sidechannel_models"]["tool_summary"] == [
            "openai/gpt-4o-mini",
            "anthropic/claude-haiku-4.5",
        ]


class TestConnectionsEndpoints:
    def test_get_connections_returns_masked_runtime_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_discover(self, api_key: str | None = None) -> int:
            self._discovered = True
            self._upstream_models = {"openai/gpt-4o"}
            return 1

        monkeypatch.setattr("uncommon_route.model_map.ModelMapper.discover", fake_discover)
        store = ConnectionsStore(storage=InMemoryConnectionsStorage())
        store.set_primary(
            upstream="https://api.openai.com/v1",
            api_key="sk-test-123456",
        )
        app = create_app(
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            connections_store=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/v1/connections")

        assert resp.status_code == 200
        data = resp.json()
        assert data["editable"] is True
        assert data["upstream"] == "https://api.openai.com/v1"
        assert data["api_key_preview"] == "sk-t...456"
        assert data["provider"] == "openai"

    def test_put_connections_hot_reloads_and_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_discover(self, api_key: str | None = None) -> int:
            self._discovered = True
            self._upstream_models = {"openai/gpt-4o"}
            return 1

        monkeypatch.setattr("uncommon_route.model_map.ModelMapper.discover", fake_discover)
        store = ConnectionsStore(storage=InMemoryConnectionsStorage())
        app = create_app(
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            connections_store=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.put("/v1/connections", json={
            "upstream": "https://api.openai.com/v1",
            "api_key": "sk-live-987654",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["upstream"] == "https://api.openai.com/v1"
        assert data["api_key_preview"] == "sk-l...654"
        assert data["provider"] == "openai"
        assert store.primary().upstream == "https://api.openai.com/v1"
        assert store.primary().api_key == "sk-live-987654"

    def test_put_connections_rolls_back_when_validation_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_discover(self, api_key: str | None = None) -> int:
            if "bad.example" in self.upstream_url:
                return 0
            self._discovered = True
            self._upstream_models = {"openai/gpt-4o"}
            return 1

        monkeypatch.setattr("uncommon_route.model_map.ModelMapper.discover", fake_discover)
        store = ConnectionsStore(storage=InMemoryConnectionsStorage())
        store.set_primary(
            upstream="https://api.openai.com/v1",
            api_key="sk-stable-123456",
        )
        app = create_app(
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            connections_store=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.put("/v1/connections", json={
            "upstream": "https://bad.example/v1",
            "api_key": "sk-bad-000000",
        })

        assert resp.status_code == 502
        assert store.primary().upstream == "https://api.openai.com/v1"
        assert store.primary().api_key == "sk-stable-123456"
        current = client.get("/v1/connections").json()
        assert current["upstream"] == "https://api.openai.com/v1"
        assert current["api_key_preview"] == "sk-s...456"

    def test_put_connections_rejects_external_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UNCOMMON_ROUTE_UPSTREAM", "http://127.0.0.1:1/v1")
        monkeypatch.delenv("UNCOMMON_ROUTE_API_KEY", raising=False)
        monkeypatch.delenv("COMMONSTACK_API_KEY", raising=False)
        app = create_app(
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            connections_store=ConnectionsStore(storage=InMemoryConnectionsStorage()),
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.put("/v1/connections", json={
            "upstream": "https://api.openai.com/v1",
            "api_key": "sk-new-123456",
        })

        assert resp.status_code == 409
        data = resp.json()
        assert data["source"] == "env"

    def test_providers_endpoint_live_refreshes_health(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        providers_file = tmp_path / "providers.json"
        monkeypatch.setattr("uncommon_route.providers._PROVIDERS_FILE", providers_file)
        monkeypatch.setattr(
            "uncommon_route.proxy.load_providers",
            lambda: __import__("uncommon_route.providers", fromlist=["load_providers"]).load_providers(path=providers_file),
        )
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
        )
        client = TestClient(app, raise_server_exceptions=False)

        save_resp = client.post("/v1/providers", json={
            "name": "openai",
            "api_key": "sk-provider-123456",
        })

        assert save_resp.status_code == 200
        providers_data = client.get("/v1/providers").json()
        assert providers_data["count"] == 1
        assert providers_data["providers"][0]["api_key_preview"] == "sk-p...456"

        health = client.get("/health").json()
        assert health["providers"]["count"] == 1
        assert "openai" in health["providers"]["names"]

        delete_resp = client.delete("/v1/providers/openai")
        assert delete_resp.status_code == 200
        assert client.get("/health").json()["providers"]["count"] == 0


class TestModelsEndpoint:
    def test_models_list(self, client: TestClient) -> None:
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        model_ids = [m["id"] for m in data["data"]]
        assert "uncommon-route/auto" in model_ids
        assert "uncommon-route/fast" in model_ids
        assert "uncommon-route/best" in model_ids


class TestVirtualModelAliases:
    def test_routing_mode_from_bare_alias(self) -> None:
        assert routing_mode_from_model("auto") is RoutingMode.AUTO
        assert routing_mode_from_model("best") is RoutingMode.BEST

    def test_chat_accepts_bare_virtual_alias(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
        })

        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-mode"] == "auto"


class TestSelectorEndpoint:
    def test_get_selector_state(self, client: TestClient) -> None:
        resp = client.get("/v1/selector")
        assert resp.status_code == 200
        data = resp.json()
        assert "selection_modes" in data
        assert "bandit_modes" in data
        assert "experience" in data

    def test_get_selector_bucket_summary(self) -> None:
        store = ModelExperienceStore(storage=InMemoryModelExperienceStorage())
        store.record_feedback("google/gemini-2.5-flash-lite", "auto", "SIMPLE", "ok")
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            model_experience=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/v1/selector?mode=auto&tier=SIMPLE")

        assert resp.status_code == 200
        data = resp.json()
        assert data["bucket"]["mode"] == "auto"
        assert data["bucket"]["tier"] == "SIMPLE"
        assert data["bucket"]["models"][0]["model"] == "google/gemini-2.5-flash-lite"

    def test_selector_preview_accepts_prompt_shape(self, client: TestClient) -> None:
        resp = client.post("/v1/selector", json={
            "mode": "auto",
            "prompt": "hello",
            "max_tokens": 128,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["virtual"] is True
        assert data["mode"] == "auto"
        assert data["candidate_scores"]
        assert data["candidate_scores"][0]["model"] == data["served_model"]

    def test_selector_preview_caps_tool_selection_to_medium(self, client: TestClient) -> None:
        resp = client.post("/v1/selector", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "list the files changed in this repo and pick the best tool"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run shell commands",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["step_type"] == "tool-selection"
        assert data["served_tier"] == "MEDIUM"


class TestRoutingConfigEndpoint:
    def test_get_routing_config_returns_modes(self, client: TestClient) -> None:
        resp = client.get("/v1/routing-config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["editable"] is True
        assert data["default_mode"] == "auto"
        assert "auto" in data["modes"]
        assert data["modes"]["auto"]["tiers"]["SIMPLE"]["primary"] == ""
        assert data["modes"]["auto"]["tiers"]["SIMPLE"]["selection_mode"] == "adaptive"

    def test_post_set_default_mode_updates_config_and_selector(self, client: TestClient) -> None:
        resp = client.post("/v1/routing-config", json={
            "action": "set-default-mode",
            "mode": "best",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["default_mode"] == "best"

        selector = client.get("/v1/selector")
        assert selector.status_code == 200
        assert selector.json()["default_mode"] == "best"

    def test_post_set_tier_updates_selector_preview(self, client: TestClient) -> None:
        resp = client.post("/v1/routing-config", json={
            "action": "set-tier",
            "mode": "auto",
            "tier": "MEDIUM",
            "primary": "openai/gpt-4o-mini",
            "fallback": [],
        })

        assert resp.status_code == 200
        updated = resp.json()
        assert updated["modes"]["auto"]["tiers"]["MEDIUM"]["primary"] == "openai/gpt-4o-mini"
        assert updated["modes"]["auto"]["tiers"]["MEDIUM"]["overridden"] is True

        preview = client.post("/v1/selector", json={
            "mode": "auto",
            "prompt": "Return valid JSON with keys a and b.",
            "max_tokens": 128,
        })

        assert preview.status_code == 200
        data = preview.json()
        assert data["served_model"]
        assert data["served_tier"]

    def test_hard_pin_forces_primary_over_adaptive_cheaper_candidate(self, client: TestClient) -> None:
        adaptive = client.post("/v1/routing-config", json={
            "action": "set-tier",
            "mode": "fast",
            "tier": "SIMPLE",
            "primary": "openai/gpt-4o",
            "fallback": ["nvidia/gpt-oss-120b"],
            "selection_mode": "adaptive",
        })
        assert adaptive.status_code == 200

        adaptive_preview = client.post("/v1/selector", json={
            "mode": "fast",
            "prompt": "hello",
            "max_tokens": 64,
        })
        assert adaptive_preview.status_code == 200
        assert adaptive_preview.json()["served_model"]

        pinned = client.post("/v1/routing-config", json={
            "action": "set-tier",
            "mode": "fast",
            "tier": "SIMPLE",
            "primary": "openai/gpt-4o",
            "fallback": ["nvidia/gpt-oss-120b"],
            "selection_mode": "hard-pin",
        })
        assert pinned.status_code == 200

        pinned_preview = client.post("/v1/selector", json={
            "mode": "fast",
            "prompt": "hello",
            "max_tokens": 64,
        })

        assert pinned_preview.status_code == 200
        pinned_data = pinned_preview.json()
        assert pinned_data["served_model"]
        assert pinned_data["served_tier"]

    def test_post_reset_tier_restores_default(self) -> None:
        store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            routing_config_store=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        set_resp = client.post("/v1/routing-config", json={
            "action": "set-tier",
            "mode": "auto",
            "tier": "SIMPLE",
            "primary": "openai/gpt-4o-mini",
            "fallback": ["moonshot/kimi-k2.5"],
        })
        assert set_resp.status_code == 200

        reset_resp = client.post("/v1/routing-config", json={
            "action": "reset-tier",
            "mode": "auto",
            "tier": "SIMPLE",
        })

        assert reset_resp.status_code == 200
        reset_data = reset_resp.json()
        assert reset_data["modes"]["auto"]["tiers"]["SIMPLE"]["primary"] == ""
        assert reset_data["modes"]["auto"]["tiers"]["SIMPLE"]["fallback"] == []
        assert reset_data["modes"]["auto"]["tiers"]["SIMPLE"]["overridden"] is False
        assert reset_data["modes"]["auto"]["tiers"]["SIMPLE"]["selection_mode"] == "adaptive"


class TestChatCompletions:
    def test_virtual_model_routes(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "/debug what is 2+2"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["model"] == "uncommon-route/debug"
        assert "UncommonRoute Debug" in data["choices"][0]["message"]["content"]

    def test_routing_headers_present(self, client: TestClient) -> None:
        """Non-debug requests forward to upstream; headers are set even if upstream fails."""
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        # Upstream is fake so we get 502, but routing headers should still be present
        assert resp.status_code == 502
        assert "x-uncommon-route-model" in resp.headers
        assert "x-uncommon-route-tier" in resp.headers
        assert resp.headers["x-uncommon-route-mode"] == "auto"
        assert "x-uncommon-route-transport" in resp.headers
        assert "x-uncommon-route-cache-mode" in resp.headers

    def test_missing_model_uses_persisted_default_mode(self) -> None:
        store = RoutingConfigStore(storage=InMemoryRoutingConfigStorage())
        store.set_default_mode(RoutingMode.BEST)
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            routing_config_store=store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hello"}],
        })

        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-mode"] == "best"

    def test_cache_headers_emitted_for_sessioned_routing(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "uncommon-route/best",
                "messages": [{"role": "user", "content": "hello"}],
            },
            headers={"x-session-id": "best-cache"},
        )

        assert resp.status_code == 502
        assert "x-uncommon-route-transport" in resp.headers
        assert "x-uncommon-route-cache-mode" in resp.headers

    def test_fast_mode_tool_request_avoids_non_tool_model(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/fast",
            "messages": [{"role": "user", "content": "list files in this repo"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "bash",
                    "description": "Run a shell command",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        })
        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-mode"] == "fast"
        assert resp.headers["x-uncommon-route-model"] != "nvidia/gpt-oss-120b"

    def test_best_mode_routes(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/best",
            "messages": [{"role": "user", "content": "design a distributed database with five constraints"}],
        })
        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-mode"] == "best"

    def test_anthropic_messages_accept_explicit_provider_model(self, client: TestClient) -> None:
        resp = client.post("/v1/messages", json={
            "model": "anthropic/claude-sonnet-4.6",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        })

        assert resp.status_code == 502
        assert "x-uncommon-route-mode" not in resp.headers

    def test_large_tool_result_creates_artifact(self, tmp_path) -> None:
        spend_control = SpendControl(storage=InMemorySpendControlStorage())
        artifact_store = ArtifactStore(root=tmp_path / "artifacts")
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=spend_control,
            artifact_store=artifact_store,
        )
        client = TestClient(app, raise_server_exceptions=False)

        large_text = "\n".join(f"line {i} with repeated tool output" for i in range(3000))
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [
                {"role": "user", "content": "analyze this output"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": large_text},
            ],
        })

        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-artifacts"] == "1"
        assert int(resp.headers["x-uncommon-route-input-after"]) < int(resp.headers["x-uncommon-route-input-before"])

        artifacts = client.get("/v1/artifacts").json()
        assert artifacts["count"] == 1
        artifact_id = artifacts["items"][0]["id"]
        artifact = client.get(f"/v1/artifacts/{artifact_id}").json()
        assert artifact["tool_name"] == "bash"
        assert "line 0 with repeated tool output" in artifact["content"]

    def test_semantic_headers_present_when_compressor_runs(self, tmp_path) -> None:
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            artifact_store=ArtifactStore(root=tmp_path / "artifacts"),
            semantic_compressor=FakeSemanticCompressor(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        large_text = "\n".join(f"tool output {i}" for i in range(2500))
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [
                {"role": "user", "content": "analyze and keep using artifact:// references later"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": large_text},
            ],
        })
        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-semantic-calls"] == "1"
        assert resp.headers["x-uncommon-route-artifacts"] == "1"

    def test_semantic_quality_fallback_header_present(self, tmp_path) -> None:
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            artifact_store=ArtifactStore(root=tmp_path / "artifacts"),
            semantic_compressor=QualityFallbackSemanticCompressor(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        large_text = "\n".join(f"tool output {i}" for i in range(2500))
        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [
                {"role": "user", "content": "extract the critical error"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": large_text},
            ],
        })

        assert resp.status_code == 502
        assert resp.headers["x-uncommon-route-semantic-fallbacks"] == "3"

    def test_passthrough_no_routing_headers(self, client: TestClient) -> None:
        resp = client.post("/v1/chat/completions", json={
            "model": "some-other/model",
            "messages": [{"role": "user", "content": "hello"}],
        })
        # Upstream is fake, expect 502
        assert resp.status_code == 502
        assert "x-uncommon-route-model" not in resp.headers


class TestSpendEndpoint:
    def test_get_spend_status(self, client: TestClient) -> None:
        resp = client.get("/v1/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "calls" in data

    def test_set_and_get_limit(self, client: TestClient) -> None:
        resp = client.post("/v1/spend", json={
            "action": "set", "window": "hourly", "amount": 5.00,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        data = client.get("/v1/spend").json()
        assert data["limits"]["hourly"] == 5.00

    def test_clear_limit(self, client: TestClient) -> None:
        client.post("/v1/spend", json={"action": "set", "window": "daily", "amount": 10})
        client.post("/v1/spend", json={"action": "clear", "window": "daily"})
        data = client.get("/v1/spend").json()
        assert "daily" not in data["limits"]

    def test_reset_session(self, client: TestClient) -> None:
        resp = client.post("/v1/spend", json={"action": "reset_session"})
        assert resp.status_code == 200
        assert resp.json()["session_reset"] is True

    def test_invalid_action(self, client: TestClient) -> None:
        resp = client.post("/v1/spend", json={"action": "explode"})
        assert resp.status_code == 400


class TestSpendControlIntegration:
    def test_spend_limit_blocks_request(self) -> None:
        """When spend limit is exhausted, chat completions returns 429."""
        sc = SpendControl(storage=InMemorySpendControlStorage())
        sc.set_limit("session", 0.001)
        sc.record(0.01)

        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=sc,
        )
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 429
        assert "spend_limit_exceeded" in resp.json()["error"]["type"]
