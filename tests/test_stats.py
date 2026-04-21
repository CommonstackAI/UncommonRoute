"""Tests for route statistics collection and persistence."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from uncommon_route.artifacts import ArtifactStore
from uncommon_route.proxy import create_app
from uncommon_route.semantic import SemanticCallResult
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.stats import (
    InMemoryRouteStatsStorage,
    RouteRecord,
    RouteStats,
)


class FakeSemanticCompressor:
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"summary for {tool_name}", model="deepseek/deepseek-chat", estimated_cost=0.001)

    async def summarize_history(self, transcript: str, *, latest_user_prompt: str, session_id: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"checkpoint for {session_id}", model="deepseek/deepseek-chat", estimated_cost=0.002)

    async def rehydrate_artifact(self, query: str, *, artifact_id: str, content: str, summary: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(text=f"rehydrated {artifact_id}", model="deepseek/deepseek-chat", estimated_cost=0.001)


class QualityFallbackSemanticCompressor(FakeSemanticCompressor):
    async def summarize_tool_result(self, content: str, *, tool_name: str, latest_user_prompt: str, request: object) -> SemanticCallResult | None:
        return SemanticCallResult(
            text=f"summary for {tool_name}",
            model="google/gemini-2.5-flash-lite",
            estimated_cost=0.001,
            quality_fallbacks=2,
        )


def _make_record(
    model: str = "moonshot/kimi-k2.5",
    tier: str = "SIMPLE",
    confidence: float = 0.9,
    method: str = "pool",
    estimated_cost: float = 0.001,
    actual_cost: float | None = None,
    savings: float = 0.95,
    ts: float | None = None,
) -> RouteRecord:
    return RouteRecord(
        timestamp=ts or time.time(),
        model=model,
        tier=tier,
        confidence=confidence,
        method=method,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        savings=savings,
        latency_us=200.0,
    )


class TestRouteStats:
    def test_empty_summary(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        s = rs.summary()
        assert s.total_requests == 0
        assert s.by_tier == {}
        assert s.avg_confidence == 0.0

    def test_record_and_count(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record())
        rs.record(_make_record(tier="COMPLEX", model="google/gemini-3.1-pro"))
        assert rs.count == 2

    def test_summary_by_tier(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record(tier="SIMPLE", confidence=0.9))
        rs.record(_make_record(tier="SIMPLE", confidence=0.8))
        rs.record(_make_record(tier="COMPLEX", confidence=0.7, model="google/gemini-3.1-pro"))
        s = rs.summary()
        assert s.total_requests == 3
        assert s.by_tier["SIMPLE"].count == 2
        assert s.by_tier["COMPLEX"].count == 1
        assert abs(s.by_tier["SIMPLE"].avg_confidence - 0.85) < 0.01

    def test_summary_by_model(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record(model="a/b", estimated_cost=0.01))
        rs.record(_make_record(model="a/b", estimated_cost=0.02))
        rs.record(_make_record(model="c/d", estimated_cost=0.05))
        s = rs.summary()
        assert s.by_model["a/b"].count == 2
        assert s.by_model["c/d"].count == 1
        assert abs(s.by_model["a/b"].total_cost - 0.03) < 1e-9

    def test_summary_by_method(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record(method="pool"))
        rs.record(_make_record(method="pool"))
        rs.record(_make_record(method="fallback"))
        s = rs.summary()
        assert s.by_method["pool"] == 2
        assert s.by_method["fallback"] == 1

    def test_actual_cost_preferred(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record(estimated_cost=0.01, actual_cost=0.005))
        s = rs.summary()
        assert abs(s.total_actual_cost - 0.005) < 1e-9
        assert abs(s.total_estimated_cost - 0.01) < 1e-9

    def test_summary_tracks_baseline_and_savings_breakdown(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(RouteRecord(**{
            **_make_record(
                model="anthropic/claude-sonnet-4.6",
                estimated_cost=0.02,
                actual_cost=0.01,
            ).__dict__,
            "baseline_cost": 0.10,
            "input_tokens_before": 2000,
            "input_tokens_after": 1000,
            "cache_read_input_tokens": 1000,
            "cache_write_input_tokens": 100,
        }))

        s = rs.summary()

        assert abs(s.total_baseline_cost - 0.10) < 1e-9
        assert abs(s.total_savings_absolute - 0.09) < 1e-9
        assert abs(s.total_savings_ratio - 0.9) < 1e-9
        assert s.total_cache_savings > 0
        assert s.total_compaction_savings > 0

    def test_summary_infers_baseline_for_legacy_records(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(RouteRecord(**{
            **_make_record(
                model="moonshot/kimi-k2.5",
                estimated_cost=0.01,
                actual_cost=0.01,
                savings=0.8,
            ).__dict__,
            "baseline_cost": 0.0,
        }))

        s = rs.summary()

        assert abs(s.total_baseline_cost - 0.05) < 1e-9
        assert abs(s.total_savings_absolute - 0.04) < 1e-9

    def test_summary_tracks_cache_usage(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rec = _make_record()
        rec = RouteRecord(**{
            **rec.__dict__,
            "usage_input_tokens": 1200,
            "usage_output_tokens": 300,
            "cache_read_input_tokens": 900,
            "cache_write_input_tokens": 100,
            "cache_hit_ratio": 0.75,
        })
        rs.record(rec)

        s = rs.summary()

        assert s.total_usage_input_tokens == 1200
        assert s.total_usage_output_tokens == 300
        assert s.total_cache_read_input_tokens == 900
        assert s.total_cache_write_input_tokens == 100
        assert abs(s.avg_cache_hit_ratio - 0.75) < 1e-9

    def test_summary_tracks_transport_and_cache_strategy(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(RouteRecord(**{
            **_make_record(model="anthropic/claude-sonnet-4.6", actual_cost=0.02).__dict__,
            "transport": "anthropic-messages",
            "served_quality": "balanced",
            "served_quality_target": "balanced",
            "served_quality_floor": "economy",
            "capability_lane": "anthropic-tool-safe",
            "cache_mode": "cache_control",
            "cache_family": "anthropic",
            "cache_breakpoints": 2,
        }))
        rs.record(_make_record(actual_cost=0.01))

        s = rs.summary()

        assert s.by_transport["anthropic-messages"].count == 1
        assert s.by_served_quality["balanced"] == 1
        assert s.by_capability_lane["anthropic-tool-safe"] == 1
        assert s.by_cache_mode["cache_control"].count == 1
        assert s.by_cache_family["anthropic"].count == 1
        assert s.by_cache_mode["none"].count == 1
        assert s.total_cache_breakpoints == 2

    def test_history_reversed(self) -> None:
        now = time.time()
        rs = RouteStats(storage=InMemoryRouteStatsStorage(), now_fn=lambda: now)
        rs.record(_make_record(model="first", ts=now - 100))
        rs.record(_make_record(model="second", ts=now - 50))
        h = rs.history()
        assert h[0].model == "second"
        assert h[1].model == "first"

    def test_history_limit(self) -> None:
        now = time.time()
        rs = RouteStats(storage=InMemoryRouteStatsStorage(), now_fn=lambda: now)
        for i in range(10):
            rs.record(_make_record(ts=now - 10 + i))
        assert len(rs.history(limit=3)) == 3

    def test_reset(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        rs.record(_make_record())
        rs.record(_make_record())
        rs.reset()
        assert rs.count == 0
        assert rs.summary().total_requests == 0

    def test_persistence_roundtrip(self) -> None:
        storage = InMemoryRouteStatsStorage()
        rs1 = RouteStats(storage=storage)
        rs1.record(RouteRecord(**{
            **_make_record(model="test/model", confidence=0.77).__dict__,
            "transport": "anthropic-messages",
            "cache_mode": "cache_control",
            "cache_family": "anthropic",
            "cache_breakpoints": 2,
            "route_reasoning": "selected for low latency",
            "routing_features_payload": {"step_type": "research"},
            "fallback_chain_payload": [{"model": "fallback/model", "reason": "cost"}],
            "candidate_scores_payload": [{"model": "test/model", "score": 0.91}],
            "status_code": 502,
            "error_code": "connect_error",
            "error_stage": "upstream_request",
            "error_message": "Upstream unreachable",
        }))
        rs1.record(_make_record(model="test/model2", tier="REASONING"))

        rs2 = RouteStats(storage=storage)
        assert rs2.count == 2
        h = rs2.history()
        assert h[0].model == "test/model2"
        assert h[0].tier == "COMPLEX"
        assert h[1].confidence == 0.77
        assert h[1].transport == "anthropic-messages"
        assert h[1].cache_mode == "cache_control"
        assert h[1].cache_family == "anthropic"
        assert h[1].cache_breakpoints == 2
        assert h[1].route_reasoning == "selected for low latency"
        assert h[1].routing_features_payload == {"step_type": "research"}
        assert h[1].fallback_chain_payload == [{"model": "fallback/model", "reason": "cost"}]
        assert h[1].candidate_scores_payload == [{"model": "test/model", "score": 0.91}]
        assert h[1].status_code == 502
        assert h[1].error_code == "connect_error"
        assert h[1].error_stage == "upstream_request"
        assert h[1].error_message == "Upstream unreachable"

    def test_retention_cleanup(self) -> None:
        t = 1_000_000.0
        rs = RouteStats(
            storage=InMemoryRouteStatsStorage(),
            now_fn=lambda: t,
        )
        rs.record(_make_record(ts=t - 8 * 86_400))  # older than 7 days
        rs.record(_make_record(ts=t - 1_000))  # recent
        assert rs.count == 1

    def test_avg_latency(self) -> None:
        rs = RouteStats(storage=InMemoryRouteStatsStorage())
        r1 = _make_record()
        r1 = RouteRecord(**{**r1.__dict__, "latency_us": 100.0})
        r2 = _make_record()
        r2 = RouteRecord(**{**r2.__dict__, "latency_us": 300.0})
        rs.record(r1)
        rs.record(r2)
        assert abs(rs.summary().avg_latency_us - 200.0) < 0.1


@pytest.fixture
def stats_client() -> TestClient:
    """Test client with in-memory stats."""
    app = create_app(
        upstream="http://127.0.0.1:1/fake",
        spend_control=SpendControl(storage=InMemorySpendControlStorage()),
        route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
    )
    return TestClient(app, raise_server_exceptions=False)


class TestStatsEndpoint:
    def test_get_stats_empty(self, stats_client: TestClient) -> None:
        resp = stats_client.get("/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["by_tier"] == {}
        assert data["by_transport"] == {}
        assert data["by_cache_mode"] == {}
        assert data["by_cache_family"] == {}
        assert data["total_cache_breakpoints"] == 0
        assert data["selector"]["experience"]["records"] == 0

    def test_stats_after_routing(self, stats_client: TestClient) -> None:
        stats_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        resp = stats_client.get("/v1/stats")
        data = resp.json()
        # Upstream is fake (502), but for non-streaming the stats still record
        assert data["total_requests"] == 1
        assert "SIMPLE" in data["by_tier"]
        assert data["by_mode"]["auto"] == 1
        assert sum(data["by_method"].values()) >= 1
        assert "by_transport" in data
        assert "by_cache_mode" in data
        assert "by_cache_family" in data
        assert "total_cache_breakpoints" in data
        assert data["avg_confidence"] > 0
        assert "avg_cache_hit_ratio" in data
        assert "avg_latency_ms" in data
        assert "total_baseline_cost" in data
        assert "total_savings_absolute" in data
        assert "total_cache_savings" in data
        assert "total_compaction_savings" in data
        assert "selection_modes" in data["selector"]
        assert "recent_feedback_changes" in data["selector"]["experience"]

    def test_recent_includes_transport_and_cache_strategy(self, stats_client: TestClient) -> None:
        resp = stats_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 502

        recent = stats_client.get("/v1/stats/recent").json()
        assert len(recent) == 1
        assert "mode" in recent[0]
        assert "transport" in recent[0]
        assert "cache_mode" in recent[0]
        assert "cache_family" in recent[0]
        assert "cache_breakpoints" in recent[0]
        assert "route_reasoning" in recent[0]
        assert recent[0]["status_code"] == 502
        assert recent[0]["error_code"] != ""
        assert recent[0]["error_stage"] in {"upstream_request", "upstream_response"}
        assert recent[0]["error_message"] != ""
        assert "feedback_action" in recent[0]

    def test_stats_include_selector_feedback_summary(self, stats_client: TestClient) -> None:
        resp = stats_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        request_id = resp.headers["x-uncommon-route-request-id"]
        fb = stats_client.post("/v1/feedback", json={"request_id": request_id, "signal": "weak"})
        assert fb.status_code == 200

        data = stats_client.get("/v1/stats").json()

        assert data["selector"]["experience"]["demoted_models"]
        assert data["selector"]["experience"]["recent_feedback_changes"][0]["last_feedback_signal"] == "weak"

    def test_stats_track_artifacts_and_input_reduction(self, tmp_path) -> None:
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
            artifact_store=ArtifactStore(root=tmp_path / "artifacts"),
            semantic_compressor=FakeSemanticCompressor(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        large_text = "\n".join(f"payload {i}" for i in range(4000))
        client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [
                {"role": "user", "content": "summarize this"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": large_text},
            ],
        })
        data = client.get("/v1/stats").json()
        assert data["total_artifacts_created"] == 1
        assert data["total_input_tokens_after"] < data["total_input_tokens_before"]
        assert data["avg_input_reduction_ratio"] > 0
        assert data["total_semantic_calls"] >= 1
        assert data["total_semantic_summaries"] >= 1

    def test_stats_track_semantic_quality_fallbacks(self, tmp_path) -> None:
        app = create_app(
            upstream="http://127.0.0.1:1/fake",
            spend_control=SpendControl(storage=InMemorySpendControlStorage()),
            route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
            artifact_store=ArtifactStore(root=tmp_path / "artifacts"),
            semantic_compressor=QualityFallbackSemanticCompressor(),
        )
        client = TestClient(app, raise_server_exceptions=False)
        large_text = "\n".join(f"payload {i}" for i in range(3000))

        client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [
                {"role": "user", "content": "extract the main failure"},
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

        data = client.get("/v1/stats").json()
        assert data["total_semantic_quality_fallbacks"] == 2

    def test_stats_capture_native_anthropic_transport(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            assert str(request.url) == "https://api.commonstack.ai/v1/messages"
            assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
            return httpx.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "anthropic/claude-sonnet-4.6",
                    "content": [{"type": "text", "text": "pong"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 18,
                        "cache_read_input_tokens": 1200,
                        "cache_creation_input_tokens": 120,
                        "output_tokens": 3,
                    },
                },
                headers={"content-type": "application/json"},
            )

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
        monkeypatch.setenv("UNCOMMON_ROUTE_API_KEY", "test-key")

        try:
            app = create_app(
                upstream="https://api.commonstack.ai/v1",
                spend_control=SpendControl(storage=InMemorySpendControlStorage()),
                route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
            )
            client = TestClient(app, raise_server_exceptions=False)

            resp = client.post("/v1/chat/completions", json={
                "model": "anthropic/claude-sonnet-4.6",
                "messages": [
                    {"role": "system", "content": "You are terse."},
                    {"role": "user", "content": "Reply with pong"},
                ],
            })

            assert resp.status_code == 200
            data = client.get("/v1/stats").json()
            assert data["by_transport"]["anthropic-messages"]["count"] == 1
            assert data["by_cache_mode"]["cache_control"]["count"] == 1
            assert data["by_cache_family"]["anthropic"]["count"] == 1
            assert data["total_cache_breakpoints"] >= 1
        finally:
            asyncio.run(async_client.aclose())

    def test_stats_reset(self, stats_client: TestClient) -> None:
        stats_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        resp = stats_client.post("/v1/stats", json={"action": "reset"})
        assert resp.status_code == 200
        assert resp.json()["reset"] is True

        data = stats_client.get("/v1/stats").json()
        assert data["total_requests"] == 0

    def test_stats_invalid_action(self, stats_client: TestClient) -> None:
        resp = stats_client.post("/v1/stats", json={"action": "explode"})
        assert resp.status_code == 400

    def test_health_includes_stats(self, stats_client: TestClient) -> None:
        data = stats_client.get("/health").json()
        assert "stats" in data
        assert data["stats"]["total_requests"] == 0

    def test_debug_not_recorded(self, stats_client: TestClient) -> None:
        """Debug requests should not appear in stats."""
        stats_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "/debug hello"}],
        })
        data = stats_client.get("/v1/stats").json()
        assert data["total_requests"] == 0

    def test_passthrough_not_recorded(self, stats_client: TestClient) -> None:
        """Non-virtual model requests should not appear in stats."""
        stats_client.post("/v1/chat/completions", json={
            "model": "some-other/model",
            "messages": [{"role": "user", "content": "hello"}],
        })
        data = stats_client.get("/v1/stats").json()
        assert data["total_requests"] == 0
