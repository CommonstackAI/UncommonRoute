"""Signal protocol and TierVote dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class TierVote:
    """A signal's prediction: tier_id (0-3) or None to abstain."""
    tier_id: int | None
    confidence: float

    @property
    def abstained(self) -> bool:
        return self.tier_id is None


class Signal(Protocol):
    """Protocol for v2 routing signals."""
    def predict(self, row: dict[str, Any]) -> TierVote: ...
