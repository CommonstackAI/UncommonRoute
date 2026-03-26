"""Feature-aware offline evaluation helpers for the router benchmark."""

from __future__ import annotations

from dataclasses import asdict, replace

from bench.dataset import TestCase
from uncommon_route.calibration import (
    compute_calibration,
    fit_temperature_scaling,
)
from uncommon_route.router.api import route
from uncommon_route.router.classifier import classify
from uncommon_route.router.config import DEFAULT_CONFIG
from uncommon_route.router.types import RoutingFeatures, ScoringConfig, Tier

TIERS = [Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX]


def collapse_tier(tier: str | Tier | None, *, default: str = "MEDIUM") -> str:
    if tier is None:
        return default
    normalized = tier.value if isinstance(tier, Tier) else str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def expected_tier_for_view(tc: TestCase, view: str) -> str:
    if view == "classifier" and tc.expected_classifier_tier:
        return collapse_tier(tc.expected_classifier_tier)
    return collapse_tier(tc.expected_tier)


def build_routing_features(payload: dict[str, object] | None) -> RoutingFeatures | None:
    if not payload:
        return None

    data = dict(payload)
    tool_names = data.get("tool_names", ())
    if tool_names is None:
        normalized_tool_names: tuple[str, ...] = ()
    elif isinstance(tool_names, (list, tuple)):
        normalized_tool_names = tuple(str(item) for item in tool_names)
    else:
        normalized_tool_names = (str(tool_names),)
    data["tool_names"] = normalized_tool_names

    for key in ("tier_floor", "tier_cap"):
        value = data.get(key)
        if value is None or isinstance(value, Tier):
            continue
        data[key] = Tier(str(value).strip().upper())

    return RoutingFeatures(**data)


def serialize_routing_features(features: RoutingFeatures | None) -> dict[str, object] | None:
    if features is None:
        return None

    payload = asdict(features)
    payload["tool_names"] = list(features.tool_names)
    payload["tier_floor"] = features.tier_floor.value if features.tier_floor else None
    payload["tier_cap"] = features.tier_cap.value if features.tier_cap else None
    return payload


def feature_slice_tags(features: RoutingFeatures | None) -> tuple[str, ...]:
    if features is None:
        return ("unannotated",)

    tags = ["annotated"]
    if features.step_type and features.step_type != "general":
        tags.append(f"step:{features.step_type}")
    if features.has_tool_results:
        tags.append("tool-results")
    if features.needs_tool_calling:
        tags.append("needs-tool-calling")
    if features.needs_structured_output:
        tags.append("structured-output")
    if features.is_agentic:
        tags.append("agentic")
    if features.is_coding:
        tags.append("coding")
    if features.tier_floor is not None:
        tags.append(f"tier-floor:{features.tier_floor.value}")
    if features.tier_cap is not None:
        tags.append(f"tier-cap:{features.tier_cap.value}")
    return tuple(tags)


def evaluate_dataset(
    dataset: list[TestCase],
    config: ScoringConfig,
    *,
    view: str,
) -> list[dict]:
    if view not in {"classifier", "route"}:
        raise ValueError(f"Unsupported benchmark view: {view}")

    routing_config = replace(DEFAULT_CONFIG, scoring=config)
    results: list[dict] = []

    for tc in dataset:
        expected = expected_tier_for_view(tc, view)
        features = build_routing_features(tc.routing_features)

        if view == "classifier":
            result = classify(tc.prompt, tc.system_prompt, config)
            actual = collapse_tier(result.tier, default="") if result.tier is not None else None
            resolved = collapse_tier(result.tier, default="MEDIUM")
            confidence = result.confidence
            score = 0.0
        else:
            decision = route(
                tc.prompt,
                tc.system_prompt,
                config=routing_config,
                routing_features=features,
            )
            actual = collapse_tier(decision.tier)
            resolved = actual
            confidence = decision.confidence
            score = None

        results.append({
            "expected": expected,
            "actual": actual,
            "resolved": resolved,
            "correct": resolved == expected,
            "score": score,
            "confidence": max(0.0, min(1.0, float(confidence))),
            "category": tc.category,
            "lang": tc.lang,
            "feature_annotated": features is not None,
            "feature_tags": list(feature_slice_tags(features)),
            "routing_features": serialize_routing_features(features),
        })

    return results


def compute_metrics(evals: list[dict]) -> dict:
    total = len(evals)
    correct = sum(1 for e in evals if e["correct"])
    ambiguous = sum(1 for e in evals if e["actual"] is None)

    per_tier: dict[str, dict] = {}
    for tier in TIERS:
        t = tier.value
        tp = sum(1 for e in evals if e["resolved"] == t and e["expected"] == t)
        fp = sum(1 for e in evals if e["resolved"] == t and e["expected"] != t)
        fn = sum(1 for e in evals if e["resolved"] != t and e["expected"] == t)
        support = sum(1 for e in evals if e["expected"] == t)
        precision = tp / (tp + fp) if tp + fp > 0 else 0.0
        recall = tp / (tp + fn) if tp + fn > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall > 0 else 0.0
        per_tier[t] = {"precision": precision, "recall": recall, "f1": f1, "support": support}

    weighted_f1 = sum(
        per_tier[tier.value]["f1"] * per_tier[tier.value]["support"] / total
        for tier in TIERS
    )

    per_lang: dict[str, dict] = {}
    per_category: dict[str, dict] = {}
    per_feature_slice: dict[str, dict] = {}
    for e in evals:
        lang_entry = per_lang.setdefault(e["lang"], {"total": 0, "correct": 0})
        lang_entry["total"] += 1
        if e["correct"]:
            lang_entry["correct"] += 1

        category_entry = per_category.setdefault(
            e["category"],
            {"total": 0, "correct": 0, "expected_tiers": set()},
        )
        category_entry["total"] += 1
        category_entry["expected_tiers"].add(e["expected"])
        if e["correct"]:
            category_entry["correct"] += 1

        for tag in e["feature_tags"]:
            slice_entry = per_feature_slice.setdefault(tag, {"total": 0, "correct": 0})
            slice_entry["total"] += 1
            if e["correct"]:
                slice_entry["correct"] += 1

    for container in (per_lang, per_feature_slice):
        for value in container.values():
            value["accuracy"] = value["correct"] / value["total"]

    for value in per_category.values():
        value["accuracy"] = value["correct"] / value["total"]
        value["expected_tier"] = ",".join(sorted(value.pop("expected_tiers")))

    calibration = compute_calibration(evals)

    return {
        "summary": {
            "accuracy": correct / total,
            "weighted_f1": weighted_f1,
            "correct": correct,
            "total": total,
            "ambiguous": ambiguous,
            "feature_annotated": sum(1 for e in evals if e["feature_annotated"]),
            "avg_confidence": calibration["avg_confidence"],
            "ece": calibration["ece"],
            "brier": calibration["brier"],
        },
        "per_tier": per_tier,
        "per_lang": per_lang,
        "per_category": per_category,
        "per_feature_slice": per_feature_slice,
        "calibration": calibration,
    }
