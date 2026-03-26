"""Implicit quality feedback from response signals and user behavior.

Extracts quality signals WITHOUT analyzing content semantics:
  1. User retrial detection (CQB-MNL validated)
  2. Token logprob confidence analysis

These signals flow into the experience store, enabling the system
to learn model quality from every request without explicit feedback.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger("uncommon-route")


# ─── 1. Retrial Detection (CQB-MNL) ───

@dataclass
class _RecentRequest:
    prompt_hash: str
    model: str
    mode: str
    tier: str
    timestamp: float
    request_id: str


class RetrialDetector:
    """Detect when users retry the same prompt — implicit negative feedback.

    Based on CQB-MNL (Bae et al., 2026): user retrials are the strongest
    implicit signal of dissatisfaction, achieving O(√t) regret bounds
    for routing optimization.

    When a retrial is detected, the previously used model receives
    negative experience feedback automatically.
    """

    def __init__(
        self,
        window_seconds: float = 120.0,
        max_history: int = 200,
    ) -> None:
        self._window_seconds = window_seconds
        self._max_history = max_history
        self._history: deque[_RecentRequest] = deque(maxlen=max_history)

    def record_request(
        self,
        prompt: str,
        model: str,
        mode: str,
        tier: str,
        request_id: str = "",
    ) -> _RecentRequest | None:
        """Record a request and check if it's a retrial.

        Returns the PREVIOUS request if this is a retrial (same prompt
        hash within the time window), or None if it's a new request.
        """
        prompt_hash = self._hash_prompt(prompt)
        now = time.time()

        previous = self._find_recent(prompt_hash, now)

        self._history.append(_RecentRequest(
            prompt_hash=prompt_hash,
            model=model,
            mode=mode,
            tier=tier,
            timestamp=now,
            request_id=request_id,
        ))

        return previous

    def _find_recent(self, prompt_hash: str, now: float) -> _RecentRequest | None:
        cutoff = now - self._window_seconds
        for req in reversed(self._history):
            if req.timestamp < cutoff:
                break
            if req.prompt_hash == prompt_hash:
                return req
        return None

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        """Fuzzy prompt hash for retrial detection.

        Normalizes aggressively: lowercase, collapse whitespace, strip
        punctuation, truncate to 200 chars.  This catches rephrased
        retries like "explain X" → "explain X please" or "explain X?"
        """
        import re
        text = prompt.strip().lower()
        text = re.sub(r"[^\w\s]", "", text)
        text = " ".join(text.split())
        text = text[:200]
        return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]

    @property
    def history_size(self) -> int:
        return len(self._history)


# ─── 2. Token Logprob Confidence ───

@dataclass(frozen=True)
class LogprobConfidence:
    """Confidence metrics derived from token log probabilities."""
    mean_logprob: float
    min_logprob: float
    entropy_mean: float
    low_confidence_ratio: float
    token_count: int
    confidence_score: float


def analyze_logprobs(response_data: dict) -> LogprobConfidence | None:
    """Extract confidence signals from response logprobs.

    Requires the upstream response to include logprobs data
    (request must have been sent with logprobs=True).

    Signals:
      - mean_logprob: average token probability (higher = more confident)
      - low_confidence_ratio: fraction of tokens with logprob < -2.0
      - entropy_mean: average entropy across tokens
      - confidence_score: combined 0-1 score

    Based on: "Dynamic Instability Detection" (2025) — token logprob
    patterns predict reasoning failures without content analysis.
    """
    logprobs_data = _extract_logprobs(response_data)
    if not logprobs_data:
        return None

    token_logprobs: list[float] = []
    token_entropies: list[float] = []

    for token_info in logprobs_data:
        logprob = token_info.get("logprob")
        if logprob is not None:
            token_logprobs.append(float(logprob))

        top_logprobs = token_info.get("top_logprobs", [])
        if top_logprobs:
            probs = [math.exp(t.get("logprob", -10)) for t in top_logprobs if t.get("logprob") is not None]
            if probs:
                total = sum(probs)
                if total > 0:
                    normalized = [p / total for p in probs]
                    entropy = -sum(p * math.log2(p) for p in normalized if p > 0)
                    token_entropies.append(entropy)

    if not token_logprobs:
        return None

    n = len(token_logprobs)
    mean_lp = sum(token_logprobs) / n
    min_lp = min(token_logprobs)
    low_conf_ratio = sum(1 for lp in token_logprobs if lp < -2.0) / n
    entropy_mean = sum(token_entropies) / len(token_entropies) if token_entropies else 0.0

    confidence = _compute_confidence_score(mean_lp, low_conf_ratio, entropy_mean)

    return LogprobConfidence(
        mean_logprob=mean_lp,
        min_logprob=min_lp,
        entropy_mean=entropy_mean,
        low_confidence_ratio=low_conf_ratio,
        token_count=n,
        confidence_score=confidence,
    )


def _compute_confidence_score(
    mean_logprob: float,
    low_conf_ratio: float,
    entropy_mean: float,
) -> float:
    """Combine logprob signals into a single 0-1 confidence score."""
    lp_score = max(0.0, min(1.0, (mean_logprob + 3.0) / 3.0))
    low_conf_score = max(0.0, 1.0 - low_conf_ratio * 2.0)
    entropy_score = max(0.0, min(1.0, 1.0 - (entropy_mean - 1.0) / 3.0))
    return lp_score * 0.4 + low_conf_score * 0.35 + entropy_score * 0.25


def _extract_logprobs(response_data: dict) -> list[dict] | None:
    """Extract token logprobs from various response formats."""
    choices = response_data.get("choices", [])
    if not choices:
        return None

    choice = choices[0]

    logprobs = choice.get("logprobs")
    if isinstance(logprobs, dict):
        content = logprobs.get("content")
        if isinstance(content, list) and content:
            return content

    if isinstance(logprobs, list):
        return logprobs

    return None


# ─── Combined Implicit Quality Score ───

@dataclass(frozen=True)
class ImplicitQualitySignal:
    """Combined implicit quality signal from all available sources."""
    is_retrial: bool = False
    retrial_previous_model: str = ""
    logprob_confidence: LogprobConfidence | None = None
    overall_quality: float = 0.5

    @property
    def should_penalize(self) -> bool:
        """Whether the quality signals suggest the response was poor."""
        if self.is_retrial:
            return True
        if self.logprob_confidence and self.logprob_confidence.confidence_score < 0.3:
            return True
        return False


def compute_implicit_quality(
    *,
    is_retrial: bool = False,
    retrial_previous_model: str = "",
    logprob_confidence: LogprobConfidence | None = None,
) -> ImplicitQualitySignal:
    """Combine all implicit signals into a quality assessment."""
    quality = 0.5

    if is_retrial:
        quality = 0.15

    if logprob_confidence is not None:
        lp_quality = logprob_confidence.confidence_score
        if is_retrial:
            quality = min(quality, lp_quality * 0.5)
        else:
            quality = lp_quality

    return ImplicitQualitySignal(
        is_retrial=is_retrial,
        retrial_previous_model=retrial_previous_model,
        logprob_confidence=logprob_confidence,
        overall_quality=quality,
    )
