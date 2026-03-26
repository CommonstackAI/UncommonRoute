"""Dynamic benchmark quality data from external sources.

Fetches model quality scores from PinchBench, OpenRouter, and other
benchmark sources.  Scores are cached locally and refreshed periodically.

Quality data replaces price-based quality assumptions:
  - PinchBench: agent task success rates (best/avg)
  - OpenRouter: model metadata + popularity signals

Usage::

    cache = BenchmarkCache()
    await cache.refresh()
    quality = cache.get_quality("minimax/minimax-m2.5")  # → 0.793
    quality = cache.get_quality("nvidia/gpt-oss-120b")   # → 0.477
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from uncommon_route.paths import data_dir

logger = logging.getLogger("uncommon-route")

_DATA_DIR = data_dir()
_CACHE_PATH = _DATA_DIR / "benchmark_cache.json"


@dataclass
class ModelBenchmarkEntry:
    overall: float = 0.5
    categories: dict[str, float] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    fetched_at: float = 0.0


class BenchmarkProvider(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @property
    def refresh_interval_s(self) -> float:
        return 6 * 3600

    @abstractmethod
    async def fetch(self) -> dict[str, ModelBenchmarkEntry]: ...


class PinchBenchProvider(BenchmarkProvider):
    """Fetch agent task success rates from PinchBench.

    PinchBench (https://pinchbench.com) tests LLM models on real-world
    OpenClaw agent tasks.  Results are published via api.pinchbench.com.
    """

    source_name = "pinchbench"

    def __init__(self, api_url: str = "https://api.pinchbench.com") -> None:
        self._api_url = api_url.rstrip("/")

    async def fetch(self) -> dict[str, ModelBenchmarkEntry]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                resp = await client.get(
                    f"{self._api_url}/api/leaderboard?version=latest",
                    headers={"user-agent": "uncommon-route/benchmark"},
                )
                if resp.status_code != 200:
                    logger.warning("PinchBench: HTTP %d", resp.status_code)
                    return {}

                data = resp.json()
                return self._parse_leaderboard(data)
        except Exception as exc:
            logger.warning("PinchBench fetch failed: %s", exc)
            return {}

    def _parse_leaderboard(self, data: dict) -> dict[str, ModelBenchmarkEntry]:
        now = time.time()

        raw_entries: dict[str, list[tuple[float, float, int]]] = {}
        for item in data.get("leaderboard", []):
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model", "")).strip()
            if not model_id:
                continue
            runs = int(item.get("submission_count", 0) or 0)
            if runs < 2:
                continue
            # API returns scores as 0-1 fractions despite field name containing "percentage"
            best = float(item.get("best_score_percentage", 0))
            avg = float(item.get("average_score_percentage", 0))
            if avg <= 0:
                continue

            canonical = self._normalize_model_id(model_id)
            raw_entries.setdefault(canonical, []).append((best, avg, runs))

        entries: dict[str, ModelBenchmarkEntry] = {}
        for canonical, scores in raw_entries.items():
            best = max(s[0] for s in scores)
            total_runs = sum(s[2] for s in scores)
            weighted_avg = sum(s[1] * s[2] for s in scores) / total_runs if total_runs > 0 else best
            entries[canonical] = ModelBenchmarkEntry(
                overall=weighted_avg,
                categories={"agent": weighted_avg, "best": best},
                raw={"best_pct": round(best * 100, 1), "avg_pct": round(weighted_avg * 100, 1), "runs": total_runs},
                fetched_at=now,
            )

        if entries:
            logger.info("PinchBench: %d models with quality data", len(entries))
        return entries

    _PROVIDER_ALIASES: dict[str, str] = {
        "z-ai": "zai-org",
        "bailian": "zai-org",
        "moonshotai": "moonshot",
        "x-ai": "xai",
    }

    @classmethod
    def _normalize_model_id(cls, raw_id: str) -> str:
        """Normalize PinchBench model IDs to canonical provider/model form.

        PinchBench entries include provider prefixes from different hosting
        setups (lmstudio/, vllm/, opencode-go/, etc.).  This extracts the
        canonical model identity and unifies provider aliases.
        """
        parts = raw_id.split("/")
        if len(parts) >= 2:
            provider_hints = {
                "anthropic", "openai", "google", "deepseek", "minimax",
                "moonshot", "moonshotai", "xai", "x-ai", "nvidia",
                "meta-llama", "mistralai", "qwen", "z-ai", "zai-org",
                "stepfun", "xiaomi", "inception", "bailian",
            }
            for i, part in enumerate(parts):
                if part.lower() in provider_hints and i + 1 < len(parts):
                    provider = cls._PROVIDER_ALIASES.get(part.lower(), part.lower())
                    model_name = "/".join(parts[i + 1:])
                    return f"{provider}/{model_name}"
        return raw_id




class LocalFileProvider(BenchmarkProvider):
    """Load benchmark quality from a local JSON file.

    Supports manual quality overrides or imported benchmark data.
    File format: {"model_id": {"overall": 0.85, "categories": {...}}, ...}
    """

    source_name = "local"

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "benchmark_quality.json")

    @property
    def refresh_interval_s(self) -> float:
        return 300

    async def fetch(self) -> dict[str, ModelBenchmarkEntry]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            entries: dict[str, ModelBenchmarkEntry] = {}
            now = time.time()
            for model_id, values in raw.items():
                if isinstance(values, dict):
                    entries[model_id] = ModelBenchmarkEntry(
                        overall=float(values.get("overall", values.get("avg", 0.5))),
                        categories=dict(values.get("categories", {})),
                        raw=dict(values.get("raw", {})),
                        fetched_at=now,
                    )
                elif isinstance(values, (int, float)):
                    entries[model_id] = ModelBenchmarkEntry(
                        overall=float(values), fetched_at=now,
                    )
            return entries
        except Exception as exc:
            logger.warning("Local benchmark file load failed: %s", exc)
            return {}


def _load_seed_data() -> dict[str, float]:
    """Load seed benchmark data.

    Checks two locations in order:
      1. User data dir: ~/.uncommon-route/benchmark_seed.json (user overrides)
      2. Package data:  uncommon_route/router/benchmark_seed.json (shipped default)

    Seed data bootstraps quality estimation before the first API fetch.
    The package ships with PinchBench baseline data so routing works
    correctly from the first request.
    """
    for path in [
        _DATA_DIR / "benchmark_seed.json",
        Path(__file__).parent / "router" / "benchmark_seed.json",
    ]:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    result = {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}
                    if result:
                        return result
            except Exception:
                continue
    return {}


_PINCHBENCH_SEED: dict[str, float] = _load_seed_data()


@dataclass
class BenchmarkCache:
    """Aggregated benchmark quality data from multiple sources."""

    _sources: dict[str, dict[str, ModelBenchmarkEntry]] = field(default_factory=dict)
    _providers: list[BenchmarkProvider] = field(default_factory=list)
    _source_weights: dict[str, float] = field(default_factory=dict)
    _last_refresh: float = 0.0

    def __post_init__(self) -> None:
        if not self._providers:
            self._providers = [
                PinchBenchProvider(),
                LocalFileProvider(),
            ]
        if not self._source_weights:
            self._source_weights = {
                "pinchbench": 0.6,
                "local": 0.8,
            }
        self._load_cache()
        self._load_seed_as_source()
        self._build_index()

    def add_provider(self, provider: BenchmarkProvider, weight: float = 0.3) -> None:
        self._providers.append(provider)
        self._source_weights[provider.source_name] = weight

    async def refresh(self, force: bool = False) -> int:
        """Fetch from all providers.  Returns number of models updated."""
        total = 0
        for provider in self._providers:
            if not force:
                source_data = self._sources.get(provider.source_name, {})
                if source_data:
                    newest = max((e.fetched_at for e in source_data.values()), default=0)
                    if time.time() - newest < provider.refresh_interval_s:
                        continue
            try:
                entries = await provider.fetch()
                if entries:
                    self._sources[provider.source_name] = entries
                    total += len(entries)
                    logger.info(
                        "Benchmark refresh: %d models from %s",
                        len(entries),
                        provider.source_name,
                    )
            except Exception as exc:
                logger.warning("Benchmark refresh failed for %s: %s", provider.source_name, exc)
        if total > 0:
            self._last_refresh = time.time()
            self._save_cache()
            self._build_index()
        return total

    def get_quality(self, model_id: str, category: str = "") -> float:
        """Get the best available quality score for a model.

        Checks all sources via exact match, fuzzy match, and model-family
        match.  Returns the highest quality found across all matching
        strategies, weighted by source reliability.
        """
        scores: list[tuple[float, float]] = []

        for source_name, entries in self._sources.items():
            entry = entries.get(model_id)
            if entry is None:
                entry = self._fuzzy_match(model_id, entries)
            if entry is not None and entry.fetched_at and entry.overall > 0:
                weight = self._source_weights.get(source_name, 0.3)
                if category and category in entry.categories:
                    cat_score = entry.categories[category]
                    if cat_score > 0:
                        scores.append((cat_score, weight))
                elif entry.overall > 0:
                    has_real_data = bool(entry.raw) or bool(entry.categories)
                    scores.append((entry.overall, weight * (1.0 if has_real_data else 0.3)))

        family = self._extract_model_family(model_id)
        family_candidates = self._family_index.get(family, [])
        for src, mid, _ in family_candidates:
            if mid == model_id:
                continue
            source_entries = self._sources.get(src)
            if source_entries is None:
                continue
            entry = source_entries.get(mid)
            if entry is not None and entry.fetched_at and entry.overall > 0:
                weight = self._source_weights.get(src, 0.3) * 0.85
                if category and category in entry.categories:
                    cat_score = entry.categories[category]
                    if cat_score > 0:
                        scores.append((cat_score, weight))
                else:
                    has_real_data = bool(entry.raw) or bool(entry.categories)
                    scores.append((entry.overall, weight * (1.0 if has_real_data else 0.3)))

        if scores:
            total_weight = sum(w for _, w in scores)
            return sum(s * w for s, w in scores) / total_weight if total_weight > 0 else 0.5

        seed = _PINCHBENCH_SEED.get(model_id)
        if seed is not None:
            return seed

        seed = self._fuzzy_seed_match(model_id)
        if seed is not None:
            return seed

        return 0.5

    def get_all_qualities(self, models: list[str], category: str = "") -> dict[str, float]:
        return {m: self.get_quality(m, category) for m in models}

    def model_count(self) -> int:
        seen: set[str] = set()
        for entries in self._sources.values():
            seen.update(entries.keys())
        return len(seen)

    def source_summary(self) -> dict[str, int]:
        return {name: len(entries) for name, entries in self._sources.items()}

    @staticmethod
    def _extract_model_family(model_id: str) -> str:
        """Extract provider/family key for model-family matching.

        ``claude-sonnet-4.6`` → ``anthropic/claude-sonnet``
        ``gemini-2.5-pro``    → ``google/gemini-pro``
        ``glm-4.7``           → ``zai-org/glm``
        """
        import re
        if "/" in model_id:
            provider, name = model_id.split("/", 1)
        else:
            provider, name = "", model_id
        provider = PinchBenchProvider._PROVIDER_ALIASES.get(provider.lower(), provider.lower())
        name = name.lower()
        name = re.sub(r"[-_]?\d+(\.\d+)*", "", name)
        name = re.sub(r"-(preview|eco|lite|air|fast|plus|next|fp\d+)$", "", name)
        name = name.strip("-_")
        return f"{provider}/{name}" if provider else name

    def _build_index(self) -> None:
        self._normalized_index: dict[str, tuple[str, str]] = {}
        self._family_index: dict[str, list[tuple[str, str, float]]] = {}
        for source_name, entries in self._sources.items():
            for model_id, entry in entries.items():
                norm = model_id.lower().replace(".", "-").replace("_", "-")
                core = model_id.split("/", 1)[-1].lower() if "/" in model_id else model_id.lower()
                self._normalized_index[norm] = (source_name, model_id)
                self._normalized_index[core] = (source_name, model_id)
                family = self._extract_model_family(model_id)
                self._family_index.setdefault(family, []).append(
                    (source_name, model_id, entry.overall)
                )

    def _fuzzy_match(self, model_id: str, entries: dict[str, ModelBenchmarkEntry]) -> ModelBenchmarkEntry | None:
        normalized = model_id.lower().replace(".", "-").replace("_", "-")
        core = model_id.split("/", 1)[-1].lower() if "/" in model_id else model_id.lower()

        for key in (normalized, core):
            hit = self._normalized_index.get(key)
            if hit is not None:
                _, canonical_id = hit
                entry = entries.get(canonical_id)
                if entry is not None:
                    return entry

        for key, entry in entries.items():
            if key.lower().replace(".", "-").replace("_", "-") == normalized:
                return entry
        for key, entry in entries.items():
            key_core = key.split("/", 1)[-1].lower() if "/" in key else key.lower()
            if core == key_core:
                return entry

        family = self._extract_model_family(model_id)
        candidates = self._family_index.get(family, [])
        best_entry: ModelBenchmarkEntry | None = None
        best_score = -1.0
        for src, mid, score in candidates:
            entry = entries.get(mid)
            if entry is not None and entry.overall > best_score:
                best_score = entry.overall
                best_entry = entry
        if best_entry is not None:
            return best_entry
        return None

    def _fuzzy_seed_match(self, model_id: str) -> float | None:
        normalized = model_id.lower().replace(".", "-").replace("_", "-")
        for seed_id, score in _PINCHBENCH_SEED.items():
            if seed_id.lower().replace(".", "-").replace("_", "-") == normalized:
                return score
        core = model_id.split("/", 1)[-1].lower() if "/" in model_id else model_id.lower()
        for seed_id, score in _PINCHBENCH_SEED.items():
            seed_core = seed_id.split("/", 1)[-1].lower() if "/" in seed_id else seed_id.lower()
            if core == seed_core:
                return score
        return None

    def _load_seed_as_source(self) -> None:
        """Load seed benchmark data as a high-confidence source.

        Seed data is curated from official benchmark websites and provides
        reliable quality scores.  It serves as a strong prior that API data
        refines rather than replaces.
        """
        if not _PINCHBENCH_SEED:
            return
        now = time.time()
        entries: dict[str, ModelBenchmarkEntry] = {}
        for model_id, score in _PINCHBENCH_SEED.items():
            entries[model_id] = ModelBenchmarkEntry(
                overall=score,
                categories={"agent": score},
                raw={"source": "seed"},
                fetched_at=now,
            )
        self._sources["seed"] = entries
        self._source_weights["seed"] = 0.8

    def _save_cache(self) -> None:
        try:
            payload: dict[str, dict] = {}
            for source_name, entries in self._sources.items():
                payload[source_name] = {
                    model_id: asdict(entry)
                    for model_id, entry in entries.items()
                }
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _CACHE_PATH.write_text(json.dumps({
                "version": 1,
                "last_refresh": self._last_refresh,
                "sources": payload,
            }, indent=2))
        except Exception as exc:
            logger.warning("Benchmark cache save failed: %s", exc)

    def _load_cache(self) -> None:
        if not _CACHE_PATH.exists():
            return
        try:
            raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            self._last_refresh = float(raw.get("last_refresh", 0))
            for source_name, entries_raw in raw.get("sources", {}).items():
                entries: dict[str, ModelBenchmarkEntry] = {}
                for model_id, values in entries_raw.items():
                    entries[model_id] = ModelBenchmarkEntry(
                        overall=float(values.get("overall", 0.5)),
                        categories=dict(values.get("categories", {})),
                        raw=dict(values.get("raw", {})),
                        fetched_at=float(values.get("fetched_at", 0)),
                    )
                self._sources[source_name] = entries
        except Exception as exc:
            logger.warning("Benchmark cache load failed: %s", exc)


_ACTIVE_CACHE: BenchmarkCache | None = None


def get_benchmark_cache() -> BenchmarkCache:
    global _ACTIVE_CACHE
    if _ACTIVE_CACHE is None:
        _ACTIVE_CACHE = BenchmarkCache()
    return _ACTIVE_CACHE
