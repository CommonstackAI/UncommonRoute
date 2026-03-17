"""Persistent routing-config overrides for mode/tier model priorities."""

from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir
from uncommon_route.router.config import DEFAULT_CONFIG, get_mode_tiers
from uncommon_route.router.types import RoutingConfig, RoutingMode, Tier, TierConfig

_DATA_DIR = data_dir()


class RoutingConfigStorage(ABC):
    @abstractmethod
    def load(self) -> dict[str, Any]: ...

    @abstractmethod
    def save(self, data: dict[str, Any]) -> None: ...


class FileRoutingConfigStorage(RoutingConfigStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "routing_config.json")

    def load(self) -> dict[str, Any]:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def save(self, data: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._path.write_text(json.dumps(data, indent=2, sort_keys=True))
            self._path.chmod(0o600)
        except Exception:
            pass


class InMemoryRoutingConfigStorage(RoutingConfigStorage):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def save(self, data: dict[str, Any]) -> None:
        self._data = copy.deepcopy(data)


def _mode_table(config: RoutingConfig, mode: RoutingMode) -> dict[Tier, TierConfig]:
    return get_mode_tiers(config, mode)


def _normalize_fallback(primary: str, fallback: list[str]) -> list[str]:
    normalized: list[str] = []
    seen = {primary}
    for raw in fallback:
        model = str(raw).strip()
        if not model or model in seen:
            continue
        normalized.append(model)
        seen.add(model)
    return normalized


def _normalize_tier_name(tier_name: str) -> str:
    normalized = str(tier_name).strip().upper()
    return "COMPLEX" if normalized == "REASONING" else normalized


def _sanitize_overrides(raw: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    modes = raw.get("modes")
    if not isinstance(modes, dict):
        return result

    for mode_name, tier_map in modes.items():
        try:
            mode = RoutingMode(str(mode_name))
        except ValueError:
            continue
        if not isinstance(tier_map, dict):
            continue
        clean_tiers: dict[str, dict[str, Any]] = {}
        for tier_name, payload in tier_map.items():
            try:
                tier = Tier(_normalize_tier_name(str(tier_name)))
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            primary = str(payload.get("primary", "")).strip()
            selection_mode = str(payload.get("selection_mode", "")).strip().lower()
            hard_pin = bool(payload.get("hard_pin", False))
            if selection_mode:
                hard_pin = selection_mode in {"hard-pin", "hard_pin", "pinned"}
            fallback_raw = payload.get("fallback", [])
            if isinstance(fallback_raw, str):
                fallback = [part.strip() for part in fallback_raw.split(",")]
            elif isinstance(fallback_raw, list):
                fallback = [str(item).strip() for item in fallback_raw]
            else:
                fallback = []
            if not primary:
                continue
            clean_tiers[tier.value] = {
                "primary": primary,
                "fallback": _normalize_fallback(primary, fallback),
                "hard_pin": hard_pin,
            }
        if clean_tiers:
            result[mode.value] = clean_tiers
    return result


def _sanitize_default_mode(raw: dict[str, Any]) -> RoutingMode:
    value = raw.get("default_mode", RoutingMode.AUTO.value)
    try:
        return RoutingMode(str(value))
    except ValueError:
        return RoutingMode.AUTO


class RoutingConfigStore:
    def __init__(
        self,
        storage: RoutingConfigStorage | None = None,
        base_config: RoutingConfig | None = None,
    ) -> None:
        self._storage = storage or FileRoutingConfigStorage()
        self._base_config = copy.deepcopy(base_config or DEFAULT_CONFIG)
        raw = self._storage.load()
        self._overrides = _sanitize_overrides(raw)
        self._default_mode = _sanitize_default_mode(raw)

    def config(self) -> RoutingConfig:
        cfg: RoutingConfig = copy.deepcopy(self._base_config)
        for mode_name, tier_map in self._overrides.items():
            mode = RoutingMode(mode_name)
            table = _mode_table(cfg, mode)
            for tier_name, payload in tier_map.items():
                tier = Tier(tier_name)
                table[tier] = TierConfig(
                    primary=str(payload["primary"]),
                    fallback=list(payload.get("fallback", [])),
                    hard_pin=bool(payload.get("hard_pin", False)),
                )
        return cfg

    def export(self) -> dict[str, Any]:
        cfg = self.config()
        modes: dict[str, dict[str, Any]] = {}
        for mode in RoutingMode:
            active = _mode_table(cfg, mode)
            overridden_tiers = self._overrides.get(mode.value, {})
            tier_rows: dict[str, Any] = {}
            for tier in Tier:
                tc = active[tier]
                tier_rows[tier.value] = {
                    "primary": tc.primary,
                    "fallback": list(tc.fallback),
                    "overridden": tier.value in overridden_tiers,
                    "hard_pin": tc.hard_pin,
                    "selection_mode": "hard-pin" if tc.hard_pin else "adaptive",
                }
            modes[mode.value] = {"tiers": tier_rows}
        return {
            "source": "local-file",
            "editable": True,
            "default_mode": self._default_mode.value,
            "modes": modes,
        }

    def default_mode(self) -> RoutingMode:
        return self._default_mode

    def set_default_mode(self, mode: RoutingMode) -> dict[str, Any]:
        self._default_mode = mode
        self._persist()
        return self.export()

    def reset_default_mode(self) -> dict[str, Any]:
        self._default_mode = RoutingMode.AUTO
        self._persist()
        return self.export()

    def set_tier(
        self,
        mode: RoutingMode,
        tier: Tier,
        *,
        primary: str,
        fallback: list[str],
        hard_pin: bool = False,
    ) -> dict[str, Any]:
        normalized_primary = str(primary).strip()
        if not normalized_primary:
            raise ValueError("primary model is required")
        normalized_fallback = _normalize_fallback(normalized_primary, fallback)

        default_tc = _mode_table(self._base_config, mode)[tier]
        mode_overrides = self._overrides.setdefault(mode.value, {})
        if (
            normalized_primary == default_tc.primary
            and normalized_fallback == list(default_tc.fallback)
            and bool(hard_pin) is bool(default_tc.hard_pin)
        ):
            mode_overrides.pop(tier.value, None)
        else:
            mode_overrides[tier.value] = {
                "primary": normalized_primary,
                "fallback": normalized_fallback,
                "hard_pin": bool(hard_pin),
            }

        if not mode_overrides:
            self._overrides.pop(mode.value, None)
        self._persist()
        return self.export()

    def reset_tier(self, mode: RoutingMode, tier: Tier) -> dict[str, Any]:
        mode_overrides = self._overrides.get(mode.value)
        if mode_overrides is not None:
            mode_overrides.pop(tier.value, None)
            if not mode_overrides:
                self._overrides.pop(mode.value, None)
            self._persist()
        return self.export()

    def reset(self) -> dict[str, Any]:
        self._overrides = {}
        self._default_mode = RoutingMode.AUTO
        self._persist()
        return self.export()

    def _persist(self) -> None:
        self._storage.save({
            "default_mode": self._default_mode.value,
            "modes": self._overrides,
        })
