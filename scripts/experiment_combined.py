"""Experiment: Embedding + Metadata combined features."""

import json, numpy as np, sys
from pathlib import Path
from collections import Counter
from sklearn.linear_model import LogisticRegressionCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from uncommon_route.signals.embedding import _normalize_content

d = Path("uncommon_route/data/v2_splits")

from sentence_transformers import SentenceTransformer
model = SentenceTransformer("BAAI/bge-small-en-v1.5")

def load_features(path):
    rows = []
    with open(path) as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    texts, meta_feats, labels = [], [], []
    for row in rows:
        msgs = row.get("messages", [])
        text = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                text = _normalize_content(m.get("content", ""))
                break
        if not text.strip():
            continue
        msg_count = len(msgs)
        has_tools = int(any(m.get("role") == "tool" or m.get("tool_calls") for m in msgs))
        tool_count = sum(1 for m in msgs if m.get("role") == "tool" or m.get("tool_calls"))
        user_len = len(text)
        user_words = len(text.split())
        has_code = int("```" in text)
        has_question = int("?" in text[-50:] if len(text) > 50 else "?" in text)
        meta_feats.append([msg_count, has_tools, tool_count, user_len, user_words, has_code, has_question])
        texts.append(text)
        labels.append(row["target_tier_id"])
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings, np.array(meta_feats, dtype=float), labels

print("Loading...")
train_emb, train_meta, train_labels = load_features(d / "train.jsonl")
hold_emb, hold_meta, hold_labels = load_features(d / "holdout.jsonl")

scaler = StandardScaler()
train_meta_s = scaler.fit_transform(train_meta)
hold_meta_s = scaler.transform(hold_meta)

train_combined = np.hstack([train_emb, train_meta_s])
hold_combined = np.hstack([hold_emb, hold_meta_s])

tier_names = {0: "LOW", 1: "MID", 2: "MH", 3: "HIGH"}

experiments = [
    ("Embedding + LogReg (baseline)", train_emb, hold_emb, LogisticRegressionCV(max_iter=2000, random_state=42)),
    ("Metadata only + GBT", train_meta_s, hold_meta_s, GradientBoostingClassifier(n_estimators=200, max_depth=4, random_state=42)),
    ("Embed+Meta + LogReg", train_combined, hold_combined, LogisticRegressionCV(max_iter=2000, random_state=42)),
    ("Embed+Meta + GBT", train_combined, hold_combined, GradientBoostingClassifier(n_estimators=200, max_depth=4, random_state=42)),
]

print()
print(f"{'Method':35s} | Overall | LOW  MID  MH   HIGH")
print("-" * 70)
for name, tr_x, ho_x, clf in experiments:
    clf.fit(tr_x, train_labels)
    preds = clf.predict(ho_x)
    acc = accuracy_score(hold_labels, preds)
    per = {}
    for t in range(4):
        mask = [l == t for l in hold_labels]
        if any(mask):
            per[t] = accuracy_score(
                [l for l, m in zip(hold_labels, mask) if m],
                [p for p, m in zip(preds, mask) if m],
            )
    print(f"{name:35s} | {acc:5.1%}   | {per.get(0,0):3.0%}  {per.get(1,0):3.0%}  {per.get(2,0):3.0%}  {per.get(3,0):3.0%}")

if __name__ == "__main__":
    pass
