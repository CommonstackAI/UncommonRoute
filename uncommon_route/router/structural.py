"""Language-agnostic structural feature extractors.

Every function here works on raw text regardless of language.
No keyword lists, no hardcoded patterns.  All signals come from:
  - Unicode character categories (unicodedata module)
  - Character-class density (brackets, punctuation)
  - Information-theoretic measures (entropy, compression)
  - Structural statistics (length, nesting, vocabulary diversity)
"""

from __future__ import annotations

import math
import re
import unicodedata

from uncommon_route.router.types import DimensionScore

# ─── Script-Aware Token Estimation ───


def _char_density_class(ch: str) -> str:
    """Classify character information density via Unicode category.

    Uses unicodedata.category() — no hardcoded script lists:
      - 'Lo' (Letter, other) = logographic/syllabic → ~1.5 chars/token
      - 'Mn'/'Mc' (Mark) following 'Lo' base = part of logographic cluster
      - All other letters = alphabetic → ~4 chars/token

    This heuristic works because Unicode 'Lo' covers CJK ideographs,
    Hangul syllables, Hiragana, Katakana, Thai, Lao, Tibetan, Yi, etc.
    — all scripts with high information density per character.
    """
    cat = unicodedata.category(ch)
    if cat == "Lo":
        return "dense"
    return "alphabetic"


def estimate_tokens(text: str) -> int:
    """Script-aware token estimation using Unicode categories.

    Dense scripts (Lo category: CJK, Kana, Hangul, Thai, ...): ~1.5 chars/token
    Alphabetic scripts (all other letters): ~4 chars/token
    Non-letter characters: ~4 chars/token
    """
    if not text:
        return 0

    dense = 0
    other = 0

    for ch in text:
        if _char_density_class(ch) == "dense":
            dense += 1
        else:
            other += 1

    tokens = dense / 1.5 + other / 4.0
    return max(1, math.ceil(tokens))


# ─── Structural Features ───

_ENUM_CHARS = set(",;，；、：:·•–—،؛")


def _is_single_question(text: str) -> bool:
    stripped = text.strip()
    if not (stripped.endswith("?") or stripped.endswith("？")):
        return False
    return sum(1 for ch in stripped if ch in _SENTENCE_ENDERS) <= 1


def score_enumeration_density(text: str) -> DimensionScore:
    """Enumeration punctuation density.  Pure character counting."""
    if len(text) < 5:
        return DimensionScore("enumeration_density", 0.0, None)

    enum_chars = _ENUM_CHARS - {",", "，"} if _is_single_question(text) else _ENUM_CHARS
    enum_count = sum(1 for ch in text if ch in enum_chars)
    density = enum_count / len(text)
    score = min(1.0, density * 50.0)

    signal = f"enum({enum_count})" if score > 0.2 else None
    return DimensionScore("enumeration_density", score, signal)


def score_normalized_length(text: str, max_tokens: int = 2000) -> DimensionScore:
    """Log-scaled length score."""
    tokens = estimate_tokens(text)
    if tokens <= 0:
        return DimensionScore("normalized_length", -0.5, "empty")

    log_ratio = math.log(tokens + 1) / math.log(max_tokens + 1)
    score = max(-0.8, min(1.0, (log_ratio * 2.0) - 1.0))

    signal = None
    if tokens < 15:
        signal = f"short ({tokens} tok)"
    elif tokens > 200:
        signal = f"long ({tokens} tok)"
    return DimensionScore("normalized_length", score, signal)


_SENTENCE_ENDERS = set(".。?？!！")


def score_sentence_count(text: str) -> DimensionScore:
    """Count sentences via sentence-ending punctuation."""
    count = sum(1 for ch in text if ch in _SENTENCE_ENDERS)
    if count <= 1:
        return DimensionScore("sentence_count", 0.0, None)
    if count == 2:
        return DimensionScore("sentence_count", 0.2, f"{count} sentences")
    if count <= 4:
        return DimensionScore("sentence_count", 0.5, f"{count} sentences")
    return DimensionScore("sentence_count", min(1.0, count * 0.12), f"{count} sentences")


_CODE_CHARS = set("{}[]();")


def score_code_markers(text: str) -> DimensionScore:
    """Code presence via bracket/semicolon density + fenced blocks."""
    char_hits = sum(1 for ch in text if ch in _CODE_CHARS)
    char_density = char_hits / max(len(text), 1)
    has_fenced_block = text.count("```") >= 2

    score = min(1.0, char_density * 8.0 + (0.4 if has_fenced_block else 0.0))
    signal = "code" if score > 0.2 else None
    return DimensionScore("code_markers", score, signal)


def score_math_symbols(text: str) -> DimensionScore:
    """Math notation density via Unicode Symbol/Math category (Sm)."""
    math_count = sum(1 for ch in text if unicodedata.category(ch) == "Sm")
    score = min(1.0, math_count * 0.3)
    signal = "math" if score > 0.2 else None
    return DimensionScore("math_symbols", score, signal)


def score_nesting_depth(text: str) -> DimensionScore:
    """Max nesting depth of brackets."""
    max_depth = 0
    depth = 0
    openers = set("({[")
    closers = set(")}]")
    for ch in text:
        if ch in openers:
            depth += 1
            max_depth = max(max_depth, depth)
        elif ch in closers:
            depth = max(0, depth - 1)
    if max_depth <= 1:
        return DimensionScore("nesting_depth", 0.0, None)
    score = min(1.0, (max_depth - 1) * 0.25)
    return DimensionScore("nesting_depth", score, f"depth={max_depth}")


def score_vocabulary_diversity(text: str) -> DimensionScore:
    """Unique token ratio."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < 3:
        return DimensionScore("vocabulary_diversity", 0.0, None)
    unique_ratio = len(set(words)) / len(words)
    score = max(0.0, min(1.0, (unique_ratio - 0.6) / 0.4))
    return DimensionScore("vocabulary_diversity", score, None)


def score_avg_word_length(text: str) -> DimensionScore:
    """Average word length — proxy for vocabulary sophistication."""
    words = re.findall(r"\w+", text)
    if len(words) < 2:
        return DimensionScore("avg_word_length", 0.0, None)
    avg = sum(len(w) for w in words) / len(words)
    score = max(0.0, min(1.0, (avg - 3.0) / 6.0))
    return DimensionScore("avg_word_length", score, None)


def score_alphabetic_ratio(text: str) -> DimensionScore:
    """Ratio of alphabetic characters.  Low ratio = noise."""
    if len(text) < 3:
        return DimensionScore("alphabetic_ratio", 0.0, None)
    alpha_count = sum(1 for ch in text if ch.isalpha())
    ratio = alpha_count / len(text)
    if ratio < 0.4:
        return DimensionScore("alphabetic_ratio", -0.8, "noise")
    return DimensionScore("alphabetic_ratio", 0.0, None)


_CODE_BLOCK = re.compile(r"```[\s\S]*?```")


def score_functional_intent(text: str) -> DimensionScore:
    """Question vs command vs description — structural signals only."""
    stripped = text.strip()
    is_question = stripped.endswith("?") or stripped.endswith("？")
    if is_question:
        has_code_block = bool(_CODE_BLOCK.search(stripped))
        if has_code_block:
            return DimensionScore("functional_intent", -0.6, "code-qa")
        return DimensionScore("functional_intent", -0.4, "question")
    words = stripped.split()
    if 1 <= len(words) <= 8:
        return DimensionScore("functional_intent", 0.2, "short-command")
    return DimensionScore("functional_intent", 0.0, None)


def score_shannon_entropy(text: str) -> DimensionScore:
    """Shannon entropy of character distribution."""
    if len(text) < 5:
        return DimensionScore("shannon_entropy", 0.0, None)
    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(text)
    entropy = -sum((c / n) * math.log2(c / n) for c in freq.values())
    score = max(0.0, min(1.0, (entropy - 3.0) / 2.0))
    signal = f"high-entropy({entropy:.1f})" if score > 0.6 else None
    return DimensionScore("shannon_entropy", score, signal)


def score_compression_complexity(text: str) -> DimensionScore:
    """Compression ratio as complexity proxy."""
    import zlib
    text_bytes = text.encode("utf-8")
    if len(text_bytes) < 20:
        return DimensionScore("compression_complexity", 0.0, None)
    compressed = zlib.compress(text_bytes, level=1)
    ratio = len(compressed) / len(text_bytes)
    score = max(0.0, min(1.0, (ratio - 0.4) / 0.6))
    return DimensionScore("compression_complexity", score, None)


def score_unique_concept_density(text: str) -> DimensionScore:
    """Distinct concept chunks separated by punctuation."""
    split_re = r"[;；、。.!?！？\n]+" if _is_single_question(text) else r"[,;，；、。.!?！？\n]+"
    chunks = re.split(split_re, text)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 3]
    if len(chunks) <= 1:
        return DimensionScore("unique_concept_density", 0.0, None)
    score = min(1.0, (len(chunks) - 1) * 0.18)
    signal = f"concepts({len(chunks)})" if score > 0.3 else None
    return DimensionScore("unique_concept_density", score, signal)


def score_requirement_phrases(text: str) -> DimensionScore:
    """Requirement density from chunk counting."""
    split_re = r"[;；、。.!?！？\n]+" if _is_single_question(text) else r"[,;，；、。.!?！？\n]+"
    chunks = re.split(split_re, text)
    meaningful = [c.strip() for c in chunks if len(c.strip()) > 8]
    if len(meaningful) <= 1:
        return DimensionScore("requirement_phrases", 0.0, None)
    score = min(1.0, (len(meaningful) - 1) * 0.20)
    signal = f"reqs({len(meaningful)})" if score > 0.2 else None
    return DimensionScore("requirement_phrases", score, signal)


# ─── Unicode Block Features ───

def _extract_script_name(ch: str) -> str:
    """Extract script family from Unicode character name.

    The first word of ``unicodedata.name()`` is the script identifier
    for most characters.  This auto-discovers scripts without a
    hardcoded list — Bengali, Tamil, Georgian, etc. are all handled.
    """
    name = unicodedata.name(ch, "")
    if not name:
        return "other"
    return name.split(" ", 1)[0].lower()


def extract_unicode_block_features(text: str) -> dict[str, float]:
    """Extract script distribution using auto-discovered script names.

    Classifies each character by its Unicode category and script name.
    Script names are extracted from ``unicodedata.name()`` — no hardcoded
    script list.  The classifier learns which script proportions predict
    difficulty from training data.
    """
    if len(text) < 2:
        return {"digits": 0.0, "punctuation": 0.0, "symbols_math": 0.0, "other": 0.0}

    counts: dict[str, int] = {}
    total = 0

    for ch in text:
        total += 1
        cat = unicodedata.category(ch)
        if cat.startswith("N"):
            bucket = "digits"
        elif cat.startswith("P"):
            bucket = "punctuation"
        elif cat.startswith("S"):
            bucket = "symbols_math"
        elif cat.startswith("L"):
            bucket = _extract_script_name(ch)
        else:
            bucket = "other"
        counts[bucket] = counts.get(bucket, 0) + 1

    if total == 0:
        return {"digits": 0.0, "punctuation": 0.0, "symbols_math": 0.0, "other": 0.0}

    return {name: count / total for name, count in counts.items()}


def extract_structural_features(text: str) -> list[DimensionScore]:
    """Extract all structural features from raw text."""
    return [
        score_normalized_length(text),
        score_enumeration_density(text),
        score_sentence_count(text),
        score_code_markers(text),
        score_math_symbols(text),
        score_nesting_depth(text),
        score_vocabulary_diversity(text),
        score_avg_word_length(text),
        score_alphabetic_ratio(text),
        score_functional_intent(text),
        score_shannon_entropy(text),
        score_compression_complexity(text),
        score_unique_concept_density(text),
        score_requirement_phrases(text),
    ]


# ─── Output Budget Estimation ───

class OutputBudget:
    SHORT = 128
    MEDIUM = 512
    LONG = 2048


def estimate_output_budget(prompt: str, tier: str) -> int:
    """Estimate output token budget from structural signals."""
    tokens = estimate_tokens(prompt)
    if tokens < 10:
        return OutputBudget.SHORT

    code = score_code_markers(prompt)
    enum = score_enumeration_density(prompt)
    if code.score > 0.3 and enum.score > 0.3:
        return OutputBudget.LONG
    if tokens > 200:
        return OutputBudget.LONG

    tier_defaults = {
        "SIMPLE": OutputBudget.SHORT,
        "MEDIUM": OutputBudget.MEDIUM,
        "COMPLEX": OutputBudget.LONG,
    }
    return tier_defaults.get(tier, OutputBudget.MEDIUM)
