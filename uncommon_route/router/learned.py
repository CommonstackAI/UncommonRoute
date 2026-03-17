"""Script-Agnostic Learned Classifier v2.

Input features (all script-agnostic):
  1. 12 structural feature scores (enumeration, sentence_count, code, math, ...)
  2. 15 Unicode block proportions (latin, cjk, hangul, arabic, cyrillic, ...)
  3. 12 keyword feature scores (optional, for same-script languages)
  Total: ~39 named features → Averaged Perceptron

Why this works across scripts:
  - Structural features are universal (commas, sentences, brackets work everywhere)
  - Unicode block features tell the model WHAT SCRIPT without needing specific chars
  - The model learns: "high CJK + question mark + short = SIMPLE" for ALL CJK languages
  - Keyword features add bonus for languages with known vocabulary

Online learning: model.update(features, tier) for incremental improvement.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

FEATURE_DIM_NGRAM = 4096
NGRAM_RANGE = (3, 5)


def _signed_hash(s: str) -> tuple[int, float]:
    h = 0
    for ch in s:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    sign = 1.0 if (h >> 16) & 1 == 0 else -1.0
    return h % FEATURE_DIM_NGRAM, sign


def _extract_ngram_features(text: str) -> dict[str, float]:
    """Char n-gram features with 'ngram_' prefix."""
    text_lower = text.lower().strip()
    features: dict[str, float] = defaultdict(float)
    for n in range(NGRAM_RANGE[0], NGRAM_RANGE[1] + 1):
        for i in range(len(text_lower) - n + 1):
            gram = text_lower[i:i + n]
            bucket, sign = _signed_hash(gram)
            features[f"ngram_{bucket}"] += sign
    norm = math.sqrt(sum(v * v for v in features.values())) or 1.0
    return {k: v / norm for k, v in features.items()}


class ScriptAgnosticClassifier:
    """Averaged Perceptron on structured features — generalizes across scripts.

    Feature groups:
      - structural_*: 12 scores from structural feature extractors
      - unicode_*: 15 Unicode block proportions
      - keyword_*: 12 scores from keyword extractors
      - ngram_*: char n-gram features (optional boost, low weight for unseen scripts)
    """

    TIERS = ("SIMPLE", "MEDIUM", "COMPLEX")
    TIER_ORDER = {"SIMPLE": 0, "MEDIUM": 1, "COMPLEX": 2}

    def __init__(self, use_ngrams: bool = True) -> None:
        self._weights: dict[str, dict[str, float]] = {t: defaultdict(float) for t in self.TIERS}
        self._avg_weights: dict[str, dict[str, float]] = {t: defaultdict(float) for t in self.TIERS}
        self._update_count = 0
        self._trained = False
        self._use_ngrams = use_ngrams

    @classmethod
    def _normalize_tier_label(cls, tier: str) -> str | None:
        normalized = str(tier).strip().upper()
        if normalized == "REASONING":
            return "COMPLEX"
        if normalized in cls.TIERS:
            return normalized
        return None

    def _build_features(
        self,
        structural_scores: dict[str, float],
        unicode_blocks: dict[str, float],
        keyword_scores: dict[str, float],
        prompt: str = "",
    ) -> dict[str, float]:
        """Build the full feature vector from component parts."""
        features: dict[str, float] = {}

        # Structural features (12 dims, script-agnostic)
        for name, score in structural_scores.items():
            features[f"s_{name}"] = score

        # Unicode block features (15 dims, script-agnostic)
        for name, proportion in unicode_blocks.items():
            features[f"u_{name}"] = proportion

        # Keyword features (12 dims, multilingual vocabulary)
        for name, score in keyword_scores.items():
            features[f"k_{name}"] = score

        # N-gram features (optional, same-script boost)
        if self._use_ngrams and prompt:
            ngram_feats = _extract_ngram_features(prompt)
            # Scale down n-grams: they're a bonus, not the backbone
            for k, v in ngram_feats.items():
                features[k] = v * 0.3

        return features

    def train(self, feature_sets: list[tuple[dict[str, float], str]], epochs: int = 10) -> None:
        """Train from pre-extracted features. Each item: (features_dict, tier_label)."""
        import random
        rng = random.Random(42)

        for _ in range(epochs):
            shuffled = list(feature_sets)
            rng.shuffle(shuffled)
            for features, tier in shuffled:
                normalized_tier = self._normalize_tier_label(tier)
                if normalized_tier is None:
                    continue
                self._do_update(features, normalized_tier)

        self._trained = True

    def update(self, features: dict[str, float], correct_tier: str) -> None:
        normalized_tier = self._normalize_tier_label(correct_tier)
        if normalized_tier is not None:
            self._do_update(features, normalized_tier)

    def _do_update(self, features: dict[str, float], correct_tier: str) -> None:
        self._update_count += 1
        scores = self._score_raw(features, use_avg=False)
        predicted = max(scores, key=scores.get)  # type: ignore[arg-type]

        if predicted != correct_tier:
            for feat, val in features.items():
                self._weights[correct_tier][feat] += val
                self._weights[predicted][feat] -= val

        for tier in self.TIERS:
            for feat, val in self._weights[tier].items():
                self._avg_weights[tier][feat] += val

    def _score_raw(self, features: dict[str, float], use_avg: bool = True) -> dict[str, float]:
        weights = self._avg_weights if use_avg and self._update_count > 0 else self._weights
        scores: dict[str, float] = {}
        for tier in self.TIERS:
            w = weights[tier]
            scores[tier] = sum(val * w.get(feat, 0.0) for feat, val in features.items())
        return scores

    COMPLEXITY_ANCHORS = {"SIMPLE": 0.0, "MEDIUM": 0.40, "COMPLEX": 0.90}

    def predict(self, features: dict[str, float]) -> tuple[str, float]:
        """Predict tier from pre-extracted features."""
        if not self._trained:
            return ("MEDIUM", 0.0)

        scores = self._score_raw(features, use_avg=True)

        max_s = max(scores.values())
        exp_scores = {t: math.exp(min(s - max_s, 50)) for t, s in scores.items()}
        total = sum(exp_scores.values())
        probs = {t: e / total for t, e in exp_scores.items()}

        # Ordinal-aware tiebreaking
        sorted_tiers = sorted(probs, key=probs.get, reverse=True)  # type: ignore[arg-type]
        best = sorted_tiers[0]
        second = sorted_tiers[1]
        if probs[best] - probs[second] < 0.10:
            d_best = abs(self.TIER_ORDER[best] - 1)
            d_second = abs(self.TIER_ORDER[second] - 1)
            if d_second < d_best:
                best = second

        return (best, probs[best])

    def predict_complexity(self, features: dict[str, float]) -> tuple[float, str, float]:
        """Return ``(complexity, tier, confidence)``.

        ``complexity`` is a continuous 0.0–1.0 score derived from the
        softmax probability-weighted tier anchors.  It replaces discrete
        tier buckets with a smooth value that the selector can use to
        interpolate scoring weights.
        """
        if not self._trained:
            return (0.33, "MEDIUM", 0.0)

        scores = self._score_raw(features, use_avg=True)
        max_s = max(scores.values())
        exp_scores = {t: math.exp(min(s - max_s, 50)) for t, s in scores.items()}
        total = sum(exp_scores.values())
        probs = {t: e / total for t, e in exp_scores.items()}

        complexity = sum(probs[t] * self.COMPLEXITY_ANCHORS[t] for t in self.TIERS)
        complexity = max(0.0, min(1.0, complexity))

        best = max(probs, key=probs.get)  # type: ignore[arg-type]
        return (complexity, best, probs[best])

    def save(self, path: Path) -> None:
        data = {
            "avg_weights": {
                t: {k: v for k, v in w.items() if abs(v) > 1e-6}
                for t, w in self._avg_weights.items()
            },
            "weights": {
                t: {k: v for k, v in w.items() if abs(v) > 1e-6}
                for t, w in self._weights.items()
            },
            "update_count": self._update_count,
            "use_ngrams": self._use_ngrams,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    @classmethod
    def _collapse_loaded_weights(
        cls,
        raw_weights: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        collapsed: dict[str, dict[str, float]] = {}
        for tier in cls.TIERS:
            if tier != "COMPLEX":
                collapsed[tier] = dict(raw_weights.get(tier, {}))
                continue
            primary = raw_weights.get("COMPLEX", {})
            legacy_reasoning = raw_weights.get("REASONING", {})
            merged: dict[str, float] = {}
            for feat in set(primary) | set(legacy_reasoning):
                values: list[float] = []
                if feat in primary:
                    values.append(primary[feat])
                if feat in legacy_reasoning:
                    values.append(legacy_reasoning[feat])
                if values:
                    merged[feat] = sum(values) / len(values)
            collapsed[tier] = merged
        return collapsed

    def load(self, path: Path) -> None:
        data = json.loads(path.read_text())
        avg_weights = self._collapse_loaded_weights(data.get("avg_weights", {}))
        weights = self._collapse_loaded_weights(data.get("weights", {}))
        self._avg_weights = {
            t: defaultdict(float, avg_weights.get(t, {})) for t in self.TIERS
        }
        self._weights = {
            t: defaultdict(float, weights.get(t, {})) for t in self.TIERS
        }
        self._update_count = data.get("update_count", 1)
        self._use_ngrams = data.get("use_ngrams", True)
        self._trained = True


# Backward compat
NgramClassifier = ScriptAgnosticClassifier
PerceptronClassifier = ScriptAgnosticClassifier
