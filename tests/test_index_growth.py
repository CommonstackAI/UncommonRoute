import json
import numpy as np
from pathlib import Path
from uncommon_route.learning.index_growth import EmbeddingIndexManager


def _make_index(tmp_path, n=5, dim=384):
    rng = np.random.RandomState(42)
    embs = rng.randn(n, dim).astype(np.float32)
    embs = embs / np.linalg.norm(embs, axis=1, keepdims=True)
    labels = [0] * n
    np.save(tmp_path / "seed_embeddings.npy", embs)
    with open(tmp_path / "seed_labels.json", "w") as f:
        json.dump(labels, f)
    return embs


def test_add_entry(tmp_path):
    _make_index(tmp_path, n=5)
    mgr = EmbeddingIndexManager(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        max_size=100,
    )
    assert mgr.size == 5
    new_vec = np.random.randn(384).astype(np.float32)
    new_vec = new_vec / np.linalg.norm(new_vec)
    mgr.add(new_vec, tier_id=2)
    assert mgr.size == 6


def test_dedup_rejects_near_duplicate(tmp_path):
    embs = _make_index(tmp_path, n=5)
    mgr = EmbeddingIndexManager(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        max_size=100,
        dedup_threshold=0.95,
    )
    near_dup = embs[0] + np.random.randn(384).astype(np.float32) * 0.001
    near_dup = near_dup / np.linalg.norm(near_dup)
    added = mgr.add(near_dup, tier_id=0)
    assert not added
    assert mgr.size == 5


def test_cap_enforced(tmp_path):
    _make_index(tmp_path, n=5)
    mgr = EmbeddingIndexManager(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        max_size=7,
    )
    for i in range(5):
        vec = np.random.randn(384).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        mgr.add(vec, tier_id=i % 4)
    assert mgr.size <= 7


def test_save_load(tmp_path):
    _make_index(tmp_path, n=5)
    mgr = EmbeddingIndexManager(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        max_size=100,
    )
    vec = np.random.randn(384).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    mgr.add(vec, tier_id=3)
    mgr.save()
    mgr2 = EmbeddingIndexManager(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        max_size=100,
    )
    assert mgr2.size == 6
