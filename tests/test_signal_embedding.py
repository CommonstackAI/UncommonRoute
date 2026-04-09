import json
import numpy as np
from pathlib import Path

from uncommon_route.signals.embedding import EmbeddingSignal, _extract_last_user_message


def _make_seed_index(tmp_path: Path):
    dim = 384
    rng = np.random.RandomState(42)
    embeddings = rng.randn(4, dim).astype(np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms
    labels = [0, 1, 2, 3]
    np.save(tmp_path / "seed_embeddings.npy", embeddings)
    with open(tmp_path / "seed_labels.json", "w") as f:
        json.dump(labels, f)
    return embeddings, labels


def test_embedding_signal_with_seed_index(tmp_path):
    embs, labels = _make_seed_index(tmp_path)
    sig = EmbeddingSignal(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        model_name=None,
    )
    sig._embed_fn = lambda text: embs[0]
    row = {"messages": [{"role": "user", "content": "test"}]}
    vote = sig.predict(row)
    assert vote.tier_id == 0
    assert 0.0 <= vote.confidence <= 1.0


def test_embedding_signal_abstains_when_no_index(tmp_path):
    sig = EmbeddingSignal(
        index_path=tmp_path / "nonexistent.npy",
        labels_path=tmp_path / "nonexistent.json",
        model_name=None,
    )
    row = {"messages": [{"role": "user", "content": "test"}]}
    vote = sig.predict(row)
    assert vote.abstained


def test_extract_last_user_message():
    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "Answer"},
        {"role": "user", "content": "Second question"},
    ]
    assert _extract_last_user_message(messages) == "Second question"


def test_extract_last_user_message_empty():
    assert _extract_last_user_message([]) == ""
    assert _extract_last_user_message([{"role": "assistant", "content": "hi"}]) == ""


def test_embedding_signal_abstains_on_empty_user_message(tmp_path):
    embs, labels = _make_seed_index(tmp_path)
    sig = EmbeddingSignal(
        index_path=tmp_path / "seed_embeddings.npy",
        labels_path=tmp_path / "seed_labels.json",
        model_name=None,
    )
    sig._embed_fn = lambda text: embs[0]
    row = {"messages": [{"role": "assistant", "content": "no user msg here"}]}
    vote = sig.predict(row)
    assert vote.abstained
