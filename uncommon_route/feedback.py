"""Feedback-driven online learning for the routing classifier.

Collects implicit (3-strike escalation) and explicit (user) feedback
to incrementally update the Averaged Perceptron weights.

Design: zero user disruption.
  - Implicit: escalation auto-triggers weight update (fully transparent)
  - Explicit: optional POST /v1/feedback with request_id from response header
  - Passive: request_id delivered via x-uncommon-route-request-id header only

Safety rails:
  - Max 100 model updates per hour (prevents abuse / runaway feedback)
  - Context buffer persisted to disk (survives restarts)
  - Online weights saved to separate file (base model never overwritten)
  - Rollback to base model in one call
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

FeedbackSignal = Literal["weak", "strong", "ok"]

TIER_ORDER = ["SIMPLE", "MEDIUM", "COMPLEX"]


def _normalize_tier(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


@dataclass
class RequestContext:
    features: dict[str, float]
    tier: str
    timestamp: float
    model: str = ""
    mode: str = "auto"


@dataclass
class FeedbackResult:
    ok: bool
    action: str
    from_tier: str = ""
    to_tier: str = ""
    reason: str = ""


class FeedbackCollector:
    """Orchestrates feedback collection and online model updates.

    Stores compact feature vectors (no raw prompts) for recent requests.
    When feedback arrives, adjusts the Perceptron weights toward the
    corrected tier and periodically persists the updated model.
    """

    def __init__(
        self,
        max_updates_per_hour: int = 100,
        save_every: int = 10,
        model_experience: Any = None,
        now_fn: Any = None,
        buffer_path: Path | None = None,
    ) -> None:
        self._buffer: dict[str, RequestContext] = {}
        self._max_hourly = max_updates_per_hour
        self._save_every = save_every
        self._model_experience = model_experience
        self._now = now_fn or time.time
        self._update_ts: list[float] = []
        self._total_updates: int = 0
        self._since_save: int = 0
        self._buffer_path = buffer_path
        if self._buffer_path:
            self._load_buffer()

    # ─── Public API ───

    def capture(
        self,
        request_id: str,
        features: dict[str, float],
        tier: str,
        *,
        model: str = "",
        mode: str = "auto",
    ) -> None:
        """Buffer compact features for a routed request (no raw prompts stored)."""
        compact = {k: v for k, v in features.items() if not k.startswith("ngram_")}
        self._buffer[request_id] = RequestContext(
            features=compact,
            tier=_normalize_tier(tier),
            timestamp=self._now(),
            model=model,
            mode=mode,
        )
        self._save_buffer()

    def rebind_request(
        self,
        request_id: str,
        *,
        tier: str | None = None,
        model: str | None = None,
        mode: str | None = None,
    ) -> None:
        ctx = self._buffer.get(request_id)
        if ctx is None:
            return
        self._buffer[request_id] = RequestContext(
            features=ctx.features,
            tier=_normalize_tier(tier or ctx.tier),
            timestamp=ctx.timestamp,
            model=model or ctx.model,
            mode=mode or ctx.mode,
        )
        self._save_buffer()

    def submit(self, request_id: str, signal: FeedbackSignal) -> FeedbackResult:
        """Process explicit user feedback for a previous request."""
        ctx = self._buffer.pop(request_id, None)
        if ctx is not None:
            self._save_buffer()

        if ctx is None:
            return FeedbackResult(
                ok=False, action="expired",
                reason="request_id not found or expired",
            )

        target = _adjust_tier(ctx.tier, signal)
        if self._model_experience is not None and ctx.model:
            self._model_experience.record_feedback(
                ctx.model,
                ctx.mode,
                ctx.tier,
                signal,
            )

        if signal == "ok":
            self._do_update(ctx.features, ctx.tier)
            return FeedbackResult(
                ok=True, action="reinforced",
                from_tier=ctx.tier, to_tier=ctx.tier,
            )

        if target == ctx.tier:
            return FeedbackResult(
                ok=True, action="no_change",
                from_tier=ctx.tier, to_tier=ctx.tier,
                reason="already at tier boundary",
            )

        if not self._rate_ok():
            return FeedbackResult(
                ok=False, action="rate_limited",
                reason=f"max {self._max_hourly} updates/hour",
            )

        self._do_update(ctx.features, target)
        return FeedbackResult(
            ok=True, action="updated",
            from_tier=ctx.tier, to_tier=target,
        )

    def has_pending(self, request_id: str) -> bool:
        """Check if feedback can still be submitted for a request."""
        return request_id in self._buffer

    def rollback(self) -> bool:
        """Reset to base model, discard online weights."""
        from uncommon_route.router.classifier import rollback_online_model

        deleted = rollback_online_model()
        self._total_updates = 0
        self._since_save = 0
        self._update_ts.clear()
        return deleted

    # ─── Introspection ───

    @property
    def pending_count(self) -> int:
        return len(self._buffer)

    @property
    def total_updates(self) -> int:
        return self._total_updates

    @property
    def online_model_active(self) -> bool:
        from uncommon_route.router.classifier import _get_online_model_path

        return _get_online_model_path().exists()

    def status(self) -> dict[str, Any]:
        now = self._now()
        hourly = sum(1 for t in self._update_ts if now - t < 3600)
        return {
            "pending_contexts": self.pending_count,
            "total_online_updates": self._total_updates,
            "updates_last_hour": hourly,
            "online_model_active": self.online_model_active,
            "max_updates_per_hour": self._max_hourly,
        }

    def clear_pending(self) -> int:
        cleared = len(self._buffer)
        self._buffer.clear()
        self._save_buffer()
        return cleared

    # ─── Internals ───

    def _do_update(self, features: dict[str, float], correct_tier: str) -> None:
        from uncommon_route.router.classifier import save_online_model, update_model

        if not update_model(features, correct_tier):
            return
        now = self._now()
        self._total_updates += 1
        self._since_save += 1
        self._update_ts.append(now)
        self._update_ts = [t for t in self._update_ts if now - t < 3600]
        if self._since_save >= self._save_every:
            save_online_model()
            self._since_save = 0

    def _rate_ok(self) -> bool:
        now = self._now()
        hourly = sum(1 for t in self._update_ts if now - t < 3600)
        return hourly < self._max_hourly

    def _save_buffer(self) -> None:
        if not self._buffer_path:
            return
        try:
            self._buffer_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                rid: {
                    "features": ctx.features,
                    "tier": ctx.tier,
                    "timestamp": ctx.timestamp,
                    "model": ctx.model,
                    "mode": ctx.mode,
                }
                for rid, ctx in self._buffer.items()
            }
            self._buffer_path.write_text(json.dumps(data))
        except Exception:
            pass

    def _load_buffer(self) -> None:
        if not self._buffer_path:
            return
        try:
            if self._buffer_path.exists():
                data = json.loads(self._buffer_path.read_text())
                for rid, entry in data.items():
                    mode = entry.get("mode")
                    if not mode:
                        continue
                    self._buffer[rid] = RequestContext(
                        features=entry["features"],
                        tier=_normalize_tier(entry["tier"]),
                        timestamp=entry["timestamp"],
                        model=entry.get("model", ""),
                        mode=str(mode),
                    )
        except Exception:
            pass


def _adjust_tier(current: str, signal: FeedbackSignal) -> str:
    normalized = _normalize_tier(current)
    idx = TIER_ORDER.index(normalized) if normalized in TIER_ORDER else 1
    if signal == "weak":
        return TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]
    if signal == "strong":
        return TIER_ORDER[max(idx - 1, 0)]
    return normalized
