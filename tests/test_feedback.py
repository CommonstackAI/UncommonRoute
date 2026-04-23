"""Tests for feedback-driven online learning."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from starlette.testclient import TestClient

from uncommon_route.connections_store import ConnectionsStore, InMemoryConnectionsStorage
from uncommon_route.feedback import (
    FeedbackCollector,
    _adjust_tier,
)
from uncommon_route.proxy import create_app
from uncommon_route.router.classifier import extract_features
from uncommon_route.router.types import (
    CapabilityLane,
    RoutingDecision,
    RoutingFeatures,
    RoutingMode,
    ServedQuality,
    Tier,
)
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.stats import InMemoryRouteStatsStorage, RouteStats
from uncommon_route.traces import InMemoryTraceStorage, TraceStore

_ADMIN_HEADERS = {"authorization": "Bearer test-admin"}


def _dummy_features() -> dict[str, float]:
    return extract_features("explain quicksort in Python")


def _make_feedback_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    feedback: FeedbackCollector | None = None,
) -> tuple[TestClient, httpx.AsyncClient]:
    def fake_route(*args, **kwargs) -> RoutingDecision:
        features = kwargs.get("routing_features") or RoutingFeatures()
        return RoutingDecision(
            model="minimax/minimax-m2.5",
            tier=Tier.SIMPLE,
            capability_lane=features.capability_lane or CapabilityLane.GENERAL,
            served_quality=ServedQuality.ECONOMY,
            served_quality_target=ServedQuality.ECONOMY,
            served_quality_floor=ServedQuality.ECONOMY,
            continuity_quality_floor=features.continuity_quality_floor,
            mode=RoutingMode.AUTO,
            confidence=0.91,
            method="pool",
            reasoning="test feedback route",
            cost_estimate=0.001,
            baseline_cost=0.004,
            savings=0.75,
            raw_confidence=0.91,
            complexity=0.2,
            routing_features=features,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_feedback",
                "object": "chat.completion",
                "created": 1,
                "model": "minimax/minimax-m2.5",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            },
            headers={"content-type": "application/json"},
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("uncommon_route.proxy._get_client", lambda: async_client)
    monkeypatch.setattr("uncommon_route.proxy.route", fake_route)

    app = create_app(
        upstream="http://127.0.0.1:1/fake",
        spend_control=SpendControl(storage=InMemorySpendControlStorage()),
        route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
        feedback=feedback or FeedbackCollector(),
        trace_store=TraceStore(storage=InMemoryTraceStorage()),
        connections_store=ConnectionsStore(storage=InMemoryConnectionsStorage()),
    )
    return TestClient(app, raise_server_exceptions=False), async_client


class TestAdjustTier:
    def test_weak_moves_up(self) -> None:
        assert _adjust_tier("SIMPLE", "weak") == "MEDIUM"
        assert _adjust_tier("MEDIUM", "weak") == "COMPLEX"
        assert _adjust_tier("REASONING", "weak") == "COMPLEX"

    def test_weak_caps_at_complex(self) -> None:
        assert _adjust_tier("COMPLEX", "weak") == "COMPLEX"
        assert _adjust_tier("REASONING", "weak") == "COMPLEX"

    def test_strong_moves_down(self) -> None:
        assert _adjust_tier("COMPLEX", "strong") == "MEDIUM"
        assert _adjust_tier("MEDIUM", "strong") == "SIMPLE"

    def test_strong_caps_at_simple(self) -> None:
        assert _adjust_tier("SIMPLE", "strong") == "SIMPLE"

    def test_ok_keeps_same(self) -> None:
        assert _adjust_tier("MEDIUM", "ok") == "MEDIUM"


class TestFeedbackCollector:
    def test_capture_and_submit_weak(self) -> None:
        fc = FeedbackCollector()
        feats = _dummy_features()
        fc.capture("req1", feats, "SIMPLE")
        result = fc.submit("req1", "weak")
        assert result.ok
        assert result.action == "updated"
        assert result.from_tier == "SIMPLE"
        assert result.to_tier == "MEDIUM"

    def test_submit_expired(self) -> None:
        fc = FeedbackCollector()
        result = fc.submit("nonexistent", "weak")
        assert not result.ok
        assert result.action == "expired"

    def test_submit_ok_reinforces(self) -> None:
        fc = FeedbackCollector()
        fc.capture("req1", _dummy_features(), "MEDIUM")
        result = fc.submit("req1", "ok")
        assert result.ok
        assert result.action == "reinforced"
        assert result.from_tier == "MEDIUM"

    def test_submit_strong(self) -> None:
        fc = FeedbackCollector()
        fc.capture("req1", _dummy_features(), "COMPLEX")
        result = fc.submit("req1", "strong")
        assert result.ok
        assert result.action == "updated"
        assert result.to_tier == "MEDIUM"

    def test_no_change_at_boundary(self) -> None:
        fc = FeedbackCollector()
        fc.capture("req1", _dummy_features(), "COMPLEX")
        result = fc.submit("req1", "weak")
        assert result.ok
        assert result.action == "no_change"

    def test_buffer_persists_indefinitely(self) -> None:
        t = time.time()
        fc = FeedbackCollector(now_fn=lambda: t)
        fc.capture("old", _dummy_features(), "SIMPLE")
        assert fc.pending_count == 1
        fc._now = lambda: t + 30 * 86_400  # 30 days later
        fc.capture("new", _dummy_features(), "MEDIUM")
        assert fc.pending_count == 2
        assert "old" in fc._buffer

    def test_rate_limiting(self) -> None:
        t = time.time()
        fc = FeedbackCollector(max_updates_per_hour=2, now_fn=lambda: t)
        feats = _dummy_features()
        fc.capture("a", feats, "SIMPLE")
        fc.submit("a", "weak")
        fc.capture("b", feats, "SIMPLE")
        fc.submit("b", "weak")
        fc.capture("c", feats, "SIMPLE")
        result = fc.submit("c", "weak")
        assert not result.ok
        assert result.action == "rate_limited"

    def test_status(self) -> None:
        fc = FeedbackCollector()
        fc.capture("req1", _dummy_features(), "SIMPLE")
        s = fc.status()
        assert s["pending_contexts"] == 1
        assert s["total_online_updates"] == 0
        assert isinstance(s["online_model_active"], bool)

    def test_ngrams_stripped(self) -> None:
        fc = FeedbackCollector()
        feats = {"s_length": 0.5, "ngram_123": 0.1, "ngram_456": 0.2, "k_code": 0.3}
        fc.capture("req1", feats, "SIMPLE")
        stored = fc._buffer["req1"].features
        assert "ngram_123" not in stored
        assert "ngram_456" not in stored
        assert "s_length" in stored
        assert "k_code" in stored


@pytest.fixture
def fb_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UNCOMMON_ROUTE_ADMIN_TOKEN", "test-admin")
    client, async_client = _make_feedback_app(monkeypatch)
    try:
        yield client
    finally:
        client.close()
        asyncio.run(async_client.aclose())


class TestFeedbackEndpoint:
    def test_get_feedback_status(self, fb_client: TestClient) -> None:
        resp = fb_client.get("/v1/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pending_contexts"] == 0

    def test_request_id_in_headers(self, fb_client: TestClient) -> None:
        resp = fb_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert "x-uncommon-route-request-id" in resp.headers
        rid = resp.headers["x-uncommon-route-request-id"]
        assert len(rid) == 12

    def test_feedback_round_trip(self, fb_client: TestClient) -> None:
        resp = fb_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        rid = resp.headers["x-uncommon-route-request-id"]

        pending = fb_client.get("/v1/feedback").json()
        assert pending["pending_contexts"] >= 1

        fb = fb_client.post("/v1/feedback", json={
            "request_id": rid, "signal": "ok",
        })
        assert fb.status_code == 200
        assert fb.json()["action"] == "reinforced"

    def test_feedback_weak_updates(self, fb_client: TestClient) -> None:
        resp = fb_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        rid = resp.headers["x-uncommon-route-request-id"]

        fb = fb_client.post("/v1/feedback", json={
            "request_id": rid, "signal": "weak",
        })
        data = fb.json()
        assert data["ok"]
        assert data["action"] == "updated"
        assert data["total_updates"] >= 1

    def test_feedback_result_persists_in_recent(self, fb_client: TestClient) -> None:
        resp = fb_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        rid = resp.headers["x-uncommon-route-request-id"]

        fb = fb_client.post("/v1/feedback", json={
            "request_id": rid, "signal": "weak",
        })
        assert fb.status_code == 200

        recent = fb_client.get("/v1/stats/recent").json()
        assert recent[0]["request_id"] == rid
        assert recent[0]["feedback_pending"] is False
        assert recent[0]["feedback_action"] == "updated"
        assert recent[0]["feedback_signal"] == "weak"
        assert recent[0]["feedback_to_tier"] != ""

        trace = fb_client.get(f"/v1/traces/{rid}", headers=_ADMIN_HEADERS).json()
        assert trace["feedback_action"] == "updated"
        assert trace["feedback_signal"] == "weak"
        assert trace["feedback_to_tier"] != ""

    def test_recent_hides_closed_feedback_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        feedback = FeedbackCollector()
        client, async_client = _make_feedback_app(monkeypatch, feedback=feedback)
        try:
            resp = client.post("/v1/chat/completions", json={
                "model": "uncommon-route/auto",
                "messages": [{"role": "user", "content": "hello"}],
            })
            rid = resp.headers["x-uncommon-route-request-id"]
            assert feedback.has_pending(rid) is True

            feedback._buffer.clear()

            recent = client.get("/v1/stats/recent").json()
            assert recent == []
        finally:
            client.close()
            asyncio.run(async_client.aclose())

    def test_stats_reset_clears_pending_feedback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        feedback = FeedbackCollector()
        client, async_client = _make_feedback_app(monkeypatch, feedback=feedback)
        try:
            resp = client.post("/v1/chat/completions", json={
                "model": "uncommon-route/auto",
                "messages": [{"role": "user", "content": "hello"}],
            })
            rid = resp.headers["x-uncommon-route-request-id"]
            assert feedback.has_pending(rid) is True
            assert client.get("/health").json()["feedback"]["pending"] == 1

            reset = client.post("/v1/stats", json={"action": "reset"})
            assert reset.status_code == 200
            assert reset.json()["feedback_cleared"] == 1
            assert client.get("/health").json()["feedback"]["pending"] == 0
            assert client.get("/v1/stats/recent").json() == []
        finally:
            client.close()
            asyncio.run(async_client.aclose())

    def test_feedback_expired_request(self, fb_client: TestClient) -> None:
        fb = fb_client.post("/v1/feedback", json={
            "request_id": "nonexistent", "signal": "weak",
        })
        assert fb.status_code == 404
        assert fb.json()["action"] == "expired"

    def test_feedback_bad_params(self, fb_client: TestClient) -> None:
        fb = fb_client.post("/v1/feedback", json={
            "request_id": "abc", "signal": "invalid",
        })
        assert fb.status_code == 400

    def test_rollback(self, fb_client: TestClient) -> None:
        fb = fb_client.post("/v1/feedback", json={"action": "rollback"}, headers=_ADMIN_HEADERS)
        assert fb.status_code == 200
        assert "rolled_back" in fb.json()

    def test_passthrough_has_request_id_and_trace(self, fb_client: TestClient) -> None:
        resp = fb_client.post("/v1/chat/completions", json={
            "model": "some-other/model",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert "x-uncommon-route-request-id" in resp.headers
        trace = fb_client.get(
            f"/v1/traces/{resp.headers['x-uncommon-route-request-id']}",
            headers=_ADMIN_HEADERS,
        ).json()
        assert trace["is_virtual"] is False
        assert trace["mode"] == "passthrough"
        assert trace["error_code"] == ""
        assert trace["attempts_payload"][0]["error_code"] == ""
        assert trace["attempts_payload"][0]["selected_model"] == "some-other/model"

    def test_health_includes_feedback(self, fb_client: TestClient) -> None:
        data = fb_client.get("/health").json()
        assert "feedback" in data
        assert data["feedback"]["pending"] == 0
