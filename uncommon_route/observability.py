"""Structured logging and metrics for v2 routing decisions."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoutingLogEntry:
    request_id: str
    signals: dict[str, Any]
    decision_tier: int
    decision_confidence: float
    method: str
    model: str
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "ts": self.timestamp,
            "request_id": self.request_id,
            "signals": self.signals,
            "decision_tier": self.decision_tier,
            "decision_confidence": self.decision_confidence,
            "method": self.method,
            "model": self.model,
        }, ensure_ascii=False)


class RoutingMetrics:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.total_requests = 0
        self.requests_by_tier: dict[int, int] = defaultdict(int)
        self.requests_by_model: dict[str, int] = defaultdict(int)
        self.cascade_count = 0
        self.conservative_count = 0
        self.escalated_count = 0
        self._confidence_sum = 0.0
        self._signal_agreement_count = 0

    def record_routing(
        self,
        tier: int,
        model: str,
        method: str,
        confidence: float,
        signals_agreed: bool = True,
    ) -> None:
        self.total_requests += 1
        self.requests_by_tier[tier] += 1
        self.requests_by_model[model] += 1
        self._confidence_sum += confidence
        if method == "cascaded":
            self.cascade_count += 1
        elif method == "conservative":
            self.conservative_count += 1
        elif method == "escalated":
            self.escalated_count += 1
        if signals_agreed:
            self._signal_agreement_count += 1

    def snapshot(self) -> dict[str, Any]:
        avg_conf = self._confidence_sum / self.total_requests if self.total_requests else 0.0
        agreement = self._signal_agreement_count / self.total_requests if self.total_requests else 0.0
        return {
            "total_requests": self.total_requests,
            "requests_by_tier": dict(self.requests_by_tier),
            "requests_by_model": dict(self.requests_by_model),
            "cascade_count": self.cascade_count,
            "conservative_count": self.conservative_count,
            "escalated_count": self.escalated_count,
            "avg_confidence": round(avg_conf, 4),
            "signal_agreement_rate": round(agreement, 4),
        }
