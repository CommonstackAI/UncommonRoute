from uncommon_route.signals.base import TierVote
from uncommon_route.decision.ensemble import Ensemble, EnsembleResult


def test_unanimous_vote():
    votes = [TierVote(tier_id=0, confidence=0.8), TierVote(tier_id=0, confidence=0.7)]
    ens = Ensemble(weights=[0.5, 0.5])
    result = ens.decide(votes)
    assert result.tier_id == 0
    assert result.confidence > 0.5


def test_disagreement_goes_with_higher_weight():
    votes = [TierVote(tier_id=0, confidence=0.9), TierVote(tier_id=3, confidence=0.6)]
    ens = Ensemble(weights=[0.6, 0.4])
    result = ens.decide(votes)
    assert result.tier_id == 0


def test_abstaining_signal_excluded():
    votes = [TierVote(tier_id=2, confidence=0.7), TierVote(tier_id=None, confidence=0.0)]
    ens = Ensemble(weights=[0.5, 0.5])
    result = ens.decide(votes)
    assert result.tier_id == 2


def test_all_abstain():
    votes = [TierVote(tier_id=None, confidence=0.0), TierVote(tier_id=None, confidence=0.0)]
    ens = Ensemble(weights=[0.5, 0.5])
    result = ens.decide(votes)
    assert result.tier_id is None


def test_risk_tolerance_shifts_threshold():
    votes = [TierVote(tier_id=0, confidence=0.8), TierVote(tier_id=0, confidence=0.7)]
    ens_conservative = Ensemble(weights=[0.5, 0.5], risk_tolerance=0.0)
    ens_aggressive = Ensemble(weights=[0.5, 0.5], risk_tolerance=1.0)
    r_conservative = ens_conservative.decide(votes)
    r_aggressive = ens_aggressive.decide(votes)
    assert r_conservative.tier_id >= r_aggressive.tier_id


def test_result_has_method():
    votes = [TierVote(tier_id=1, confidence=0.9)]
    ens = Ensemble(weights=[1.0])
    result = ens.decide(votes)
    assert result.method in ("direct", "conservative")


def test_conservative_fallback_caps_at_tier_3():
    """When best_tier=3 but confidence is low, conservative path should cap at 3 (not 4)."""
    # tier 3 wins the vote but with only ~52% confidence → below conservative threshold
    votes = [TierVote(tier_id=3, confidence=0.8), TierVote(tier_id=1, confidence=0.7)]
    ens = Ensemble(weights=[0.5, 0.5], risk_tolerance=0.0)  # threshold = 0.55 + 0.15 = 0.70
    result = ens.decide(votes)
    # tier_3 score = 0.8*0.5 = 0.4, tier_1 score = 0.7*0.5 = 0.35, total = 0.75
    # normalized: tier_3 = 0.533, tier_1 = 0.467 → 0.533 < 0.70 → conservative path
    assert result.tier_id == 3  # capped at 3, not bumped to 4
    assert result.method == "conservative"


def test_conservative_bumps_tier_up():
    """When confidence is low and best_tier < 3, conservative should bump +1."""
    votes = [TierVote(tier_id=1, confidence=0.8), TierVote(tier_id=0, confidence=0.7)]
    ens = Ensemble(weights=[0.5, 0.5], risk_tolerance=0.0)  # high threshold
    result = ens.decide(votes)
    assert result.method == "conservative", f"Expected conservative, got {result.method}"
    assert result.tier_id == 2


def test_votes_weights_length_mismatch():
    """Mismatched lengths should raise ValueError."""
    import pytest
    votes = [TierVote(tier_id=0, confidence=0.8)]
    ens = Ensemble(weights=[0.5, 0.5])  # 2 weights but 1 vote
    with pytest.raises(ValueError):
        ens.decide(votes)
