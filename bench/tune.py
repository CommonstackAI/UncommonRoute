"""Offline weight tuner — grid search for optimal scoring config.

Inspired by MixLLM's contextual bandit: instead of online learning,
we do batch optimization on the benchmark dataset.

Usage:
    python -m bench.tune                # run grid search
    python -m bench.tune --fine         # finer grid (slower)
"""

from __future__ import annotations

import copy
import itertools
import json
import sys
from dataclasses import fields
from pathlib import Path

from uncommon_route.router.classifier import classify
from uncommon_route.router.types import (
    ScoringConfig,
    StructuralWeights,
    KeywordWeights,
    TierBoundaries,
    Tier,
)
from bench.dataset import DATASET

TIERS = [Tier.SIMPLE, Tier.MEDIUM, Tier.COMPLEX]


def _collapse_tier(tier: str) -> str:
    normalized = str(tier).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _accuracy(config: ScoringConfig) -> tuple[float, float, dict[str, float]]:
    """Evaluate a config. Returns (accuracy, weighted_f1, per_tier_f1)."""
    results = []
    for tc in DATASET:
        r = classify(tc.prompt, tc.system_prompt, config)
        resolved = _collapse_tier(r.tier.value if r.tier else "MEDIUM")
        results.append({"expected": _collapse_tier(tc.expected_tier), "resolved": resolved})

    total = len(results)
    correct = sum(1 for r in results if r["expected"] == r["resolved"])

    per_tier_f1: dict[str, float] = {}
    for tier in TIERS:
        t = tier.value
        tp = sum(1 for r in results if r["resolved"] == t and r["expected"] == t)
        fp = sum(1 for r in results if r["resolved"] == t and r["expected"] != t)
        fn = sum(1 for r in results if r["resolved"] != t and r["expected"] == t)
        p = tp / (tp + fp) if tp + fp else 0
        rc = tp / (tp + fn) if tp + fn else 0
        f1 = 2 * p * rc / (p + rc) if p + rc else 0
        per_tier_f1[t] = f1

    support = {t.value: sum(1 for r in results if r["expected"] == t.value) for t in TIERS}
    wf1 = sum(per_tier_f1[t.value] * support[t.value] / total for t in TIERS)

    return correct / total, wf1, per_tier_f1


def _grid_search_boundaries(base_config: ScoringConfig, fine: bool = False) -> ScoringConfig:
    """Search optimal tier boundaries."""
    step = 0.01 if fine else 0.02

    sm_range = [round(x * step, 3) for x in range(-2, 8)]
    mc_range = [round(x * step + 0.06, 3) for x in range(0, 12)]

    best_score = 0.0
    best_config = base_config
    total = len(sm_range) * len(mc_range)

    print(f"  搜索 tier boundaries ({total} 组合)...")

    for sm, mc in itertools.product(sm_range, mc_range):
        if sm >= mc:
            continue
        cfg = copy.deepcopy(base_config)
        cfg.tier_boundaries = TierBoundaries(simple_medium=sm, medium_complex=mc)
        acc, wf1, _ = _accuracy(cfg)
        score = wf1  # optimize for weighted F1
        if score > best_score:
            best_score = score
            best_config = cfg

    b = best_config.tier_boundaries
    print(f"  最优 boundaries: SM={b.simple_medium} MC={b.medium_complex} → wF1={best_score:.3f}")
    return best_config


def _grid_search_confidence(base_config: ScoringConfig) -> ScoringConfig:
    """Search optimal confidence parameters."""
    steepness_range = [8, 10, 12, 15, 18, 20, 25]
    threshold_range = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65]

    best_score = 0.0
    best_config = base_config

    print(f"  搜索 confidence 参数 ({len(steepness_range) * len(threshold_range)} 组合)...")

    for steep, thresh in itertools.product(steepness_range, threshold_range):
        cfg = copy.deepcopy(base_config)
        cfg.confidence_steepness = steep
        cfg.confidence_threshold = thresh
        _, wf1, _ = _accuracy(cfg)
        if wf1 > best_score:
            best_score = wf1
            best_config = cfg

    print(f"  最优 confidence: steepness={best_config.confidence_steepness} threshold={best_config.confidence_threshold} → wF1={best_score:.3f}")
    return best_config


def main() -> None:
    fine = "--fine" in sys.argv

    print()
    print("╔═══════════════════════════════════════╗")
    print("║   UncommonRoute Auto-Tuner            ║")
    print("╚═══════════════════════════════════════╝")
    print()

    base = ScoringConfig()
    acc0, wf1_0, tier_f1_0 = _accuracy(base)
    print(f"  当前配置: accuracy={acc0:.3f} wF1={wf1_0:.3f}")
    for t in TIERS:
        print(f"    {t.value}: F1={tier_f1_0[t.value]:.3f}")
    print()

    # Phase 1: boundaries
    cfg = _grid_search_boundaries(base, fine=fine)
    print()

    # Phase 2: confidence
    cfg = _grid_search_confidence(cfg)
    print()

    # Final evaluation
    acc, wf1, tier_f1 = _accuracy(cfg)
    print(f"  调优后: accuracy={acc:.3f} wF1={wf1:.3f}")
    for t in TIERS:
        delta = tier_f1[t.value] - tier_f1_0[t.value]
        sign = "+" if delta >= 0 else ""
        print(f"    {t.value}: F1={tier_f1[t.value]:.3f} ({sign}{delta:.3f})")
    print()

    improvement = wf1 - wf1_0
    if improvement > 0.005:
        print(f"  发现改进: wF1 {sign}{improvement:.3f}")
        print()
        print("  建议更新 types.py 中的默认配置:")
        print(f"    TierBoundaries(")
        print(f"        simple_medium={cfg.tier_boundaries.simple_medium},")
        print(f"        medium_complex={cfg.tier_boundaries.medium_complex},")
        print(f"    )")
        print(f"    confidence_steepness={cfg.confidence_steepness}")
        print(f"    confidence_threshold={cfg.confidence_threshold}")

        out_path = Path(__file__).parent / "results" / "tuned-config.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "tier_boundaries": {
                "simple_medium": cfg.tier_boundaries.simple_medium,
                "medium_complex": cfg.tier_boundaries.medium_complex,
            },
            "confidence_steepness": cfg.confidence_steepness,
            "confidence_threshold": cfg.confidence_threshold,
            "metrics": {"accuracy": acc, "weighted_f1": wf1, "per_tier_f1": tier_f1},
        }, indent=2))
        print(f"\n  配置已保存: {out_path}")
    else:
        print("  当前配置已接近最优，无显著改进空间。")

    print()


if __name__ == "__main__":
    main()
