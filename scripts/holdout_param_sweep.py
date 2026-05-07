"""Run holdout routing experiments for the v2 signal ensemble.

Uses the same LLMRouterBench evaluation helpers as ``scripts/eval_v2.py`` but
monkey-patches routing parameters in-process so we can sweep multiple variants
without editing source defaults until a winner is confirmed.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "LLMRouterBench"))
sys.path.insert(0, str(ROOT / "UncommonRoute"))

from main.eval.predictors import FunctionPredictor
from main.eval.runner import build_eval_summary, evaluate_question_bank_rows
from main.eval.sampling import load_all_question_bank_rows, rows_per_benchmark

import uncommon_route.decision.ensemble as ensemble_mod
from scripts.eval_v2 import _load_calibrator_if_exists
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.structural import StructuralSignal


BASELINE_WEIGHTS = (0.55, 0.45)
BASELINE_LOW_ESCALATION_THRESHOLD = 0.60
BASELINE_CLASSIFIER_FALLBACK_THRESHOLD = 0.95
THREE_SIGNAL_WEIGHTS = (0.50, 0.10, 0.40)


@dataclass(frozen=True)
class ExperimentConfig:
    weights_a: float = BASELINE_WEIGHTS[0]
    weights_c: float = BASELINE_WEIGHTS[1]
    low_escalation_threshold: float = BASELINE_LOW_ESCALATION_THRESHOLD
    signal_b_conditional: bool = False
    classifier_fallback_threshold: float = BASELINE_CLASSIFIER_FALLBACK_THRESHOLD


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _sort_benchmarks(items: dict[str, Any]) -> dict[str, Any]:
    return {key: items[key] for key in sorted(items)}


def _changed_dimensions(config: ExperimentConfig) -> int:
    return sum(
        [
            (config.weights_a, config.weights_c) != BASELINE_WEIGHTS,
            config.low_escalation_threshold != BASELINE_LOW_ESCALATION_THRESHOLD,
            config.signal_b_conditional,
            config.classifier_fallback_threshold != BASELINE_CLASSIFIER_FALLBACK_THRESHOLD,
        ]
    )


def _config_name(config: ExperimentConfig) -> str:
    parts = [
        f"A={config.weights_a:.2f}",
        f"C={config.weights_c:.2f}",
        f"low={config.low_escalation_threshold:.2f}",
        f"B={'on' if config.signal_b_conditional else 'off'}",
        f"clf={config.classifier_fallback_threshold:.2f}",
    ]
    return " | ".join(parts)


def _summarize_exact(
    *,
    config: ExperimentConfig,
    per_row: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    total = len(per_row)
    exact_match = sum(1 for row in per_row if row.get("match") is True)
    pass_count = sum(1 for row in per_row if row.get("passed") is True)
    by_benchmark: dict[str, Any] = {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in per_row:
        grouped.setdefault(row["benchmark"], []).append(row)

    for benchmark, rows in grouped.items():
        benchmark_total = len(rows)
        benchmark_exact = sum(1 for row in rows if row.get("match") is True)
        benchmark_pass = sum(1 for row in rows if row.get("passed") is True)
        by_benchmark[benchmark] = {
            "total": benchmark_total,
            "exact_match": benchmark_exact,
            "pass_count": benchmark_pass,
            "tier_match_accuracy": benchmark_exact / benchmark_total if benchmark_total else 0.0,
            "pass_rate": benchmark_pass / benchmark_total if benchmark_total else 0.0,
        }

    return {
        "name": _config_name(config),
        "config": asdict(config),
        "changed_dimensions": _changed_dimensions(config),
        "samples": total,
        "exact_match": exact_match,
        "pass_count": pass_count,
        "tier_match_accuracy": exact_match / total if total else 0.0,
        "pass_rate": pass_count / total if total else 0.0,
        "api_errors": len(errors),
        "cost_savings_score": summary["section_11"]["cost_savings_score"],
        "overall_score_percent": summary["router_accounting"]["overall_score_percent"],
        "by_benchmark": _sort_benchmarks(by_benchmark),
    }


def _attach_delta(result: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    enriched["delta_vs_baseline"] = {
        "tier_match_accuracy": result["tier_match_accuracy"] - baseline["tier_match_accuracy"],
        "pass_rate": result["pass_rate"] - baseline["pass_rate"],
        "exact_match": result["exact_match"] - baseline["exact_match"],
        "pass_count": result["pass_count"] - baseline["pass_count"],
    }
    return enriched


def _print_group(title: str, results: list[dict[str, Any]]) -> None:
    print()
    print(f"== {title} ==")
    for result in results:
        delta = result["delta_vs_baseline"]
        print(
            f"{result['name']}: "
            f"acc={_fmt_pct(result['tier_match_accuracy'])} ({result['exact_match']}/{result['samples']}, "
            f"{delta['exact_match']:+d}) "
            f"pass={_fmt_pct(result['pass_rate'])} ({result['pass_count']}/{result['samples']}, "
            f"{delta['pass_count']:+d})"
        )


def main() -> None:
    split = "holdout"
    index_dir = Path("uncommon_route/data/v2_splits")
    split_path = index_dir / f"{split}.jsonl"
    if not split_path.exists():
        raise SystemExit(f"Missing split: {split_path}")

    rows = load_all_question_bank_rows(split_path)
    benchmark_counts = rows_per_benchmark(rows)

    sig_a = MetadataSignal()
    sig_b = StructuralSignal()
    sig_c = EmbeddingSignal(
        index_path=index_dir / "seed_embeddings.npy",
        labels_path=index_dir / "seed_labels.json",
        model_name="BAAI/bge-small-en-v1.5",
        classifier_fallback_threshold=BASELINE_CLASSIFIER_FALLBACK_THRESHOLD,
    )
    calibrator = _load_calibrator_if_exists(index_dir)

    if sig_c._embed_fn is None:
        raise RuntimeError("Embedding model failed to load; ensure local HF cache is available")

    base_embed = sig_c._embed_fn
    embed_cache: dict[str, Any] = {}

    def cached_embed(text: str) -> Any:
        if text not in embed_cache:
            embed_cache[text] = base_embed(text)
        return embed_cache[text]

    sig_c._embed_fn = cached_embed

    print(f"Loaded {len(rows)} holdout rows.")
    print("Precomputing Signal A and Signal B votes...")
    vote_a_by_id = {row["id"]: sig_a.predict(row) for row in rows}
    vote_b_by_id = {row["id"]: sig_b.predict(row) for row in rows}

    fallback_thresholds = [0.90, 0.92, 0.95, 0.97]
    vote_c_by_threshold: dict[float, dict[str, Any]] = {}
    print("Precomputing Signal C votes by classifier fallback threshold...")
    for threshold in fallback_thresholds:
        sig_c._clf_fallback_threshold = threshold
        vote_c_by_threshold[threshold] = {
            row["id"]: sig_c.predict(row)
            for row in rows
        }

    def run_config(config: ExperimentConfig) -> dict[str, Any]:
        original_low_threshold = ensemble_mod._LOW_ESCALATION_THRESHOLD
        ensemble_mod._LOW_ESCALATION_THRESHOLD = config.low_escalation_threshold
        ensemble_2sig = Ensemble(
            weights=[config.weights_a, config.weights_c],
            risk_tolerance=0.5,
            calibrator=calibrator,
        )
        ensemble_3sig = Ensemble(
            weights=list(THREE_SIGNAL_WEIGHTS),
            risk_tolerance=0.5,
            calibrator=calibrator,
        )

        def predict(row: dict[str, Any]) -> int:
            row_id = row["id"]
            vote_a = vote_a_by_id[row_id]
            vote_c = vote_c_by_threshold[config.classifier_fallback_threshold][row_id]

            if config.signal_b_conditional and len(row.get("messages", [])) >= 4:
                vote_b = vote_b_by_id[row_id]
                result = ensemble_3sig.decide([vote_a, vote_b, vote_c])
            else:
                result = ensemble_2sig.decide([vote_a, vote_c])

            return 1 if result.tier_id is None else result.tier_id

        try:
            predictor = FunctionPredictor(predict)
            per_row, errors, correct = evaluate_question_bank_rows(
                predictor,
                rows,
                predictor_label=_config_name(config),
            )
            summary = build_eval_summary(
                per_row=per_row,
                errors=errors,
                correct=correct,
                predictor_label=_config_name(config),
                shard=split_path,
                sample_mode=f"{split}_split",
                seed=42,
                proportional_quotas=None,
                benchmark_counts=benchmark_counts,
            )
            return _summarize_exact(
                config=config,
                per_row=per_row,
                errors=errors,
                summary=summary,
            )
        finally:
            ensemble_mod._LOW_ESCALATION_THRESHOLD = original_low_threshold

    baseline_config = ExperimentConfig()
    baseline = run_config(baseline_config)

    weight_variants = [
        ExperimentConfig(weights_a=0.50, weights_c=0.50),
        ExperimentConfig(weights_a=0.45, weights_c=0.55),
        ExperimentConfig(weights_a=0.40, weights_c=0.60),
    ]
    low_threshold_variants = [
        ExperimentConfig(low_escalation_threshold=0.55),
        ExperimentConfig(low_escalation_threshold=0.58),
        ExperimentConfig(low_escalation_threshold=0.62),
        ExperimentConfig(low_escalation_threshold=0.65),
    ]
    signal_b_variants = [
        ExperimentConfig(signal_b_conditional=True),
    ]
    classifier_variants = [
        ExperimentConfig(classifier_fallback_threshold=0.90),
        ExperimentConfig(classifier_fallback_threshold=0.92),
        ExperimentConfig(classifier_fallback_threshold=0.97),
    ]

    experiment_groups = {
        "baseline": [baseline],
        "weights": [run_config(config) for config in weight_variants],
        "low_confidence_escalation": [run_config(config) for config in low_threshold_variants],
        "signal_b_conditional": [run_config(config) for config in signal_b_variants],
        "classifier_fallback": [run_config(config) for config in classifier_variants],
    }

    experiment_groups_with_delta = {
        group: [_attach_delta(result, baseline) for result in results]
        for group, results in experiment_groups.items()
    }

    all_weight_options = [BASELINE_WEIGHTS, (0.50, 0.50), (0.45, 0.55), (0.40, 0.60)]
    all_low_thresholds = [0.60, 0.55, 0.58, 0.62, 0.65]
    all_signal_b_options = [False, True]
    all_classifier_thresholds = fallback_thresholds

    print("Searching full combination grid...")
    combo_results: list[dict[str, Any]] = []
    for weights, low_threshold, signal_b_conditional, clf_threshold in itertools.product(
        all_weight_options,
        all_low_thresholds,
        all_signal_b_options,
        all_classifier_thresholds,
    ):
        combo_results.append(
            run_config(
                ExperimentConfig(
                    weights_a=weights[0],
                    weights_c=weights[1],
                    low_escalation_threshold=low_threshold,
                    signal_b_conditional=signal_b_conditional,
                    classifier_fallback_threshold=clf_threshold,
                )
            )
        )

    valid_combo_results = [
        result
        for result in combo_results
        if result["pass_rate"] >= 0.90
    ]
    best_combo = max(
        valid_combo_results,
        key=lambda result: (
            result["tier_match_accuracy"],
            result["pass_rate"],
            -result["changed_dimensions"],
        ),
    )

    top_valid_combos = sorted(
        valid_combo_results,
        key=lambda result: (
            result["tier_match_accuracy"],
            result["pass_rate"],
            -result["changed_dimensions"],
        ),
        reverse=True,
    )[:10]

    payload = {
        "baseline": baseline,
        "experiments": experiment_groups_with_delta,
        "combo_search": {
            "searched": len(combo_results),
            "valid_pass_ge_90": len(valid_combo_results),
            "best": _attach_delta(best_combo, baseline),
            "top_10": [_attach_delta(result, baseline) for result in top_valid_combos],
        },
        "original_values": {
            "weights_a": BASELINE_WEIGHTS[0],
            "weights_c": BASELINE_WEIGHTS[1],
            "low_escalation_threshold": BASELINE_LOW_ESCALATION_THRESHOLD,
            "signal_b_conditional": False,
            "classifier_fallback_threshold": BASELINE_CLASSIFIER_FALLBACK_THRESHOLD,
        },
    }

    print()
    print("== Baseline ==")
    print(
        f"{baseline['name']}: "
        f"acc={_fmt_pct(baseline['tier_match_accuracy'])} ({baseline['exact_match']}/{baseline['samples']}) "
        f"pass={_fmt_pct(baseline['pass_rate'])} ({baseline['pass_count']}/{baseline['samples']})"
    )
    _print_group("Weights", experiment_groups_with_delta["weights"])
    _print_group("Low-Confidence Escalation", experiment_groups_with_delta["low_confidence_escalation"])
    _print_group("Signal B Conditional", experiment_groups_with_delta["signal_b_conditional"])
    _print_group("Classifier Fallback", experiment_groups_with_delta["classifier_fallback"])
    print()
    print("== Best Combination (pass >= 90%) ==")
    best_with_delta = payload["combo_search"]["best"]
    print(
        f"{best_with_delta['name']}: "
        f"acc={_fmt_pct(best_with_delta['tier_match_accuracy'])} ({best_with_delta['exact_match']}/{best_with_delta['samples']}, "
        f"{best_with_delta['delta_vs_baseline']['exact_match']:+d}) "
        f"pass={_fmt_pct(best_with_delta['pass_rate'])} ({best_with_delta['pass_count']}/{best_with_delta['samples']}, "
        f"{best_with_delta['delta_vs_baseline']['pass_count']:+d})"
    )

    out_path = ROOT / "UncommonRoute" / "tmp" / "holdout_param_sweep_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved detailed results to {out_path}")


if __name__ == "__main__":
    main()
