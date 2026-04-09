import json
from uncommon_route.observability import RoutingMetrics, RoutingLogEntry


def test_metrics_increment():
    m = RoutingMetrics()
    m.record_routing(tier=0, model="deepseek-chat", method="direct", confidence=0.8)
    m.record_routing(tier=3, model="claude-opus", method="conservative", confidence=0.5)
    assert m.total_requests == 2
    assert m.requests_by_tier[0] == 1
    assert m.requests_by_tier[3] == 1
    assert m.conservative_count == 1


def test_metrics_snapshot():
    m = RoutingMetrics()
    m.record_routing(tier=0, model="test", method="direct", confidence=0.9)
    snap = m.snapshot()
    assert snap["total_requests"] == 1
    assert "avg_confidence" in snap


def test_log_entry_json():
    entry = RoutingLogEntry(
        request_id="req_123",
        signals={"metadata": {"tier": 0, "confidence": 0.85}},
        decision_tier=0,
        decision_confidence=0.82,
        method="direct",
        model="deepseek-chat",
    )
    j = entry.to_json()
    parsed = json.loads(j)
    assert parsed["request_id"] == "req_123"
    assert parsed["decision_tier"] == 0


def test_metrics_reset():
    m = RoutingMetrics()
    m.record_routing(tier=0, model="test", method="direct", confidence=0.9)
    m.reset()
    assert m.total_requests == 0
