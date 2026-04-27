from __future__ import annotations

from uncommon_route.router import api
from uncommon_route.router.types import RoutingFeatures
from uncommon_route.signals.base import TierVote


class _FakeSignal:
    def __init__(self, vote: TierVote) -> None:
        self.vote = vote
        self._embed_fn = None

    def predict(self, row: dict) -> TierVote:
        return self.vote


def test_soft_structural_floor_keeps_short_implementation_out_of_economy(monkeypatch) -> None:
    """A short implementation ask should not be flattened to SIMPLE by A+C."""
    monkeypatch.setattr(api, "_ensure_v2_signals", lambda: None)
    monkeypatch.setattr(api, "_v2_sig_a", _FakeSignal(TierVote(0, 0.75)))
    monkeypatch.setattr(api, "_v2_sig_b", _FakeSignal(TierVote(3, 0.70)))
    monkeypatch.setattr(api, "_v2_sig_c", _FakeSignal(TierVote(0, 0.44)))
    monkeypatch.setattr(api, "_v2_calibrator", None)

    result = api._v2_classify(
        "Implement a Python CLI app with tests, packaging, error handling, and documentation.",
        system_prompt=None,
        messages=None,
        routing_features=RoutingFeatures(),
        context_features=None,
    )

    assert result.tier_id == 1
    assert "v2:structural-medium-floor" in result.signals_text
