"""Phase 1 evaluation: Signal A + Signal C ensemble on holdout.

Usage:
    python scripts/eval_v2.py                           # default holdout
    python scripts/eval_v2.py --risk-tolerance 0.3      # conservative
    python scripts/eval_v2.py --risk-tolerance 0.8      # aggressive
"""

from __future__ import annotations

import argparse
import math
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "LLMRouterBench"))
sys.path.insert(0, str(ROOT / "UncommonRoute"))

from main.eval.runner import evaluate_question_bank_rows, build_eval_summary
from main.eval.sampling import load_all_question_bank_rows, rows_per_benchmark
from main.eval.predictors import FunctionPredictor

from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.decision.ensemble import Ensemble


def make_v2_predictor(risk_tolerance: float, index_dir: Path):
    sig_a = MetadataSignal()
    sig_c = EmbeddingSignal(
        index_path=index_dir / "seed_embeddings.npy",
        labels_path=index_dir / "seed_labels.json",
        model_name="BAAI/bge-small-en-v1.5",
    )
    ensemble = Ensemble(
        weights=[0.55, 0.45],
        risk_tolerance=risk_tolerance,
    )

    def predict(row: dict) -> int:
        vote_a = sig_a.predict(row)
        vote_c = sig_c.predict(row)
        result = ensemble.decide([vote_a, vote_c])
        if result.tier_id is None:
            return 1
        return result.tier_id

    return predict


def fmt(v) -> str:
    try:
        if math.isnan(v):
            return "  NaN"
    except (TypeError, ValueError):
        pass
    return f"{v:>5.1f}"


def main():
    parser = argparse.ArgumentParser(description="Phase 1 v2 evaluation")
    parser.add_argument("--split", choices=["holdout", "calibration", "train"], default="holdout")
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
    predict_fn = make_v2_predictor(args.risk_tolerance, index_dir)
    predictor = FunctionPredictor(predict_fn)
    label = f"v2_phase1_rt{args.risk_tolerance}"

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
    print(f"  UncommonRoute v2 Phase 1 — {args.split} evaluation")
    print("=" * 64)
    print(f"  risk_tolerance: {args.risk_tolerance}")
    print(f"  Samples: {summary['sampled']}")
    print("-" * 64)
    print(f"  Tier Match Accuracy :  {summary['tier_match_accuracy']:>6.1%}")
    print(f"  Pass Rate           :  {s11['pass_rate']:>6.1%}")
    print(f"  Cost Savings Score  :  {fmt(s11['cost_savings_score'])}")
    print(f"  Overall Score       :  {fmt(acct['overall_score_percent'])}")
    print(f"  API Errors          :  {summary['api_errors']}")
    print("-" * 64)
    print("  Per Benchmark:")
    for bk, d in summary["by_benchmark"].items():
        cs = d["cost_savings_score"]
        cs_str = fmt(cs)
        print(f"    {bk:12s}  acc={d['tier_match_accuracy']:5.1%}  pass={d['pass_rate']:5.1%}  savings={cs_str}")
    print("=" * 64)

    # Phase 1 gate
    acc = summary["tier_match_accuracy"]
    cs = s11["cost_savings_score"]
    cs_valid = not (isinstance(cs, float) and math.isnan(cs))
    gate_pass = acc >= 0.60 and cs_valid and cs >= 30
    print()
    if gate_pass:
        print(f"  GATE: PASS (accuracy={acc:.1%} >= 60%, cost_savings={cs:.1f} >= 30)")
    else:
        print(f"  GATE: FAIL (accuracy={acc:.1%}, cost_savings={fmt(cs)})")
        print("  ACTION: Diagnose before proceeding to Phase 2.")


if __name__ == "__main__":
    main()
