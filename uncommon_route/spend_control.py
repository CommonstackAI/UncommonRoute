"""Spend control — time-windowed spending limits.

Features:
  - Per-request limits (e.g., max $0.10 per call)
  - Hourly limits (e.g., max $3.00 per hour)
  - Daily limits (e.g., max $20.00 per day)
  - Session limits (e.g., max $5.00 per session)
  - Rolling windows (last 1h, last 24h)
  - Persistent storage (~/.uncommon-route/spending.json)
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from uncommon_route.paths import data_dir

HOUR_S = 3600
DAY_S = 86400

SpendWindow = Literal["per_request", "hourly", "daily", "session"]
_ALL_WINDOWS: list[SpendWindow] = ["per_request", "hourly", "daily", "session"]

_DATA_DIR = data_dir()


@dataclass
class SpendLimits:
    per_request: float | None = None
    hourly: float | None = None
    daily: float | None = None
    session: float | None = None


@dataclass
class SpendRecord:
    timestamp: float
    amount: float
    model: str | None = None
    action: str | None = None


@dataclass
class CheckResult:
    allowed: bool
    blocked_by: SpendWindow | None = None
    remaining: float | None = None
    reason: str | None = None
    reset_in_s: int | None = None


@dataclass
class SpendingStatus:
    limits: SpendLimits
    spent: dict[str, float]
    remaining: dict[str, float | None]
    calls: int


class SpendControlStorage(ABC):
    @abstractmethod
    def load(self) -> dict[str, Any] | None: ...

    @abstractmethod
    def save(self, data: dict[str, Any]) -> None: ...


class FileSpendControlStorage(SpendControlStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "spending.json")

    def load(self) -> dict[str, Any] | None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text())
                return self._validate(raw)
        except Exception:
            pass
        return None

    def save(self, data: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(data, indent=2, default=str))
            self._path.chmod(0o600)
        except Exception as exc:
            import sys
            print(f"[UncommonRoute] Failed to save spending data: {exc}", file=sys.stderr)

    @staticmethod
    def _validate(raw: dict) -> dict[str, Any]:
        limits: dict[str, float] = {}
        for key in ("per_request", "hourly", "daily", "session"):
            val = raw.get("limits", {}).get(key)
            if isinstance(val, (int, float)) and val > 0:
                limits[key] = float(val)
        history: list[dict] = []
        for r in raw.get("history", []):
            if isinstance(r, dict) and isinstance(r.get("timestamp"), (int, float)) and isinstance(r.get("amount"), (int, float)):
                history.append({
                    "timestamp": r["timestamp"],
                    "amount": r["amount"],
                    "model": r.get("model"),
                    "action": r.get("action"),
                })
        return {"limits": limits, "history": history}


class InMemorySpendControlStorage(SpendControlStorage):
    def __init__(self) -> None:
        self._data: dict[str, Any] | None = None

    def load(self) -> dict[str, Any] | None:
        return json.loads(json.dumps(self._data)) if self._data else None

    def save(self, data: dict[str, Any]) -> None:
        self._data = json.loads(json.dumps(data, default=str))


class SpendControl:
    """Time-windowed spending limiter with persistent storage."""

    def __init__(
        self,
        storage: SpendControlStorage | None = None,
        now_fn: Any = None,
    ) -> None:
        self._storage = storage or FileSpendControlStorage()
        self._now = now_fn or time.time
        self._limits = SpendLimits()
        self._history: list[SpendRecord] = []
        self._session_spent: float = 0.0
        self._session_calls: int = 0
        self._load()

    def set_limit(self, window: SpendWindow, amount: float) -> None:
        if amount <= 0 or not isinstance(amount, (int, float)):
            raise ValueError("Limit must be a positive number")
        setattr(self._limits, window, float(amount))
        self._save()

    def clear_limit(self, window: SpendWindow) -> None:
        setattr(self._limits, window, None)
        self._save()

    @property
    def limits(self) -> SpendLimits:
        return self._limits

    def check(self, estimated_cost: float, additional_committed: float = 0.0) -> CheckResult:
        """Check whether ``estimated_cost`` fits under every configured window.

        ``additional_committed`` covers in-flight reservations (estimated costs of
        concurrent requests that passed ``check`` but have not yet been settled).
        It is added to every windowed denominator but NOT to the per-request
        comparison, which stays strictly about a single call.
        """
        now = self._now()

        if self._limits.per_request is not None and estimated_cost > self._limits.per_request:
            return CheckResult(
                allowed=False,
                blocked_by="per_request",
                remaining=self._limits.per_request,
                reason=f"Per-request limit: ${estimated_cost:.4f} > ${self._limits.per_request:.2f} max",
            )

        if self._limits.hourly is not None:
            hourly_spent = self._window_total(now - HOUR_S, now) + additional_committed
            remaining = self._limits.hourly - hourly_spent
            if estimated_cost > remaining:
                oldest = next((r for r in self._history if r.timestamp >= now - HOUR_S), None)
                reset_in = int(oldest.timestamp + HOUR_S - now) if oldest else 0
                return CheckResult(
                    allowed=False, blocked_by="hourly", remaining=remaining,
                    reason=f"Hourly limit: ${hourly_spent + estimated_cost:.2f} > ${self._limits.hourly:.2f}",
                    reset_in_s=max(0, reset_in),
                )

        if self._limits.daily is not None:
            daily_spent = self._window_total(now - DAY_S, now) + additional_committed
            remaining = self._limits.daily - daily_spent
            if estimated_cost > remaining:
                oldest = next((r for r in self._history if r.timestamp >= now - DAY_S), None)
                reset_in = int(oldest.timestamp + DAY_S - now) if oldest else 0
                return CheckResult(
                    allowed=False, blocked_by="daily", remaining=remaining,
                    reason=f"Daily limit: ${daily_spent + estimated_cost:.2f} > ${self._limits.daily:.2f}",
                    reset_in_s=max(0, reset_in),
                )

        if self._limits.session is not None:
            remaining = self._limits.session - self._session_spent - additional_committed
            if estimated_cost > remaining:
                return CheckResult(
                    allowed=False, blocked_by="session", remaining=remaining,
                    reason=f"Session limit: ${self._session_spent + additional_committed + estimated_cost:.2f} > ${self._limits.session:.2f}",
                )

        return CheckResult(allowed=True)

    def record(self, amount: float, model: str | None = None, action: str | None = None) -> None:
        if amount < 0:
            raise ValueError("Amount must be non-negative")
        self._history.append(SpendRecord(
            timestamp=self._now(), amount=amount, model=model, action=action,
        ))
        self._session_spent += amount
        self._session_calls += 1
        self._cleanup()
        self._save()

    def get_spending(self, window: Literal["hourly", "daily", "session"]) -> float:
        now = self._now()
        if window == "hourly":
            return self._window_total(now - HOUR_S, now)
        if window == "daily":
            return self._window_total(now - DAY_S, now)
        return self._session_spent

    def get_remaining(self, window: Literal["hourly", "daily", "session"]) -> float | None:
        limit = getattr(self._limits, window, None)
        if limit is None:
            return None
        return max(0.0, limit - self.get_spending(window))

    def status(self) -> SpendingStatus:
        now = self._now()
        hourly = self._window_total(now - HOUR_S, now)
        daily = self._window_total(now - DAY_S, now)
        return SpendingStatus(
            limits=SpendLimits(
                per_request=self._limits.per_request,
                hourly=self._limits.hourly,
                daily=self._limits.daily,
                session=self._limits.session,
            ),
            spent={"hourly": hourly, "daily": daily, "session": self._session_spent},
            remaining={
                "hourly": (self._limits.hourly - hourly) if self._limits.hourly else None,
                "daily": (self._limits.daily - daily) if self._limits.daily else None,
                "session": (self._limits.session - self._session_spent) if self._limits.session else None,
            },
            calls=self._session_calls,
        )

    def history(self, limit: int | None = None) -> list[SpendRecord]:
        records = list(reversed(self._history))
        return records[:limit] if limit else records

    def reset_session(self) -> None:
        self._session_spent = 0.0
        self._session_calls = 0

    def _window_total(self, start: float, end: float) -> float:
        return sum(r.amount for r in self._history if start <= r.timestamp <= end)

    def _cleanup(self) -> None:
        cutoff = self._now() - DAY_S
        self._history = [r for r in self._history if r.timestamp >= cutoff]

    def _save(self) -> None:
        self._storage.save({
            "limits": {k: v for k, v in vars(self._limits).items() if v is not None},
            "history": [
                {"timestamp": r.timestamp, "amount": r.amount, "model": r.model, "action": r.action}
                for r in self._history
            ],
        })

    def _load(self) -> None:
        data = self._storage.load()
        if not data:
            return
        for key in ("per_request", "hourly", "daily", "session"):
            val = data.get("limits", {}).get(key)
            if val is not None:
                setattr(self._limits, key, float(val))
        for r in data.get("history", []):
            self._history.append(SpendRecord(
                timestamp=r["timestamp"], amount=r["amount"],
                model=r.get("model"), action=r.get("action"),
            ))
        self._cleanup()


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{(seconds + 59) // 60} min"
    hours = seconds // 3600
    mins = (seconds % 3600 + 59) // 60
    return f"{hours}h {mins}m" if mins > 0 else f"{hours}h"
