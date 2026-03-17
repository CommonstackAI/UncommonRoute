"""Language-agnostic structural feature extractors.

Every function here works on raw text regardless of language.
These form Level 2 of the cascade classifier.
"""

from __future__ import annotations

import math
import re
import unicodedata

from uncommon_route.router.types import DimensionScore

# ─── Script-Aware Token Estimation ───

_CJK_RANGES = (
    (0x4E00, 0x9FFF),    # CJK Unified
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0xF900, 0xFAFF),    # CJK Compat
    (0x3040, 0x309F),    # Hiragana
    (0x30A0, 0x30FF),    # Katakana
    (0xAC00, 0xD7AF),    # Hangul
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_arabic_hebrew(ch: str) -> bool:
    cat = unicodedata.category(ch)
    if cat.startswith("L"):
        name = unicodedata.name(ch, "")
        return "ARABIC" in name or "HEBREW" in name
    return False


def estimate_tokens(text: str) -> int:
    """Script-aware token estimation.

    Latin/Cyrillic: ~4 chars/token
    CJK: ~1.5 chars/token
    Arabic/Hebrew: ~3 chars/token
    """
    if not text:
        return 0

    cjk_chars = 0
    arabic_chars = 0
    other_chars = 0

    for ch in text:
        if _is_cjk(ch):
            cjk_chars += 1
        elif _is_arabic_hebrew(ch):
            arabic_chars += 1
        else:
            other_chars += 1

    tokens = cjk_chars / 1.5 + arabic_chars / 3.0 + other_chars / 4.0
    return max(1, math.ceil(tokens))


# ─── 7 Structural Features ───

def score_normalized_length(text: str, max_tokens: int = 2000) -> DimensionScore:
    """Log-scaled length score. Continuous [-1, 1] instead of binary threshold.

    Short text gets mild negative (not -1.0), long text gets gradual positive.
    """
    tokens = estimate_tokens(text)

    if tokens <= 0:
        return DimensionScore("normalized_length", -0.5, "empty")

    log_ratio = math.log(tokens + 1) / math.log(max_tokens + 1)
    score = (log_ratio * 2.0) - 1.0  # map [0, 1] → [-1, 1]
    score = max(-0.8, min(1.0, score))  # clamp, never fully -1.0

    signal = None
    if tokens < 15:
        signal = f"short ({tokens} tok)"
    elif tokens > 200:
        signal = f"long ({tokens} tok)"

    return DimensionScore("normalized_length", score, signal)


_ENUM_CHARS = set(",;，；、：:·•–—،؛")

def score_enumeration_density(text: str) -> DimensionScore:
    """Comma / semicolon / enumeration density. Language-agnostic.

    High density signals compound requirements → COMPLEX.
    """
    if len(text) < 5:
        return DimensionScore("enumeration_density", 0.0, None)

    enum_count = sum(1 for ch in text if ch in _ENUM_CHARS)
    density = enum_count / len(text)

    # Also detect "A, B, C, and D" style enumeration
    list_pattern_count = len(re.findall(r",\s*(?:and|or|及|和|或|と|및|و|и|y|e|und|et)\s", text, re.IGNORECASE))

    combined = density * 50.0 + list_pattern_count * 0.15
    score = min(1.0, combined)

    signal = None
    if score > 0.2:
        signal = f"enum({enum_count})"

    return DimensionScore("enumeration_density", score, signal)


_SENTENCE_ENDERS = set(".。?？!！")

def score_sentence_count(text: str) -> DimensionScore:
    """Count sentences. More sentences = more complex task description."""
    count = sum(1 for ch in text if ch in _SENTENCE_ENDERS)

    if count <= 1:
        return DimensionScore("sentence_count", 0.0, None)
    if count == 2:
        return DimensionScore("sentence_count", 0.2, f"{count} sentences")
    if count <= 4:
        return DimensionScore("sentence_count", 0.5, f"{count} sentences")

    return DimensionScore("sentence_count", min(1.0, count * 0.12), f"{count} sentences")


_CODE_PATTERNS = re.compile(
    r"```"
    r"|(?:function|def|class|import|from|const|let|var|return|async|await)\s"
    r"|(?:=>|->|::|\|>)"
    r"|(?:\{\s*\n|\}\s*\n)"
    r"|(?://|#!|/\*)",
    re.IGNORECASE,
)
_CODE_CHARS = set("{}[]();")

def score_code_markers(text: str) -> DimensionScore:
    """Detect code presence from syntax markers. Universal across languages."""
    pattern_hits = len(_CODE_PATTERNS.findall(text))
    char_hits = sum(1 for ch in text if ch in _CODE_CHARS)
    char_density = char_hits / max(len(text), 1)

    score = min(1.0, pattern_hits * 0.25 + char_density * 8.0)

    signal = None
    if score > 0.2:
        signal = "code"

    return DimensionScore("code_markers", score, signal)


_MATH_SYMBOLS = set("∑∀∃∈∉⊂⊃∪∩≥≤≠≈∞∫∂∇±√∝∠∆")
_MATH_PATTERNS = re.compile(
    r"\\(?:frac|sum|int|prod|lim|sqrt|forall|exists|infty|partial|nabla)"
    r"|(?:n\s*\+\s*1|n\s*-\s*1)"
    r"|(?:O\(|Θ\(|Ω\()"
    r"|(?:\d+\s*[+\-*/^]\s*\d+\s*=)"
    r"|(?:≥|≤|≠|→|⟹|⟺)",
)

def score_math_symbols(text: str) -> DimensionScore:
    """Detect mathematical / formal notation."""
    symbol_hits = sum(1 for ch in text if ch in _MATH_SYMBOLS)
    pattern_hits = len(_MATH_PATTERNS.findall(text))

    score = min(1.0, symbol_hits * 0.3 + pattern_hits * 0.35)

    signal = None
    if score > 0.2:
        signal = "math"

    return DimensionScore("math_symbols", score, signal)


def score_nesting_depth(text: str) -> DimensionScore:
    """Max nesting depth of brackets. Deep nesting = structural complexity."""
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
    """Unique token ratio. Technical / complex text uses more diverse vocabulary."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < 3:
        return DimensionScore("vocabulary_diversity", 0.0, None)

    unique_ratio = len(set(words)) / len(words)
    # High diversity (> 0.85) is common in technical prompts
    # Low diversity (< 0.6) is common in simple/repetitive prompts
    score = max(0.0, (unique_ratio - 0.6) / 0.4)  # map [0.6, 1.0] → [0, 1]
    score = min(1.0, score)

    return DimensionScore("vocabulary_diversity", score, None)


def score_avg_word_length(text: str) -> DimensionScore:
    """Average word length — proxy for vocabulary sophistication.

    Flesch-Kincaid inspired: complex/technical text uses longer words.
    "What is DNS?" → avg ~3.0 → simple.
    "Implement distributed authentication" → avg ~10 → complex.
    """
    words = re.findall(r"\w+", text)
    if len(words) < 2:
        return DimensionScore("avg_word_length", 0.0, None)

    avg = sum(len(w) for w in words) / len(words)
    # Map [3, 9] → [0, 1]: short words=simple, long words=complex
    score = max(0.0, min(1.0, (avg - 3.0) / 6.0))

    return DimensionScore("avg_word_length", score, None)


def score_alphabetic_ratio(text: str) -> DimensionScore:
    """Ratio of alphabetic characters. Low ratio = noise / random symbols → SIMPLE."""
    if len(text) < 3:
        return DimensionScore("alphabetic_ratio", 0.0, None)

    alpha_count = sum(1 for ch in text if ch.isalpha())
    ratio = alpha_count / len(text)

    if ratio < 0.4:
        return DimensionScore("alphabetic_ratio", -0.8, "noise")

    return DimensionScore("alphabetic_ratio", 0.0, None)


_IMPERATIVE_STARTS = re.compile(
    r"^(?:fix|write|build|create|implement|design|develop|deploy|run|test|debug|refactor"
    r"|check|add|remove|update|delete|install|configure|set up|make|generate|optimize"
    r"|rewrite|convert|extract|classify|categorize|explain|summarize|compare"
    r"|修复|写|创建|实现|设计|开发|部署|运行|测试|调试|改写|提取"
    r"|исправь|напиши|создай|реализуй|запусти|протестируй|перепиши"
    r"|arregla|escribe|crea|implementa)\b",
    re.IGNORECASE,
)

_CODE_BLOCK = re.compile(r"```[\s\S]*?```")


def score_functional_intent(text: str) -> DimensionScore:
    """Detect functional intent: question vs command vs description.

    PromptPrism inspired: functional structure is a strong routing signal.
    - Question (without code block) → QA-like → SIMPLE bias
    - Question WITH code block → code QA → still SIMPLE (not COMPLEX)
    - Short command → task → MEDIUM bias
    """
    stripped = text.strip()

    is_question = stripped.endswith("?") or stripped.endswith("？")

    if is_question:
        has_code_block = bool(_CODE_BLOCK.search(stripped))
        if has_code_block:
            return DimensionScore("functional_intent", -0.6, "code-qa")
        return DimensionScore("functional_intent", -0.4, "question")

    words = stripped.split()
    if 1 <= len(words) <= 8 and _IMPERATIVE_STARTS.match(stripped):
        return DimensionScore("functional_intent", 0.2, "short-command")

    return DimensionScore("functional_intent", 0.0, None)


def score_shannon_entropy(text: str) -> DimensionScore:
    """Shannon entropy of character distribution — information-theoretic complexity.

    High entropy = many distinct characters used uniformly → complex/technical text.
    Low entropy = few characters, repetitive → simple text.
    Completely language-agnostic, no keywords needed.
    """
    if len(text) < 5:
        return DimensionScore("shannon_entropy", 0.0, None)

    freq: dict[str, int] = {}
    for ch in text:
        freq[ch] = freq.get(ch, 0) + 1

    n = len(text)
    entropy = 0.0
    for count in freq.values():
        p = count / n
        entropy -= p * math.log2(p)

    # Normalize: English text entropy is typically 3.5-4.5 bits/char.
    # Simple prompts ~3.0-3.8, complex prompts ~4.0-4.8.
    # Map [3.0, 5.0] → [0, 1]
    score = max(0.0, min(1.0, (entropy - 3.0) / 2.0))

    signal = None
    if score > 0.6:
        signal = f"high-entropy({entropy:.1f})"

    return DimensionScore("shannon_entropy", score, signal)


def score_compression_complexity(text: str) -> DimensionScore:
    """Compression ratio as complexity proxy (gzip-inspired).

    Complex text with many unique concepts compresses poorly.
    Simple/repetitive text compresses well.
    Uses zlib for speed (no file I/O overhead).
    """
    import zlib

    text_bytes = text.encode("utf-8")
    if len(text_bytes) < 20:
        return DimensionScore("compression_complexity", 0.0, None)

    compressed = zlib.compress(text_bytes, level=1)  # level=1 for speed
    ratio = len(compressed) / len(text_bytes)

    # Ratio range: ~0.3 (very compressible) to ~1.1 (incompressible + header)
    # Map [0.4, 1.0] → [0, 1]
    score = max(0.0, min(1.0, (ratio - 0.4) / 0.6))

    return DimensionScore("compression_complexity", score, None)


def score_unique_concept_density(text: str) -> DimensionScore:
    """Count unique "concept chunks" — approximation of requirement count.

    Instead of keyword matching, count distinct noun-like segments separated
    by commas, conjunctions, or sentence boundaries.
    More unique chunks = more complex task.
    """
    # Split by common delimiters across languages
    chunks = re.split(r"[,;，；、。.!?！？\n]+", text)
    chunks = [c.strip() for c in chunks if len(c.strip()) > 3]

    if len(chunks) <= 1:
        return DimensionScore("unique_concept_density", 0.0, None)

    # Measure: how many distinct chunks exist
    # 2 chunks = mildly complex, 5+ = very complex
    score = min(1.0, (len(chunks) - 1) * 0.18)

    signal = None
    if score > 0.3:
        signal = f"concepts({len(chunks)})"

    return DimensionScore("unique_concept_density", score, signal)


_REQUIREMENT_PHRASES = re.compile(
    r"\b(?:with|including|support(?:ing|s)?|that (?:has|handles|supports|includes|covers)"
    r"|plus|as well as|along with|equipped with"
    r"|包括|支持|包含|具备|涵盖"
    r"|включая|поддерж|содержащ|с поддержкой|с обработкой"
    r"|含む|サポート|対応"
    r"|포함|지원"
    r"|incluyendo|con soporte|que incluye"
    r"|incluindo|com suporte"
    r"|einschließlich|mit Unterstützung"
    r"|y compris|avec support"
    r"|بما في ذلك|يدعم|يشمل|يتضمن"
    r")\b",
    re.IGNORECASE,
)


def score_requirement_phrases(text: str) -> DimensionScore:
    """Count requirement/inclusion phrases — direct signal for multi-requirement tasks.

    "Design a system WITH auth, INCLUDING logging, that SUPPORTS sharding"
    has 3 requirement phrases → strong COMPLEX signal.
    Works across languages without keyword lists for technical terms.
    """
    hits = len(_REQUIREMENT_PHRASES.findall(text))

    if hits == 0:
        return DimensionScore("requirement_phrases", 0.0, None)

    score = min(1.0, hits * 0.25)

    return DimensionScore("requirement_phrases", score, f"reqs({hits})")


# ─── Unicode Block Features (script-agnostic) ───

_UNICODE_BLOCKS = {
    "basic_latin": (0x0000, 0x007F),
    "latin_ext": (0x0080, 0x024F),
    "cyrillic": (0x0400, 0x04FF),
    "arabic": (0x0600, 0x06FF),
    "devanagari": (0x0900, 0x097F),
    "thai": (0x0E00, 0x0E7F),
    "hangul_jamo": (0x1100, 0x11FF),
    "cjk_unified": (0x4E00, 0x9FFF),
    "hiragana": (0x3040, 0x309F),
    "katakana": (0x30A0, 0x30FF),
    "hangul_syllables": (0xAC00, 0xD7AF),
    "punctuation": None,  # handled specially
    "digits": None,       # handled specially
    "symbols_math": None, # handled specially
}


def extract_unicode_block_features(text: str) -> dict[str, float]:
    """Extract Unicode block distribution — script-agnostic feature vector.

    Returns a dict of ~15 features, each in [0, 1], representing the proportion
    of text in each Unicode block. Works identically for ALL languages because
    it captures WHAT SCRIPT is used, not WHAT CHARACTERS specifically.

    Insight from uniblock (EMNLP 2019): Unicode block distribution provides
    a language-agnostic signal that lets models generalize across scripts.
    """
    if len(text) < 2:
        return {name: 0.0 for name in _UNICODE_BLOCKS}

    counts: dict[str, int] = {name: 0 for name in _UNICODE_BLOCKS}
    total = 0

    for ch in text:
        cp = ord(ch)
        total += 1

        if ch.isdigit():
            counts["digits"] += 1
        elif unicodedata.category(ch).startswith("P"):
            counts["punctuation"] += 1
        elif unicodedata.category(ch).startswith("S"):
            counts["symbols_math"] += 1
        else:
            matched = False
            for name, rng in _UNICODE_BLOCKS.items():
                if rng is None:
                    continue
                if rng[0] <= cp <= rng[1]:
                    counts[name] += 1
                    matched = True
                    break
            if not matched:
                counts["basic_latin"] += 1  # fallback

    if total == 0:
        return {name: 0.0 for name in _UNICODE_BLOCKS}

    return {name: count / total for name, count in counts.items()}


def extract_structural_features(text: str) -> list[DimensionScore]:
    """Extract all 13 structural features from raw text.

    7 original + 3 new (avg_word_length, alphabetic_ratio, functional_intent)
    + 3 information-theoretic (shannon_entropy, compression_complexity, unique_concept_density)
    """
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
        score_unique_concept_density(text),
        score_requirement_phrases(text),
    ]


# ─── Output Budget Estimation (R2-Router inspired) ───

class OutputBudget:
    SHORT = 128       # factual QA, yes/no, definitions
    MEDIUM = 512      # explanations, simple code
    LONG = 2048       # complex code, system design
    EXTENDED = 4096   # full implementations


_SHORT_SIGNALS = re.compile(
    r"\b(?:yes or no|true or false|brief|one.?liner|short|concise|name)\b"
    r"|(?:是否|简短|简要|一句话)"
    r"|(?:да или нет|кратко)"
    r"|(?:sí o no|breve)",
    re.IGNORECASE,
)

_LONG_SIGNALS = re.compile(
    r"\b(?:comprehensive|detailed|full|complete|thorough|in.?depth|step by step|from scratch)\b"
    r"|(?:包括|完整|详细|全面|从零)"
    r"|(?:подробно|полностью|полноценный|с нуля)"
    r"|(?:completo|detallado|desde cero)",
    re.IGNORECASE,
)


def estimate_output_budget(prompt: str, tier: str) -> int:
    """Estimate optimal output token budget from prompt + tier.

    R2-Router insight: strong model + constrained output often beats
    weak model + unlimited output, at lower cost.
    """
    lower = prompt.lower()

    if _SHORT_SIGNALS.search(lower):
        return OutputBudget.SHORT

    if _LONG_SIGNALS.search(lower):
        return OutputBudget.LONG

    code = score_code_markers(prompt)
    enum = score_enumeration_density(prompt)

    if code.score > 0.3 and enum.score > 0.3:
        return OutputBudget.LONG

    tier_defaults = {
        "SIMPLE": OutputBudget.SHORT,
        "MEDIUM": OutputBudget.MEDIUM,
        "COMPLEX": OutputBudget.LONG,
    }
    return tier_defaults.get(tier, OutputBudget.MEDIUM)
