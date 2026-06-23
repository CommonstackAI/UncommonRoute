"""Artifact storage for large tool outputs and other offloaded payloads."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from uncommon_route.paths import data_dir
from uncommon_route.router.structural import estimate_tokens

_DATA_DIR = data_dir()
DEFAULT_ARTIFACTS_DIR = _DATA_DIR / "artifacts"
_DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
_ENABLED_VALUES = {"1", "true", "yes", "on", "enabled"}


def content_persistence_enabled(env: dict[str, str] | None = None) -> bool:
    if env is None:
        env = os.environ
    capture = str(env.get("UNCOMMON_ROUTE_CAPTURE_CONTENT", "1")).strip().lower()
    if capture in _DISABLED_VALUES:
        return False
    disabled = str(env.get("UNCOMMON_ROUTE_DISABLE_ARTIFACTS", "")).strip().lower()
    if disabled in _ENABLED_VALUES:
        return False
    return True


def _write_private_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        path.chmod(0o600)
    except Exception:
        pass


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    created_at: float
    kind: str
    role: str
    session_id: str
    tool_name: str
    tool_call_id: str
    content_type: str
    char_count: int
    token_estimate: int
    sha256: str
    preview: str
    summary: str = ""


class ArtifactStore:
    """Filesystem-backed artifact store."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        now_fn: Any = None,
        enabled: bool | None = None,
    ) -> None:
        self._root = root or DEFAULT_ARTIFACTS_DIR
        self._now = now_fn or time.time
        self._enabled = content_persistence_enabled() if enabled is None else bool(enabled)
        if self._enabled:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def enabled(self) -> bool:
        return self._enabled

    def store_text(
        self,
        content: str,
        *,
        kind: str = "raw",
        role: str,
        session_id: str = "",
        tool_name: str = "",
        tool_call_id: str = "",
        content_type: str = "text/plain",
        summary: str = "",
    ) -> ArtifactRecord:
        if not self._enabled:
            raise RuntimeError("Artifact storage is disabled")
        created_at = self._now()
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = self._find_existing(
            sha256=sha256,
            kind=kind,
            role=role,
            session_id=session_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        if existing is not None:
            if summary and not existing.summary:
                updated = ArtifactRecord(**{**asdict(existing), "summary": summary})
                _write_private_text(self._meta_path(existing.id), json.dumps(asdict(updated), indent=2))
                return updated
            return existing
        artifact_id = uuid.uuid4().hex[:12]
        preview = content[:240].replace("\r\n", "\n").replace("\r", "\n")
        record = ArtifactRecord(
            id=artifact_id,
            created_at=created_at,
            kind=kind,
            role=role,
            session_id=session_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            content_type=content_type,
            char_count=len(content),
            token_estimate=estimate_tokens(content),
            sha256=sha256,
            preview=preview,
            summary=summary,
        )
        _write_private_text(self._content_path(artifact_id), content)
        _write_private_text(self._meta_path(artifact_id), json.dumps(asdict(record), indent=2))
        return record

    def update_summary(self, artifact_id: str, summary: str) -> dict[str, Any] | None:
        artifact = self.get(artifact_id)
        if artifact is None:
            return None
        artifact["summary"] = summary
        _write_private_text(
            self._meta_path(artifact_id),
            json.dumps({k: v for k, v in artifact.items() if k != "content"}, indent=2),
        )
        return artifact

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        if not self._enabled:
            return None
        meta_path = self._meta_path(artifact_id)
        content_path = self._content_path(artifact_id)
        if not meta_path.exists() or not content_path.exists():
            return None
        meta = json.loads(meta_path.read_text())
        meta["content"] = content_path.read_text()
        return meta

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self._enabled:
            return []
        items: list[dict[str, Any]] = []
        for meta_path in sorted(self._root.glob("*.json"), reverse=True):
            try:
                items.append(json.loads(meta_path.read_text()))
            except Exception:
                continue
            if len(items) >= limit:
                break
        return items

    def count(self) -> int:
        if not self._enabled:
            return 0
        return sum(1 for _ in self._root.glob("*.json"))

    def _find_existing(
        self,
        *,
        sha256: str,
        kind: str,
        role: str,
        session_id: str,
        tool_name: str,
        tool_call_id: str,
    ) -> ArtifactRecord | None:
        for meta_path in self._root.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                continue
            if (
                meta.get("sha256") == sha256
                and meta.get("kind", "raw") == kind
                and meta.get("role") == role
                and meta.get("session_id", "") == session_id
                and meta.get("tool_name", "") == tool_name
                and meta.get("tool_call_id", "") == tool_call_id
            ):
                return ArtifactRecord(**meta)
        return None

    def _meta_path(self, artifact_id: str) -> Path:
        return self._root / f"{artifact_id}.json"

    def _content_path(self, artifact_id: str) -> Path:
        return self._root / f"{artifact_id}.txt"
