import pytest

from uncommon_route.signals.base import TierVote, Signal


def test_tier_vote_active():
    vote = TierVote(tier_id=2, confidence=0.8)
    assert vote.tier_id == 2
    assert vote.confidence == 0.8
    assert not vote.abstained


def test_tier_vote_abstain():
    vote = TierVote(tier_id=None, confidence=0.0)
    assert vote.tier_id is None
    assert vote.abstained


def test_signal_is_protocol():
    class DummySignal:
        def predict(self, row: dict) -> TierVote:
            return TierVote(tier_id=0, confidence=1.0)

    sig = DummySignal()
    result = sig.predict({"messages": []})
    assert result.tier_id == 0


def test_tier_vote_invalid_tier_id():
    with pytest.raises(ValueError):
        TierVote(tier_id=5, confidence=0.5)


def test_tier_vote_invalid_confidence():
    with pytest.raises(ValueError):
        TierVote(tier_id=0, confidence=1.5)


def test_tier_vote_float_tier_id_rejected():
    with pytest.raises(TypeError):
        TierVote(tier_id=1.5, confidence=0.5)
