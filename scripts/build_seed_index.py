"""Build embedding seed index from train split.

Usage: python scripts/build_seed_index.py
"""

from __future__ import annotations

import json
import numpy as np
from pathlib import Path


def _extract_text(content) -> str:
    """Normalize message content — handles both string and list formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # OpenAI multi-part format: [{"type": "text", "text": "..."}, ...]
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return str(content) if content else ""


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
                text = _extract_text(m.get("content", ""))
                if text.strip():
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
