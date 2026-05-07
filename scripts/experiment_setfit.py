"""Experiment: SetFit fine-tuning for tier classification.

Fine-tunes bge-small-en-v1.5 with contrastive learning on our 646 train samples,
then evaluates on holdout. Compares with frozen-embedding LogReg baseline.

Anti-overfitting: trains on train split only, evaluates on holdout once.
"""

import json
import numpy as np
from pathlib import Path
from collections import Counter
from sklearn.linear_model import LogisticRegressionCV
from sklearn.metrics import accuracy_score

# Shared content normalizer
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uncommon_route.signals.embedding import _normalize_content


def load_split(path: Path):
    rows = []
    with open(path) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    texts, labels, benchmarks = [], [], []
    for row in rows:
        for m in reversed(row.get("messages", [])):
            if m.get("role") == "user":
                text = _normalize_content(m.get("content", ""))
                if text.strip():
                    texts.append(text)
                    labels.append(row["target_tier_id"])
                    benchmarks.append(row.get("benchmark", "?"))
                break
    return texts, labels, benchmarks


def main():
    d = Path("uncommon_route/data/v2_splits")
    train_texts, train_labels, _ = load_split(d / "train.jsonl")
    holdout_texts, holdout_labels, holdout_benchmarks = load_split(d / "holdout.jsonl")

    print(f"Train: {len(train_texts)} samples, dist={Counter(train_labels)}")
    print(f"Holdout: {len(holdout_texts)} samples, dist={Counter(holdout_labels)}")

    tier_names = {0: "LOW", 1: "MID", 2: "MID_HIGH", 3: "HIGH"}

    # ─── Baseline: frozen embeddings + LogReg ───
    print("\n=== Baseline: Frozen bge-small + LogReg ===")
    from sentence_transformers import SentenceTransformer
    frozen_model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    train_emb = frozen_model.encode(train_texts, normalize_embeddings=True, show_progress_bar=False)
    holdout_emb = frozen_model.encode(holdout_texts, normalize_embeddings=True, show_progress_bar=False)

    clf_baseline = LogisticRegressionCV(max_iter=2000, random_state=42)
    clf_baseline.fit(train_emb, train_labels)
    baseline_preds = clf_baseline.predict(holdout_emb)
    baseline_acc = accuracy_score(holdout_labels, baseline_preds)
    print(f"  Overall accuracy: {baseline_acc:.1%}")
    for tier in range(4):
        mask = [l == tier for l in holdout_labels]
        if any(mask):
            tier_preds = [p for p, m in zip(baseline_preds, mask) if m]
            tier_true = [l for l, m in zip(holdout_labels, mask) if m]
            print(f"  {tier_names[tier]:8s} n={sum(mask):3d}  acc={accuracy_score(tier_true, tier_preds):.0%}")

    # ─── Experiment: SetFit fine-tuned embeddings ───
    print("\n=== SetFit: Fine-tuned bge-small ===")
    from setfit import SetFitModel, SetFitTrainer
    from datasets import Dataset

    train_ds = Dataset.from_dict({"text": train_texts, "label": train_labels})
    holdout_ds = Dataset.from_dict({"text": holdout_texts, "label": holdout_labels})

    setfit_model = SetFitModel.from_pretrained(
        "BAAI/bge-small-en-v1.5",
        labels=list(tier_names.values()),
    )

    trainer = SetFitTrainer(
        model=setfit_model,
        train_dataset=train_ds,
        eval_dataset=holdout_ds,
        num_iterations=20,  # contrastive pairs per sample
        num_epochs=1,
        batch_size=16,
        seed=42,
    )

    print("  Training...")
    trainer.train()

    print("  Evaluating...")
    setfit_preds = setfit_model.predict(holdout_texts)
    # Convert to list of ints
    setfit_preds = [int(p) for p in setfit_preds]
    setfit_acc = accuracy_score(holdout_labels, setfit_preds)
    print(f"  Overall accuracy: {setfit_acc:.1%}")
    for tier in range(4):
        mask = [l == tier for l in holdout_labels]
        if any(mask):
            tier_preds = [p for p, m in zip(setfit_preds, mask) if m]
            tier_true = [l for l, m in zip(holdout_labels, mask) if m]
            print(f"  {tier_names[tier]:8s} n={sum(mask):3d}  acc={accuracy_score(tier_true, tier_preds):.0%}")

    # ─── Per-benchmark breakdown ───
    print(f"\n=== Per-benchmark comparison ===")
    for bench in sorted(set(holdout_benchmarks)):
        mask = [b == bench for b in holdout_benchmarks]
        if sum(mask) < 3:
            continue
        b_true = [l for l, m in zip(holdout_labels, mask) if m]
        b_base = [p for p, m in zip(baseline_preds, mask) if m]
        b_setfit = [p for p, m in zip(setfit_preds, mask) if m]
        base_a = accuracy_score(b_true, b_base)
        setfit_a = accuracy_score(b_true, b_setfit)
        diff = setfit_a - base_a
        arrow = "↑" if diff > 0 else "↓" if diff < 0 else "="
        print(f"  {bench:12s} n={sum(mask):3d}  frozen={base_a:.1%}  setfit={setfit_a:.1%}  {arrow}{abs(diff):.1%}")

    # ─── Summary ───
    print(f"\n{'='*50}")
    print(f"Frozen LogReg: {baseline_acc:.1%}")
    print(f"SetFit:        {setfit_acc:.1%}  ({'+' if setfit_acc > baseline_acc else ''}{(setfit_acc-baseline_acc)*100:.1f}pp)")
    print(f"{'='*50}")

    # Save SetFit model if it's better
    if setfit_acc > baseline_acc:
        save_path = d / "setfit_model"
        setfit_model.save_pretrained(str(save_path))
        print(f"\nSetFit model saved to {save_path}")


if __name__ == "__main__":
    main()
