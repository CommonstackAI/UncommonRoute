"""Circuit breaker for model health management.

Tracks consecutive failures per model and temporarily removes
unhealthy models from the routing pool.

States:
  - closed: model is healthy, all requests go through
  - open: model has failed repeatedly, skip it for recovery_timeout_s
  - half-open: recovery period expired, allow one probe request
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT_S = 60.0
FALLBACK_STATUS_CODES = frozenset({400, 404, 422, 500, 502, 503, 504})


@dataclass
class ModelHealth:
    consecutive_failures: int = 0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    state: str = "closed"
    total_failures: int = 0
    total_successes: int = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure_at = time.time()
        if self.consecutive_failures >= FAILURE_THRESHOLD:
            self.state = "open"

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.total_successes += 1
        self.last_success_at = time.time()
        self.state = "closed"

    def available(self, recovery_s: float = RECOVERY_TIMEOUT_S) -> bool:
        if self.state != "open":
            return True
        if time.time() - self.last_failure_at > recovery_s:
            self.state = "half-open"
            return True
        return False


class CircuitBreakerRegistry:
    def __init__(self, recovery_timeout_s: float = RECOVERY_TIMEOUT_S) -> None:
        self._models: dict[str, ModelHealth] = {}
        self._recovery_timeout_s = recovery_timeout_s

    def record_failure(self, model: str) -> None:
        health = self._models.setdefault(model, ModelHealth())
        health.record_failure()

    def record_success(self, model: str) -> None:
        health = self._models.setdefault(model, ModelHealth())
        health.record_success()

    def is_available(self, model: str) -> bool:
        health = self._models.get(model)
        if health is None:
            return True
        return health.available(self._recovery_timeout_s)

    def filter_available(self, models: list[str]) -> list[str]:
        return [m for m in models if self.is_available(m)]

    def should_try_fallback(self, status_code: int, content: bytes) -> bool:
        if status_code not in FALLBACK_STATUS_CODES:
            return False
        if status_code in (500, 502, 503, 504):
            return True
        try:
            text = content.decode("utf-8", errors="replace").lower()
            return any(
                p in text
                for p in ("model not found", "model not available", "model does not exist", "unsupported model", "invalid model", "no such model")
            )
        except Exception:
            return False

    def status(self) -> dict[str, dict[str, object]]:
        return {
            model: {
                "state": health.state,
                "consecutive_failures": health.consecutive_failures,
                "total_failures": health.total_failures,
                "total_successes": health.total_successes,
                "available": health.available(self._recovery_timeout_s),
            }
            for model, health in self._models.items()
            if health.total_failures > 0 or health.state != "closed"
        }
