"""Shared, configurable routing-signal tuning helpers.

This module keeps prompt escalation heuristics as weighted text-shape scores
instead of domain keyword allowlists. Explicit protocol affordances such as
JSON response formats remain separate because they describe request shape, not
task domain.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from uncommon_route.router.structural import estimate_tokens, extract_structural_features
from uncommon_route.router.types import Tier


@dataclass(frozen=True, slots=True)
class RoutingSignalTuning:
    strong_embedding_low_confidence: float = 0.90
    short_medium_substance_score: float = 0.20
    high_substance_score: float = 0.46
    contextual_prior_score: float = 0.18
    contextual_complex_prior_score: float = 0.30
    contextual_latest_medium_score: float = 0.06
    contextual_latest_complex_score: float = 0.24
    contextual_latest_medium_token_floor: int = 12
    contextual_question_medium_token_floor: int = 8
    contextual_latest_complex_token_floor: int = 12
    contextual_short_latest_complex_entropy_score: float = 0.45
    tool_prompt_high_risk_substance_score: float = 0.55
    vision_prompt_token_floor: int = 3
    compact_system_prompt_char_limit: int = 1200
    compact_system_prompt_word_limit: int = 180


DEFAULT_SIGNAL_TUNING = RoutingSignalTuning()

_TOKEN_RE = re.compile(r"[\w]+(?:[-/][\w]+)*", re.UNICODE)
_STRUCTURED_FORMAT_RE = re.compile(
    r"\b(?:json|json_schema|xml|yaml|csv|schema)\b|markdown\s+table",
    re.IGNORECASE,
)
_STRUCTURED_DIRECTIVE_RE = re.compile(
    r"\b(?:respond|output|return|format|structured|valid|matching)\b",
    re.IGNORECASE,
)
_CLIENT_WRAPPER_BLOCK_RE = re.compile(
    r"<(?P<tag>system-reminder|assistant-reminder|user-prompt-submit-hook)>\s*.*?\s*</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def word_count(text: str) -> int:
    return len(_TOKEN_RE.findall(text or ""))


def strip_client_wrapper_blocks(text: str) -> str:
    """Remove client-injected wrapper blocks from user-visible prompt text."""
    return " ".join(_CLIENT_WRAPPER_BLOCK_RE.sub(" ", str(text or "")).split())


def text_substance_score(text: str) -> float:
    """Return a language-agnostic prompt-substance score in [0, 1].

    The score is intentionally based on morphology and structure: length,
    long-token density, compound identifiers, acronyms, digits, punctuation,
    and the existing structural dimensions. It avoids domain words such as
    product names, legal terms, algorithms, or task labels.
    """
    text = str(text or "")
    if not text.strip():
        return 0.0

    tokens = _TOKEN_RE.findall(text)
    token_count = max(len(tokens), 1)
    long_token_ratio = sum(1 for token in tokens if len(token) >= 8) / token_count
    compound_count = sum(1 for token in tokens if "-" in token or "/" in token or "_" in token)
    acronym_count = sum(1 for token in tokens if len(token) >= 2 and token.isupper())
    digit_count = sum(1 for ch in text if ch.isdigit())
    punctuation_count = sum(1 for ch in text if unicodedata.category(ch).startswith("P"))
    punctuation_density = punctuation_count / max(len(text), 1)

    dims = {dim.name: dim.score for dim in extract_structural_features(text)}
    structural_score = max(
        dims.get("enumeration_density", 0.0),
        dims.get("requirement_phrases", 0.0),
        dims.get("unique_concept_density", 0.0),
        dims.get("code_markers", 0.0),
        dims.get("math_symbols", 0.0),
        dims.get("nesting_depth", 0.0),
    )

    token_score = _clamp01((estimate_tokens(text) - 8) / 34)
    long_token_score = _clamp01((long_token_ratio - 0.08) / 0.35)
    compound_score = _clamp01(compound_count / 2)
    acronym_score = _clamp01(acronym_count / 2)
    digit_score = _clamp01(digit_count / 4)
    punctuation_score = _clamp01((punctuation_density - 0.04) / 0.08)

    return _clamp01(
        0.28 * token_score
        + 0.25 * long_token_score
        + 0.14 * compound_score
        + 0.08 * acronym_score
        + 0.08 * digit_score
        + 0.07 * punctuation_score
        + 0.10 * structural_score
    )


def text_high_substance_score(text: str) -> float:
    """Return a stricter score for allowing public COMPLEX floors.

    Medium floors can rely on broad structural substance. High floors need
    stronger morphology: dense long-token usage, compound identifiers, acronyms,
    and enough length. This prevents ordinary comma-separated implementation
    chores from becoming premium just because they list several steps.
    """
    text = str(text or "")
    if not text.strip():
        return 0.0

    tokens = _TOKEN_RE.findall(text)
    token_count = max(len(tokens), 1)
    long_token_ratio = sum(1 for token in tokens if len(token) >= 8) / token_count
    compound_count = sum(1 for token in tokens if "-" in token or "/" in token or "_" in token)
    acronym_count = sum(1 for token in tokens if len(token) >= 2 and token.isupper())
    dense_char_count = sum(1 for ch in text if unicodedata.category(ch) == "Lo")
    dims = {dim.name: dim.score for dim in extract_structural_features(text)}
    structural_score = max(
        dims.get("code_markers", 0.0),
        dims.get("math_symbols", 0.0),
        dims.get("nesting_depth", 0.0),
        dims.get("unique_concept_density", 0.0),
        dims.get("requirement_phrases", 0.0),
    )

    token_score = _clamp01((estimate_tokens(text) - 8) / 34)
    long_token_score = _clamp01((long_token_ratio - 0.08) / 0.35)
    compound_score = _clamp01(compound_count / 2)
    acronym_score = _clamp01(acronym_count / 2)

    base_score = _clamp01(
        0.30 * token_score
        + 0.35 * long_token_score
        + 0.20 * compound_score
        + 0.10 * acronym_score
        + 0.05 * structural_score
    )
    dense_script_score = 0.0
    if dense_char_count >= 12:
        dense_script_score = _clamp01(
            0.22
            + 0.55 * _clamp01((dense_char_count - 12) / 40)
            + 0.15 * structural_score
        )
    return max(base_score, dense_script_score)


def system_prompt_has_structured_output_constraint(system_prompt: str) -> bool:
    """Detect explicit structured-output protocol constraints.

    This is deliberately narrow: it recognizes output serialization protocols
    only when paired with a directive-like system prompt.
    """
    normalized = " ".join(str(system_prompt or "").split())
    if not normalized:
        return False
    if system_prompt_is_title_generation_sidechannel(normalized):
        return False
    if not _STRUCTURED_FORMAT_RE.search(normalized):
        return False
    if _STRUCTURED_DIRECTIVE_RE.search(normalized):
        return True
    return any(ch in normalized for ch in "{}[]<>|")


def system_prompt_is_title_generation_sidechannel(system_prompt: str) -> bool:
    """Detect Claude Code's session-title side-channel request."""
    normalized = " ".join(str(system_prompt or "").lower().split())
    if not normalized:
        return False
    return (
        "generate a concise" in normalized
        and "title" in normalized
        and "return json" in normalized
        and '"title"' in normalized
    )


def compact_structural_system_prompt(
    system_prompt: str | None,
    *,
    tuning: RoutingSignalTuning = DEFAULT_SIGNAL_TUNING,
) -> str | None:
    """Return a compact system prompt that should affect prompt classification."""
    value = str(system_prompt or "").strip()
    if not value:
        return None
    if (
        len(value) > tuning.compact_system_prompt_char_limit
        or len(value.split()) > tuning.compact_system_prompt_word_limit
    ):
        return None
    if not system_prompt_has_structured_output_constraint(value):
        return None
    return value


def contextual_followup_floor_from_text(
    *,
    prior_text: str,
    latest_text: str,
    tuning: RoutingSignalTuning = DEFAULT_SIGNAL_TUNING,
) -> Tier | None:
    """Infer a follow-up floor from prior/latest text-shape scores."""
    prior = strip_client_wrapper_blocks(str(prior_text or "")).strip()
    latest = strip_client_wrapper_blocks(str(latest_text or "")).strip()
    if not prior or not latest:
        return None

    prior_score = text_substance_score(prior)
    latest_score = text_substance_score(latest)
    if (
        prior_score >= tuning.contextual_complex_prior_score
        and latest_score >= tuning.contextual_latest_complex_score
        and _latest_supports_complex_followup_floor(latest, tuning=tuning)
    ):
        return Tier.COMPLEX
    if (
        prior_score >= tuning.contextual_prior_score
        and latest_score >= tuning.contextual_latest_medium_score
        and _latest_supports_medium_followup_floor(latest, tuning=tuning)
    ):
        return Tier.MEDIUM
    return None


def _latest_supports_medium_followup_floor(
    latest_text: str,
    *,
    tuning: RoutingSignalTuning = DEFAULT_SIGNAL_TUNING,
) -> bool:
    """Require enough latest-turn shape before inheriting a MEDIUM floor."""
    token_estimate = estimate_tokens(latest_text)
    if token_estimate >= tuning.contextual_latest_medium_token_floor:
        return True

    dims = {dim.name: dim.score for dim in extract_structural_features(latest_text)}
    return (
        token_estimate >= tuning.contextual_question_medium_token_floor
        and dims.get("functional_intent", 0.0) < 0.0
    )


def _latest_supports_complex_followup_floor(
    latest_text: str,
    *,
    tuning: RoutingSignalTuning = DEFAULT_SIGNAL_TUNING,
) -> bool:
    """Require enough latest-turn shape before inheriting a COMPLEX floor."""
    token_estimate = estimate_tokens(latest_text)
    if token_estimate >= tuning.contextual_latest_complex_token_floor:
        return True
    if token_estimate < 10:
        return False

    dims = {dim.name: dim.score for dim in extract_structural_features(latest_text)}
    return dims.get("shannon_entropy", 0.0) >= tuning.contextual_short_latest_complex_entropy_score


def vision_prompt_needs_medium_floor(
    *,
    has_vision: bool,
    prompt: str,
    tuning: RoutingSignalTuning = DEFAULT_SIGNAL_TUNING,
) -> bool:
    if not has_vision:
        return False
    text = str(prompt or "").strip()
    return estimate_tokens(text) >= tuning.vision_prompt_token_floor
