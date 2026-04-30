import json
import logging
import numpy as np
from pathlib import Path

import uncommon_route.signals.embedding as embedding
from uncommon_route.signals.embedding import (
    EmbeddingSignal,
    _extract_agent_state,
    _extract_last_user_message,
)


def _make_seed_index(tmp_path: Path):
    dim = 384
    rng = np.random.RandomState(42)
    # Create cluster of similar vectors for tier 0 so KNN has enough
    # same-label neighbors to exceed MIN_CONFIDENCE_TO_VOTE (0.30).
    base = rng.randn(dim).astype(np.float32)
    vecs = [base + rng.randn(dim).astype(np.float32) * 0.2 for _ in range(5)]
    # Add a distant vector for tier 3
    far = rng.randn(dim).astype(np.float32)
    vecs.append(far)
    embeddings = np.array(vecs, dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms
    labels = [0, 0, 0, 0, 0, 3]
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


def test_embedding_signal_warns_when_auto_classifier_cannot_load(tmp_path, monkeypatch, caplog):
    _make_seed_index(tmp_path)
    (tmp_path / "embedding_classifier.pkl").write_bytes(b"not actually used")

    def raise_missing_dependency(_file):
        raise ModuleNotFoundError("No module named 'xgboost'")

    monkeypatch.setattr(embedding.pickle, "load", raise_missing_dependency)

    with caplog.at_level(logging.WARNING, logger="uncommon-route.embedding"):
        sig = EmbeddingSignal(
            index_path=tmp_path / "seed_embeddings.npy",
            labels_path=tmp_path / "seed_labels.json",
            model_name=None,
        )

    assert sig._classifier is None
    assert "Failed to auto-load embedding classifier" in caplog.text
    assert "xgboost" in caplog.text


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


def test_extract_agent_state_keeps_recent_intent_and_tool_arguments():
    messages = [
        {"role": "user", "content": "Fix the issue."},
        {"role": "assistant", "content": "I have confirmed the root cause. Now I will implement the fix."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({
                            "command": "sed -n '241,270p' /testbed/django/db/models/fields/json.py",
                        }),
                    },
                },
            ],
        },
        {"role": "tool", "content": "<returncode>0</returncode>\n<output>class JSONExact...</output>"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({"command": "cat /testbed/tests/test_sqlite.py"}),
                    },
                },
            ],
        },
        {"role": "tool", "content": "<returncode>0</returncode>\n<output>DATABASES = {...}</output>"},
    ]

    state = _extract_agent_state(messages)

    assert "confirmed the root cause" in state
    assert "sed -n '241,270p' /testbed/django/db/models/fields/json.py" in state
    assert "cat /testbed/tests/test_sqlite.py" in state


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
