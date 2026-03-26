"""Confidence calibration utilities for final routed decisions."""

from __future__ import annotations

import hashlib
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir
from uncommon_route.router.types import AnswerDepth, RoutingMode, Tier

_EPSILON = 1e-6
_DATA_DIR = data_dir()


def _clamp_probability(value: float) -> float:
    return max(_EPSILON, min(1.0 - _EPSILON, float(value)))


def apply_temperature(confidence: float, temperature: float) -> float:
    safe_confidence = _clamp_probability(confidence)
    safe_temperature = max(0.05, float(temperature))
    logit = math.log(safe_confidence / (1.0 - safe_confidence))
    scaled = logit / safe_temperature
    return 1.0 / (1.0 + math.exp(-scaled))


def compute_calibration(
    evals: list[dict[str, float | bool]],
    *,
    bucket_count: int = 10,
    temperature: float | None = None,
) -> dict[str, object]:
    if not evals:
        return {
            "accuracy": 0.0,
            "avg_confidence": 0.0,
            "ece": 0.0,
            "mce": 0.0,
            "brier": 0.0,
            "nll": 0.0,
            "temperature": temperature or 1.0,
            "buckets": [],
        }

    transformed: list[tuple[float, float]] = []
    for item in evals:
        raw_confidence = _clamp_probability(float(item["confidence"]))
        confidence = apply_temperature(raw_confidence, temperature or 1.0)
        transformed.append((confidence, 1.0 if item["correct"] else 0.0))

    return _compute_calibration_metrics(
        transformed,
        bucket_count=bucket_count,
        temperature=temperature or 1.0,
    )


def _compute_calibration_metrics(
    transformed: list[tuple[float, float]],
    *,
    bucket_count: int = 10,
    temperature: float = 1.0,
) -> dict[str, object]:
    if not transformed:
        return {
            "accuracy": 0.0,
            "avg_confidence": 0.0,
            "ece": 0.0,
            "mce": 0.0,
            "brier": 0.0,
            "nll": 0.0,
            "temperature": temperature,
            "buckets": [],
        }

    buckets = [
        {
            "index": index,
            "lower": index / bucket_count,
            "upper": (index + 1) / bucket_count,
            "total": 0,
            "sum_confidence": 0.0,
            "sum_correct": 0.0,
        }
        for index in range(bucket_count)
    ]
    for confidence, correct in transformed:
        index = min(int(confidence * bucket_count), bucket_count - 1)
        bucket = buckets[index]
        bucket["total"] += 1
        bucket["sum_confidence"] += confidence
        bucket["sum_correct"] += correct

    ece = 0.0
    mce = 0.0
    for bucket in buckets:
        if bucket["total"] == 0:
            bucket["avg_confidence"] = 0.0
            bucket["accuracy"] = 0.0
            bucket["gap"] = 0.0
        else:
            avg_confidence = bucket["sum_confidence"] / bucket["total"]
            accuracy = bucket["sum_correct"] / bucket["total"]
            gap = abs(avg_confidence - accuracy)
            bucket["avg_confidence"] = avg_confidence
            bucket["accuracy"] = accuracy
            bucket["gap"] = gap
            ece += gap * (bucket["total"] / len(transformed))
            mce = max(mce, gap)
        del bucket["sum_confidence"]
        del bucket["sum_correct"]

    avg_confidence = sum(confidence for confidence, _ in transformed) / len(transformed)
    accuracy = sum(correct for _, correct in transformed) / len(transformed)
    brier = sum((confidence - correct) ** 2 for confidence, correct in transformed) / len(transformed)
    nll = -sum(math.log(confidence if correct else 1.0 - confidence) for confidence, correct in transformed) / len(
        transformed
    )

    return {
        "accuracy": accuracy,
        "avg_confidence": avg_confidence,
        "ece": ece,
        "mce": mce,
        "brier": brier,
        "nll": nll,
        "temperature": temperature,
        "buckets": buckets,
    }


def fit_temperature_scaling(
    evals: list[dict[str, float | bool]],
    *,
    min_temperature: float = 0.5,
    max_temperature: float = 3.0,
    step: float = 0.05,
) -> dict[str, object]:
    best_temperature = 1.0
    best_metrics = compute_calibration(evals, temperature=1.0)

    current = min_temperature
    while current <= max_temperature + (_EPSILON / 10):
        metrics = compute_calibration(evals, temperature=round(current, 4))
        if metrics["ece"] < best_metrics["ece"] - 1e-12 or (
            abs(float(metrics["ece"]) - float(best_metrics["ece"])) <= 1e-12
            and metrics["nll"] < best_metrics["nll"] - 1e-12
        ):
            best_temperature = round(current, 4)
            best_metrics = metrics
        current += step

    return {
        "temperature": best_temperature,
        "raw": compute_calibration(evals, temperature=1.0),
        "calibrated": best_metrics,
    }


def _normalize_tier_label(tier: Tier | str) -> str:
    raw = tier.value if isinstance(tier, Tier) else str(tier)
    normalized = raw.strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _normalize_mode(mode: RoutingMode | str) -> str:
    return mode.value if isinstance(mode, RoutingMode) else str(mode).strip().lower()


def _normalize_depth(depth: AnswerDepth | str) -> str:
    return depth.value if isinstance(depth, AnswerDepth) else str(depth).strip().lower()


def _complexity_band(complexity: float) -> str:
    if complexity < 0.20:
        return "very-low"
    if complexity < 0.33:
        return "low"
    if complexity < 0.50:
        return "mid-low"
    if complexity < 0.67:
        return "mid"
    if complexity < 0.85:
        return "high"
    return "very-high"


def _tier_band_bounds(tier: Tier | str) -> tuple[float, float]:
    normalized = _normalize_tier_label(tier)
    if normalized == "SIMPLE":
        return (0.0, 0.33)
    if normalized == "MEDIUM":
        return (0.33, 0.67)
    return (0.67, 1.0)


def _tier_band_position(tier: Tier | str, complexity: float) -> float:
    lower, upper = _tier_band_bounds(tier)
    span = max(_EPSILON, upper - lower)
    return max(0.0, min(1.0, (float(complexity) - lower) / span))


def _route_boundary_tags(tier: Tier | str, complexity: float) -> tuple[str, ...]:
    normalized_tier = _normalize_tier_label(tier)
    position = _tier_band_position(normalized_tier, complexity)
    labels: list[str] = []
    if position <= 0.20:
        labels.append("boundary:lower")
    elif position >= 0.80:
        labels.append("boundary:upper")
    else:
        labels.append("boundary:middle")

    if normalized_tier != "COMPLEX" and position >= 0.70:
        labels.append("pressure:upgrade")
    if normalized_tier != "SIMPLE" and position <= 0.30:
        labels.append("pressure:downgrade")
    return tuple(labels)


def _normalize_feature_tags(
    *,
    step_type: str,
    hint_tags: tuple[str, ...] | list[str],
    feature_tags: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    duplicate_tags = {
        f"step:{str(step_type or 'general').strip().lower()}",
        *(str(tag).strip() for tag in hint_tags if str(tag).strip()),
    }
    normalized: list[str] = []
    for tag in feature_tags:
        value = str(tag).strip()
        if not value or value in duplicate_tags:
            continue
        normalized.append(value)
    return tuple(dict.fromkeys(normalized))


def build_route_confidence_tags(
    *,
    mode: RoutingMode | str,
    tier: Tier | str,
    complexity: float,
    step_type: str = "general",
    answer_depth: AnswerDepth | str = AnswerDepth.STANDARD,
    constraint_tags: tuple[str, ...] | list[str] = (),
    hint_tags: tuple[str, ...] | list[str] = (),
    feature_tags: tuple[str, ...] | list[str] = (),
    streaming: bool = False,
) -> tuple[str, ...]:
    normalized_feature_tags = _normalize_feature_tags(
        step_type=step_type,
        hint_tags=hint_tags,
        feature_tags=feature_tags,
    )
    tags = [
        f"mode:{_normalize_mode(mode)}",
        f"tier:{_normalize_tier_label(tier)}",
        f"step:{str(step_type or 'general').strip().lower()}",
        f"depth:{_normalize_depth(answer_depth)}",
        f"complexity:{_complexity_band(complexity)}",
    ]
    if streaming:
        tags.append("streaming")
    tags.extend(_route_boundary_tags(tier, complexity))
    tags.extend(f"constraint:{tag}" for tag in constraint_tags if tag)
    tags.extend(f"hint:{tag}" for tag in hint_tags if tag)
    tags.extend(f"feature:{tag}" for tag in normalized_feature_tags)
    return tuple(dict.fromkeys(tags))


@dataclass(frozen=True, slots=True)
class RouteConfidenceEstimate:
    confidence: float
    raw_confidence: float
    source: str = "classifier"
    version: str = ""
    sample_count: int = 0
    temperature: float = 1.0
    applied_adjustments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteCalibrationSnapshot:
    version: str = ""
    updated_at: float = 0.0
    labeled_examples: int = 0
    eligible_examples: int = 0
    training_examples: int = 0
    holdout_examples: int = 0
    temperature: float = 1.0
    global_accuracy: float = 0.0
    avg_raw_confidence: float = 0.0
    avg_calibrated_confidence: float = 0.0
    raw_ece: float = 0.0
    calibrated_ece: float = 0.0
    selected_strategy: str = ""
    holdout_passed: bool = False
    holdout_raw_ece: float = 0.0
    holdout_calibrated_ece: float = 0.0
    holdout_raw_nll: float = 0.0
    holdout_calibrated_nll: float = 0.0
    min_examples: int = 0
    min_tag_examples: int = 0
    prior_strength: float = 0.0
    max_label_age_s: float = 0.0
    adjustments: dict[str, dict[str, float | int]] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return bool(self.version) and self.labeled_examples >= self.min_examples

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "version": self.version,
            "updated_at": self.updated_at,
            "labeled_examples": self.labeled_examples,
            "eligible_examples": self.eligible_examples or self.labeled_examples,
            "training_examples": self.training_examples or self.labeled_examples,
            "holdout_examples": self.holdout_examples,
            "temperature": self.temperature,
            "global_accuracy": self.global_accuracy,
            "avg_raw_confidence": self.avg_raw_confidence,
            "avg_calibrated_confidence": self.avg_calibrated_confidence,
            "raw_ece": self.raw_ece,
            "calibrated_ece": self.calibrated_ece,
            "selected_strategy": self.selected_strategy,
            "holdout_passed": self.holdout_passed,
            "holdout_raw_ece": self.holdout_raw_ece,
            "holdout_calibrated_ece": self.holdout_calibrated_ece,
            "holdout_raw_nll": self.holdout_raw_nll,
            "holdout_calibrated_nll": self.holdout_calibrated_nll,
            "min_examples": self.min_examples,
            "min_tag_examples": self.min_tag_examples,
            "prior_strength": self.prior_strength,
            "max_label_age_s": self.max_label_age_s,
            "adjustment_count": len(self.adjustments),
            "top_adjustments": [
                {
                    "tag": tag,
                    "count": int(values.get("count", 0)),
                    "accuracy": round(float(values.get("accuracy", 0.0)), 6),
                    "avg_confidence": round(float(values.get("avg_confidence", 0.0)), 6),
                    "delta": round(float(values.get("delta", 0.0)), 6),
                    "weight": round(float(values.get("weight", 0.0)), 6),
                }
                for tag, values in sorted(
                    self.adjustments.items(),
                    key=lambda item: (int(item[1].get("count", 0)), abs(float(item[1].get("delta", 0.0)))),
                    reverse=True,
                )[:8]
            ],
        }


class RouteCalibrationStorage(ABC):
    @abstractmethod
    def load(self) -> dict[str, object] | None: ...

    @abstractmethod
    def save(self, payload: dict[str, object]) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class FileRouteCalibrationStorage(RouteCalibrationStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "route-calibration.json")

    def load(self) -> dict[str, object] | None:
        try:
            if self._path.exists():
                payload = json.loads(self._path.read_text())
                if isinstance(payload, dict):
                    return payload
        except Exception:
            pass
        return None

    def save(self, payload: dict[str, object]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(payload, indent=2))
            self._path.chmod(0o600)
        except Exception:
            pass

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass


class InMemoryRouteCalibrationStorage(RouteCalibrationStorage):
    def __init__(self) -> None:
        self._payload: dict[str, object] | None = None

    def load(self) -> dict[str, object] | None:
        return dict(self._payload) if self._payload is not None else None

    def save(self, payload: dict[str, object]) -> None:
        self._payload = dict(payload)

    def clear(self) -> None:
        self._payload = None


class RouteConfidenceCalibrator:
    def __init__(
        self,
        storage: RouteCalibrationStorage | None = None,
        *,
        now_fn: Any = None,
        min_examples: int = 8,
        min_tag_examples: int = 3,
        prior_strength: float = 8.0,
        max_runtime_adjustments: int = 4,
        holdout_fraction: float = 0.2,
        min_holdout_examples: int = 4,
        max_label_age_s: float = 7 * 86_400,
    ) -> None:
        self._storage = storage or FileRouteCalibrationStorage()
        self._now = now_fn or time.time
        self._min_examples = max(1, int(min_examples))
        self._min_tag_examples = max(1, int(min_tag_examples))
        self._prior_strength = max(0.1, float(prior_strength))
        self._max_runtime_adjustments = max(1, int(max_runtime_adjustments))
        self._holdout_fraction = max(0.0, min(0.5, float(holdout_fraction)))
        self._min_holdout_examples = max(1, int(min_holdout_examples))
        self._max_label_age_s = max(0.0, float(max_label_age_s))
        self._snapshot = self._load_snapshot()

    def snapshot(self) -> RouteCalibrationSnapshot:
        return self._snapshot

    def status(self) -> dict[str, object]:
        payload = self._snapshot.as_dict()
        payload["active"] = self._snapshot.active and not self._snapshot_is_stale(self._snapshot)
        payload["stale"] = self._snapshot_is_stale(self._snapshot)
        payload["age_s"] = self._snapshot_age_s(self._snapshot)
        return payload

    def reset(self) -> None:
        self._storage.clear()
        self._snapshot = self._empty_snapshot()

    def calibrate(
        self,
        raw_confidence: float,
        *,
        mode: RoutingMode | str,
        tier: Tier | str,
        complexity: float,
        step_type: str = "general",
        answer_depth: AnswerDepth | str = AnswerDepth.STANDARD,
        constraint_tags: tuple[str, ...] | list[str] = (),
        hint_tags: tuple[str, ...] | list[str] = (),
        feature_tags: tuple[str, ...] | list[str] = (),
        streaming: bool = False,
    ) -> RouteConfidenceEstimate:
        raw = _clamp_probability(raw_confidence)
        snapshot = self._snapshot
        if not self._snapshot_is_usable(snapshot):
            return RouteConfidenceEstimate(confidence=raw, raw_confidence=raw)

        matching_tags = build_route_confidence_tags(
            mode=mode,
            tier=tier,
            complexity=complexity,
            step_type=step_type,
            answer_depth=answer_depth,
            constraint_tags=constraint_tags,
            hint_tags=hint_tags,
            feature_tags=feature_tags,
            streaming=streaming,
        )
        calibrated, applied_adjustments = self._apply_adjustments(
            raw,
            temperature=snapshot.temperature,
            adjustments=snapshot.adjustments,
            matching_tags=matching_tags,
        )

        return RouteConfidenceEstimate(
            confidence=calibrated,
            raw_confidence=raw,
            source="route_calibrated",
            version=snapshot.version,
            sample_count=snapshot.labeled_examples,
            temperature=snapshot.temperature,
            applied_adjustments=applied_adjustments,
        )

    def fit_from_route_records(self, records: list[object]) -> RouteCalibrationSnapshot:
        examples = self._extract_labeled_examples(records)
        if not examples:
            self.reset()
            return self._snapshot

        examples.sort(key=lambda item: (float(item.get("label_time", 0.0)), int(item.get("order", 0))))
        train_examples, holdout_examples = self._split_examples(examples)
        fit = self._fit_from_examples(train_examples)

        selected_strategy = "full"
        selected_temperature = float(fit["temperature"])
        selected_adjustments = dict(fit["adjustments"])
        holdout_raw_metrics: dict[str, object] | None = None
        holdout_selected_metrics: dict[str, object] | None = None

        if holdout_examples:
            (
                selected_strategy,
                selected_temperature,
                selected_adjustments,
                holdout_raw_metrics,
                holdout_selected_metrics,
            ) = self._select_holdout_model(
                holdout_examples,
                temperature=float(fit["temperature"]),
                adjustments=dict(fit["adjustments"]),
            )

        evals = [{"confidence": example["raw_confidence"], "correct": bool(example["correct"])} for example in examples]
        raw_metrics = compute_calibration(evals, temperature=1.0)
        calibrated_metrics = self._evaluate_examples(
            examples,
            temperature=selected_temperature,
            adjustments=selected_adjustments,
        )

        snapshot = self._empty_snapshot(
            version=(
                self._snapshot_version(train_examples, selected_temperature, selected_adjustments)
                if selected_strategy != "raw"
                else ""
            ),
            updated_at=float(self._now()),
            labeled_examples=len(examples),
            eligible_examples=len(examples),
            training_examples=len(train_examples),
            holdout_examples=len(holdout_examples),
            temperature=selected_temperature,
            global_accuracy=float(calibrated_metrics["accuracy"]),
            avg_raw_confidence=float(raw_metrics["avg_confidence"]),
            avg_calibrated_confidence=float(calibrated_metrics["avg_confidence"]),
            raw_ece=float(raw_metrics["ece"]),
            calibrated_ece=float(calibrated_metrics["ece"]),
            selected_strategy=selected_strategy,
            holdout_passed=(not holdout_examples) or selected_strategy != "raw",
            holdout_raw_ece=float(holdout_raw_metrics["ece"]) if holdout_raw_metrics else 0.0,
            holdout_calibrated_ece=float(holdout_selected_metrics["ece"]) if holdout_selected_metrics else 0.0,
            holdout_raw_nll=float(holdout_raw_metrics["nll"]) if holdout_raw_metrics else 0.0,
            holdout_calibrated_nll=float(holdout_selected_metrics["nll"]) if holdout_selected_metrics else 0.0,
            adjustments=selected_adjustments,
        )
        self._snapshot = snapshot
        self._storage.save(asdict(snapshot))
        return snapshot

    def _empty_snapshot(self, **overrides: object) -> RouteCalibrationSnapshot:
        payload = {
            "min_examples": self._min_examples,
            "min_tag_examples": self._min_tag_examples,
            "prior_strength": self._prior_strength,
            "max_label_age_s": self._max_label_age_s,
        }
        payload.update(overrides)
        return RouteCalibrationSnapshot(**payload)

    def _snapshot_age_s(self, snapshot: RouteCalibrationSnapshot) -> float:
        if snapshot.updated_at <= 0:
            return 0.0
        return max(0.0, float(self._now()) - float(snapshot.updated_at))

    def _snapshot_is_stale(self, snapshot: RouteCalibrationSnapshot) -> bool:
        if self._max_label_age_s <= 0 or snapshot.updated_at <= 0:
            return False
        return self._snapshot_age_s(snapshot) > self._max_label_age_s

    def _snapshot_is_usable(self, snapshot: RouteCalibrationSnapshot) -> bool:
        return snapshot.active and not self._snapshot_is_stale(snapshot)

    def _select_adjustments(
        self,
        matching_tags: tuple[str, ...] | list[str],
        adjustments: dict[str, dict[str, float | int]],
    ) -> list[tuple[str, float, float, int]]:
        selected: list[tuple[str, float, float, int]] = []
        for tag in matching_tags:
            values = adjustments.get(tag)
            if not values:
                continue
            delta = float(values.get("delta", 0.0))
            if abs(delta) <= 1e-6:
                continue
            selected.append(
                (
                    str(tag),
                    delta,
                    float(values.get("weight", 0.0)),
                    int(values.get("count", 0)),
                )
            )
        selected.sort(key=lambda item: (abs(item[1]) * item[2], abs(item[1]), item[2], item[3]), reverse=True)
        return selected[: self._max_runtime_adjustments]

    def _apply_adjustments(
        self,
        raw_confidence: float,
        *,
        temperature: float,
        adjustments: dict[str, dict[str, float | int]],
        matching_tags: tuple[str, ...] | list[str],
    ) -> tuple[float, tuple[str, ...]]:
        raw = _clamp_probability(raw_confidence)
        base_confidence = apply_temperature(raw, temperature)
        selected = self._select_adjustments(matching_tags, adjustments)

        if selected:
            total_weight = sum(weight for _, _, weight, _ in selected)
            weighted_delta = (
                sum(delta * weight for _, delta, weight, _ in selected) / total_weight if total_weight > 0 else 0.0
            )
        else:
            weighted_delta = 0.0

        logit = math.log(base_confidence / (1.0 - base_confidence))
        calibrated = 1.0 / (1.0 + math.exp(-(logit + weighted_delta)))
        return _clamp_probability(calibrated), tuple(tag for tag, _, _, _ in selected)

    def _fit_from_examples(self, examples: list[dict[str, object]]) -> dict[str, object]:
        evals = [{"confidence": example["raw_confidence"], "correct": bool(example["correct"])} for example in examples]
        temperature_fit = fit_temperature_scaling(evals)
        temperature = float(temperature_fit["temperature"])
        calibrated_metrics = temperature_fit["calibrated"]
        global_accuracy = float(calibrated_metrics["accuracy"])
        global_avg_confidence = float(calibrated_metrics["avg_confidence"])

        grouped: dict[str, dict[str, float]] = {}
        for example in examples:
            scaled_confidence = apply_temperature(float(example["raw_confidence"]), temperature)
            for tag in example["tags"]:
                entry = grouped.setdefault(str(tag), {"count": 0.0, "correct": 0.0, "conf_sum": 0.0})
                entry["count"] += 1.0
                entry["correct"] += float(example["correct"])
                entry["conf_sum"] += scaled_confidence

        adjustments: dict[str, dict[str, float | int]] = {}
        for tag, values in grouped.items():
            count = int(values["count"])
            if count < self._min_tag_examples:
                continue
            accuracy = values["correct"] / values["count"]
            avg_confidence = values["conf_sum"] / values["count"]
            smoothed_accuracy = (values["correct"] + (self._prior_strength * global_accuracy)) / (
                values["count"] + self._prior_strength
            )
            smoothed_confidence = (values["conf_sum"] + (self._prior_strength * global_avg_confidence)) / (
                values["count"] + self._prior_strength
            )
            delta = math.log(_clamp_probability(smoothed_accuracy) / (1.0 - _clamp_probability(smoothed_accuracy)))
            delta -= math.log(_clamp_probability(smoothed_confidence) / (1.0 - _clamp_probability(smoothed_confidence)))
            adjustments[tag] = {
                "count": count,
                "correct": int(values["correct"]),
                "accuracy": accuracy,
                "avg_confidence": avg_confidence,
                "delta": delta,
                "weight": values["count"] / (values["count"] + self._prior_strength),
            }

        return {
            "temperature": temperature,
            "adjustments": adjustments,
        }

    def _evaluate_examples(
        self,
        examples: list[dict[str, object]],
        *,
        temperature: float,
        adjustments: dict[str, dict[str, float | int]],
    ) -> dict[str, object]:
        transformed: list[tuple[float, float]] = []
        for example in examples:
            calibrated, _ = self._apply_adjustments(
                float(example["raw_confidence"]),
                temperature=temperature,
                adjustments=adjustments,
                matching_tags=tuple(example["tags"]),
            )
            transformed.append((calibrated, 1.0 if bool(example["correct"]) else 0.0))
        return _compute_calibration_metrics(
            transformed,
            temperature=temperature,
        )

    def _split_examples(
        self,
        examples: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        if not examples or not all(bool(example.get("has_feedback_time")) for example in examples):
            return examples, []
        if len(examples) < self._min_examples + self._min_holdout_examples:
            return examples, []
        holdout_size = max(self._min_holdout_examples, int(round(len(examples) * self._holdout_fraction)))
        holdout_size = min(holdout_size, len(examples) - self._min_examples)
        if holdout_size < self._min_holdout_examples:
            return examples, []
        return examples[:-holdout_size], examples[-holdout_size:]

    def _select_holdout_model(
        self,
        holdout_examples: list[dict[str, object]],
        *,
        temperature: float,
        adjustments: dict[str, dict[str, float | int]],
    ) -> tuple[str, float, dict[str, dict[str, float | int]], dict[str, object], dict[str, object]]:
        raw_metrics = self._evaluate_examples(
            holdout_examples,
            temperature=1.0,
            adjustments={},
        )
        best_strategy = "raw"
        best_temperature = 1.0
        best_adjustments: dict[str, dict[str, float | int]] = {}
        best_metrics = raw_metrics

        temperature_only_metrics = self._evaluate_examples(
            holdout_examples,
            temperature=temperature,
            adjustments={},
        )
        if self._metrics_improve(temperature_only_metrics, best_metrics):
            best_strategy = "temperature"
            best_temperature = temperature
            best_adjustments = {}
            best_metrics = temperature_only_metrics

        if adjustments:
            full_metrics = self._evaluate_examples(
                holdout_examples,
                temperature=temperature,
                adjustments=adjustments,
            )
            if self._metrics_improve(full_metrics, best_metrics):
                best_strategy = "full"
                best_temperature = temperature
                best_adjustments = adjustments
                best_metrics = full_metrics

        return best_strategy, best_temperature, best_adjustments, raw_metrics, best_metrics

    def _metrics_improve(
        self,
        candidate: dict[str, object],
        baseline: dict[str, object],
    ) -> bool:
        tolerance = 1e-6
        candidate_ece = float(candidate["ece"])
        baseline_ece = float(baseline["ece"])
        candidate_nll = float(candidate["nll"])
        baseline_nll = float(baseline["nll"])
        ece_improved = candidate_ece < baseline_ece - tolerance
        nll_improved = candidate_nll < baseline_nll - tolerance
        ece_safe = candidate_ece <= baseline_ece + tolerance
        nll_safe = candidate_nll <= baseline_nll + tolerance
        return (ece_improved and nll_safe) or (nll_improved and ece_safe)

    def _extract_labeled_examples(self, records: list[object]) -> list[dict[str, object]]:
        examples: list[dict[str, object]] = []
        now = float(self._now())
        for order, record in enumerate(records):
            signal = str(getattr(record, "feedback_signal", "") or "").strip().lower()
            if signal not in {"ok", "weak", "strong"}:
                continue
            feedback_action = str(getattr(record, "feedback_action", "") or "").strip().lower()
            raw_confidence = getattr(record, "raw_confidence", None)
            if raw_confidence is None or float(raw_confidence) <= 0.0:
                raw_confidence = getattr(record, "confidence", 0.0)
            tier_value = getattr(record, "decision_tier", "") or getattr(record, "tier", "MEDIUM")
            from_tier = str(getattr(record, "feedback_from_tier", "") or tier_value)
            to_tier = str(getattr(record, "feedback_to_tier", "") or self._shift_feedback_tier(from_tier, signal))
            if signal != "ok":
                if feedback_action == "no_change":
                    continue
                if _normalize_tier_label(from_tier) == _normalize_tier_label(to_tier):
                    continue
            label_time = float(getattr(record, "feedback_submitted_at", 0.0) or 0.0)
            if label_time > 0 and self._max_label_age_s > 0 and (now - label_time) > self._max_label_age_s:
                continue
            tags = build_route_confidence_tags(
                mode=getattr(record, "mode", "auto"),
                tier=tier_value,
                complexity=float(getattr(record, "complexity", 0.33)),
                step_type=str(getattr(record, "step_type", "general") or "general"),
                answer_depth=str(getattr(record, "answer_depth", "standard") or "standard"),
                constraint_tags=tuple(getattr(record, "constraint_tags", []) or ()),
                hint_tags=tuple(getattr(record, "hint_tags", []) or ()),
                feature_tags=tuple(getattr(record, "feature_tags", []) or ()),
                streaming=bool(getattr(record, "streaming", False)),
            )
            examples.append(
                {
                    "raw_confidence": _clamp_probability(float(raw_confidence)),
                    "correct": signal == "ok",
                    "tags": tags,
                    "label_time": label_time if label_time > 0 else float(getattr(record, "timestamp", 0.0) or 0.0),
                    "has_feedback_time": label_time > 0,
                    "order": order,
                }
            )
        return examples

    def _shift_feedback_tier(self, tier: str, signal: str) -> str:
        order = ["SIMPLE", "MEDIUM", "COMPLEX"]
        normalized = _normalize_tier_label(tier)
        index = order.index(normalized) if normalized in order else 1
        if signal == "weak":
            return order[min(index + 1, len(order) - 1)]
        if signal == "strong":
            return order[max(index - 1, 0)]
        return normalized

    def _load_snapshot(self) -> RouteCalibrationSnapshot:
        payload = self._storage.load() or {}
        if not payload:
            return self._empty_snapshot()
        return RouteCalibrationSnapshot(
            version=str(payload.get("version", "")),
            updated_at=float(payload.get("updated_at", 0.0)),
            labeled_examples=int(payload.get("labeled_examples", 0)),
            eligible_examples=int(payload.get("eligible_examples", payload.get("labeled_examples", 0))),
            training_examples=int(payload.get("training_examples", payload.get("labeled_examples", 0))),
            holdout_examples=int(payload.get("holdout_examples", 0)),
            temperature=float(payload.get("temperature", 1.0)),
            global_accuracy=float(payload.get("global_accuracy", 0.0)),
            avg_raw_confidence=float(payload.get("avg_raw_confidence", 0.0)),
            avg_calibrated_confidence=float(payload.get("avg_calibrated_confidence", 0.0)),
            raw_ece=float(payload.get("raw_ece", 0.0)),
            calibrated_ece=float(payload.get("calibrated_ece", 0.0)),
            selected_strategy=str(payload.get("selected_strategy", "")),
            holdout_passed=bool(payload.get("holdout_passed", False)),
            holdout_raw_ece=float(payload.get("holdout_raw_ece", 0.0)),
            holdout_calibrated_ece=float(payload.get("holdout_calibrated_ece", 0.0)),
            holdout_raw_nll=float(payload.get("holdout_raw_nll", 0.0)),
            holdout_calibrated_nll=float(payload.get("holdout_calibrated_nll", 0.0)),
            min_examples=int(payload.get("min_examples", self._min_examples)),
            min_tag_examples=int(payload.get("min_tag_examples", self._min_tag_examples)),
            prior_strength=float(payload.get("prior_strength", self._prior_strength)),
            max_label_age_s=float(payload.get("max_label_age_s", self._max_label_age_s)),
            adjustments=dict(payload.get("adjustments", {}) or {}),
        )

    def _snapshot_version(
        self,
        examples: list[dict[str, object]],
        temperature: float,
        adjustments: dict[str, dict[str, float | int]],
    ) -> str:
        digest = hashlib.md5(
            json.dumps(
                {
                    "labels": len(examples),
                    "temperature": round(temperature, 6),
                    "adjustments": adjustments,
                },
                sort_keys=True,
                default=str,
            ).encode(),
        ).hexdigest()[:12]
        return f"route-cal-{digest}"


_ACTIVE_ROUTE_CONFIDENCE_CALIBRATOR: RouteConfidenceCalibrator | None = None


def get_active_route_confidence_calibrator() -> RouteConfidenceCalibrator:
    global _ACTIVE_ROUTE_CONFIDENCE_CALIBRATOR
    if _ACTIVE_ROUTE_CONFIDENCE_CALIBRATOR is None:
        _ACTIVE_ROUTE_CONFIDENCE_CALIBRATOR = RouteConfidenceCalibrator()
    return _ACTIVE_ROUTE_CONFIDENCE_CALIBRATOR
