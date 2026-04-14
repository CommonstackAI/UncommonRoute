"""Signal C: embedding-based tier prediction.

Uses frozen bge-small embeddings with either:
  1. A trained classifier (logistic regression) — if embedding_classifier.pkl exists
  2. KNN fallback — distance-weighted vote of K nearest neighbors
"""

from __future__ import annotations

import json
import logging
import pickle
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import numpy as np

from uncommon_route.signals.base import TierVote

logger = logging.getLogger("uncommon-route.embedding")

K_NEIGHBORS = 7
MIN_CONFIDENCE_TO_VOTE = 0.3


def _extract_last_user_message(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a_norm = a / (np.linalg.norm(a) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return b_norm @ a_norm


class EmbeddingSignal:
    def __init__(
        self,
        index_path: Path | None = None,
        labels_path: Path | None = None,
        model_name: str | None = "BAAI/bge-small-en-v1.5",
        classifier_path: Path | None = None,
        use_classifier: bool = True,
        classifier_fallback_threshold: float = 0.80,
    ):
        self._embeddings: np.ndarray | None = None
        self._labels: list[int] | None = None
        self._embed_fn: Callable[[str], np.ndarray] | None = None
        self._classifier: Any = None  # sklearn classifier (optional)
        self._clf_fallback_threshold = classifier_fallback_threshold

        if index_path and Path(index_path).exists() and labels_path and Path(labels_path).exists():
            self._embeddings = np.load(index_path)
            with open(labels_path, encoding="utf-8") as f:
                self._labels = json.load(f)
            logger.info(f"Loaded embedding index: {len(self._labels)} vectors")

        # Try to load trained classifier (skip entirely when use_classifier=False)
        if use_classifier:
            if classifier_path and Path(classifier_path).exists():
                try:
                    with open(classifier_path, "rb") as f:
                        self._classifier = pickle.load(f)
                    logger.info("Loaded trained embedding classifier from %s", classifier_path)
                except Exception as e:
                    logger.warning("Failed to load classifier: %s — falling back to KNN", e)
            elif index_path:
                # Auto-detect classifier next to the index
                auto_clf = Path(index_path).parent / "embedding_classifier.pkl"
                if auto_clf.exists():
                    try:
                        with open(auto_clf, "rb") as f:
                            self._classifier = pickle.load(f)
                        logger.info("Auto-loaded embedding classifier from %s", auto_clf)
                    except Exception:
                        pass

        if model_name:
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer(model_name)
                self._embed_fn = lambda text: model.encode(text, normalize_embeddings=True)
            except ImportError:
                logger.warning("sentence-transformers not installed; embedding signal will abstain")
            except Exception as e:
                logger.warning(f"Failed to load embedding model {model_name}: {e}")

    def predict(self, row: dict[str, Any]) -> TierVote:
        if self._embed_fn is None:
            return TierVote(tier_id=None, confidence=0.0)

        text = _extract_last_user_message(row.get("messages", []))
        if not text.strip():
            return TierVote(tier_id=None, confidence=0.0)

        query_vec = self._embed_fn(text)

        # Hybrid: classifier first, KNN fallback when classifier is uncertain.
        # This is the unified approach for both production and preview paths.
        if self._classifier is not None:
            vote = self._predict_classifier(query_vec)
            if vote.confidence >= self._clf_fallback_threshold:
                return vote
            # Classifier unsure — fall back to KNN for semantic grounding
            if self._embeddings is not None and self._labels is not None:
                return self._predict_knn(query_vec)
            return vote

        # No classifier — KNN only
        if self._embeddings is None or self._labels is None:
            return TierVote(tier_id=None, confidence=0.0)
        return self._predict_knn(query_vec)

    def _predict_classifier(self, query_vec: np.ndarray) -> TierVote:
        """Predict using trained logistic regression on frozen embedding."""
        vec = query_vec.reshape(1, -1)
        pred = int(self._classifier.predict(vec)[0])
        proba = self._classifier.predict_proba(vec)[0]
        confidence = float(proba[pred])

        if confidence < MIN_CONFIDENCE_TO_VOTE:
            return TierVote(tier_id=None, confidence=confidence)
        return TierVote(tier_id=pred, confidence=confidence)

    def _predict_knn(self, query_vec: np.ndarray) -> TierVote:
        """Predict using KNN distance-weighted vote."""
        sims = _cosine_similarity(query_vec, self._embeddings)

        k = min(K_NEIGHBORS, len(self._labels))
        top_k_idx = np.argsort(sims)[-k:][::-1]
        top_k_sims = sims[top_k_idx]
        top_k_labels = [self._labels[i] for i in top_k_idx]

        # If top neighbors aren't similar enough, abstain — the query is
        # out-of-distribution relative to our training set.
        avg_top_sim = float(np.mean(top_k_sims[:3]))
        if avg_top_sim < 0.60:
            return TierVote(tier_id=None, confidence=0.0)

        tier_scores: dict[int, float] = Counter()
        for label, sim in zip(top_k_labels, top_k_sims):
            tier_scores[label] += max(0.0, float(sim))

        if not tier_scores:
            return TierVote(tier_id=None, confidence=0.0)

        total = sum(tier_scores.values())
        best_tier = max(tier_scores, key=lambda t: tier_scores[t])
        confidence = tier_scores[best_tier] / total if total > 0 else 0.0

        if confidence < MIN_CONFIDENCE_TO_VOTE:
            return TierVote(tier_id=None, confidence=confidence)

        return TierVote(tier_id=best_tier, confidence=confidence)
