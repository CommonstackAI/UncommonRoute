"""Fit temperature scaling on the calibration split, save to disk.

Usage: python scripts/fit_calibration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "LLMRouterBench"))
sys.path.insert(0, str(ROOT / "UncommonRoute"))

from main.eval.sampling import load_all_question_bank_rows

from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.structural import StructuralSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.decision.calibration import fit_platt_from_evals, save_calibrator


def main():
    index_dir = Path("uncommon_route/data/v2_splits")
    cal_path = index_dir / "calibration.jsonl"
    if not cal_path.exists():
        print(f"ERROR: {cal_path} not found.")
        sys.exit(1)

    rows = load_all_question_bank_rows(cal_path)
    print(f"Calibration rows: {len(rows)}")

    sig_a = MetadataSignal()
    sig_b = StructuralSignal()
    sig_c = EmbeddingSignal(
        index_path=index_dir / "seed_embeddings.npy",
        labels_path=index_dir / "seed_labels.json",
        model_name="BAAI/bge-small-en-v1.5",
    )
    ensemble = Ensemble(weights=[0.50, 0.10, 0.40], risk_tolerance=0.5)

    evals = []
    for row in rows:
        vote_a = sig_a.predict(row)
        vote_b = sig_b.predict(row)
        vote_c = sig_c.predict(row)
        result = ensemble.decide([vote_a, vote_b, vote_c])
        if result.tier_id is not None:
            correct = result.tier_id == row["target_tier_id"]
            evals.append({"confidence": result.confidence, "correct": correct})

    print(f"Evaluable: {len(evals)}")
    calibrator = fit_platt_from_evals(evals)
    print(f"Optimal temperature: {calibrator.temperature}")

    out_path = index_dir / "calibration_params.json"
    save_calibrator(calibrator, out_path)
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
