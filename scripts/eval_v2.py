"""v2 evaluation: Signal ensemble on holdout.

Default: 2-signal (A+C) with conditional Signal B activation on longer
conversations and shadow tracking elsewhere.

Usage:
    python scripts/eval_v2.py                                    # conditional B + shadow elsewhere
    python scripts/eval_v2.py --signals 2                        # 2-signal, no shadow
    python scripts/eval_v2.py --signals 3                        # 3-signal (force B active)
    python scripts/eval_v2.py --risk-tolerance 0.3               # conservative
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

# Make UncommonRoute importable when run from a checkout without `pip install -e .`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# CommonRouterBench (PyPI: CommonRouterBench, import: main) is required.
# Prefer the installed package; fall back to a sibling checkout (repo dev layout).
try:
    from main.eval.runner import evaluate_question_bank_rows, build_eval_summary  # noqa: F401
    from main.eval.sampling import load_all_question_bank_rows, rows_per_benchmark  # noqa: F401
    from main.eval.predictors import FunctionPredictor  # noqa: F401
except ModuleNotFoundError:
    _sibling_candidates = [
        ROOT.parent / "LLMRouterBench",
        ROOT.parent / "CommonRouterBench",
    ]
    for _c in _sibling_candidates:
        if (_c / "main" / "__init__.py").exists():
            sys.path.insert(0, str(_c))
            break
    else:
        print(
            "ERROR: CommonRouterBench not found.\n"
            "  Install it: pip install CommonRouterBench\n"
            "  Or check out https://github.com/CommonstackAI/CommonRouterBench as a sibling directory.",
            file=sys.stderr,
        )
        sys.exit(2)
    from main.eval.runner import evaluate_question_bank_rows, build_eval_summary  # noqa: F401
    from main.eval.sampling import load_all_question_bank_rows, rows_per_benchmark  # noqa: F401
    from main.eval.predictors import FunctionPredictor  # noqa: F401

from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.decision.calibration import PlattCalibrator, load_calibrator
from uncommon_route.router.api import _should_activate_signal_b


def _load_calibrator_if_exists(index_dir: Path) -> PlattCalibrator | None:
    cal_path = index_dir / "calibration_params.json"
    if cal_path.exists():
        return load_calibrator(cal_path)
    return None


def make_v2_predictor(risk_tolerance: float, index_dir: Path, signals: int = 2, shadow: bool = True):
    sig_a = MetadataSignal()
    sig_c = EmbeddingSignal(
        index_path=index_dir / "seed_embeddings.npy",
        labels_path=index_dir / "seed_labels.json",
        model_name="BAAI/bge-small-en-v1.5",
    )
    calibrator = _load_calibrator_if_exists(index_dir)

    if signals == 3:
        # Force Signal B active (for testing / comparison)
        from uncommon_route.signals.structural import StructuralSignal
        sig_b = StructuralSignal()
        ensemble = Ensemble(
            weights=[0.50, 0.10, 0.40],
            risk_tolerance=risk_tolerance,
            calibrator=calibrator,
        )

        def predict_3(row: dict) -> int:
            vote_a = sig_a.predict(row)
            vote_b = sig_b.predict(row)
            vote_c = sig_c.predict(row)
            result = ensemble.decide([vote_a, vote_b, vote_c])
            return 1 if result.tier_id is None else result.tier_id

        return predict_3, None  # no shadow tracker in forced mode

    # Default: 2-signal with conditional Signal B activation and optional shadow
    ensemble_2sig = Ensemble(
        weights=[0.55, 0.45],
        risk_tolerance=risk_tolerance,
        calibrator=calibrator,
    )
    ensemble_3sig = None

    shadow_tracker = None
    sig_b = None
    if shadow:
        from uncommon_route.signals.structural import StructuralSignal
        from uncommon_route.learning.shadow import ShadowTracker
        sig_b = StructuralSignal()
        shadow_tracker = ShadowTracker(eval_window=200, promote_after=3)
        ensemble_3sig = Ensemble(
            weights=[0.50, 0.10, 0.40],
            risk_tolerance=risk_tolerance,
            calibrator=calibrator,
        )

    def predict_2(row: dict) -> int:
        vote_a = sig_a.predict(row)
        vote_c = sig_c.predict(row)

        activate_b = _should_activate_signal_b(row)
        promoted_b = shadow_tracker is not None and shadow_tracker.promoted
        if (activate_b or promoted_b) and sig_b is not None and ensemble_3sig is not None:
            vote_b = sig_b.predict(row)
            result = ensemble_3sig.decide([vote_a, vote_b, vote_c])
            return 1 if result.tier_id is None else result.tier_id

        result = ensemble_2sig.decide([vote_a, vote_c])
        tier_id = 1 if result.tier_id is None else result.tier_id

        # Shadow: run Signal B on rows where it stays inactive so we can
        # continue counterfactual tracking.
        if sig_b is not None and shadow_tracker is not None and not activate_b:
            vote_b = sig_b.predict(row)
            gold = row.get("target_tier_id")  # available in eval, None in production
            shadow_tracker.record(
                signal_a_pred=vote_a.tier_id, signal_a_conf=vote_a.confidence,
                signal_b_pred=vote_b.tier_id, signal_b_conf=vote_b.confidence,
                signal_c_pred=vote_c.tier_id, signal_c_conf=vote_c.confidence,
                ensemble_2sig_tier=tier_id,
                gold_tier=gold,
            )

        return tier_id

    return predict_2, shadow_tracker


def fmt(v) -> str:
    try:
        if math.isnan(v):
            return "  NaN"
    except (TypeError, ValueError):
        pass
    return f"{v:>5.1f}"


def main():
    parser = argparse.ArgumentParser(description="v2 evaluation")
    parser.add_argument("--split", choices=["holdout", "calibration", "train"], default="holdout")
    parser.add_argument("--signals", type=int, choices=[2, 3], default=2, help="2=A+C (default), 3=A+B+C (force B active)")
    parser.add_argument("--shadow", action="store_true", default=True, help="Enable Signal B shadow mode (default: on)")
    parser.add_argument("--no-shadow", dest="shadow", action="store_false", help="Disable Signal B shadow mode")
    parser.add_argument("--risk-tolerance", type=float, default=0.5)
    parser.add_argument("--index-dir", default="uncommon_route/data/v2_splits")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    index_dir = Path(args.index_dir)
    split_path = index_dir / f"{args.split}.jsonl"
    if not split_path.exists():
        print(f"ERROR: {split_path} not found. Run split_data.py first.")
        sys.exit(1)

    rows = load_all_question_bank_rows(split_path)
    predict_fn, shadow_tracker = make_v2_predictor(
        args.risk_tolerance, index_dir, signals=args.signals,
        shadow=(args.shadow and args.signals == 2),
    )
    predictor = FunctionPredictor(predict_fn)
    mode_label = f"{args.signals}sig"
    if args.signals == 2 and args.shadow:
        mode_label += "+cond_b"
    label = f"v2_{mode_label}_rt{args.risk_tolerance}"

    progress = (lambda msg: print(msg, file=sys.stderr)) if args.verbose else None

    per_row, errors, correct = evaluate_question_bank_rows(
        predictor, rows, predictor_label=label, progress=progress,
    )

    bc = rows_per_benchmark(rows)
    summary = build_eval_summary(
        per_row=per_row, errors=errors, correct=correct,
        predictor_label=label, shard=split_path,
        sample_mode=f"{args.split}_split", seed=42,
        proportional_quotas=None, benchmark_counts=bc,
    )

    s11 = summary["section_11"]
    acct = summary["router_accounting"]

    print()
    print("=" * 64)
    print(f"  UncommonRoute v2 ({mode_label}) — {args.split} evaluation")
    print("=" * 64)
    print(f"  risk_tolerance: {args.risk_tolerance}")
    print(f"  Samples: {summary['sampled']}")
    print("-" * 64)
    print(f"  Tier Match Accuracy :  {summary['tier_match_accuracy']:>6.1%}")
    print(f"  Pass Rate           :  {s11['pass_rate']:>6.1%}")
    print(f"  Cost Savings Score  :  {fmt(s11['cost_savings_score'])}")
    print(f"  Overall Score       :  {fmt(acct['overall_score_percent'])}")
    print(f"  API Errors          :  {summary['api_errors']}")

    if shadow_tracker is not None:
        print(f"  Shadow B records    :  {shadow_tracker.record_count}")
        print(f"  Shadow B promoted   :  {shadow_tracker.promoted}")
        print(f"  Shadow B streak     :  {shadow_tracker.consecutive_wins}")

    print("-" * 64)
    print("  Per Benchmark:")
    for bk, d in summary["by_benchmark"].items():
        cs = d["cost_savings_score"]
        cs_str = fmt(cs)
        print(f"    {bk:12s}  acc={d['tier_match_accuracy']:5.1%}  pass={d['pass_rate']:5.1%}  savings={cs_str}")
    print("=" * 64)

    # Gate check
    acc = summary["tier_match_accuracy"]
    cs = s11["cost_savings_score"]
    cs_valid = not (isinstance(cs, float) and math.isnan(cs))
    gate_pass = acc >= 0.60 and cs_valid and cs >= 30
    print()
    if gate_pass:
        print(f"  GATE: PASS (accuracy={acc:.1%} >= 60%, cost_savings={cs:.1f} >= 30)")
    else:
        print(f"  GATE: FAIL (accuracy={acc:.1%}, cost_savings={fmt(cs)})")
        print("  ACTION: Diagnose before proceeding.")


if __name__ == "__main__":
    main()
