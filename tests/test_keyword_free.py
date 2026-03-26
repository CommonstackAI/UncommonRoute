"""Test: can the classifier work WITHOUT any keyword features?

Goal: prove that structural + Unicode + n-gram features alone
can classify prompts correctly, making keyword lists unnecessary.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

from uncommon_route.router.structural import (
    extract_structural_features,
    extract_unicode_block_features,
)
from uncommon_route.router.learned import ScriptAgnosticClassifier


def _load_training_data() -> list[dict]:
    """Load train.jsonl only (not all data files)."""
    data_dir = Path(__file__).parent.parent / "bench" / "data"
    train_path = data_dir / "train.jsonl"
    cases = []
    with open(train_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _extract_keyword_free_features(prompt: str, model: ScriptAgnosticClassifier) -> dict[str, float]:
    """Extract features WITHOUT any keyword lists — structural + Unicode + n-grams only."""
    struct_dims = extract_structural_features(prompt)
    structural_scores = {d.name: d.score for d in struct_dims}

    unicode_blocks = extract_unicode_block_features(prompt)

    features: dict[str, float] = {}
    for name, score in structural_scores.items():
        features[f"s_{name}"] = score
    for name, prop in unicode_blocks.items():
        features[f"u_{name}"] = prop

    # N-gram features (these learn keyword-equivalent patterns from data)
    from uncommon_route.router.learned import _extract_ngram_features
    ngram_feats = _extract_ngram_features(prompt)
    for k, v in ngram_feats.items():
        features[k] = v * 0.5  # give n-grams more weight since no keywords

    return features


def _extract_full_features(prompt: str, model: ScriptAgnosticClassifier) -> dict[str, float]:
    """Extract features (same as keyword-free since keywords were removed)."""
    struct_dims = extract_structural_features(prompt)
    structural_scores = {d.name: d.score for d in struct_dims}
    unicode_blocks = extract_unicode_block_features(prompt)

    return model._build_features(structural_scores, unicode_blocks, keyword_scores=None, prompt=prompt)


def _collapse_tier(tier: str) -> str:
    t = tier.strip().upper()
    if t == "REASONING":
        return "COMPLEX"
    return t


def test_keyword_free_training_accuracy():
    """Train two models: one with keywords, one without. Compare accuracy."""
    cases = _load_training_data()
    if len(cases) < 50:
        print(f"\n  Only {len(cases)} training cases found, skipping")
        return

    # Prepare feature sets for both approaches
    model_with_kw = ScriptAgnosticClassifier(use_ngrams=True)
    model_no_kw = ScriptAgnosticClassifier(use_ngrams=True)

    features_with_kw: list[tuple[dict[str, float], str]] = []
    features_no_kw: list[tuple[dict[str, float], str]] = []

    valid_tiers = {"SIMPLE", "MEDIUM", "COMPLEX"}
    for case in cases:
        prompt = case["prompt"]
        tier = _collapse_tier(case["expected_tier"])
        if tier not in valid_tiers:
            continue
        features_with_kw.append((_extract_full_features(prompt, model_with_kw), tier))
        features_no_kw.append((_extract_keyword_free_features(prompt, model_no_kw), tier))

    print(f"\n  Training on {len(features_no_kw)} examples")

    # Train both models
    model_with_kw.train(features_with_kw, epochs=12)
    model_no_kw.train(features_no_kw, epochs=12)

    # Evaluate on training data (overfitting expected, but we want to see the gap)
    correct_with_kw = 0
    correct_no_kw = 0
    disagreements = []

    for i, case in enumerate(cases):
        prompt = case["prompt"]
        tier = _collapse_tier(case["expected_tier"])
        if tier not in valid_tiers:
            continue

        pred_kw, _ = model_with_kw.predict(features_with_kw[i][0] if i < len(features_with_kw) else {})
        pred_no_kw, _ = model_no_kw.predict(features_no_kw[i][0] if i < len(features_no_kw) else {})

        if pred_kw == tier:
            correct_with_kw += 1
        if pred_no_kw == tier:
            correct_no_kw += 1

        if pred_kw != pred_no_kw:
            disagreements.append((prompt[:60], tier, pred_kw, pred_no_kw))

    total = len(features_no_kw)
    acc_kw = correct_with_kw / total * 100
    acc_no_kw = correct_no_kw / total * 100

    print(f"  WITH keywords:    {correct_with_kw}/{total} = {acc_kw:.1f}%")
    print(f"  WITHOUT keywords: {correct_no_kw}/{total} = {acc_no_kw:.1f}%")
    print(f"  Gap: {acc_kw - acc_no_kw:+.1f}pp")
    print(f"  Disagreements: {len(disagreements)}/{total}")

    if disagreements:
        print(f"\n  Sample disagreements (first 10):")
        for prompt, expected, pred_kw, pred_no_kw in disagreements[:10]:
            print(f"    '{prompt}...'")
            print(f"      expected={expected}  with_kw={pred_kw}  no_kw={pred_no_kw}")


def test_keyword_free_held_out():
    """Train on train.jsonl, test on test.jsonl — proper held-out evaluation."""
    data_dir = Path(__file__).parent.parent / "bench" / "data"
    train_path = data_dir / "train.jsonl"
    test_path = data_dir / "test.jsonl"

    if not train_path.exists() or not test_path.exists():
        print(f"\n  train.jsonl or test.jsonl not found, skipping held-out test")
        return

    def load_jsonl(path):
        cases = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
        return cases

    train_cases = load_jsonl(train_path)
    test_cases = load_jsonl(test_path)

    model_no_kw = ScriptAgnosticClassifier(use_ngrams=True)
    model_with_kw = ScriptAgnosticClassifier(use_ngrams=True)
    valid_tiers = {"SIMPLE", "MEDIUM", "COMPLEX"}

    train_features_no_kw = []
    train_features_kw = []
    for case in train_cases:
        tier = _collapse_tier(case["expected_tier"])
        if tier not in valid_tiers:
            continue
        train_features_no_kw.append((_extract_keyword_free_features(case["prompt"], model_no_kw), tier))
        train_features_kw.append((_extract_full_features(case["prompt"], model_with_kw), tier))

    model_no_kw.train(train_features_no_kw, epochs=12)
    model_with_kw.train(train_features_kw, epochs=12)

    print(f"\n  Trained on {len(train_features_no_kw)} examples, testing on {len(test_cases)}")

    correct_kw = 0
    correct_no_kw = 0
    total = 0

    for case in test_cases:
        tier = _collapse_tier(case["expected_tier"])
        if tier not in valid_tiers:
            continue
        total += 1

        feats_kw = _extract_full_features(case["prompt"], model_with_kw)
        feats_no_kw = _extract_keyword_free_features(case["prompt"], model_no_kw)

        pred_kw, _ = model_with_kw.predict(feats_kw)
        pred_no_kw, _ = model_no_kw.predict(feats_no_kw)

        if pred_kw == tier:
            correct_kw += 1
        if pred_no_kw == tier:
            correct_no_kw += 1

    acc_kw = correct_kw / total * 100 if total > 0 else 0
    acc_no_kw = correct_no_kw / total * 100 if total > 0 else 0

    print(f"  Held-out WITH keywords:    {correct_kw}/{total} = {acc_kw:.1f}%")
    print(f"  Held-out WITHOUT keywords: {correct_no_kw}/{total} = {acc_no_kw:.1f}%")
    print(f"  Gap: {acc_kw - acc_no_kw:+.1f}pp")


def test_keyword_free_specific_cases():
    """Test specific prompts that rely on keyword detection today."""
    model = ScriptAgnosticClassifier(use_ngrams=True)

    cases = _load_training_data()
    valid_tiers = {"SIMPLE", "MEDIUM", "COMPLEX"}
    features = [
        (_extract_keyword_free_features(c["prompt"], model), _collapse_tier(c["expected_tier"]))
        for c in cases
        if _collapse_tier(c["expected_tier"]) in valid_tiers
    ]
    model.train(features, epochs=12)

    test_prompts = [
        ("hello", "SIMPLE"),
        ("what is 2+2?", "SIMPLE"),
        ("translate hello to French", "SIMPLE"),
        ("prove that sqrt(2) is irrational", "COMPLEX"),
        ("implement a distributed consensus algorithm with Byzantine fault tolerance", "COMPLEX"),
        ("write a Python function to sort a list", "MEDIUM"),
        ("explain what a closure is in JavaScript", "MEDIUM"),
        ("design a microservice architecture with event sourcing, CQRS, and saga patterns", "COMPLEX"),
        ("你好", "SIMPLE"),
        ("用Python写一个快速排序", "MEDIUM"),
        ("证明哥德尔不完备定理", "COMPLEX"),
    ]

    print(f"\n  Keyword-free classifier on specific cases:")
    correct = 0
    total = len(test_prompts)
    for prompt, expected in test_prompts:
        feats = _extract_keyword_free_features(prompt, model)
        complexity, pred, conf = model.predict_complexity(feats)
        match = "✓" if pred == expected else "✗"
        if pred == expected:
            correct += 1
        print(f"    {match} '{prompt[:50]:50s}' expected={expected:8s} got={pred:8s} complexity={complexity:.2f}")

    print(f"\n  Accuracy: {correct}/{total} = {correct/total*100:.0f}%")
