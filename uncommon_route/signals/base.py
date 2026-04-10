"""TierVote dataclass for v2 signal predictions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TierVote:
    """A signal's prediction: tier_id (0-3) or None to abstain."""
    tier_id: int | None
    confidence: float

    def __post_init__(self):
        if self.tier_id is not None:
            if not isinstance(self.tier_id, int):
                raise TypeError(f"tier_id must be int or None, got {type(self.tier_id).__name__}")
            if not (0 <= self.tier_id <= 3):
                raise ValueError(f"tier_id must be 0-3 or None, got {self.tier_id}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")

    @property
    def abstained(self) -> bool:
        return self.tier_id is None
