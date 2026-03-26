"""Adaptive model experience memory for candidate selection."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from uncommon_route.paths import data_dir
from uncommon_route.router.types import RoutingMode, Tier

FeedbackSignal = Literal["weak", "strong", "ok"]

_DATA_DIR = data_dir()


def _normalize_tier_label(tier: Tier | str) -> str:
    raw = tier.value if isinstance(tier, Tier) else str(tier)
    normalized = raw.strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


@dataclass
class ModelExperienceRecord:
    model: str
    mode: str
    tier: str
    requests: int = 0
    successes: int = 0
    failures: int = 0
    success_ewma: float = 0.5
    ttft_ms_ewma: float = 0.0
    tps_ewma: float = 0.0
    preference_ewma: float = 0.0
    cache_hit_ratio_ewma: float = 0.0
    cache_write_ratio_ewma: float = 0.0
    input_cost_multiplier_ewma: float = 1.0
    reward_ewma: float = 0.5
    reward_count: int = 0
    feedback_count: int = 0
    last_used_at: float = 0.0
    last_feedback_at: float = 0.0
    last_feedback_signal: str = ""


@dataclass(frozen=True, slots=True)
class CandidateExperience:
    reliability: float = 0.5
    latency: float = 0.5
    feedback: float = 0.5
    cache_affinity: float = 0.5
    input_cost_multiplier: float = 1.0
    reward_mean: float = 0.5
    samples: int = 0


class ModelExperienceStorage(ABC):
    @abstractmethod
    def load(self) -> list[dict[str, object]]: ...

    @abstractmethod
    def save(self, records: list[dict[str, object]]) -> None: ...


class FileModelExperienceStorage(ModelExperienceStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "model-experience.json")

    def load(self) -> list[dict[str, object]]:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def save(self, records: list[dict[str, object]]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(records, indent=2))
            self._path.chmod(0o600)
        except Exception:
            pass


class InMemoryModelExperienceStorage(ModelExperienceStorage):
    def __init__(self) -> None:
        self._records: list[dict[str, object]] = []

    def load(self) -> list[dict[str, object]]:
        return list(self._records)

    def save(self, records: list[dict[str, object]]) -> None:
        self._records = list(records)


class ModelExperienceStore:
    def __init__(
        self,
        storage: ModelExperienceStorage | None = None,
        *,
        alpha: float = 0.25,
        now_fn: object = None,
    ) -> None:
        self._storage = storage or FileModelExperienceStorage()
        self._alpha = max(0.05, min(0.9, alpha))
        self._now = now_fn if callable(now_fn) else time.time
        self._records: dict[str, ModelExperienceRecord] = {}
        self._load()

    def observe(
        self,
        model: str,
        mode: RoutingMode | str,
        tier: Tier | str,
        *,
        success: bool,
        ttft_ms: float | None = None,
        tps: float | None = None,
        total_input_tokens: int | None = None,
        uncached_input_tokens: int | None = None,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        input_cost_multiplier: float | None = None,
    ) -> None:
        record = self._get_or_create(model, mode, tier)
        record.requests += 1
        if success:
            record.successes += 1
        else:
            record.failures += 1
        target = 1.0 if success else 0.0
        record.success_ewma = self._blend(record.success_ewma, target)
        if ttft_ms is not None and ttft_ms > 0:
            record.ttft_ms_ewma = self._blend_metric(record.ttft_ms_ewma, ttft_ms)
        if tps is not None and tps > 0:
            record.tps_ewma = self._blend_metric(record.tps_ewma, tps)
        normalized_total_input = max(
            0,
            int(total_input_tokens or 0),
            int(uncached_input_tokens or 0) + max(0, int(cache_read_tokens)) + max(0, int(cache_write_tokens)),
        )
        if normalized_total_input > 0:
            hit_ratio = max(0.0, min(1.0, float(cache_read_tokens) / float(normalized_total_input)))
            write_ratio = max(0.0, min(1.0, float(cache_write_tokens) / float(normalized_total_input)))
            record.cache_hit_ratio_ewma = self._blend_metric(record.cache_hit_ratio_ewma, hit_ratio)
            record.cache_write_ratio_ewma = self._blend_metric(record.cache_write_ratio_ewma, write_ratio)
        if input_cost_multiplier is not None and input_cost_multiplier > 0:
            record.input_cost_multiplier_ewma = self._blend_metric(
                record.input_cost_multiplier_ewma,
                input_cost_multiplier,
            )
        # Only update reward for FAILURES (real signal).
        # HTTP 200 success is an availability signal, not a quality signal.
        # Quality reward comes from: explicit feedback, retrial detection,
        # logprob confidence — not from HTTP status codes.
        if not success:
            record.reward_ewma = self._blend(record.reward_ewma, 0.0)
            record.reward_count += 1
        record.last_used_at = float(self._now())
        self._save()

    def record_feedback(
        self,
        model: str,
        mode: RoutingMode | str,
        tier: Tier | str,
        signal: FeedbackSignal,
    ) -> None:
        record = self._get_or_create(model, mode, tier)
        # "ok" = model was appropriate for this tier (positive)
        # "weak" = model was too weak, should have used stronger (negative)
        # "strong" = model was overkill, could have used cheaper (mild negative)
        delta = {
            "ok": 0.14,
            "weak": -0.22,
            "strong": -0.10,
        }[signal]
        next_value = max(-1.0, min(1.0, record.preference_ewma + delta))
        record.preference_ewma = self._blend(record.preference_ewma, next_value)
        record.reward_ewma = self._blend(record.reward_ewma, _reward_from_feedback(signal))
        record.reward_count += 1
        record.feedback_count += 1
        now = float(self._now())
        record.last_used_at = now
        record.last_feedback_at = now
        record.last_feedback_signal = signal
        self._save()

    def snapshot(
        self,
        model: str,
        mode: RoutingMode | str,
        tier: Tier | str,
    ) -> CandidateExperience:
        record = self._records.get(self._key(model, mode, tier))
        if record is None:
            return CandidateExperience()
        ttft_score = 0.5
        if record.ttft_ms_ewma > 0:
            ttft_score = 1.0 / (1.0 + (record.ttft_ms_ewma / 1500.0))
        tps_score = 0.5
        if record.tps_ewma > 0:
            tps_score = min(1.0, record.tps_ewma / 80.0)
        latency = max(0.0, min(1.0, (ttft_score * 0.6) + (tps_score * 0.4)))
        feedback = max(0.0, min(1.0, 0.5 + (record.preference_ewma * 0.5)))
        cache_affinity = max(
            0.0,
            min(
                1.0,
                0.45 + (record.cache_hit_ratio_ewma * 0.75) - (record.cache_write_ratio_ewma * 0.20),
            ),
        )
        return CandidateExperience(
            reliability=max(0.0, min(1.0, record.success_ewma)),
            latency=latency,
            feedback=feedback,
            cache_affinity=cache_affinity,
            input_cost_multiplier=max(0.1, min(2.0, record.input_cost_multiplier_ewma)),
            reward_mean=max(0.0, min(1.0, record.reward_ewma)),
            samples=record.reward_count,
        )

    def bucket_pulls(
        self,
        mode: RoutingMode | str,
        tier: Tier | str,
    ) -> int:
        mode_value = mode.value if isinstance(mode, RoutingMode) else str(mode)
        tier_value = _normalize_tier_label(tier)
        return sum(
            record.requests
            for record in self._records.values()
            if record.mode == mode_value and record.tier == tier_value
        )

    def count(self) -> int:
        return len(self._records)

    def summary(self) -> dict[str, object]:
        feedback_touched = [
            record for record in self._records.values()
            if record.feedback_count > 0 and record.last_feedback_at > 0
        ]
        return {
            "records": len(self._records),
            "active_buckets": len({(record.mode, record.tier) for record in self._records.values()}),
            "top_feedback_models": [
                self._serialize_summary_record(record)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: (item.preference_ewma, item.success_ewma, item.requests),
                    reverse=True,
                )[:8]
            ],
            "promoted_models": [
                self._serialize_summary_record(record)
                for record in sorted(
                    [record for record in feedback_touched if record.preference_ewma >= 0],
                    key=lambda item: (item.preference_ewma, item.last_feedback_at, item.requests),
                    reverse=True,
                )[:6]
            ],
            "demoted_models": [
                self._serialize_summary_record(record)
                for record in sorted(
                    [record for record in feedback_touched if record.preference_ewma < 0],
                    key=lambda item: (item.preference_ewma, -item.last_feedback_at, -item.requests),
                )[:6]
            ],
            "recent_feedback_changes": [
                {
                    **self._serialize_summary_record(record),
                    "direction": "promoted" if record.last_feedback_signal == "ok" else "demoted",
                }
                for record in sorted(
                    feedback_touched,
                    key=lambda item: item.last_feedback_at,
                    reverse=True,
                )[:10]
            ],
        }

    def bucket_summary(
        self,
        mode: RoutingMode | str,
        tier: Tier | str,
        *,
        limit: int = 12,
    ) -> dict[str, object]:
        mode_value = mode.value if isinstance(mode, RoutingMode) else str(mode)
        tier_value = _normalize_tier_label(tier)
        records = [
            record
            for record in self._records.values()
            if record.mode == mode_value and record.tier == tier_value
        ]
        ranked = sorted(
            records,
            key=lambda item: (
                item.preference_ewma,
                item.reward_ewma,
                item.success_ewma,
                item.cache_hit_ratio_ewma,
                item.requests,
                item.last_used_at,
            ),
            reverse=True,
        )
        return {
            "mode": mode_value,
            "tier": tier_value,
            "count": len(records),
            "models": [
                self._serialize_summary_record(record)
                for record in ranked[: max(1, limit)]
            ],
        }

    def _get_or_create(
        self,
        model: str,
        mode: RoutingMode | str,
        tier: Tier | str,
    ) -> ModelExperienceRecord:
        key = self._key(model, mode, tier)
        record = self._records.get(key)
        if record is None:
            record = ModelExperienceRecord(
                model=model,
                mode=mode.value if isinstance(mode, RoutingMode) else str(mode),
                tier=_normalize_tier_label(tier),
            )
            self._records[key] = record
        return record

    def _blend(self, current: float, new_value: float) -> float:
        return (current * (1.0 - self._alpha)) + (new_value * self._alpha)

    def _blend_metric(self, current: float, new_value: float) -> float:
        if current <= 0:
            return new_value
        return self._blend(current, new_value)

    def _key(
        self,
        model: str,
        mode: RoutingMode | str,
        tier: Tier | str,
    ) -> str:
        mode_value = mode.value if isinstance(mode, RoutingMode) else str(mode)
        tier_value = _normalize_tier_label(tier)
        return f"{mode_value}|{tier_value}|{model}"

    def _save(self) -> None:
        self._storage.save([asdict(record) for record in self._records.values()])

    def _load(self) -> None:
        for raw in self._storage.load():
            if not isinstance(raw, dict):
                continue
            try:
                record = ModelExperienceRecord(
                    model=str(raw.get("model", "")),
                    mode=str(raw.get("mode", "")),
                    tier=_normalize_tier_label(str(raw.get("tier", ""))),
                    requests=int(raw.get("requests", 0)),
                    successes=int(raw.get("successes", 0)),
                    failures=int(raw.get("failures", 0)),
                    success_ewma=float(raw.get("success_ewma", 0.5)),
                    ttft_ms_ewma=float(raw.get("ttft_ms_ewma", 0.0)),
                    tps_ewma=float(raw.get("tps_ewma", 0.0)),
                    preference_ewma=float(raw.get("preference_ewma", 0.0)),
                    cache_hit_ratio_ewma=float(raw.get("cache_hit_ratio_ewma", 0.0)),
                    cache_write_ratio_ewma=float(raw.get("cache_write_ratio_ewma", 0.0)),
                    input_cost_multiplier_ewma=float(raw.get("input_cost_multiplier_ewma", 1.0)),
                    reward_ewma=float(raw.get("reward_ewma", 0.5)),
                    reward_count=int(raw.get("reward_count", 0)),
                    feedback_count=int(raw.get("feedback_count", 0)),
                    last_used_at=float(raw.get("last_used_at", 0.0)),
                    last_feedback_at=float(raw.get("last_feedback_at", 0.0)),
                    last_feedback_signal=str(raw.get("last_feedback_signal", "")),
                )
            except (TypeError, ValueError):
                continue
            if not record.mode:
                continue
            key = self._key(record.model, record.mode, record.tier)
            existing = self._records.get(key)
            self._records[key] = self._merge_records(existing, record) if existing else record

    def _serialize_summary_record(self, record: ModelExperienceRecord) -> dict[str, object]:
        return {
            "model": record.model,
            "mode": record.mode,
            "tier": _normalize_tier_label(record.tier),
            "feedback": round(0.5 + (record.preference_ewma * 0.5), 3),
            "reward": round(record.reward_ewma, 3),
            "reliability": round(record.success_ewma, 3),
            "cache_hit_ratio": round(record.cache_hit_ratio_ewma, 3),
            "cache_write_ratio": round(record.cache_write_ratio_ewma, 3),
            "input_cost_multiplier": round(record.input_cost_multiplier_ewma, 3),
            "samples": record.requests,
            "feedback_count": record.feedback_count,
            "last_used_at": round(record.last_used_at, 3),
            "last_feedback_at": round(record.last_feedback_at, 3),
            "last_feedback_signal": record.last_feedback_signal,
        }

    def _merge_records(
        self,
        current: ModelExperienceRecord,
        incoming: ModelExperienceRecord,
    ) -> ModelExperienceRecord:
        current_weight = max(current.requests, 1)
        incoming_weight = max(incoming.requests, 1)

        def _weighted(a: float, b: float, wa: int, wb: int) -> float:
            return ((a * wa) + (b * wb)) / max(wa + wb, 1)

        return ModelExperienceRecord(
            model=current.model or incoming.model,
            mode=current.mode or incoming.mode,
            tier=_normalize_tier_label(current.tier or incoming.tier),
            requests=current.requests + incoming.requests,
            successes=current.successes + incoming.successes,
            failures=current.failures + incoming.failures,
            success_ewma=_weighted(current.success_ewma, incoming.success_ewma, current_weight, incoming_weight),
            ttft_ms_ewma=_weighted(current.ttft_ms_ewma, incoming.ttft_ms_ewma, current_weight, incoming_weight),
            tps_ewma=_weighted(current.tps_ewma, incoming.tps_ewma, current_weight, incoming_weight),
            preference_ewma=_weighted(
                current.preference_ewma,
                incoming.preference_ewma,
                max(current.feedback_count, 1),
                max(incoming.feedback_count, 1),
            ),
            cache_hit_ratio_ewma=_weighted(current.cache_hit_ratio_ewma, incoming.cache_hit_ratio_ewma, current_weight, incoming_weight),
            cache_write_ratio_ewma=_weighted(current.cache_write_ratio_ewma, incoming.cache_write_ratio_ewma, current_weight, incoming_weight),
            input_cost_multiplier_ewma=_weighted(
                current.input_cost_multiplier_ewma,
                incoming.input_cost_multiplier_ewma,
                current_weight,
                incoming_weight,
            ),
            reward_ewma=_weighted(
                current.reward_ewma,
                incoming.reward_ewma,
                max(current.reward_count, 1),
                max(incoming.reward_count, 1),
            ),
            reward_count=current.reward_count + incoming.reward_count,
            feedback_count=current.feedback_count + incoming.feedback_count,
            last_used_at=max(current.last_used_at, incoming.last_used_at),
            last_feedback_at=max(current.last_feedback_at, incoming.last_feedback_at),
            last_feedback_signal=(
                incoming.last_feedback_signal
                if incoming.last_feedback_at >= current.last_feedback_at
                else current.last_feedback_signal
            ),
        )



def _reward_from_feedback(signal: FeedbackSignal) -> float:
    return {
        "ok": 0.9,
        "weak": 0.15,
        "strong": 0.35,
    }[signal]
