"""Tests for independent request trace persistence and query endpoints."""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

from uncommon_route.feedback import FeedbackCollector
from uncommon_route.proxy import create_app
from uncommon_route.spend_control import InMemorySpendControlStorage, SpendControl
from uncommon_route.stats import InMemoryRouteStatsStorage, RouteStats
from uncommon_route.traces import FileTraceStorage, InMemoryTraceStorage, RequestTrace, TraceStore

_ADMIN_HEADERS = {"authorization": "Bearer test-admin"}


@pytest.fixture
def trace_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("UNCOMMON_ROUTE_ADMIN_TOKEN", "test-admin")
    app = create_app(
        upstream="http://127.0.0.1:1/fake",
        spend_control=SpendControl(storage=InMemorySpendControlStorage()),
        route_stats=RouteStats(storage=InMemoryRouteStatsStorage()),
        feedback=FeedbackCollector(),
        trace_store=TraceStore(storage=InMemoryTraceStorage()),
    )
    return TestClient(app, raise_server_exceptions=False)


class TestTraceStore:
    def test_trace_store_persists_records_and_feedback(self, tmp_path) -> None:
        path = tmp_path / "traces.json"
        traces = TraceStore(storage=FileTraceStorage(path=path))
        traces.record(RequestTrace(
            timestamp=time.time(),
            request_id="req-trace-001",
            requested_model="uncommon-route/auto",
            model="moonshot/kimi-k2.5",
            status_code=502,
            mode="auto",
            tier="SIMPLE",
            decision_tier="SIMPLE",
            served_quality="economy",
            served_quality_target="balanced",
            served_quality_floor="economy",
            capability_lane="anthropic-tool-safe",
            method="pool",
            endpoint="chat_completions",
            is_virtual=True,
            prompt_preview="hello world",
            prompt_hash="abc123",
            route_reasoning="selected for latency",
            requested_transport="anthropic-messages",
            transport="openai-chat",
            transport_reason="provider does not advertise stable anthropic-native transport here",
            transport_preference_source="default-openai",
            error_code="connect_error",
            error_stage="upstream_request",
            error_message="Upstream unreachable",
        ))
        updated = traces.record_feedback(
            "req-trace-001",
            signal="weak",
            ok=True,
            action="updated",
            from_tier="SIMPLE",
            to_tier="MEDIUM",
            reason="quality issue",
        )

        assert updated is True

        reloaded = TraceStore(storage=FileTraceStorage(path=path))
        detail = reloaded.find("req-trace-001")
        assert detail is not None
        assert detail["requested_transport"] == "anthropic-messages"
        assert detail["transport"] == "openai-chat"
        assert detail["transport_reason"] == "provider does not advertise stable anthropic-native transport here"
        assert detail["transport_preference_source"] == "default-openai"
        assert detail["served_quality"] == "economy"
        assert detail["served_quality_target"] == "balanced"
        assert detail["capability_lane"] == "anthropic-tool-safe"
        assert detail["error_code"] == "connect_error"
        assert detail["feedback_action"] == "updated"
        assert detail["feedback_to_tier"] == "MEDIUM"
        assert reloaded.recent(limit=5, errors_only=True)[0]["request_id"] == "req-trace-001"


class TestTraceEndpoints:
    def test_traces_endpoint_captures_passthrough_failures(self, trace_client: TestClient) -> None:
        resp = trace_client.post("/v1/chat/completions", json={
            "model": "some-other/model",
            "messages": [{"role": "user", "content": "hello"}],
        })

        assert resp.status_code == 502
        request_id = resp.headers["x-uncommon-route-request-id"]

        listing = trace_client.get("/v1/traces?limit=10&errors_only=1", headers=_ADMIN_HEADERS)
        assert listing.status_code == 200
        payload = listing.json()
        assert payload["total_requests"] == 1
        assert payload["summary"]["passthrough_requests"] == 1
        assert payload["items"][0]["request_id"] == request_id
        assert payload["items"][0]["error_code"] != ""
        assert payload["items"][0]["attempts_payload"][0]["error_code"] == payload["items"][0]["error_code"]

        detail = trace_client.get(f"/v1/traces/{request_id}", headers=_ADMIN_HEADERS)
        assert detail.status_code == 200
        trace = detail.json()
        assert trace["is_virtual"] is False
        assert trace["mode"] == "passthrough"
        assert trace["status_code"] == 502
        assert trace["error_stage"] in {"upstream_request", "upstream_response"}

    def test_health_and_reset_include_trace_counts(self, trace_client: TestClient) -> None:
        virtual_resp = trace_client.post("/v1/chat/completions", json={
            "model": "uncommon-route/auto",
            "messages": [{"role": "user", "content": "hello"}],
        })
        passthrough_resp = trace_client.post("/v1/chat/completions", json={
            "model": "some-other/model",
            "messages": [{"role": "user", "content": "hello"}],
        })

        assert virtual_resp.status_code == 502
        assert passthrough_resp.status_code == 502

        health = trace_client.get("/health")
        assert health.status_code == 200
        trace_health = health.json()["traces"]
        assert trace_health["total_requests"] == 2
        assert trace_health["error_count"] == 2
        assert trace_health["virtual_requests"] == 1
        assert trace_health["passthrough_requests"] == 1

        reset = trace_client.post("/v1/stats", json={"action": "reset"})
        assert reset.status_code == 200
        assert reset.json()["traces_cleared"] == 2

        listing = trace_client.get("/v1/traces", headers=_ADMIN_HEADERS)
        assert listing.status_code == 200
        assert listing.json()["total_requests"] == 0
