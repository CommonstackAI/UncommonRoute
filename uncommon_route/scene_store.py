"""Named scene routing — persistent, user-defined routing profiles.

A *scene* is a named routing preset that overrides (or constrains) the
normal classifier → selector pipeline.  Scenes let users declare
domain-specific routing policies like "intimate" (always opus, hard-pin)
or "construction_crew" (cheap models only, adaptive selection).

Two behaviours depending on ``hard_pin``:

* **hard_pin=True** — bypass classifier *and* selector; use
  ``primary`` directly with ``fallback`` as the ordered chain.
* **hard_pin=False** — constrain candidate pool to the scene's model
  list, optionally apply tier_floor/tier_cap, but let the full
  classifier → selector pipeline score within that pool.
  This preserves latency/reliability/feedback/bandit scoring.

Trigger precedence (checked in proxy):

1. ``x-uncommon-route-scene: <name>`` request header
2. Virtual model ID ``uncommon-route/scene/<name>``
3. OpenClaw session → scene mapping (injected as header by plugin)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir
from uncommon_route.router.types import (
    RoutingConstraints,
    Tier,
)

logger = logging.getLogger("uncommon-route.scene")

_DATA_DIR = data_dir()
_SCENES_FILE = _DATA_DIR / "scenes.json"


@dataclass
class SceneConfig:
    """A named routing scene."""

    name: str
    primary: str
    fallback: list[str] = field(default_factory=list)
    hard_pin: bool = False
    description: str = ""

    # Optional tier floor/cap — lets a scene say "at least MEDIUM".
    tier_floor: Tier | None = None
    tier_cap: Tier | None = None

    # Provider-level constraint (e.g. only allow anthropic/).
    allowed_providers: list[str] = field(default_factory=list)

    # Per-scene spend limit ($/request).  None = no limit.
    max_cost_per_request: float | None = None

    def model_pool(self) -> list[str]:
        """Return ordered candidate list: [primary, *fallback]."""
        seen: set[str] = set()
        pool: list[str] = []
        for model in [self.primary, *self.fallback]:
            normalized = model.strip()
            if normalized and normalized not in seen:
                pool.append(normalized)
                seen.add(normalized)
        return pool

    def as_routing_constraints(self) -> RoutingConstraints:
        """Build a RoutingConstraints from scene settings."""
        return RoutingConstraints(
            allowed_models=tuple(self.model_pool()),
            allowed_providers=tuple(self.allowed_providers),
            max_cost=self.max_cost_per_request,
        )

    def as_selection_weights(self) -> None:
        """Reserved for future use when route() supports injected weights."""
        return None


def _serialize_scene(scene: SceneConfig) -> dict[str, Any]:
    """Convert SceneConfig → JSON-safe dict."""
    data = asdict(scene)
    # Convert Tier enums to strings
    if data.get("tier_floor") is not None:
        data["tier_floor"] = data["tier_floor"].value if isinstance(data["tier_floor"], Tier) else str(data["tier_floor"])
    else:
        data.pop("tier_floor", None)
    if data.get("tier_cap") is not None:
        data["tier_cap"] = data["tier_cap"].value if isinstance(data["tier_cap"], Tier) else str(data["tier_cap"])
    else:
        data.pop("tier_cap", None)
    # Strip None/empty optionals for cleaner JSON
    if not data.get("allowed_providers"):
        data.pop("allowed_providers", None)
    if data.get("max_cost_per_request") is None:
        data.pop("max_cost_per_request", None)
    return data


def _deserialize_scene(data: dict[str, Any]) -> SceneConfig | None:
    """Parse a JSON dict → SceneConfig.  Returns None on invalid data."""
    try:
        name = str(data.get("name", "")).strip().lower()
        primary = str(data.get("primary", "")).strip()
        if not name or not primary:
            return None

        fallback_raw = data.get("fallback", [])
        fallback = (
            [str(f).strip() for f in fallback_raw]
            if isinstance(fallback_raw, list)
            else [str(fallback_raw).strip()]
        )

        tier_floor = None
        if "tier_floor" in data and data["tier_floor"]:
            try:
                tier_floor = Tier(str(data["tier_floor"]).upper())
            except ValueError:
                pass

        tier_cap = None
        if "tier_cap" in data and data["tier_cap"]:
            try:
                tier_cap = Tier(str(data["tier_cap"]).upper())
            except ValueError:
                pass

        allowed_providers = data.get("allowed_providers", [])
        if not isinstance(allowed_providers, list):
            allowed_providers = []

        return SceneConfig(
            name=name,
            primary=primary,
            fallback=[f for f in fallback if f],
            hard_pin=bool(data.get("hard_pin", False)),
            description=str(data.get("description", "")),
            tier_floor=tier_floor,
            tier_cap=tier_cap,
            allowed_providers=[str(p).strip() for p in allowed_providers if str(p).strip()],
            max_cost_per_request=data.get("max_cost_per_request"),
        )
    except Exception:
        logger.warning("Failed to deserialize scene: %s", data, exc_info=True)
        return None


class SceneStore:
    """CRUD store for named scenes, backed by a JSON file."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _SCENES_FILE
        self._scenes: dict[str, SceneConfig] = {}
        self._load()

    def _load(self) -> None:
        """Load scenes from disk.  Silently ignores corrupt files."""
        self._scenes = {}
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text())
                if isinstance(raw, dict):
                    scenes_data = raw.get("scenes", {})
                    if isinstance(scenes_data, dict):
                        for _key, scene_data in scenes_data.items():
                            scene = _deserialize_scene(scene_data)
                            if scene:
                                self._scenes[scene.name] = scene
        except Exception:
            logger.warning("Failed to load scenes from %s", self._path, exc_info=True)

    def _save(self) -> None:
        """Persist scenes to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = {
                "version": "1.0",
                "scenes": {
                    name: _serialize_scene(scene)
                    for name, scene in sorted(self._scenes.items())
                },
            }
            self._path.write_text(json.dumps(data, indent=2, sort_keys=True))
            self._path.chmod(0o600)
        except Exception:
            logger.error("Failed to save scenes to %s", self._path, exc_info=True)

    def get(self, name: str) -> SceneConfig | None:
        """Look up a scene by name (case-insensitive)."""
        return self._scenes.get(name.strip().lower())

    def list(self) -> list[SceneConfig]:
        """Return all scenes, sorted by name."""
        return sorted(self._scenes.values(), key=lambda s: s.name)

    def add(self, scene: SceneConfig) -> SceneConfig:
        """Add or update a scene.  Returns the stored scene."""
        normalized = SceneConfig(
            name=scene.name.strip().lower(),
            primary=scene.primary.strip(),
            fallback=[f.strip() for f in scene.fallback if f.strip()],
            hard_pin=scene.hard_pin,
            description=scene.description,
            tier_floor=scene.tier_floor,
            tier_cap=scene.tier_cap,
            allowed_providers=scene.allowed_providers,
            max_cost_per_request=scene.max_cost_per_request,
        )
        # Deduplicate fallback against primary
        normalized = replace(
            normalized,
            fallback=[f for f in normalized.fallback if f != normalized.primary],
        )
        self._scenes[normalized.name] = normalized
        self._save()
        return normalized

    def remove(self, name: str) -> bool:
        """Remove a scene.  Returns True if it existed."""
        key = name.strip().lower()
        if key in self._scenes:
            del self._scenes[key]
            self._save()
            return True
        return False

    def resolve(self, name: str) -> SceneConfig | None:
        """Resolve a scene name, with graceful degradation.

        Returns None if the scene doesn't exist (caller falls back to
        normal routing).
        """
        scene = self.get(name)
        if scene is None:
            logger.debug("Scene '%s' not found, falling back to normal routing", name)
        return scene

    def export(self) -> dict[str, Any]:
        """Export all scenes as a JSON-serializable dict."""
        return {
            "version": "1.0",
            "scenes": {
                name: _serialize_scene(scene)
                for name, scene in sorted(self._scenes.items())
            },
        }

    def import_scenes(self, data: dict[str, Any]) -> int:
        """Import scenes from a dict (merge, not replace).  Returns count added."""
        count = 0
        scenes_data = data.get("scenes", {})
        if isinstance(scenes_data, dict):
            for _key, scene_data in scenes_data.items():
                scene = _deserialize_scene(scene_data)
                if scene:
                    self._scenes[scene.name] = scene
                    count += 1
        if count:
            self._save()
        return count
