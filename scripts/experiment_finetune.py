"""Experiment: Contrastive fine-tuning of bge-small for tier classification.

Uses sentence-transformers directly with contrastive pairs.
Same idea as SetFit but without the dependency issues.
"""

import json
import numpy as np
from pathlib import Path
from collections import Counter
from sklearn.linear_model import LogisticRegressionCV
from sklearn.metrics import accuracy_score
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uncommon_route.signals.embedding import _normalize_content


def load_split(path):
    rows = []
    with open(path) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    texts, labels = [], []
    for row in rows:
        for m in reversed(row.get("messages", [])):
            if m.get("role") == "user":
                text = _normalize_content(m.get("content", ""))
                if text.strip():
                    texts.append(text)
                    labels.append(row["target_tier_id"])
                break
    return texts, labels


def make_contrastive_pairs(texts, labels, n_pairs=5000, seed=42):
    """Generate (text_a, text_b, label) pairs for contrastive learning.
    label=1.0 if same tier, label=0.0 if different tier.
    """
    rng = random.Random(seed)
    by_tier = {}
    for i, (t, l) in enumerate(zip(texts, labels)):
        by_tier.setdefault(l, []).append(i)

    pairs = []
    tiers = list(by_tier.keys())

    for _ in range(n_pairs):
        if rng.random() < 0.5:
            # Positive pair (same tier)
            tier = rng.choice(tiers)
            if len(by_tier[tier]) < 2:
                continue
            i, j = rng.sample(by_tier[tier], 2)
            pairs.append((texts[i], texts[j], 1.0))
        else:
            # Negative pair (different tier)
            t1, t2 = rng.sample(tiers, 2)
            i = rng.choice(by_tier[t1])
            j = rng.choice(by_tier[t2])
            pairs.append((texts[i], texts[j], 0.0))

    return pairs


def main():
    d = Path("uncommon_route/data/v2_splits")
    train_texts, train_labels = load_split(d / "train.jsonl")
    holdout_texts, holdout_labels = load_split(d / "holdout.jsonl")
    tier_names = {0: "LOW", 1: "MID", 2: "MID_HIGH", 3: "HIGH"}

    print(f"Train: {len(train_texts)}, dist={Counter(train_labels)}")
    print(f"Holdout: {len(holdout_texts)}, dist={Counter(holdout_labels)}")

    # ─── Baseline ───
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    print("\n=== Baseline: Frozen bge-small + LogReg ===")
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    train_emb = model.encode(train_texts, normalize_embeddings=True, show_progress_bar=False)
    holdout_emb = model.encode(holdout_texts, normalize_embeddings=True, show_progress_bar=False)

    clf = LogisticRegressionCV(max_iter=2000, random_state=42)
    clf.fit(train_emb, train_labels)
    base_preds = clf.predict(holdout_emb)
    base_acc = accuracy_score(holdout_labels, base_preds)
    print(f"  Accuracy: {base_acc:.1%}")
    for t in range(4):
        mask = [l == t for l in holdout_labels]
        if any(mask):
            ta = accuracy_score([l for l, m in zip(holdout_labels, mask) if m],
                               [p for p, m in zip(base_preds, mask) if m])
            print(f"  {tier_names[t]:8s} n={sum(mask):3d}  acc={ta:.0%}")

    # ─── Fine-tune with contrastive learning ───
    print("\n=== Fine-tuning bge-small with contrastive pairs ===")
    pairs = make_contrastive_pairs(train_texts, train_labels, n_pairs=8000, seed=42)
    print(f"  Generated {len(pairs)} contrastive pairs")

    train_examples = [InputExample(texts=[a, b], label=l) for a, b, l in pairs]
    train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=32)
    train_loss = losses.CosineSimilarityLoss(model)

    print("  Training (3 epochs)...")
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=3,
        warmup_steps=100,
        show_progress_bar=True,
    )

    # ─── Evaluate fine-tuned ───
    print("\n=== Fine-tuned bge-small + LogReg ===")
    ft_train_emb = model.encode(train_texts, normalize_embeddings=True, show_progress_bar=False)
    ft_holdout_emb = model.encode(holdout_texts, normalize_embeddings=True, show_progress_bar=False)

    clf_ft = LogisticRegressionCV(max_iter=2000, random_state=42)
    clf_ft.fit(ft_train_emb, train_labels)
    ft_preds = clf_ft.predict(ft_holdout_emb)
    ft_acc = accuracy_score(holdout_labels, ft_preds)
    print(f"  Accuracy: {ft_acc:.1%}")
    for t in range(4):
        mask = [l == t for l in holdout_labels]
        if any(mask):
            ta = accuracy_score([l for l, m in zip(holdout_labels, mask) if m],
                               [p for p, m in zip(ft_preds, mask) if m])
            print(f"  {tier_names[t]:8s} n={sum(mask):3d}  acc={ta:.0%}")

    # ─── Summary ───
    print(f"\n{'='*50}")
    print(f"Frozen:     {base_acc:.1%}")
    print(f"Fine-tuned: {ft_acc:.1%}  ({'+' if ft_acc >= base_acc else ''}{(ft_acc-base_acc)*100:.1f}pp)")
    print(f"{'='*50}")

    if ft_acc > base_acc:
        save_path = d / "finetuned_bge_small"
        model.save(str(save_path))
        print(f"\nModel saved to {save_path}")

        # Also save the new classifier
        import pickle
        with open(d / "embedding_classifier_ft.pkl", "wb") as f:
            pickle.dump(clf_ft, f)
        np.save(d / "seed_embeddings_ft.npy", ft_train_emb)
        print("Saved fine-tuned embeddings + classifier")


if __name__ == "__main__":
    main()
