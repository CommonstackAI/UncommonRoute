"""Benchmark integration tests — ensures routing accuracy doesn't regress.

Runs the cascade classifier against the labeled dataset and asserts
minimum accuracy / F1 thresholds per tier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.dataset import DATASET, TestCase  # noqa: E402
from uncommon_route.router.classifier import classify  # noqa: E402
from uncommon_route.router.types import ScoringConfig, Tier  # noqa: E402

MIN_OVERALL_ACCURACY = 0.95
MIN_TIER_F1: dict[str, float] = {
    "SIMPLE": 0.93,
    "MEDIUM": 0.90,
    "COMPLEX": 0.93,
}
TIERS = [Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX]


def _collapse_tier(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _evaluate(dataset: list[TestCase]) -> list[dict]:
    cfg = ScoringConfig()
    results: list[dict] = []
    for tc in dataset:
        result = classify(tc.prompt, tc.system_prompt, cfg)
        resolved = _collapse_tier(result.tier.value if result.tier else "MEDIUM")
        results.append({
            "expected": _collapse_tier(tc.expected_tier),
            "resolved": resolved,
            "correct": resolved == _collapse_tier(tc.expected_tier),
            "confidence": result.confidence,
            "lang": tc.lang,
            "category": tc.category,
        })
    return results


def _tier_f1(evals: list[dict]) -> dict[str, float]:
    f1s: dict[str, float] = {}
    for tier in TIERS:
        t = tier.value
        tp = sum(1 for e in evals if e["resolved"] == t and e["expected"] == t)
        fp = sum(1 for e in evals if e["resolved"] == t and e["expected"] != t)
        fn = sum(1 for e in evals if e["resolved"] != t and e["expected"] == t)
        prec = tp / (tp + fp) if tp + fp > 0 else 0.0
        rec = tp / (tp + fn) if tp + fn > 0 else 0.0
        f1s[t] = (2 * prec * rec / (prec + rec)) if prec + rec > 0 else 0.0
    return f1s


@pytest.fixture(scope="module")
def eval_results() -> list[dict]:
    return _evaluate(DATASET)


class TestBenchmarkAccuracy:
    """Regression guard — accuracy must not drop below thresholds."""

    def test_overall_accuracy(self, eval_results: list[dict]) -> None:
        correct = sum(1 for e in eval_results if e["correct"])
        accuracy = correct / len(eval_results)
        assert accuracy >= MIN_OVERALL_ACCURACY, (
            f"Overall accuracy {accuracy:.3f} < {MIN_OVERALL_ACCURACY} "
            f"({correct}/{len(eval_results)})"
        )

    def test_per_tier_f1(self, eval_results: list[dict]) -> None:
        f1s = _tier_f1(eval_results)
        for tier_name, threshold in MIN_TIER_F1.items():
            assert f1s[tier_name] >= threshold, (
                f"{tier_name} F1 {f1s[tier_name]:.3f} < {threshold}"
            )

    def test_no_extreme_confusion(self, eval_results: list[dict]) -> None:
        """SIMPLE should never be classified as COMPLEX and vice versa."""
        for e in eval_results:
            if e["expected"] == "SIMPLE":
                assert e["resolved"] != "COMPLEX", (
                    f"SIMPLE → COMPLEX: {e['category']} ({e['lang']})"
                )
            if e["expected"] == "COMPLEX":
                assert e["resolved"] != "SIMPLE", (
                    f"COMPLEX → SIMPLE: {e['category']} ({e['lang']})"
                )


class TestClassifierSmoke:
    """Quick sanity checks for individual classifier stages."""

    def test_greeting_is_simple(self) -> None:
        assert classify("hello").tier == Tier.SIMPLE

    def test_empty_is_simple(self) -> None:
        assert classify("").tier == Tier.SIMPLE

    def test_code_snippet_question_not_reasoning(self) -> None:
        result = classify("What does this code do?\n```python\nprint('hello')\n```")
        assert result.tier in (Tier.SIMPLE, Tier.MEDIUM)

    def test_complex_requirements(self) -> None:
        prompt = (
            "Design a distributed caching system with TTL-based expiration, "
            "LRU eviction, cross-datacenter replication, automatic failover, "
            "write-behind caching with configurable flush intervals, "
            "and a RESTful management API with role-based access control."
        )
        assert classify(prompt).tier == Tier.COMPLEX

    def test_math_proof_is_complex(self) -> None:
        assert classify(
            "Prove that √2 is irrational using proof by contradiction"
        ).tier == Tier.COMPLEX

    def test_chinese_greeting(self) -> None:
        result = classify("你好")
        assert result.tier == Tier.SIMPLE
        assert result.confidence > 0.0

    def test_confidence_range(self) -> None:
        result = classify("explain quicksort")
        assert 0.0 <= result.confidence <= 1.0
