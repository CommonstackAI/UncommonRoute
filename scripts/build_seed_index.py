"""Build embedding seed index from train split.

Usage: python scripts/build_seed_index.py
"""

from __future__ import annotations

import json
import numpy as np
from pathlib import Path


def main():
    train_path = Path("uncommon_route/data/v2_splits/train.jsonl")
    if not train_path.exists():
        raise FileNotFoundError(f"Run split_data.py first: {train_path}")

    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("BAAI/bge-small-en-v1.5")

    rows = []
    with open(train_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    texts, labels = [], []
    for row in rows:
        for m in reversed(row.get("messages", [])):
            if m.get("role") == "user":
                text = m.get("content", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
                    labels.append(row["target_tier_id"])
                break

    print(f"Embedding {len(texts)} texts...")
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    out_dir = Path("uncommon_route/data/v2_splits")
    np.save(out_dir / "seed_embeddings.npy", embeddings)
    with open(out_dir / "seed_labels.json", "w") as f:
        json.dump(labels, f)
    print(f"Saved: {embeddings.shape[0]} embeddings ({embeddings.shape[1]}d) + labels")


if __name__ == "__main__":
    main()
