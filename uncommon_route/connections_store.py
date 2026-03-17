"""Persistent primary-connection configuration for UncommonRoute.

This store backs dashboard-managed upstream settings. The effective runtime
connection can still be overridden by CLI flags or environment variables.
"""

from __future__ import annotations

import copy
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from uncommon_route.paths import data_dir

_DATA_DIR = data_dir()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _pick_first(*pairs: tuple[str, str]) -> tuple[str, str]:
    for value, source in pairs:
        if value:
            return value, source
    return "", "unset"


@dataclass(frozen=True, slots=True)
class PrimaryConnection:
    upstream: str = ""
    api_key: str = ""


@dataclass(frozen=True, slots=True)
class EffectivePrimaryConnection:
    upstream: str
    api_key: str
    source: str
    upstream_source: str
    api_key_source: str
    editable: bool


class ConnectionsStorage(ABC):
    @abstractmethod
    def load(self) -> dict[str, Any]: ...

    @abstractmethod
    def save(self, data: dict[str, Any]) -> None: ...


class FileConnectionsStorage(ConnectionsStorage):
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (_DATA_DIR / "connections.json")

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


class InMemoryConnectionsStorage(ConnectionsStorage):
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def load(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def save(self, data: dict[str, Any]) -> None:
        self._data = copy.deepcopy(data)


def _sanitize_primary(raw: dict[str, Any]) -> PrimaryConnection:
    primary = raw.get("primary")
    if not isinstance(primary, dict):
        return PrimaryConnection()
    return PrimaryConnection(
        upstream=_clean_text(primary.get("upstream")),
        api_key=_clean_text(primary.get("api_key")),
    )


def mask_api_key(api_key: str) -> str:
    value = _clean_text(api_key)
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    if len(value) <= 8:
        return f"{value[:2]}..."
    return f"{value[:4]}...{value[-3:]}"


class ConnectionsStore:
    def __init__(self, storage: ConnectionsStorage | None = None) -> None:
        self._storage = storage or FileConnectionsStorage()
        self._primary = _sanitize_primary(self._storage.load())

    def primary(self) -> PrimaryConnection:
        return self._primary

    def export(self) -> dict[str, Any]:
        return {
            "source": "local-file",
            "editable": True,
            "primary": {
                "upstream": self._primary.upstream,
                "has_api_key": bool(self._primary.api_key),
            },
        }

    def set_primary(
        self,
        *,
        upstream: str,
        api_key: str,
    ) -> dict[str, Any]:
        self._primary = PrimaryConnection(
            upstream=_clean_text(upstream),
            api_key=_clean_text(api_key),
        )
        self._persist()
        return self.export()

    def reset(self) -> dict[str, Any]:
        self._primary = PrimaryConnection()
        self._persist()
        return self.export()

    def _persist(self) -> None:
        self._storage.save({
            "primary": {
                "upstream": self._primary.upstream,
                "api_key": self._primary.api_key,
            },
        })


def resolve_primary_connection(
    *,
    cli_upstream: str | None = None,
    cli_api_key: str | None = None,
    env: Mapping[str, str] | None = None,
    store: ConnectionsStore | None = None,
) -> EffectivePrimaryConnection:
    env_map = env or os.environ
    active_store = store or ConnectionsStore()
    stored = active_store.primary()

    env_upstream = _clean_text(env_map.get("UNCOMMON_ROUTE_UPSTREAM", ""))
    env_api_key = _clean_text(
        env_map.get("UNCOMMON_ROUTE_API_KEY", "")
        or env_map.get("COMMONSTACK_API_KEY", ""),
    )

    upstream, upstream_source = _pick_first(
        (_clean_text(cli_upstream), "flag"),
        (env_upstream, "env"),
        (stored.upstream, "file"),
    )
    api_key, api_key_source = _pick_first(
        (_clean_text(cli_api_key), "flag"),
        (env_api_key, "env"),
        (stored.api_key, "file"),
    )

    if upstream_source == "flag" or api_key_source == "flag":
        source = "flag"
    elif upstream_source == "env" or api_key_source == "env":
        source = "env"
    elif upstream_source == "file" or api_key_source == "file":
        source = "file"
    else:
        source = "unset"

    return EffectivePrimaryConnection(
        upstream=upstream,
        api_key=api_key,
        source=source,
        upstream_source=upstream_source,
        api_key_source=api_key_source,
        editable=source in {"file", "unset"},
    )
