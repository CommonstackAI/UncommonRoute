"""Route statistics — records every routing decision for analytics.

Tracks tier distribution, model usage, confidence, savings, and latency
across all routed requests. Persistent storage with 7-day rolling window.

Storage: ~/.uncommon-route/stats.json
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from uncommon_route.paths import data_dir
from uncommon_route.router.config import DEFAULT_MODEL_PRICING
from uncommon_route.router.types import ModelPricing

RETENTION_S = 7 * 86_400  # 7 days
MAX_RECORDS = 10_000

RouteMethod = Literal["pool", "fallback", "passthrough", "override"]

_DATA_DIR = data_dir()


def _normalize_tier_label(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _normalize_served_quality(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"economy", "balanced", "premium"} else ""


@dataclass
class RouteRecord:
    timestamp: float
    model: str
    tier: str
    confidence: float
    method: RouteMethod
    estimated_cost: float
    raw_confidence: float = 0.0
    confidence_source: str = ""
    calibration_version: str = ""
    calibration_sample_count: int = 0
    calibration_temperature: float = 1.0
    calibration_applied_tags: tuple[str, ...] | list[str] | None = None
    baseline_cost: float = 0.0
    requested_model: str = ""
    mode: str = "auto"
    decision_tier: str = ""
    served_quality: str = ""
    served_quality_target: str = ""
    served_quality_floor: str = ""
    capability_lane: str = ""
    actual_cost: float | None = None
    savings: float = 0.0
    latency_us: float = 0.0
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    cache_hit_ratio: float = 0.0
    transport: str = "openai-chat"
    cache_mode: str = "none"
    cache_family: str = "generic"
    cache_breakpoints: int = 0
    input_tokens_before: int = 0
    input_tokens_after: int = 0
    artifacts_created: int = 0
    compacted_messages: int = 0
    semantic_summaries: int = 0
    semantic_calls: int = 0
    semantic_failures: int = 0
    semantic_quality_fallbacks: int = 0
    checkpoint_created: bool = False
    rehydrated_artifacts: int = 0
    sidechannel_estimated_cost: float = 0.0
    sidechannel_actual_cost: float | None = None
    session_id: str | None = None
    step_type: str = "general"
    fallback_reason: str = ""
    streaming: bool = False
    request_id: str = ""
    prompt_preview: str = ""
    complexity: float = 0.33
    route_reasoning: str = ""
    constraint_tags: list[str] | None = None
    hint_tags: list[str] | None = None
    feature_tags: list[str] | None = None
    answer_depth: str = "standard"
    routing_features_payload: dict[str, Any] | None = None
    fallback_chain_payload: list[dict[str, Any]] | None = None
    candidate_scores_payload: list[dict[str, Any]] | None = None
    status_code: int = 0
    error_code: str = ""
    error_stage: str = ""
    error_message: str = ""
    feedback_signal: str = ""
    feedback_ok: bool = False
    feedback_action: str = ""
    feedback_from_tier: str = ""
    feedback_to_tier: str = ""
    feedback_reason: str = ""
    feedback_submitted_at: float = 0.0


@dataclass
class TierSummary:
    count: int = 0
    avg_confidence: float = 0.0
    avg_savings: float = 0.0
    total_cost: float = 0.0


@dataclass
class ModelSummary:
    count: int = 0
    total_cost: float = 0.0


@dataclass
class StatsSummary:
    total_requests: int
    time_range_s: float
    by_tier: dict[str, TierSummary]
    by_decision_tier: dict[str, int]
    by_served_quality: dict[str, int]
    by_capability_lane: dict[str, int]
    by_model: dict[str, ModelSummary]
    by_transport: dict[str, ModelSummary]
    by_cache_mode: dict[str, ModelSummary]
    by_cache_family: dict[str, ModelSummary]
    by_mode: dict[str, int]
    by_method: dict[str, int]
    complexity_distribution: dict[str, int]
    avg_confidence: float
    avg_savings: float
    avg_latency_us: float
    avg_input_reduction_ratio: float
    avg_cache_hit_ratio: float
    total_estimated_cost: float
    total_baseline_cost: float
    total_actual_cost: float
    total_savings_absolute: float
    total_savings_ratio: float
    total_cache_savings: float
    total_compaction_savings: float
    total_usage_input_tokens: int
    total_usage_output_tokens: int
    total_cache_read_input_tokens: int
    total_cache_write_input_tokens: int
    total_cache_breakpoints: int
    total_input_tokens_before: int
    total_input_tokens_after: int
    total_artifacts_created: int
    total_compacted_messages: int
    total_semantic_summaries: int
    total_semantic_calls: int
    total_semantic_failures: int
    total_semantic_quality_fallbacks: int
    total_checkpoints_created: int
    total_rehydrated_artifacts: int


# ─── Storage abstraction ───


class RouteStatsStorage(ABC):
    @abstractmethod
    def load(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save(self, records: list[dict[str, Any]]) -> None: ...


class FileRouteStatsStorage(RouteStatsStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "stats.json")

    def load(self) -> list[dict[str, Any]]:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def save(self, records: list[dict[str, Any]]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(records, default=str))
            self._path.chmod(0o600)
        except Exception as exc:
            import sys
            print(f"[UncommonRoute] Failed to save stats: {exc}", file=sys.stderr)


class InMemoryRouteStatsStorage(RouteStatsStorage):
    def __init__(self) -> None:
        self._data: list[dict[str, Any]] = []

    def load(self) -> list[dict[str, Any]]:
        return list(self._data)

    def save(self, records: list[dict[str, Any]]) -> None:
        self._data = list(records)


# ─── Collector ───


def _effective_cost(r: RouteRecord) -> float:
    main_cost = r.actual_cost if r.actual_cost is not None else r.estimated_cost
    side_cost = r.sidechannel_actual_cost if r.sidechannel_actual_cost is not None else r.sidechannel_estimated_cost
    return main_cost + side_cost


def _baseline_cost(r: RouteRecord) -> float:
    if r.baseline_cost > 0:
        return r.baseline_cost
    main_estimated = max(0.0, r.estimated_cost - r.sidechannel_estimated_cost)
    if main_estimated <= 0:
        return 0.0
    if 0.0 <= r.savings < 0.999999:
        denominator = 1.0 - r.savings
        if denominator > 0:
            return main_estimated / denominator
    if r.savings == 0.0:
        return main_estimated
    return 0.0


def _get_stats_pricing() -> dict[str, ModelPricing]:
    """Use dynamic pricing if available, otherwise static fallback."""
    from uncommon_route.proxy import _active_pricing
    return _active_pricing or DEFAULT_MODEL_PRICING


def _cache_savings(r: RouteRecord) -> float:
    pricing = _get_stats_pricing().get(r.model)
    if pricing is None:
        return 0.0
    cached_input_price = pricing.cached_input_price if pricing.cached_input_price is not None else pricing.input_price
    cache_write_price = pricing.cache_write_price if pricing.cache_write_price is not None else pricing.input_price
    read_delta = ((pricing.input_price - cached_input_price) * r.cache_read_input_tokens) / 1_000_000
    write_delta = ((pricing.input_price - cache_write_price) * r.cache_write_input_tokens) / 1_000_000
    return read_delta + write_delta


def _compaction_savings(r: RouteRecord) -> float:
    if r.input_tokens_before <= r.input_tokens_after:
        return 0.0
    pricing = _get_stats_pricing().get(r.model)
    if pricing is None:
        return 0.0
    reduced_tokens = r.input_tokens_before - r.input_tokens_after
    return (reduced_tokens / 1_000_000) * pricing.input_price


class RouteStats:
    """Route-level statistics collector with persistent storage."""

    def __init__(
        self,
        storage: RouteStatsStorage | None = None,
        now_fn: Any = None,
    ) -> None:
        self._storage = storage or FileRouteStatsStorage()
        self._now = now_fn or time.time
        self._records: list[RouteRecord] = []
        self._load()

    def record(self, rec: RouteRecord) -> None:
        rec.tier = _normalize_tier_label(rec.tier)
        rec.decision_tier = _normalize_tier_label(rec.decision_tier) if rec.decision_tier else ""
        rec.served_quality = _normalize_served_quality(rec.served_quality)
        rec.served_quality_target = _normalize_served_quality(rec.served_quality_target)
        rec.served_quality_floor = _normalize_served_quality(rec.served_quality_floor)
        rec.capability_lane = str(rec.capability_lane or "").strip().lower()
        rec.feedback_from_tier = _normalize_tier_label(rec.feedback_from_tier) if rec.feedback_from_tier else ""
        rec.feedback_to_tier = _normalize_tier_label(rec.feedback_to_tier) if rec.feedback_to_tier else ""
        self._records.append(rec)
        self._cleanup()
        self._save()

    def record_feedback(
        self,
        request_id: str,
        *,
        signal: str,
        ok: bool,
        action: str,
        from_tier: str = "",
        to_tier: str = "",
        reason: str = "",
    ) -> bool:
        for record in reversed(self._records):
            if record.request_id != request_id:
                continue
            record.feedback_signal = signal
            record.feedback_ok = ok
            record.feedback_action = action
            record.feedback_from_tier = _normalize_tier_label(from_tier) if from_tier else ""
            record.feedback_to_tier = _normalize_tier_label(to_tier) if to_tier else ""
            record.feedback_reason = reason
            record.feedback_submitted_at = self._now()
            self._save()
            return True
        return False

    def history(self, limit: int | None = None) -> list[RouteRecord]:
        records = list(reversed(self._records))
        return records[:limit] if limit else records

    def export_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        records = self.history(limit=limit)
        return [_record_payload(record) for record in records]

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        """Most recent routed requests that carry a request_id (for feedback)."""
        records = [r for r in reversed(self._records) if r.request_id]
        return [
            {
                "request_id": r.request_id,
                "timestamp": r.timestamp,
                "mode": r.mode,
                "model": r.model,
                "tier": _normalize_tier_label(r.tier),
                "decision_tier": _normalize_tier_label(r.decision_tier or r.tier),
                "served_quality": _normalize_served_quality(r.served_quality),
                "served_quality_target": _normalize_served_quality(r.served_quality_target),
                "served_quality_floor": _normalize_served_quality(r.served_quality_floor),
                "capability_lane": str(r.capability_lane or "").strip().lower(),
                "method": r.method,
                "cost": _effective_cost(r),
                "savings": r.savings,
                "raw_confidence": r.raw_confidence,
                "confidence_source": r.confidence_source,
                "transport": r.transport,
                "cache_mode": r.cache_mode,
                "cache_family": r.cache_family,
                "cache_breakpoints": r.cache_breakpoints,
                "cache_hit_ratio": r.cache_hit_ratio,
                "cache_read_input_tokens": r.cache_read_input_tokens,
                "input_tokens_before": r.input_tokens_before,
                "input_tokens_after": r.input_tokens_after,
                "artifacts_created": r.artifacts_created,
                "prompt_preview": r.prompt_preview,
                "complexity": getattr(r, "complexity", 0.33),
                "route_reasoning": r.route_reasoning,
                "constraint_tags": list(r.constraint_tags or []),
                "hint_tags": list(r.hint_tags or []),
                "feature_tags": list(r.feature_tags or []),
                "answer_depth": r.answer_depth,
                "status_code": r.status_code,
                "error_code": r.error_code,
                "error_stage": r.error_stage,
                "error_message": r.error_message,
                "feedback_signal": r.feedback_signal,
                "feedback_ok": r.feedback_ok,
                "feedback_action": r.feedback_action,
                "feedback_from_tier": r.feedback_from_tier,
                "feedback_to_tier": r.feedback_to_tier,
                "feedback_reason": r.feedback_reason,
                "feedback_submitted_at": r.feedback_submitted_at,
            }
            for r in records[:limit]
        ]

    def summary(self) -> StatsSummary:
        if not self._records:
            return StatsSummary(
                total_requests=0, time_range_s=0.0,
                by_tier={}, by_decision_tier={}, by_served_quality={}, by_capability_lane={}, by_model={},
                by_transport={}, by_cache_mode={}, by_cache_family={},
                by_mode={}, by_method={}, complexity_distribution={},
                avg_confidence=0.0, avg_savings=0.0, avg_latency_us=0.0,
                avg_input_reduction_ratio=0.0, avg_cache_hit_ratio=0.0,
                total_estimated_cost=0.0, total_baseline_cost=0.0, total_actual_cost=0.0,
                total_savings_absolute=0.0, total_savings_ratio=0.0,
                total_cache_savings=0.0, total_compaction_savings=0.0,
                total_usage_input_tokens=0, total_usage_output_tokens=0,
                total_cache_read_input_tokens=0, total_cache_write_input_tokens=0,
                total_cache_breakpoints=0,
                total_input_tokens_before=0, total_input_tokens_after=0,
                total_artifacts_created=0, total_compacted_messages=0,
                total_semantic_summaries=0, total_semantic_calls=0,
                total_semantic_failures=0, total_semantic_quality_fallbacks=0,
                total_checkpoints_created=0,
                total_rehydrated_artifacts=0,
            )

        now = self._now()
        oldest = min(r.timestamp for r in self._records)
        n = len(self._records)

        tier_groups: dict[str, list[RouteRecord]] = {}
        model_groups: dict[str, list[RouteRecord]] = {}
        transport_groups: dict[str, list[RouteRecord]] = {}
        cache_mode_groups: dict[str, list[RouteRecord]] = {}
        cache_family_groups: dict[str, list[RouteRecord]] = {}
        mode_counts: dict[str, int] = {}
        decision_tier_counts: dict[str, int] = {}
        served_quality_counts: dict[str, int] = {}
        capability_lane_counts: dict[str, int] = {}
        method_counts: dict[str, int] = {}
        complexity_dist = {"simple": 0, "medium": 0, "complex": 0}

        for r in self._records:
            tier = _normalize_tier_label(r.tier)
            tier_groups.setdefault(tier, []).append(r)
            model_groups.setdefault(r.model, []).append(r)
            transport_groups.setdefault(r.transport, []).append(r)
            cache_mode_groups.setdefault(r.cache_mode, []).append(r)
            cache_family_groups.setdefault(r.cache_family, []).append(r)
            mode_counts[r.mode] = mode_counts.get(r.mode, 0) + 1
            decision_tier = _normalize_tier_label(r.decision_tier or r.tier)
            decision_tier_counts[decision_tier] = decision_tier_counts.get(decision_tier, 0) + 1
            if r.served_quality:
                served_quality_counts[r.served_quality] = served_quality_counts.get(r.served_quality, 0) + 1
            if r.capability_lane:
                capability_lane_counts[r.capability_lane] = capability_lane_counts.get(r.capability_lane, 0) + 1
            method_counts[r.method] = method_counts.get(r.method, 0) + 1
            c = getattr(r, "complexity", 0.33)
            if c < 0.33:
                complexity_dist["simple"] += 1
            elif c < 0.67:
                complexity_dist["medium"] += 1
            else:
                complexity_dist["complex"] += 1

        by_tier: dict[str, TierSummary] = {}
        for tier, recs in tier_groups.items():
            cnt = len(recs)
            by_tier[tier] = TierSummary(
                count=cnt,
                avg_confidence=sum(r.confidence for r in recs) / cnt,
                avg_savings=sum(r.savings for r in recs) / cnt,
                total_cost=sum(_effective_cost(r) for r in recs),
            )

        by_model: dict[str, ModelSummary] = {}
        for model, recs in model_groups.items():
            by_model[model] = ModelSummary(
                count=len(recs),
                total_cost=sum(_effective_cost(r) for r in recs),
            )

        by_transport: dict[str, ModelSummary] = {}
        for transport, recs in transport_groups.items():
            by_transport[transport] = ModelSummary(
                count=len(recs),
                total_cost=sum(_effective_cost(r) for r in recs),
            )

        by_cache_mode: dict[str, ModelSummary] = {}
        for cache_mode, recs in cache_mode_groups.items():
            by_cache_mode[cache_mode] = ModelSummary(
                count=len(recs),
                total_cost=sum(_effective_cost(r) for r in recs),
            )

        by_cache_family: dict[str, ModelSummary] = {}
        for cache_family, recs in cache_family_groups.items():
            by_cache_family[cache_family] = ModelSummary(
                count=len(recs),
                total_cost=sum(_effective_cost(r) for r in recs),
            )

        total_before = sum(r.input_tokens_before for r in self._records)
        total_after = sum(r.input_tokens_after for r in self._records)
        ratios = [
            (r.input_tokens_before - r.input_tokens_after) / r.input_tokens_before
            for r in self._records
            if r.input_tokens_before > 0
        ]
        total_est = sum(r.estimated_cost for r in self._records)
        total_baseline = sum(_baseline_cost(r) for r in self._records)
        total_act = sum(_effective_cost(r) for r in self._records)
        total_cache_savings = sum(_cache_savings(r) for r in self._records)
        total_compaction_savings = sum(_compaction_savings(r) for r in self._records)
        total_savings_absolute = total_baseline - total_act
        total_savings_ratio = (total_savings_absolute / total_baseline) if total_baseline > 0 else 0.0

        return StatsSummary(
            total_requests=n,
            time_range_s=now - oldest,
            by_tier=by_tier,
            by_decision_tier=decision_tier_counts,
            by_served_quality=served_quality_counts,
            by_capability_lane=capability_lane_counts,
            by_model=by_model,
            by_transport=by_transport,
            by_cache_mode=by_cache_mode,
            by_cache_family=by_cache_family,
            by_mode=mode_counts,
            by_method=method_counts,
            complexity_distribution=complexity_dist,
            avg_confidence=sum(r.confidence for r in self._records) / n,
            avg_savings=sum(r.savings for r in self._records) / n,
            avg_latency_us=sum(r.latency_us for r in self._records) / n,
            avg_input_reduction_ratio=(sum(ratios) / len(ratios)) if ratios else 0.0,
            avg_cache_hit_ratio=sum(r.cache_hit_ratio for r in self._records) / n,
            total_estimated_cost=total_est,
            total_baseline_cost=total_baseline,
            total_actual_cost=total_act,
            total_savings_absolute=total_savings_absolute,
            total_savings_ratio=total_savings_ratio,
            total_cache_savings=total_cache_savings,
            total_compaction_savings=total_compaction_savings,
            total_usage_input_tokens=sum(r.usage_input_tokens for r in self._records),
            total_usage_output_tokens=sum(r.usage_output_tokens for r in self._records),
            total_cache_read_input_tokens=sum(r.cache_read_input_tokens for r in self._records),
            total_cache_write_input_tokens=sum(r.cache_write_input_tokens for r in self._records),
            total_cache_breakpoints=sum(r.cache_breakpoints for r in self._records),
            total_input_tokens_before=total_before,
            total_input_tokens_after=total_after,
            total_artifacts_created=sum(r.artifacts_created for r in self._records),
            total_compacted_messages=sum(r.compacted_messages for r in self._records),
            total_semantic_summaries=sum(r.semantic_summaries for r in self._records),
            total_semantic_calls=sum(r.semantic_calls for r in self._records),
            total_semantic_failures=sum(r.semantic_failures for r in self._records),
            total_semantic_quality_fallbacks=sum(r.semantic_quality_fallbacks for r in self._records),
            total_checkpoints_created=sum(1 for r in self._records if r.checkpoint_created),
            total_rehydrated_artifacts=sum(r.rehydrated_artifacts for r in self._records),
        )

    def reset(self) -> None:
        self._records.clear()
        self._save()

    @property
    def count(self) -> int:
        return len(self._records)

    def _cleanup(self) -> None:
        cutoff = self._now() - RETENTION_S
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        if len(self._records) > MAX_RECORDS:
            self._records = self._records[-MAX_RECORDS:]

    def _save(self) -> None:
        self._storage.save([_record_payload(r) for r in self._records])

    def _load(self) -> None:
        for r in self._storage.load():
            if not isinstance(r, dict) or "timestamp" not in r:
                continue
            self._records.append(RouteRecord(
                timestamp=r["timestamp"],
                requested_model=r.get("requested_model", ""),
                mode=r.get("mode", "auto"),
                model=r.get("model", ""),
                tier=_normalize_tier_label(r.get("tier", "")),
                decision_tier=_normalize_tier_label(r.get("decision_tier", "")) if r.get("decision_tier", "") else "",
                served_quality=_normalize_served_quality(r.get("served_quality", "")),
                served_quality_target=_normalize_served_quality(r.get("served_quality_target", "")),
                served_quality_floor=_normalize_served_quality(r.get("served_quality_floor", "")),
                capability_lane=str(r.get("capability_lane", "") or "").strip().lower(),
                confidence=r.get("confidence", 0.0),
                raw_confidence=r.get("raw_confidence", r.get("confidence", 0.0)),
                confidence_source=r.get("confidence_source", ""),
                calibration_version=r.get("calibration_version", ""),
                calibration_sample_count=r.get("calibration_sample_count", 0),
                calibration_temperature=r.get("calibration_temperature", 1.0),
                calibration_applied_tags=list(r.get("calibration_applied_tags", []) or []),
                method=r.get("method", "pool"),
                estimated_cost=r.get("estimated_cost", 0.0),
                baseline_cost=r.get("baseline_cost", 0.0),
                actual_cost=r.get("actual_cost"),
                savings=r.get("savings", 0.0),
                latency_us=r.get("latency_us", 0.0),
                usage_input_tokens=r.get("usage_input_tokens", 0),
                usage_output_tokens=r.get("usage_output_tokens", 0),
                cache_read_input_tokens=r.get("cache_read_input_tokens", 0),
                cache_write_input_tokens=r.get("cache_write_input_tokens", 0),
                cache_hit_ratio=r.get("cache_hit_ratio", 0.0),
                transport=r.get("transport", "openai-chat"),
                cache_mode=r.get("cache_mode", "none"),
                cache_family=r.get("cache_family", "generic"),
                cache_breakpoints=r.get("cache_breakpoints", 0),
                input_tokens_before=r.get("input_tokens_before", 0),
                input_tokens_after=r.get("input_tokens_after", 0),
                artifacts_created=r.get("artifacts_created", 0),
                compacted_messages=r.get("compacted_messages", 0),
                semantic_summaries=r.get("semantic_summaries", 0),
                semantic_calls=r.get("semantic_calls", 0),
                semantic_failures=r.get("semantic_failures", 0),
                semantic_quality_fallbacks=r.get("semantic_quality_fallbacks", 0),
                checkpoint_created=r.get("checkpoint_created", False),
                rehydrated_artifacts=r.get("rehydrated_artifacts", 0),
                sidechannel_estimated_cost=r.get("sidechannel_estimated_cost", 0.0),
                sidechannel_actual_cost=r.get("sidechannel_actual_cost"),
                session_id=r.get("session_id"),
                step_type=r.get("step_type", "general"),
                fallback_reason=r.get("fallback_reason", ""),
                streaming=r.get("streaming", False),
                request_id=r.get("request_id", ""),
                prompt_preview=r.get("prompt_preview", ""),
                complexity=r.get("complexity", 0.33),
                route_reasoning=r.get("route_reasoning", ""),
                constraint_tags=list(r.get("constraint_tags", []) or []),
                hint_tags=list(r.get("hint_tags", []) or []),
                feature_tags=list(r.get("feature_tags", []) or []),
                answer_depth=r.get("answer_depth", "standard"),
                routing_features_payload=dict(r.get("routing_features_payload", {}) or {}),
                fallback_chain_payload=list(r.get("fallback_chain_payload", []) or []),
                candidate_scores_payload=list(r.get("candidate_scores_payload", []) or []),
                status_code=int(r.get("status_code", 0) or 0),
                error_code=r.get("error_code", ""),
                error_stage=r.get("error_stage", ""),
                error_message=r.get("error_message", ""),
                feedback_signal=r.get("feedback_signal", ""),
                feedback_ok=r.get("feedback_ok", False),
                feedback_action=r.get("feedback_action", ""),
                feedback_from_tier=_normalize_tier_label(r.get("feedback_from_tier", "")) if r.get("feedback_from_tier", "") else "",
                feedback_to_tier=_normalize_tier_label(r.get("feedback_to_tier", "")) if r.get("feedback_to_tier", "") else "",
                feedback_reason=r.get("feedback_reason", ""),
                feedback_submitted_at=r.get("feedback_submitted_at", 0.0),
            ))
        self._cleanup()


def _record_payload(r: RouteRecord) -> dict[str, Any]:
    return {
        "timestamp": r.timestamp,
        "requested_model": r.requested_model,
        "mode": r.mode,
        "model": r.model,
        "tier": _normalize_tier_label(r.tier),
        "decision_tier": _normalize_tier_label(r.decision_tier) if r.decision_tier else "",
        "served_quality": _normalize_served_quality(r.served_quality),
        "served_quality_target": _normalize_served_quality(r.served_quality_target),
        "served_quality_floor": _normalize_served_quality(r.served_quality_floor),
        "capability_lane": str(r.capability_lane or "").strip().lower(),
        "confidence": r.confidence,
        "raw_confidence": r.raw_confidence,
        "confidence_source": r.confidence_source,
        "calibration_version": r.calibration_version,
        "calibration_sample_count": r.calibration_sample_count,
        "calibration_temperature": r.calibration_temperature,
        "calibration_applied_tags": list(r.calibration_applied_tags or []),
        "method": r.method,
        "estimated_cost": r.estimated_cost,
        "baseline_cost": r.baseline_cost,
        "actual_cost": r.actual_cost,
        "savings": r.savings,
        "latency_us": r.latency_us,
        "usage_input_tokens": r.usage_input_tokens,
        "usage_output_tokens": r.usage_output_tokens,
        "cache_read_input_tokens": r.cache_read_input_tokens,
        "cache_write_input_tokens": r.cache_write_input_tokens,
        "cache_hit_ratio": r.cache_hit_ratio,
        "transport": r.transport,
        "cache_mode": r.cache_mode,
        "cache_family": r.cache_family,
        "cache_breakpoints": r.cache_breakpoints,
        "input_tokens_before": r.input_tokens_before,
        "input_tokens_after": r.input_tokens_after,
        "artifacts_created": r.artifacts_created,
        "compacted_messages": r.compacted_messages,
        "semantic_summaries": r.semantic_summaries,
        "semantic_calls": r.semantic_calls,
        "semantic_failures": r.semantic_failures,
        "semantic_quality_fallbacks": r.semantic_quality_fallbacks,
        "checkpoint_created": r.checkpoint_created,
        "rehydrated_artifacts": r.rehydrated_artifacts,
        "sidechannel_estimated_cost": r.sidechannel_estimated_cost,
        "sidechannel_actual_cost": r.sidechannel_actual_cost,
        "session_id": r.session_id,
        "step_type": r.step_type,
        "fallback_reason": r.fallback_reason,
        "streaming": r.streaming,
        "request_id": r.request_id,
        "prompt_preview": r.prompt_preview,
        "complexity": r.complexity,
        "route_reasoning": r.route_reasoning,
        "constraint_tags": list(r.constraint_tags or []),
        "hint_tags": list(r.hint_tags or []),
        "feature_tags": list(r.feature_tags or []),
        "answer_depth": r.answer_depth,
        "routing_features_payload": dict(r.routing_features_payload or {}),
        "fallback_chain_payload": list(r.fallback_chain_payload or []),
        "candidate_scores_payload": list(r.candidate_scores_payload or []),
        "status_code": r.status_code,
        "error_code": r.error_code,
        "error_stage": r.error_stage,
        "error_message": r.error_message,
        "feedback_signal": r.feedback_signal,
        "feedback_ok": r.feedback_ok,
        "feedback_action": r.feedback_action,
        "feedback_from_tier": _normalize_tier_label(r.feedback_from_tier) if r.feedback_from_tier else "",
        "feedback_to_tier": _normalize_tier_label(r.feedback_to_tier) if r.feedback_to_tier else "",
        "feedback_reason": r.feedback_reason,
        "feedback_submitted_at": r.feedback_submitted_at,
    }
