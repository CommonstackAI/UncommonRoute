"""Train a tier classifier on frozen bge-small embeddings.

Replaces KNN with a learned classifier for Signal C. Uses L2-regularized
logistic regression to prevent overfitting on the small (487-sample) dataset.

Anti-overfitting measures:
  1. Frozen embeddings — no 33M param fine-tuning
  2. L2 regularization (C=1.0, tuned via cross-validation)
  3. Train/val/holdout strict separation
  4. Reports train-val-holdout gap to detect overfitting
  5. Refuses to save if train-holdout gap > 15%

Usage:
    python scripts/train_embedding_classifier.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "LLMRouterBench"))
sys.path.insert(0, str(ROOT / "UncommonRoute"))


def load_split(path: Path, model):
    """Load a JSONL split — returns embeddings, metadata features, and labels."""
    from uncommon_route.signals.embedding import _extract_last_user_message, EmbeddingSignal

    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    texts = []
    meta_feats = []
    labels = []
    for row in rows:
        messages = row.get("messages", [])
        text = _extract_last_user_message(messages)
        if text.strip():
            texts.append(text)
            meta_feats.append(EmbeddingSignal._extract_meta_features(messages, text))
            labels.append(row["target_tier_id"])

    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return (
        np.array(embeddings, dtype=np.float32),
        np.array(meta_feats, dtype=np.float32),
        np.array(labels, dtype=np.int32),
    )


def main():
    splits_dir = Path("uncommon_route/data/v2_splits")

    # Check all splits exist
    for name in ("train.jsonl", "calibration.jsonl", "holdout.jsonl"):
        if not (splits_dir / name).exists():
            print(f"ERROR: {splits_dir / name} not found. Run split_data.py first.")
            sys.exit(1)

    print("Loading embedding model (frozen, no fine-tuning)...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")

    print("Computing embeddings + metadata features for each split...")
    E_train, M_train, y_train = load_split(splits_dir / "train.jsonl", model)
    E_cal, M_cal, y_cal = load_split(splits_dir / "calibration.jsonl", model)
    E_holdout, M_holdout, y_holdout = load_split(splits_dir / "holdout.jsonl", model)

    print(f"  Train:      {len(y_train)} samples ({E_train.shape[1]}d emb + {M_train.shape[1]} meta)")
    print(f"  Calibration: {len(y_cal)} samples")
    print(f"  Holdout:     {len(y_holdout)} samples")

    # Scale metadata features
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    M_train_s = scaler.fit_transform(M_train)
    M_cal_s = scaler.transform(M_cal)
    M_holdout_s = scaler.transform(M_holdout)

    # Combine embedding + scaled metadata
    X_train = np.hstack([E_train, M_train_s])
    X_cal = np.hstack([E_cal, M_cal_s])
    X_holdout = np.hstack([E_holdout, M_holdout_s])

    # Train L2-regularized logistic regression with cross-validation for C
    from sklearn.linear_model import LogisticRegressionCV

    print("\nTraining logistic regression on embedding+metadata features...")
    clf = LogisticRegressionCV(
        Cs=[0.01, 0.1, 0.5, 1.0, 5.0, 10.0],
        cv=5,
        penalty="l2",
        solver="lbfgs",
        max_iter=2000,
        random_state=42,
    )
    clf.fit(X_train, y_train)

    best_C = clf.C_[0]
    print(f"  Best C: {best_C}")

    # Evaluate on all splits
    train_acc = clf.score(X_train, y_train)
    cal_acc = clf.score(X_cal, y_cal)
    holdout_acc = clf.score(X_holdout, y_holdout)

    print(f"\n{'='*60}")
    print(f"  OVERFITTING CHECK")
    print(f"{'='*60}")
    print(f"  Train accuracy:      {train_acc:.1%}")
    print(f"  Calibration accuracy: {cal_acc:.1%}")
    print(f"  Holdout accuracy:     {holdout_acc:.1%}")
    print(f"  Train-Holdout gap:    {(train_acc - holdout_acc)*100:.1f} percentage points")

    gap = train_acc - holdout_acc
    if gap > 0.15:
        print(f"\n  ⚠️ OVERFITTING DETECTED: gap={gap:.1%} > 15%")
        print(f"  Model NOT saved. Increase regularization or reduce features.")
        sys.exit(1)
    elif gap > 0.10:
        print(f"\n  ⚠ Moderate gap ({gap:.1%}). Proceeding with caution.")
    else:
        print(f"\n  ✓ Gap is acceptable ({gap:.1%} <= 10%)")

    # Per-tier accuracy on holdout
    print(f"\n  Per-tier holdout accuracy:")
    tier_names = ["low", "mid", "mid_high", "high"]
    for tid in range(4):
        mask = y_holdout == tid
        if mask.sum() > 0:
            tier_acc = clf.score(X_holdout[mask], y_holdout[mask])
            print(f"    {tier_names[tid]:10s}  n={mask.sum():3d}  acc={tier_acc:.1%}")

    # Compare with KNN baseline
    from sklearn.neighbors import KNeighborsClassifier
    knn = KNeighborsClassifier(n_neighbors=7, weights="distance")
    knn.fit(X_train, y_train)
    knn_holdout_acc = knn.score(X_holdout, y_holdout)
    print(f"\n  KNN baseline (K=7) holdout: {knn_holdout_acc:.1%}")
    print(f"  LogReg improvement:         +{(holdout_acc - knn_holdout_acc)*100:.1f} pts")

    # Save the model + scaler
    import pickle
    out_path = splits_dir / "embedding_classifier.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(clf, f)
    scaler_path = splits_dir / "meta_scaler.pkl"
    with open(scaler_path, "wb") as f:
        pickle.dump(scaler, f)
    print(f"\n  Saved classifier to {out_path}")
    print(f"  Saved metadata scaler to {scaler_path}")

    # Also save the embeddings for holdout (for E2E validation)
    np.save(splits_dir / "holdout_embeddings.npy", X_holdout)
    np.save(splits_dir / "holdout_labels.npy", y_holdout)


if __name__ == "__main__":
    main()
