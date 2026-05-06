"""Session identity utilities.

Provides ``derive_session_id`` for cache key generation, composition
checkpoint tracking, and routing continuity for agent/tool sessions.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


def derive_session_id(messages: list[dict[str, Any]]) -> str | None:
    """Derive a session ID from the first user message (SHA-256 prefix).

    Used for cache key grouping, composition checkpoints, and agent/tool
    session continuity.

    Skips boilerplate user messages whose content is identical across
    sessions for the same workspace — notably Codex CLI's
    ``<environment_context>`` block, which wraps cwd/shell/date and would
    otherwise collapse every Codex conversation in the same directory into
    a single session.
    """
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        text = (content if isinstance(content, str) else str(content)).strip()
        if not text:
            continue
        if text.startswith("<environment_context>") and text.endswith(
            "</environment_context>"
        ):
            continue
        return hashlib.sha256(text.encode()).hexdigest()[:8]
    return None


@dataclass
class _SessionRecord:
    session_id: str
    msg_hashes: tuple[str, ...]
    last_seen_ts: float


class RecentSessions:
    """In-memory registry for derive_session_id_v2.

    Stores recent sessions by msg_hashes prefix. Evicts via TTL on every
    insert; if still over capacity, evicts least-recently-touched entries.
    """

    def __init__(
        self,
        capacity: int = 5_000,
        ttl_seconds: float = 6 * 3600.0,
    ) -> None:
        self._capacity = capacity
        self._ttl = ttl_seconds
        # Ordered by access time (oldest first), keyed by session_id.
        self._records: OrderedDict[str, _SessionRecord] = OrderedDict()
        # Reverse map: upstream response.id → session_id
        self._by_response_id: dict[str, str] = {}

    def find_by_response_id(self, response_id: str) -> str | None:
        return self._by_response_id.get(response_id)

    def record_response_id(self, session_id: str, response_id: str) -> None:
        self._by_response_id[response_id] = session_id

    def evict_expired(self, now: float) -> None:
        """Eagerly evict stale records. Call before lookups to ensure TTL is respected."""
        self._evict_expired(now)

    def find_prefix_match(self, msg_hashes: tuple[str, ...]) -> _SessionRecord | None:
        """Return the longest existing record whose msg_hashes is a (non-strict)
        prefix of the incoming msg_hashes — equal-length matches included so
        repeated single-shot prompts collapse into the same session, mirroring
        v1's content-equality semantics."""
        best: _SessionRecord | None = None
        n = len(msg_hashes)
        for rec in self._records.values():
            m = len(rec.msg_hashes)
            if m > n:
                continue
            if msg_hashes[:m] == rec.msg_hashes:
                if best is None or m > len(best.msg_hashes):
                    best = rec
        return best

    def upsert(self, record: _SessionRecord) -> None:
        # Move-to-end on touch (LRU).
        if record.session_id in self._records:
            self._records.move_to_end(record.session_id)
        self._records[record.session_id] = record
        self._evict_expired(record.last_seen_ts)
        self._evict_over_capacity()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        stale_ids = [
            sid for sid, rec in self._records.items() if rec.last_seen_ts < cutoff
        ]
        for sid in stale_ids:
            self._drop(sid)

    def _evict_over_capacity(self) -> None:
        while len(self._records) > self._capacity:
            sid, _ = self._records.popitem(last=False)
            self._drop_response_ids_for(sid)

    def _drop(self, session_id: str) -> None:
        self._records.pop(session_id, None)
        self._drop_response_ids_for(session_id)

    def _drop_response_ids_for(self, session_id: str) -> None:
        stale = [r for r, s in self._by_response_id.items() if s == session_id]
        for r in stale:
            self._by_response_id.pop(r, None)


def derive_session_id_v2(
    *,
    msg_hashes: list[str],
    first_user_v2: str,
    system_hash: str,
    metadata_uid: str,
    prev_response: str,
    registry: RecentSessions,
    now: float,
) -> str:
    """Robust session id derivation.

    Priority:
      1. previous_response_id chain hit (OpenAI Responses)
      2. msg_hashes strict-prefix match in registry
      3. fresh id seeded by (first_user_v2, system_hash, metadata_uid, now)

    `now` is included in the fresh-id seed so two parallel conversations
    starting with identical first messages still get distinct ids.
    """
    hashes = tuple(msg_hashes)

    # Eagerly evict stale records before any lookup so TTL is respected.
    registry.evict_expired(now)

    # 1. Response-chain rescue.
    if prev_response:
        chained = registry.find_by_response_id(prev_response)
        if chained is not None and chained in registry._records:
            rec = registry._records[chained]
            rec.msg_hashes = hashes
            rec.last_seen_ts = now
            registry.upsert(rec)
            return chained

    # 2. Prefix match.
    match = registry.find_prefix_match(hashes)
    if match is not None:
        match.msg_hashes = hashes
        match.last_seen_ts = now
        registry.upsert(match)
        return match.session_id

    # 3. Fresh id.
    seed = f"{first_user_v2}|{system_hash}|{metadata_uid}".encode("utf-8")
    new_sid = hashlib.sha256(seed + str(now).encode("utf-8")).hexdigest()[:8]
    registry.upsert(
        _SessionRecord(session_id=new_sid, msg_hashes=hashes, last_seen_ts=now)
    )
    return new_sid
