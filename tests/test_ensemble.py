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
    """When best_tier=3, conservative should stay at 3 (not go to 4)."""
    votes = [TierVote(tier_id=3, confidence=0.4), TierVote(tier_id=3, confidence=0.3)]
    ens = Ensemble(weights=[0.5, 0.5], risk_tolerance=0.0)  # very conservative threshold
    result = ens.decide(votes)
    assert result.tier_id == 3
    assert result.method in ("direct", "conservative")


def test_votes_weights_length_mismatch():
    """Mismatched lengths should raise ValueError."""
    import pytest
    votes = [TierVote(tier_id=0, confidence=0.8)]
    ens = Ensemble(weights=[0.5, 0.5])  # 2 weights but 1 vote
    with pytest.raises(ValueError):
        ens.decide(votes)
