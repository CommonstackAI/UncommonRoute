"""Model discovery and dynamic pool management.

On startup, fetches ``/v1/models`` from the upstream and builds:
  - Full model catalog with upstream-sourced pricing
  - Auto-inferred capabilities from model names
  - Bidirectional name mappings (internal <-> upstream)

Static model lists (``DEFAULT_MODEL_PRICING``, etc.) serve only as
seed/fallback data.  The live model pool is built from upstream reality.

Usage::

    mapper = ModelMapper("https://api.commonstack.ai/v1")
    await mapper.discover(api_key="csk-...")
    pricing = mapper.dynamic_pricing        # all models with live pricing
    caps = mapper.dynamic_capabilities      # all models with inferred capabilities
    upstream_name = mapper.resolve("moonshot/kimi-k2.5")
    # => "moonshotai/kimi-k2.5"
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from uncommon_route.router.types import ModelCapabilities, ModelPricing

logger = logging.getLogger("uncommon-route")

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

GATEWAY_DOMAINS: dict[str, str] = {
    "commonstack.ai": "commonstack",
}

DIRECT_PROVIDER_DOMAINS: dict[str, str] = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.deepseek.com": "deepseek",
    "api.minimax.io": "minimax",
    "api.minimax.chat": "minimax",
    "generativelanguage.googleapis.com": "google",
    "api.x.ai": "xai",
    "api.moonshot.cn": "moonshot",
}


def detect_provider(url: str) -> tuple[str, bool]:
    """Return ``(provider_name, is_gateway)`` from an upstream URL."""
    url_lower = url.lower()
    for domain, name in GATEWAY_DOMAINS.items():
        if domain in url_lower:
            return name, True
    for domain, name in DIRECT_PROVIDER_DOMAINS.items():
        if domain in url_lower:
            return name, False
    return "unknown", False


# ---------------------------------------------------------------------------
# Seed aliases — bootstrap data for cold start only.
# Runtime learning supplements and eventually replaces these.
# ---------------------------------------------------------------------------

SEED_ALIASES: dict[str, list[str]] = {
    "deepseek/deepseek-chat": [
        "deepseek/deepseek-v3.2",
        "deepseek/deepseek-v3.1",
        "deepseek/deepseek-v3",
    ],
    "deepseek/deepseek-reasoner": [
        "deepseek/deepseek-r1-0528",
        "deepseek/deepseek-r1",
    ],
    "google/gemini-2.5-flash-lite": [
        "google/gemini-3.1-flash-lite-preview",
    ],
}

# Backward compat
KNOWN_ALIASES = SEED_ALIASES


# ---------------------------------------------------------------------------
# Pricing parser
# ---------------------------------------------------------------------------

def _parse_upstream_pricing(raw: dict | None) -> ModelPricing:
    """Convert per-token pricing from upstream into per-1M-token ModelPricing."""
    if not raw or not isinstance(raw, dict):
        return ModelPricing(0.0, 0.0)
    try:
        input_price = float(raw.get("prompt", 0)) * 1_000_000
        output_price = float(raw.get("completion", 0)) * 1_000_000
        cached_input = raw.get("input_cache_reads")
        cache_write = raw.get("input_cache_writes")
        return ModelPricing(
            input_price=round(input_price, 4),
            output_price=round(output_price, 4),
            cached_input_price=round(float(cached_input) * 1_000_000, 4) if cached_input else None,
            cache_write_price=round(float(cache_write) * 1_000_000, 4) if cache_write else None,
        )
    except (ValueError, TypeError):
        return ModelPricing(0.0, 0.0)


# ---------------------------------------------------------------------------
# Capability inference
# ---------------------------------------------------------------------------

_REASONING_POSITIVE = ("reason", "r1", "thinking", "think", "o1-", "o3", "o4-")
_REASONING_NEGATIVE = ("non-reason", "non_reason", "no-reason", "non-thinking")


def infer_capabilities(
    model_id: str,
    pricing: ModelPricing,
    *,
    has_explicit_pricing: bool = True,
) -> ModelCapabilities:
    """Infer model capabilities from its name and pricing.

    Heuristic accuracy ~85%.  The bandit/feedback system corrects errors
    over time via runtime observations.
    """
    name = model_id.lower()
    core = name.split("/", 1)[-1] if "/" in name else name
    provider = name.split("/", 1)[0] if "/" in name else ""

    reasoning = False
    if any(p in core for p in _REASONING_POSITIVE):
        if not any(p in core for p in _REASONING_NEGATIVE):
            reasoning = True
    if provider == "anthropic" and "opus" in core:
        reasoning = True

    vision = False
    if any(p in core for p in ("-vl", "vision", "image")):
        vision = True
    if provider in ("anthropic", "google"):
        vision = True
    if provider == "openai" and any(p in core for p in ("4o", "gpt-5", "gpt-4")):
        vision = True

    tool_calling = True

    free = (
        has_explicit_pricing
        and pricing.input_price <= 0.0
        and pricing.output_price <= 0.0
    )

    return ModelCapabilities(
        tool_calling=tool_calling,
        vision=vision,
        reasoning=reasoning,
        free=free,
    )


# ---------------------------------------------------------------------------
# Normalization helpers for fuzzy matching
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Normalize a model name for comparison."""
    name = name.lower()
    name = re.sub(r"-\d{8}$", "", name)
    name = name.removesuffix("-preview")
    name = re.sub(r"(\d)\.(\d)", r"\1-\2", name)
    return name


def _core(model_id: str) -> str:
    """Model name without provider prefix."""
    return model_id.split("/", 1)[-1] if "/" in model_id else model_id


def _provider_prefix(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else ""


# ---------------------------------------------------------------------------
# DiscoveredModel
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """A model discovered from the upstream catalog."""
    id: str
    provider: str
    owned_by: str
    pricing: ModelPricing
    capabilities: ModelCapabilities


# ---------------------------------------------------------------------------
# ModelMapper
# ---------------------------------------------------------------------------

@dataclass
class ModelMapper:
    """Discovers upstream models and provides a dynamic pool with pricing,
    capabilities, and name resolution.

    After ``discover()`` runs, the mapper knows every model the upstream
    serves and can provide live pricing and inferred capabilities for each.
    Static config (``DEFAULT_MODEL_PRICING``, etc.) is only used as a
    fallback when discovery has not yet run.
    """

    upstream_url: str
    provider: str = ""
    is_gateway: bool = False

    _upstream_models: set[str] = field(default_factory=set, repr=False)
    _map: dict[str, str] = field(default_factory=dict, repr=False)
    _discovered: bool = False

    _pool: dict[str, DiscoveredModel] = field(default_factory=dict, repr=False)
    _learned_aliases: dict[str, str] = field(default_factory=dict, repr=False)
    _last_discovery_time: float = 0.0

    def __post_init__(self) -> None:
        self.provider, self.is_gateway = detect_provider(self.upstream_url)
        self._load_learned_aliases()

    # ---- discovery --------------------------------------------------------

    async def discover(self, api_key: str | None = None) -> int:
        """Fetch ``/v1/models`` from upstream and build the model pool.

        Extracts pricing and infers capabilities from upstream data.
        Returns the number of models discovered (0 on failure).
        """
        if not self.upstream_url:
            return 0

        models_url = f"{self.upstream_url.rstrip('/')}/models"
        headers: dict[str, str] = {"user-agent": "uncommon-route/model-discovery"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
            ) as client:
                resp = await client.get(models_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(
                        "Model discovery: HTTP %d from %s", resp.status_code, models_url,
                    )
                    return 0
                data = resp.json()
                raw = data.get("data", [])

                self._pool.clear()
                self._upstream_models.clear()

                for m in raw:
                    if not isinstance(m, dict) or "id" not in m:
                        continue
                    model_id = m["id"]
                    self._upstream_models.add(model_id)

                    pricing = _parse_upstream_pricing(m.get("pricing"))
                    capabilities = infer_capabilities(
                        model_id,
                        pricing,
                        has_explicit_pricing=isinstance(m.get("pricing"), dict) and bool(m.get("pricing")),
                    )
                    provider_name = _provider_prefix(model_id) or m.get("owned_by", "unknown")

                    self._pool[model_id] = DiscoveredModel(
                        id=model_id,
                        provider=provider_name,
                        owned_by=m.get("owned_by", ""),
                        pricing=pricing,
                        capabilities=capabilities,
                    )

                self._build_map()
                self._discovered = True
                self._last_discovery_time = time.monotonic()
                return len(self._upstream_models)
        except httpx.ConnectError:
            logger.warning("Model discovery: cannot connect to %s", models_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Model discovery: %s", exc)
        return 0

    def _build_map(self) -> None:
        """Match every internal model name to the best upstream candidate.

        Priority: learned alias > exact match > seed alias > fuzzy match.
        """
        from uncommon_route.router.config import DEFAULT_MODEL_PRICING

        self._map.clear()
        for internal in DEFAULT_MODEL_PRICING:
            if internal in self._upstream_models:
                continue
            if internal in self._learned_aliases:
                candidate = self._learned_aliases[internal]
                if candidate in self._upstream_models:
                    self._map[internal] = candidate
                    continue
            alias = self._seed_alias_match(internal)
            if alias:
                self._map[internal] = alias
                continue
            match = self._fuzzy_match(internal)
            if match:
                self._map[internal] = match

    def _seed_alias_match(self, internal: str) -> str | None:
        """Check seed alias table for known renames."""
        for candidate in SEED_ALIASES.get(internal, []):
            if candidate in self._upstream_models:
                return candidate
        return None

    def _fuzzy_match(self, internal: str) -> str | None:
        """Find the best-matching upstream model for *internal*.

        Scoring heuristic (highest wins, minimum 50 to accept):
          - Exact core name match:           100
          - Normalized core name match:       90
          - Substring containment:            70 * (shorter/longer)
          - Same provider prefix bonus:       +10
          - Similar provider prefix bonus:    +5
        """
        int_core = _core(internal)
        int_norm = _normalize(int_core)
        int_prov = _provider_prefix(internal)

        best_score = 0
        best: str | None = None

        for upstream in self._upstream_models:
            up_core = _core(upstream)
            up_norm = _normalize(up_core)
            up_prov = _provider_prefix(upstream)

            if int_core == up_core:
                score = 100
            elif int_norm == up_norm:
                score = 90
            elif int_norm in up_norm or up_norm in int_norm:
                longer = max(len(int_norm), len(up_norm))
                shorter = min(len(int_norm), len(up_norm))
                score = int(70 * (shorter / longer)) if longer else 0
            else:
                continue

            if int_prov and up_prov:
                if int_prov == up_prov:
                    score += 10
                elif int_prov in up_prov or up_prov in int_prov:
                    score += 5

            if score > best_score:
                best_score = score
                best = upstream

        return best if best_score >= 50 else None

    # ---- resolution -------------------------------------------------------

    def resolve(self, internal_name: str) -> str:
        """Translate an internal model name to what the upstream expects.

        Priority:
          1. Learned alias
          2. Dynamic map (from ``/v1/models`` discovery + fuzzy matching)
          3. Exact match in upstream model set
          4. Gateway -> keep full ``provider/model``; direct -> strip prefix
        """
        if internal_name in self._learned_aliases:
            candidate = self._learned_aliases[internal_name]
            if not self._discovered or candidate in self._upstream_models:
                return candidate

        if internal_name in self._map:
            return self._map[internal_name]

        if self._discovered and internal_name in self._upstream_models:
            return internal_name

        if not self.is_gateway and "/" in internal_name:
            return internal_name.split("/", 1)[-1]

        return internal_name

    # ---- dynamic pricing & capabilities -----------------------------------

    @property
    def dynamic_pricing(self) -> dict[str, ModelPricing]:
        """Live pricing for all known models (upstream IDs + internal aliases).

        When discovery has run, this replaces ``DEFAULT_MODEL_PRICING``.
        Both upstream IDs and internal names are included as keys so that
        existing code looking up either form finds valid pricing.
        """
        if not self._discovered:
            return {}
        result: dict[str, ModelPricing] = {}
        for model_id, dm in self._pool.items():
            result[model_id] = dm.pricing
        for internal, upstream in self._map.items():
            if upstream in self._pool:
                result[internal] = self._pool[upstream].pricing
        return result

    @property
    def dynamic_capabilities(self) -> dict[str, ModelCapabilities]:
        """Inferred capabilities for all known models."""
        if not self._discovered:
            return {}
        result: dict[str, ModelCapabilities] = {}
        for model_id, dm in self._pool.items():
            result[model_id] = dm.capabilities
        for internal, upstream in self._map.items():
            if upstream in self._pool:
                result[internal] = self._pool[upstream].capabilities
        return result

    @property
    def available_models(self) -> list[str]:
        """All available upstream model IDs, sorted."""
        return sorted(self._pool.keys())

    def get_pricing(self, model_id: str) -> ModelPricing | None:
        """Look up pricing for a single model (internal or upstream ID)."""
        if model_id in self._pool:
            return self._pool[model_id].pricing
        resolved = self.resolve(model_id)
        if resolved in self._pool:
            return self._pool[resolved].pricing
        return None

    def get_capabilities(self, model_id: str) -> ModelCapabilities | None:
        """Look up capabilities for a single model."""
        if model_id in self._pool:
            return self._pool[model_id].capabilities
        resolved = self.resolve(model_id)
        if resolved in self._pool:
            return self._pool[resolved].capabilities
        return None

    # ---- learned aliases --------------------------------------------------

    def record_alias(self, failed_name: str, working_name: str) -> None:
        """Record a mapping learned from a successful runtime fallback.

        Called when the proxy sends ``failed_name`` upstream, gets a model
        error, then falls back to ``working_name`` which succeeds.
        """
        if failed_name == working_name:
            return
        if self._discovered and working_name not in self._upstream_models:
            return
        self._learned_aliases[failed_name] = working_name
        self._map[failed_name] = working_name
        self._save_learned_aliases()
        logger.info("Learned alias: %s -> %s", failed_name, working_name)

    def _load_learned_aliases(self) -> None:
        path = self._learned_aliases_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if isinstance(data, dict):
                    self._learned_aliases = data
            except Exception:  # noqa: BLE001
                self._learned_aliases = {}

    def _save_learned_aliases(self) -> None:
        try:
            path = self._learned_aliases_path()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_text(json.dumps(self._learned_aliases, indent=2))
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _learned_aliases_path() -> Path:
        from uncommon_route.paths import data_dir
        return data_dir() / "learned_aliases.json"

    # ---- rediscovery ------------------------------------------------------

    def should_rediscover(self, interval_seconds: float = 300.0) -> bool:
        """Whether enough time has passed since last discovery."""
        if not self._discovered:
            return True
        return (time.monotonic() - self._last_discovery_time) >= interval_seconds

    # ---- inspection -------------------------------------------------------

    def is_available(self, model_name: str) -> bool | None:
        """``True`` if model resolves to a known upstream ID, ``None`` if unknown."""
        if not self._discovered:
            return None
        resolved = self.resolve(model_name)
        return resolved in self._upstream_models

    def unresolved_models(self) -> list[str]:
        """Internal names that have no confirmed upstream equivalent."""
        if not self._discovered:
            return []
        from uncommon_route.router.config import DEFAULT_MODEL_PRICING

        out: list[str] = []
        for name in DEFAULT_MODEL_PRICING:
            resolved = self.resolve(name)
            if resolved not in self._upstream_models:
                out.append(name)
        return out

    def mapping_table(self) -> list[dict[str, str | bool | None]]:
        """Legacy mapping table for backward-compat dashboard."""
        from uncommon_route.router.config import DEFAULT_MODEL_PRICING

        rows: list[dict[str, str | bool | None]] = []
        for name in DEFAULT_MODEL_PRICING:
            resolved = self.resolve(name)
            available: bool | None = None
            if self._discovered:
                available = resolved in self._upstream_models
            rows.append({
                "internal": name,
                "resolved": resolved,
                "mapped": name != resolved,
                "available": available,
            })
        return rows

    def pool_table(self) -> list[dict]:
        """Full model pool for the new dashboard — provider-grouped view."""
        rows: list[dict] = []
        for model_id in sorted(self._pool.keys()):
            dm = self._pool[model_id]
            rows.append({
                "id": dm.id,
                "provider": dm.provider,
                "owned_by": dm.owned_by,
                "pricing": {
                    "input": dm.pricing.input_price,
                    "output": dm.pricing.output_price,
                    "cached_input": dm.pricing.cached_input_price,
                    "cache_write": dm.pricing.cache_write_price,
                },
                "capabilities": {
                    "tool_calling": dm.capabilities.tool_calling,
                    "vision": dm.capabilities.vision,
                    "reasoning": dm.capabilities.reasoning,
                    "free": dm.capabilities.free,
                },
            })
        return rows

    @property
    def discovered(self) -> bool:
        return self._discovered

    @property
    def upstream_model_count(self) -> int:
        return len(self._upstream_models)

    @property
    def pool_size(self) -> int:
        return len(self._pool)
