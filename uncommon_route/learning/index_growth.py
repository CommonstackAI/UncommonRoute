"""Embedding index growth with deduplication and cap."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("uncommon-route.index")


class EmbeddingIndexManager:
    def __init__(
        self,
        index_path: Path,
        labels_path: Path,
        max_size: int = 10_000,
        dedup_threshold: float = 0.95,
    ):
        self._index_path = Path(index_path)
        self._labels_path = Path(labels_path)
        self._max_size = max_size
        self._dedup_threshold = dedup_threshold

        if self._index_path.exists() and self._labels_path.exists():
            self._embeddings = np.load(self._index_path)
            with open(self._labels_path, encoding="utf-8") as f:
                self._labels = json.load(f)
        else:
            self._embeddings = np.empty((0, 384), dtype=np.float32)
            self._labels = []

    @property
    def size(self) -> int:
        return len(self._labels)

    def add(self, embedding: np.ndarray, tier_id: int) -> bool:
        if self.size >= self._max_size:
            self._prune_oldest(self._max_size - 1)
        if self.size > 0:
            sims = self._embeddings @ embedding
            if np.max(sims) >= self._dedup_threshold:
                max_idx = int(np.argmax(sims))
                if self._labels[max_idx] == tier_id:
                    return False
        self._embeddings = np.vstack([self._embeddings, embedding.reshape(1, -1)])
        self._labels.append(tier_id)
        return True

    def _prune_oldest(self, keep: int) -> None:
        if self.size <= keep:
            return
        self._embeddings = self._embeddings[-keep:]
        self._labels = self._labels[-keep:]

    def save(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self._index_path, self._embeddings)
        with open(self._labels_path, "w", encoding="utf-8") as f:
            json.dump(self._labels, f)
