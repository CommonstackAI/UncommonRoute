"""v2 API endpoints: route-preview and explain."""

from __future__ import annotations
from typing import Any

from uncommon_route.signals.metadata import MetadataSignal
from uncommon_route.signals.structural import StructuralSignal
from uncommon_route.signals.embedding import EmbeddingSignal
from uncommon_route.decision.ensemble import Ensemble
from uncommon_route.v2_tiers import ID_TO_TIER

_TIER_COST_ESTIMATE = {0: 0.0005, 1: 0.0012, 2: 0.003, 3: 0.02}

# Shared signal instances (initialized once, reused across requests)
_sig_a: MetadataSignal | None = None
_sig_b: StructuralSignal | None = None
_sig_c: EmbeddingSignal | None = None


def init_signals(index_dir=None):
    """Initialize signal singletons. Call once at proxy startup.

    If index_dir is not provided, auto-discovers the seed index
    from the standard user data directory (~/.uncommon-route/v2_splits/).
    """
    global _sig_a, _sig_b, _sig_c
    _sig_a = MetadataSignal()
    _sig_b = StructuralSignal()

    # Auto-discover seed index if not explicitly provided
    if not index_dir:
        try:
            from uncommon_route.paths import data_dir
            candidate = data_dir() / "v2_splits"
            if (candidate / "seed_embeddings.npy").exists():
                index_dir = str(candidate)
        except Exception:
            pass

    if index_dir:
        from pathlib import Path
        d = Path(index_dir)
        # For preview: use KNN (no classifier) because the logistic regression
        # classifier is heavily biased toward LOW on the imbalanced training set.
        # KNN preserves semantic similarity — e.g. "prove Kepler conjecture"
        # correctly matches complex math/reasoning neighbors.
        _sig_c = EmbeddingSignal(
            index_path=d / "seed_embeddings.npy",
            labels_path=d / "seed_labels.json",
            model_name="BAAI/bge-small-en-v1.5",
            use_classifier=False,  # force KNN — classifier is biased toward LOW
        )
    else:
        _sig_c = EmbeddingSignal(model_name=None)


def route_preview(
    prompt: str,
    risk_tolerance: float = 0.5,
    system_prompt: str | None = None,
    step_index: int = 1,
    total_steps: int = 1,
) -> dict[str, Any]:
    """Preview routing decision without sending the request."""
    global _sig_a, _sig_b, _sig_c
    if _sig_a is None:
        init_signals()

    row = {
        "messages": [{"role": "user", "content": prompt}],
        "benchmark": "", "scenario": "general",
        "step_index": step_index, "total_steps": total_steps,
    }
    if system_prompt:
        row["messages"].insert(0, {"role": "system", "content": system_prompt})

    vote_a = _sig_a.predict(row)
    vote_b = _sig_b.predict(row)
    vote_c = _sig_c.predict(row) if _sig_c else None

    # 3-signal ensemble for preview with adaptive weights.
    # MetadataSignal is constant for single prompts (always LOW 0.75) — low weight.
    # Short prompts: structural features are unreliable, trust embedding semantics.
    # Long prompts: structural features are informative, trust them more.
    word_count = len(prompt.split())
    if word_count <= 8:
        w_a, w_b, w_c = 0.10, 0.20, 0.70
    elif word_count <= 20:
        w_a, w_b, w_c = 0.15, 0.35, 0.50
    else:
        w_a, w_b, w_c = 0.20, 0.45, 0.35

    active_votes = [vote_a, vote_b]
    active_weights = [w_a, w_b]
    if vote_c and not vote_c.abstained:
        active_votes.append(vote_c)
        active_weights.append(w_c)

    ensemble = Ensemble(weights=active_weights, risk_tolerance=risk_tolerance)
    result = ensemble.decide(active_votes)
    tier = result.tier_id if result.tier_id is not None else 1

    signals = [
        {"name": "metadata", "tier": vote_a.tier_id, "confidence": round(vote_a.confidence, 4)},
        {"name": "structural", "tier": vote_b.tier_id, "confidence": round(vote_b.confidence, 4)},
    ]
    if vote_c:
        signals.append({"name": "embedding", "tier": vote_c.tier_id, "confidence": round(vote_c.confidence, 4)})

    return {
        "tier": tier,
        "tier_name": ID_TO_TIER.get(tier, "unknown"),
        "confidence": round(result.confidence, 4),
        "method": result.method,
        "signals": signals,
        "cost_estimate": _TIER_COST_ESTIMATE.get(tier, 0.02),
        "cost_baseline": _TIER_COST_ESTIMATE[3],
    }


def build_explain_response(
    signal_a: dict, signal_b: dict, signal_c: dict,
    decision_tier: int, decision_confidence: float,
    method: str, model: str,
    cost_estimate: float, cost_baseline: float,
) -> dict[str, Any]:
    """Build structured explanation response for a past routing decision."""
    savings = cost_baseline - cost_estimate
    savings_ratio = savings / cost_baseline if cost_baseline > 0 else 0.0
    return {
        "decision": {
            "tier": decision_tier,
            "tier_name": ID_TO_TIER.get(decision_tier, "unknown"),
            "confidence": round(decision_confidence, 4),
            "method": method, "model": model,
        },
        "signals": [
            {**signal_a, "name": "metadata"},
            {**signal_b, "name": "structural"},
            {**signal_c, "name": "embedding"},
        ],
        "cost": {
            "estimated": round(cost_estimate, 6),
            "baseline": round(cost_baseline, 6),
            "savings": round(savings, 6),
            "savings_ratio": round(savings_ratio, 4),
        },
    }
