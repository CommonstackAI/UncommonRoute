# Conversation Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist enough request/response data to assemble full agent conversations (every user msg, assistant reply, tool call, tool result), and add a robust `session_id` v2 derivation in shadow mode — both backed by a single JSONL store with a hot/cold field split.

**Architecture:** Replace the JSON full-rewrite trace store with a daily-rotated JSONL family (`~/.uncommon-route/traces/YYYY-MM-DD.jsonl`). Hot fields (routing metadata + v2 scalars) stay in memory; cold fields (request_messages, response_text, response_tool_calls) live on disk only and are read on demand by the new `/v1/sessions/{id}/conversation` endpoint. `derive_session_id_v2` runs alongside the legacy `derive_session_id` using a prefix-trie matcher with TTL/LRU eviction.

**Tech Stack:** Python 3.11+, `pytest`, `starlette` (existing). No new third-party dependencies.

**Spec:** `docs/specs/2026-04-27-conversation-persistence-design.md`

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `uncommon_route/normalize.py` | **new** | Pure helpers: `normalize_message_text`, `normalize_messages_to_hashes`, `hash16`. |
| `uncommon_route/session.py` | modify | Add `derive_session_id_v2` + `_RecentSessions` registry. Keep `derive_session_id` byte-identical. |
| `uncommon_route/content_capture.py` | **new** | Per-transport non-streaming and streaming content extractors with 64 KB cap. |
| `uncommon_route/traces.py` | modify | New JSONL storage; hot/cold split; feedback events; legacy migration; new schema fields. |
| `uncommon_route/proxy.py` | modify | Wire v2 inputs into trace creation; call content capture; add `/v1/sessions/{session_id}/conversation` route. |
| `uncommon_route/cli.py` | modify | Add `traces purge` subcommand. |
| `tests/test_normalize.py` | **new** | Unit tests for normalization helpers. |
| `tests/test_session_v2.py` | **new** | Unit tests for `derive_session_id_v2` and `_RecentSessions`. |
| `tests/test_content_capture.py` | **new** | Per-transport extractor tests with canned fixtures. |
| `tests/test_traces_jsonl.py` | **new** | Storage layer round-trip, hot/cold split, feedback events. |
| `tests/test_traces_migration.py` | **new** | Legacy `traces.json` → daily JSONL migration. |
| `tests/test_conversation_endpoint.py` | **new** | End-to-end assembly endpoint tests. |
| `tests/test_session.py` | extend | Confirm `derive_session_id` remains byte-identical. |

---

## Task 1: Normalization Helpers

**Files:**
- Create: `uncommon_route/normalize.py`
- Test: `tests/test_normalize.py`

These pure helpers turn arbitrary message content (string, list-of-blocks with text/tool_use/tool_result/image) into a stable text representation, then hash it. Used by §5 (msg_hashes) and §6 (`derive_session_id_v2`) of the spec.

- [ ] **Step 1.1: Write failing tests**

```python
# tests/test_normalize.py
"""Tests for message normalization helpers."""

from __future__ import annotations

import json

from uncommon_route.normalize import (
    hash16,
    normalize_message_text,
    normalize_messages_to_hashes,
)


class TestNormalizeMessageText:
    def test_string_content(self) -> None:
        msg = {"role": "user", "content": "  hello   world  "}
        assert normalize_message_text(msg) == "hello world"

    def test_text_blocks(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
        }
        assert normalize_message_text(msg) == "first second"

    def test_tool_use_block(self) -> None:
        msg = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Read", "input": {"path": "/tmp/x"}}
            ],
        }
        out = normalize_message_text(msg)
        assert "[tool_use:Read(" in out
        # arguments are canonical JSON (sorted keys, no whitespace)
        assert '{"path":"/tmp/x"}' in out

    def test_tool_result_block(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "ok"}
            ],
        }
        assert normalize_message_text(msg) == "ok"

    def test_tool_result_with_blocks(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "abc",
                    "content": [{"type": "text", "text": "nested"}],
                }
            ],
        }
        assert normalize_message_text(msg) == "nested"

    def test_image_block_placeholder(self) -> None:
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "see"},
                {"type": "image", "source": {"data": "base64..."}},
            ],
        }
        assert normalize_message_text(msg) == "see [image]"

    def test_whitespace_collapse(self) -> None:
        msg = {"role": "user", "content": "a\n\nb\t  c\r\nd"}
        assert normalize_message_text(msg) == "a b c d"

    def test_empty_content(self) -> None:
        assert normalize_message_text({"role": "user", "content": ""}) == ""
        assert normalize_message_text({"role": "user"}) == ""


class TestHash16:
    def test_returns_16_hex_chars(self) -> None:
        h = hash16("hello")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        assert hash16("x") == hash16("x")

    def test_different_inputs(self) -> None:
        assert hash16("a") != hash16("b")


class TestNormalizeMessagesToHashes:
    def test_one_hash_per_message(self) -> None:
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
        ]
        hashes = normalize_messages_to_hashes(messages)
        assert len(hashes) == 2
        assert all(len(h) == 16 for h in hashes)

    def test_same_messages_same_hashes(self) -> None:
        messages = [{"role": "user", "content": "x"}]
        assert normalize_messages_to_hashes(messages) == normalize_messages_to_hashes(messages)

    def test_role_independent_only_content_matters(self) -> None:
        # Same content text → same hash regardless of role label.
        # (Role is captured by message position in the list, not in the hash.)
        a = normalize_messages_to_hashes([{"role": "user", "content": "x"}])
        b = normalize_messages_to_hashes([{"role": "assistant", "content": "x"}])
        assert a == b
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
pytest tests/test_normalize.py -v
```

Expected: `ModuleNotFoundError: No module named 'uncommon_route.normalize'`.

- [ ] **Step 1.3: Implement `uncommon_route/normalize.py`**

```python
"""Message normalization for stable hashing.

Used to derive content-block-aware fingerprints of conversation prefixes,
independent of multimodal payload bytes (images), tool argument key
ordering, and whitespace variation.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_WHITESPACE_RUN = re.compile(r"\s+")


def hash16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _flatten_block(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    btype = block.get("type", "")
    if btype == "text":
        return str(block.get("text", ""))
    if btype == "tool_use":
        name = block.get("name", "")
        args = block.get("input", block.get("arguments", {}))
        canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return f"[tool_use:{name}({canonical})]"
    if btype == "tool_result":
        inner = block.get("content", "")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, list):
            return " ".join(_flatten_block(b) for b in inner)
        return str(inner)
    if btype in ("image", "input_image"):
        return "[image]"
    return ""


def normalize_message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(p for p in (_flatten_block(b) for b in content) if p)
    else:
        text = str(content) if content else ""
    text = _WHITESPACE_RUN.sub(" ", text).strip()
    return text


def normalize_messages_to_hashes(messages: list[dict[str, Any]]) -> list[str]:
    return [hash16(normalize_message_text(m)) for m in messages]
```

- [ ] **Step 1.4: Run tests to verify they pass**

```
pytest tests/test_normalize.py -v
```

Expected: all green.

- [ ] **Step 1.5: Commit**

```
git add uncommon_route/normalize.py tests/test_normalize.py
git commit -m "Add message normalization helpers for stable hashing"
```

---

## Task 2: `derive_session_id_v2` with Prefix-Trie Registry

**Files:**
- Modify: `uncommon_route/session.py` (append, do not touch existing `derive_session_id`)
- Test: `tests/test_session_v2.py`
- Modify: `tests/test_session.py` (add byte-identical regression check)

Implements §6 of the spec: prefix-trie + LRU + TTL + `previous_response_id` rescue.

- [ ] **Step 2.1: Write failing tests**

```python
# tests/test_session_v2.py
"""Tests for derive_session_id_v2 — prefix-trie session matching."""

from __future__ import annotations

from uncommon_route.session import RecentSessions, derive_session_id_v2


class TestDeriveSessionIdV2:
    def test_fresh_conversation_creates_new_id(self) -> None:
        registry = RecentSessions()
        sid = derive_session_id_v2(
            msg_hashes=["aaaaaaaaaaaaaaaa"],
            first_user_v2="aaaaaaaaaaaaaaaa",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        assert isinstance(sid, str)
        assert len(sid) == 8

    def test_strict_prefix_extension_reuses_id(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["a", "b", "c", "d"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_001.0,
        )
        assert sid1 == sid2

    def test_non_prefix_creates_new_id(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        sid2 = derive_session_id_v2(
            msg_hashes=["x", "y"],  # different first hash → not a prefix
            first_user_v2="x",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_001.0,
        )
        assert sid1 != sid2

    def test_compact_simulates_prefix_break(self) -> None:
        """When /compact rewrites history, the new prefix doesn't match."""
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a", "b", "c"],
            first_user_v2="a",
            system_hash="s",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        # Compact: history rewritten to a single summary message.
        sid2 = derive_session_id_v2(
            msg_hashes=["summary"],
            first_user_v2="summary",
            system_hash="s",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_010.0,
        )
        assert sid1 != sid2

    def test_previous_response_id_rescues_session(self) -> None:
        registry = RecentSessions()
        sid1 = derive_session_id_v2(
            msg_hashes=["a"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=1_000.0,
        )
        registry.record_response_id(sid1, "resp_xyz")

        sid2 = derive_session_id_v2(
            msg_hashes=["different"],  # would otherwise create a new session
            first_user_v2="different",
            system_hash="",
            metadata_uid="",
            prev_response="resp_xyz",  # but the chain claims it's the same one
            registry=registry,
            now=1_005.0,
        )
        assert sid1 == sid2

    def test_ttl_eviction(self) -> None:
        registry = RecentSessions(ttl_seconds=60.0)
        sid1 = derive_session_id_v2(
            msg_hashes=["a"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=0.0,
        )
        # 120 s later: extension that was a prefix should NOT match (sid1 evicted)
        sid2 = derive_session_id_v2(
            msg_hashes=["a", "b"],
            first_user_v2="a",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=120.0,
        )
        assert sid1 != sid2

    def test_capacity_lru_eviction(self) -> None:
        registry = RecentSessions(capacity=3, ttl_seconds=10_000.0)
        # Fill capacity.
        sids = []
        for i in range(3):
            sids.append(
                derive_session_id_v2(
                    msg_hashes=[f"msg_{i}"],
                    first_user_v2=f"msg_{i}",
                    system_hash="",
                    metadata_uid="",
                    prev_response="",
                    registry=registry,
                    now=float(i),
                )
            )
        # Touch sid 1 and 2 to make 0 the LRU.
        derive_session_id_v2(
            msg_hashes=["msg_1", "extra"],
            first_user_v2="msg_1",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=10.0,
        )
        derive_session_id_v2(
            msg_hashes=["msg_2", "extra"],
            first_user_v2="msg_2",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=11.0,
        )
        # Insert a 4th: pushes capacity over → evict LRU (sid 0).
        derive_session_id_v2(
            msg_hashes=["msg_NEW"],
            first_user_v2="msg_NEW",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=12.0,
        )
        # Now sid 0's prefix should miss (it was evicted).
        sid_zero_again = derive_session_id_v2(
            msg_hashes=["msg_0", "extra"],
            first_user_v2="msg_0",
            system_hash="",
            metadata_uid="",
            prev_response="",
            registry=registry,
            now=13.0,
        )
        assert sid_zero_again != sids[0]


class TestRecentSessionsRecordResponseId:
    def test_lookup_after_record(self) -> None:
        registry = RecentSessions()
        registry.record_response_id("sess_abc", "resp_001")
        assert registry.find_by_response_id("resp_001") == "sess_abc"

    def test_lookup_miss_returns_none(self) -> None:
        registry = RecentSessions()
        assert registry.find_by_response_id("resp_unknown") is None
```

```python
# tests/test_session.py — append a regression test confirming v1 unchanged.

class TestDeriveSessionIdRegression:
    """Lock the legacy derivation byte-identical so anyone using session_id as a
    cache key sees no behavior change."""

    def test_specific_known_input_known_output(self) -> None:
        # This value was captured from the existing implementation.
        # If you change derive_session_id, this test must be updated only with
        # a deliberate decision documented in the changelog.
        messages = [{"role": "user", "content": "hello"}]
        assert derive_session_id(messages) == "2cf24dba"
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
pytest tests/test_session_v2.py tests/test_session.py::TestDeriveSessionIdRegression -v
```

Expected: import error for `derive_session_id_v2`/`RecentSessions`; the regression test should pass already if our hypothesis about the existing impl is right.

- [ ] **Step 2.3: Implement `derive_session_id_v2` and `RecentSessions` in `uncommon_route/session.py`**

Append to existing file (do **not** modify `derive_session_id`):

```python
# Append to uncommon_route/session.py

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field


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

    def find_prefix_match(self, msg_hashes: tuple[str, ...]) -> _SessionRecord | None:
        """Return the longest existing record whose msg_hashes is a strict
        prefix of the incoming msg_hashes."""
        best: _SessionRecord | None = None
        n = len(msg_hashes)
        for rec in self._records.values():
            m = len(rec.msg_hashes)
            if m >= n:
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
      3. fresh id seeded by (first_user_v2, system_hash, metadata_uid)
    """
    hashes = tuple(msg_hashes)

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
```

- [ ] **Step 2.4: Run tests to verify they pass**

```
pytest tests/test_session_v2.py tests/test_session.py -v
```

Expected: all green.

- [ ] **Step 2.5: Commit**

```
git add uncommon_route/session.py tests/test_session_v2.py tests/test_session.py
git commit -m "Add derive_session_id_v2 with prefix-trie matcher (shadow)"
```

---

## Task 3: JSONL Storage Layer

**Files:**
- Modify: `uncommon_route/traces.py:119-158` (replace `TraceStorage` ABC, `FileTraceStorage`, `InMemoryTraceStorage`)
- Test: `tests/test_traces_jsonl.py`

Implements §8 of spec. New storage interface: `append`, `load_recent_days`, `load_for_request`, `purge`. Keeps `InMemoryTraceStorage` for tests.

- [ ] **Step 3.1: Write failing tests**

```python
# tests/test_traces_jsonl.py
"""Tests for the JSONL-based trace storage layer."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from uncommon_route.traces import (
    FileTraceStorage,
    InMemoryTraceStorage,
)


def _row(ts: float, request_id: str, **extra: object) -> dict:
    base = {
        "request_id": request_id,
        "timestamp": ts,
        "model": "x",
        "session_id": "s1",
    }
    base.update(extra)
    return base


class TestFileTraceStorageAppend:
    def test_append_creates_dated_jsonl(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        # 2026-04-27 00:30:00 UTC
        storage.append(_row(1777593000.0, "req1"))
        f = tmp_path / "2026-04-27.jsonl"
        assert f.exists()
        lines = f.read_text().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["request_id"] == "req1"

    def test_append_groups_by_utc_date(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1777593000.0, "a"))  # 2026-04-27
        storage.append(_row(1777679400.0, "b"))  # 2026-04-28
        assert (tmp_path / "2026-04-27.jsonl").exists()
        assert (tmp_path / "2026-04-28.jsonl").exists()

    def test_append_preserves_order_within_day(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1.0, "a"))
        storage.append(_row(2.0, "b"))
        storage.append(_row(3.0, "c"))
        f = tmp_path / "1970-01-01.jsonl"
        ids = [json.loads(line)["request_id"] for line in f.read_text().splitlines()]
        assert ids == ["a", "b", "c"]


class TestFileTraceStorageLoadRecentDays:
    def test_returns_only_requested_days(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        # 3 days of records.
        d0 = 1777593000.0  # 2026-04-27
        d1 = d0 + 86400
        d2 = d1 + 86400
        storage.append(_row(d0, "old"))
        storage.append(_row(d1, "mid"))
        storage.append(_row(d2, "new"))
        # Asking for last 2 days as of d2 returns mid + new.
        rows = list(storage.load_recent_days(days=2, now=d2 + 100))
        ids = [r["request_id"] for r in rows]
        assert ids == ["mid", "new"]

    def test_handles_missing_directory(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "absent")
        rows = list(storage.load_recent_days(days=2, now=time.time()))
        assert rows == []


class TestFileTraceStorageLoadForRequest:
    def test_finds_specific_request(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        ts = 1777593000.0
        storage.append(_row(ts, "wanted", request_messages=[{"role": "user", "content": "hi"}]))
        storage.append(_row(ts + 1, "other"))
        row = storage.load_for_request("wanted", ts)
        assert row is not None
        assert row["request_messages"][0]["content"] == "hi"

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1777593000.0, "a"))
        assert storage.load_for_request("nope", 1777593000.0) is None


class TestFileTraceStoragePurge:
    def test_purge_removes_all_files(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path)
        storage.append(_row(1.0, "a"))
        storage.append(_row(86401.0, "b"))
        storage.purge()
        # Directory may still exist but should be empty of jsonl.
        leftovers = list(tmp_path.glob("*.jsonl"))
        assert leftovers == []


class TestInMemoryTraceStorage:
    def test_append_and_load(self) -> None:
        storage = InMemoryTraceStorage()
        storage.append(_row(1.0, "a"))
        storage.append(_row(2.0, "b"))
        rows = list(storage.load_recent_days(days=99, now=time.time()))
        ids = [r["request_id"] for r in rows]
        assert ids == ["a", "b"]

    def test_load_for_request_in_memory(self) -> None:
        storage = InMemoryTraceStorage()
        storage.append(_row(1.0, "a", request_messages=[{"role": "user", "content": "x"}]))
        row = storage.load_for_request("a", 1.0)
        assert row is not None
        assert row["request_messages"][0]["content"] == "x"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```
pytest tests/test_traces_jsonl.py -v
```

Expected: import errors / signature errors — `FileTraceStorage` does not yet take `base_dir` kw and lacks `append` / `load_recent_days` / `load_for_request` / `purge`.

- [ ] **Step 3.3: Replace storage classes in `uncommon_route/traces.py`**

Replace lines 119-158 (the existing `TraceStorage` ABC, `FileTraceStorage`, `InMemoryTraceStorage`) with:

```python
class TraceStorage(ABC):
    @abstractmethod
    def append(self, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    def purge(self) -> None: ...


def _date_str(ts: float) -> str:
    """UTC date for grouping into daily files."""
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


class FileTraceStorage(TraceStorage):
    """Append-only JSONL store rotated daily."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or (data_dir() / "traces")

    def append(self, record: dict[str, Any]) -> None:
        try:
            self._base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            day = _date_str(float(record.get("timestamp", time.time())))
            path = self._base_dir / f"{day}.jsonl"
            line = json.dumps(record, default=str, ensure_ascii=False)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                path.chmod(0o600)
            except Exception:
                pass
        except Exception:
            pass

    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]:
        if not self._base_dir.exists():
            return []
        # Build list of acceptable date strings.
        import datetime as _dt
        end = _dt.datetime.utcfromtimestamp(now).date()
        accepted = {
            (end - _dt.timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(max(1, days))
        }
        files = sorted(
            f for f in self._base_dir.glob("*.jsonl") if f.stem in accepted
        )
        out: list[dict[str, Any]] = []
        for f in files:
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
            except Exception:
                continue
        return out

    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None:
        if not self._base_dir.exists():
            return None
        # Try the exact day, then ±1 day to absorb clock skew at midnight.
        import datetime as _dt
        center = _dt.datetime.utcfromtimestamp(timestamp).date()
        candidates = [
            center,
            center - _dt.timedelta(days=1),
            center + _dt.timedelta(days=1),
        ]
        for day in candidates:
            f = self._base_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
            if not f.exists():
                continue
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("request_id") == request_id:
                        return row
            except Exception:
                continue
        return None

    def purge(self) -> None:
        if not self._base_dir.exists():
            return
        for f in self._base_dir.glob("*.jsonl"):
            try:
                f.unlink()
            except Exception:
                pass


class InMemoryTraceStorage(TraceStorage):
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, record: dict[str, Any]) -> None:
        self._rows.append(dict(record))

    def load_recent_days(
        self, days: int, *, now: float
    ) -> list[dict[str, Any]]:
        cutoff = now - days * 86400.0
        return [r for r in self._rows if float(r.get("timestamp", 0.0)) >= cutoff]

    def load_for_request(
        self, request_id: str, timestamp: float
    ) -> dict[str, Any] | None:
        for r in self._rows:
            if r.get("request_id") == request_id:
                return dict(r)
        return None

    def purge(self) -> None:
        self._rows.clear()
```

Note: this leaves `TraceStore` temporarily broken because it still calls the old `load`/`save`. We fix that in Task 5. **Tests for TraceStore in `tests/test_proxy.py` etc. may fail at the end of this commit — that's expected and we will fix in Task 5.** Run only the new tests in this task:

- [ ] **Step 3.4: Run new tests to verify they pass**

```
pytest tests/test_traces_jsonl.py -v
```

Expected: all green.

- [ ] **Step 3.5: Commit**

```
git add uncommon_route/traces.py tests/test_traces_jsonl.py
git commit -m "Replace JSON full-rewrite trace storage with daily JSONL append"
```

---

## Task 4: Legacy `traces.json` Migration

**Files:**
- Modify: `uncommon_route/traces.py` (add `migrate_legacy_json` function)
- Test: `tests/test_traces_migration.py`

Implements §11 of spec.

- [ ] **Step 4.1: Write failing tests**

```python
# tests/test_traces_migration.py
"""Tests for the one-time legacy traces.json migration."""

from __future__ import annotations

import json
from pathlib import Path

from uncommon_route.traces import migrate_legacy_json


class TestMigrateLegacyJson:
    def test_splits_into_daily_jsonl(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        new_dir = tmp_path / "traces"
        legacy.write_text(
            json.dumps(
                [
                    {"request_id": "a", "timestamp": 1777593000.0},  # 2026-04-27
                    {"request_id": "b", "timestamp": 1777593600.0},  # same day
                    {"request_id": "c", "timestamp": 1777679400.0},  # 2026-04-28
                ]
            )
        )

        migrated = migrate_legacy_json(legacy, new_dir)

        assert migrated is True
        assert (new_dir / "2026-04-27.jsonl").exists()
        assert (new_dir / "2026-04-28.jsonl").exists()
        a_day = (new_dir / "2026-04-27.jsonl").read_text().splitlines()
        assert len(a_day) == 2
        # Legacy file is renamed, not deleted.
        assert not legacy.exists()
        assert (legacy.parent / "traces.json.bak").exists()

    def test_idempotent_when_traces_dir_already_exists(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        new_dir = tmp_path / "traces"
        new_dir.mkdir()
        (new_dir / "2026-04-27.jsonl").write_text("")
        legacy.write_text("[]")

        migrated = migrate_legacy_json(legacy, new_dir)

        # Already migrated → no-op.
        assert migrated is False
        assert legacy.exists()  # untouched

    def test_no_legacy_file_is_no_op(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "traces"
        migrated = migrate_legacy_json(tmp_path / "traces.json", new_dir)
        assert migrated is False
        assert not new_dir.exists() or list(new_dir.glob("*")) == []

    def test_corrupt_legacy_file_does_not_crash(self, tmp_path: Path) -> None:
        legacy = tmp_path / "traces.json"
        legacy.write_text("not valid json")
        new_dir = tmp_path / "traces"
        migrated = migrate_legacy_json(legacy, new_dir)
        assert migrated is False
        # Original left in place for human inspection.
        assert legacy.exists()
```

- [ ] **Step 4.2: Run tests to verify they fail**

```
pytest tests/test_traces_migration.py -v
```

Expected: `ImportError: cannot import name 'migrate_legacy_json'`.

- [ ] **Step 4.3: Add migration function to `uncommon_route/traces.py`**

Append (above `TraceStore`):

```python
def migrate_legacy_json(legacy_path: Path, target_dir: Path) -> bool:
    """One-time migration of the old single-file traces.json into daily JSONL.

    Returns True if migration ran, False if it was skipped (target dir already
    populated, no legacy file, or legacy file is not parseable).
    """
    if target_dir.exists() and any(target_dir.glob("*.jsonl")):
        return False
    if not legacy_path.exists():
        return False
    try:
        records = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(records, list):
        return False

    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = float(rec.get("timestamp", 0.0))
        day = _date_str(ts)
        path = target_dir / f"{day}.jsonl"
        try:
            line = json.dumps(rec, default=str, ensure_ascii=False)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            continue

    try:
        legacy_path.rename(legacy_path.with_suffix(".json.bak"))
    except Exception:
        pass
    return True
```

- [ ] **Step 4.4: Run tests to verify they pass**

```
pytest tests/test_traces_migration.py -v
```

Expected: all green.

- [ ] **Step 4.5: Commit**

```
git add uncommon_route/traces.py tests/test_traces_migration.py
git commit -m "Add one-time migration from legacy traces.json to daily JSONL"
```

---

## Task 5: Update `RequestTrace` Schema and `TraceStore`

**Files:**
- Modify: `uncommon_route/traces.py` (`RequestTrace` dataclass, `_trace_payload`, `TraceStore`, `_load_legacy`)
- Tests: extend `tests/test_traces_jsonl.py` and `tests/test_traces.py` (existing if present)

This is the largest change. We add new fields to `RequestTrace`, update the serializer, switch `TraceStore` to use JSONL storage with hot/cold split, and call the migration on init.

- [ ] **Step 5.1: Write failing tests**

Append to `tests/test_traces_jsonl.py`:

```python
# Append to tests/test_traces_jsonl.py
from uncommon_route.traces import RequestTrace, TraceStore, COLD_FIELDS


class TestRequestTraceNewFields:
    def test_v2_fields_have_defaults(self) -> None:
        t = RequestTrace(timestamp=1.0, request_id="r", model="m", status_code=200)
        assert t.messages_count == 0
        assert t.msg_hashes is None
        assert t.first_user_hash_v2 == ""
        assert t.system_hash == ""
        assert t.metadata_user_id == ""
        assert t.previous_response_id == ""
        assert t.user_agent == ""
        assert t.session_id_v2 == ""

    def test_content_fields_have_defaults(self) -> None:
        t = RequestTrace(timestamp=1.0, request_id="r", model="m", status_code=200)
        assert t.request_messages is None
        assert t.request_system == ""
        assert t.request_tools_count == 0
        assert t.response_text == ""
        assert t.response_tool_calls is None
        assert t.response_finish_reason == ""
        assert t.content_truncated is False


class TestTraceStoreHotColdSplit:
    def test_cold_fields_excluded_from_in_memory(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("UNCOMMON_ROUTE_DATA_DIR", str(tmp_path))
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store = TraceStore(storage=storage, hot_days=2, now_fn=lambda: 1000.0)
        store.record(
            RequestTrace(
                timestamp=999.0,
                request_id="r1",
                model="m",
                status_code=200,
                request_messages=[{"role": "user", "content": "hi"}],
                response_text="hello back",
            )
        )
        # In-memory record has the trace but not the cold fields.
        recent = store.recent(limit=10)
        assert len(recent) == 1
        for f in COLD_FIELDS:
            assert recent[0].get(f) in (None, "", [], False), (
                f"cold field {f} leaked into hot path: {recent[0][f]!r}"
            )

    def test_load_content_returns_cold_fields(self, tmp_path: Path) -> None:
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store = TraceStore(storage=storage, hot_days=2, now_fn=lambda: 1000.0)
        store.record(
            RequestTrace(
                timestamp=999.0,
                request_id="r1",
                model="m",
                status_code=200,
                request_messages=[{"role": "user", "content": "hi"}],
                response_text="hello back",
            )
        )
        cold = store.load_content("r1")
        assert cold is not None
        assert cold["request_messages"] == [{"role": "user", "content": "hi"}]
        assert cold["response_text"] == "hello back"


class TestTraceStoreFeedbackEvents:
    def test_feedback_event_overlays_trace_on_load(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("UNCOMMON_ROUTE_DATA_DIR", str(tmp_path))
        storage = FileTraceStorage(base_dir=tmp_path / "traces")
        store1 = TraceStore(storage=storage, hot_days=2, now_fn=lambda: 1000.0)
        store1.record(
            RequestTrace(
                timestamp=999.0, request_id="r1", model="m", status_code=200
            )
        )
        ok = store1.record_feedback(
            "r1",
            signal="rerun_simple",
            ok=True,
            action="downgrade",
            from_tier="MEDIUM",
            to_tier="SIMPLE",
            reason="user-corrected",
        )
        assert ok is True
        # Re-load fresh.
        store2 = TraceStore(storage=storage, hot_days=2, now_fn=lambda: 1000.0)
        recent = store2.recent(limit=10)
        assert len(recent) == 1
        assert recent[0]["feedback_signal"] == "rerun_simple"
        assert recent[0]["feedback_action"] == "downgrade"
```

- [ ] **Step 5.2: Run tests to verify they fail**

```
pytest tests/test_traces_jsonl.py -v
```

Expected: failures on the new test classes (fields don't exist; `TraceStore` doesn't accept `hot_days`).

- [ ] **Step 5.3: Add new fields to `RequestTrace`**

In `uncommon_route/traces.py`, after the existing fields (around line 116), add:

```python
    # --- Track A: session_id v2 inputs ---
    messages_count: int = 0
    msg_hashes: list[str] | None = None
    first_user_hash_v2: str = ""
    system_hash: str = ""
    metadata_user_id: str = ""
    previous_response_id: str = ""
    user_agent: str = ""
    session_id_v2: str = ""

    # --- Track B: cold (content) fields ---
    request_messages: list[dict[str, Any]] | None = None
    request_system: str = ""
    request_tools_count: int = 0
    response_text: str = ""
    response_tool_calls: list[dict[str, Any]] | None = None
    response_finish_reason: str = ""
    content_truncated: bool = False
```

- [ ] **Step 5.4: Define `COLD_FIELDS` and update `_trace_payload`**

Add module-level constant after the dataclass:

```python
COLD_FIELDS: tuple[str, ...] = (
    "request_messages",
    "request_system",
    "response_text",
    "response_tool_calls",
    "response_finish_reason",
)
```

In `_trace_payload`, add the new keys to the returned dict (preserve existing keys; just append):

```python
        # Append into the existing _trace_payload dict literal:
        "messages_count": trace.messages_count,
        "msg_hashes": list(trace.msg_hashes or []),
        "first_user_hash_v2": trace.first_user_hash_v2,
        "system_hash": trace.system_hash,
        "metadata_user_id": trace.metadata_user_id,
        "previous_response_id": trace.previous_response_id,
        "user_agent": trace.user_agent,
        "session_id_v2": trace.session_id_v2,
        "request_messages": list(trace.request_messages or []) if trace.request_messages else None,
        "request_system": trace.request_system,
        "request_tools_count": trace.request_tools_count,
        "response_text": trace.response_text,
        "response_tool_calls": list(trace.response_tool_calls or []) if trace.response_tool_calls else None,
        "response_finish_reason": trace.response_finish_reason,
        "content_truncated": trace.content_truncated,
```

- [ ] **Step 5.5: Rewrite `TraceStore` to use JSONL + hot/cold + feedback events**

Replace `class TraceStore` (currently around lines 161-215+) with:

```python
class TraceStore:
    def __init__(
        self,
        storage: TraceStorage | None = None,
        now_fn: Any = None,
        *,
        hot_days: int | None = None,
    ) -> None:
        if storage is None:
            # Default: file storage with one-time legacy migration.
            target_dir = data_dir() / "traces"
            legacy = data_dir() / "traces.json"
            try:
                migrate_legacy_json(legacy, target_dir)
            except Exception:
                pass
            storage = FileTraceStorage(base_dir=target_dir)
        self._storage = storage
        self._now = now_fn or time.time
        if hot_days is None:
            try:
                hot_days = int(os.environ.get("UNCOMMON_ROUTE_TRACE_HOT_DAYS", "2"))
            except Exception:
                hot_days = 2
        self._hot_days = max(1, hot_days)
        self._records: list[RequestTrace] = []
        self._load()

    @property
    def count(self) -> int:
        return len(self._records)

    def record(self, trace: RequestTrace) -> None:
        trace.tier = _normalize_tier_label(trace.tier)
        trace.decision_tier = _normalize_tier_label(trace.decision_tier) if trace.decision_tier else ""
        trace.served_quality = _normalize_served_quality(trace.served_quality)
        trace.served_quality_target = _normalize_served_quality(trace.served_quality_target)
        trace.served_quality_floor = _normalize_served_quality(trace.served_quality_floor)
        trace.capability_lane = str(trace.capability_lane or "").strip().lower()
        trace.feedback_from_tier = _normalize_tier_label(trace.feedback_from_tier) if trace.feedback_from_tier else ""
        trace.feedback_to_tier = _normalize_tier_label(trace.feedback_to_tier) if trace.feedback_to_tier else ""

        payload = _trace_payload(trace)
        # Persist full row (incl. cold fields) to disk.
        self._storage.append({"type": "trace", **payload})
        # Hot copy: drop cold fields before keeping in memory.
        hot = RequestTrace(**{k: v for k, v in trace.__dict__.items()})
        for f in COLD_FIELDS:
            setattr(hot, f, _empty_for(f))
        self._records.append(hot)
        self._cleanup()

    def record_feedback(
        self,
        request_id: str,
        *,
        signal: str,
        ok: bool,
        action: str,
        from_tier: str = "",
        to_tier: str = "",
        reason: str = "",
    ) -> bool:
        # Apply to in-memory state.
        applied = False
        for record in reversed(self._records):
            if record.request_id != request_id:
                continue
            record.feedback_signal = signal
            record.feedback_ok = ok
            record.feedback_action = action
            record.feedback_from_tier = _normalize_tier_label(from_tier) if from_tier else ""
            record.feedback_to_tier = _normalize_tier_label(to_tier) if to_tier else ""
            record.feedback_reason = reason
            record.feedback_submitted_at = self._now()
            applied = True
            break
        # Persist as event regardless (so a future load reconstructs state).
        try:
            self._storage.append({
                "type": "feedback",
                "request_id": request_id,
                "timestamp": self._now(),
                "feedback_signal": signal,
                "feedback_ok": bool(ok),
                "feedback_action": action,
                "feedback_from_tier": _normalize_tier_label(from_tier) if from_tier else "",
                "feedback_to_tier": _normalize_tier_label(to_tier) if to_tier else "",
                "feedback_reason": reason,
                "feedback_submitted_at": self._now(),
            })
        except Exception:
            pass
        return applied

    def reset(self) -> None:
        self._records.clear()
        try:
            self._storage.purge()
        except Exception:
            pass

    def history(self, limit: int | None = None) -> list[RequestTrace]:
        records = list(reversed(self._records))
        if limit is not None and limit > 0:
            records = records[:limit]
        return records

    def recent(self, limit: int = 50, *, errors_only: bool = False) -> list[dict[str, Any]]:
        records = list(reversed(self._records))
        if errors_only:
            records = [r for r in records if r.status_code >= 400]
        return [_trace_payload(r) for r in records[:limit]]

    def find(self, request_id: str) -> dict[str, Any] | None:
        for r in reversed(self._records):
            if r.request_id == request_id:
                return _trace_payload(r)
        return None

    def latest_for_session(
        self, session_id: str, *, before_timestamp: float | None = None
    ) -> RequestTrace | None:
        for r in reversed(self._records):
            if r.session_id != session_id:
                continue
            if before_timestamp is not None and r.timestamp >= before_timestamp:
                continue
            return r
        return None

    def summary(self) -> dict[str, Any]:
        # Keep the same summary shape the rest of the system relies on.
        # (Existing logic — copy from prior implementation if non-trivial.)
        # If your prior summary was complex, preserve it as-is here.
        from collections import Counter
        if not self._records:
            return {"total_requests": 0}
        rs = list(self._records)
        total = len(rs)
        errors = sum(1 for r in rs if r.status_code >= 400)
        virtual = sum(1 for r in rs if r.is_virtual)
        passthrough = total - virtual
        by_endpoint = dict(Counter(r.endpoint for r in rs))
        by_mode = dict(Counter(r.mode for r in rs))
        by_method = dict(Counter(r.method for r in rs))
        by_status = dict(Counter(str(r.status_code) for r in rs))
        by_error_code = dict(Counter(r.error_code for r in rs if r.error_code))
        by_quality = dict(Counter(r.served_quality for r in rs if r.served_quality))
        by_lane = dict(Counter(r.capability_lane for r in rs if r.capability_lane))
        return {
            "total_requests": total,
            "error_count": errors,
            "virtual_requests": virtual,
            "passthrough_requests": passthrough,
            "by_endpoint": by_endpoint,
            "by_mode": by_mode,
            "by_method": by_method,
            "by_status": by_status,
            "by_error_code": by_error_code,
            "by_served_quality": by_quality,
            "by_capability_lane": by_lane,
        }

    def load_content(self, request_id: str) -> dict[str, Any] | None:
        for r in self._records:
            if r.request_id == request_id:
                cold = self._storage.load_for_request(request_id, r.timestamp)
                if cold is None:
                    return None
                return {f: cold.get(f) for f in COLD_FIELDS}
        return None

    def _cleanup(self) -> None:
        # Cap in-memory record count by hot window count, not absolute MAX.
        cutoff_ts = self._now() - (self._hot_days * 86400.0)
        self._records = [r for r in self._records if r.timestamp >= cutoff_ts]

    def _load(self) -> None:
        rows = self._storage.load_recent_days(self._hot_days, now=self._now())
        # Build by request_id, applying feedback events in order.
        traces: dict[str, RequestTrace] = {}
        order: list[str] = []
        for row in rows:
            rtype = row.get("type", "trace")
            if rtype == "trace":
                rid = str(row.get("request_id", ""))
                if not rid:
                    continue
                # Drop cold fields when constructing the hot record.
                hot_row = {k: v for k, v in row.items() if k != "type" and k not in COLD_FIELDS}
                # Backfill cold field defaults for the dataclass.
                for f in COLD_FIELDS:
                    hot_row.setdefault(f, _empty_for(f))
                trace = _row_to_trace(hot_row)
                if rid not in traces:
                    order.append(rid)
                traces[rid] = trace
            elif rtype == "feedback":
                rid = str(row.get("request_id", ""))
                t = traces.get(rid)
                if t is None:
                    continue
                t.feedback_signal = str(row.get("feedback_signal", ""))
                t.feedback_ok = bool(row.get("feedback_ok", False))
                t.feedback_action = str(row.get("feedback_action", ""))
                t.feedback_from_tier = str(row.get("feedback_from_tier", ""))
                t.feedback_to_tier = str(row.get("feedback_to_tier", ""))
                t.feedback_reason = str(row.get("feedback_reason", ""))
                t.feedback_submitted_at = float(row.get("feedback_submitted_at", 0.0) or 0.0)
        self._records = [traces[rid] for rid in order if rid in traces]


def _empty_for(field_name: str) -> Any:
    if field_name in ("request_messages", "response_tool_calls"):
        return None
    if field_name == "request_tools_count":
        return 0
    if field_name == "content_truncated":
        return False
    return ""


def _row_to_trace(row: dict[str, Any]) -> RequestTrace:
    # Tolerate missing fields by relying on dataclass defaults.
    field_names = {f.name for f in RequestTrace.__dataclass_fields__.values()}
    kwargs = {k: v for k, v in row.items() if k in field_names}
    return RequestTrace(**kwargs)
```

Add this import at the top of `traces.py` if not present:

```python
import os
```

Note: the prior `_load_legacy`-style code should be **removed**. Run a grep to confirm:

```
grep -n "_load_legacy\|self\._save\|def _save" uncommon_route/traces.py
```

Remove any lingering `_save` / `_load_legacy` references.

- [ ] **Step 5.6: Run tests to verify they pass**

```
pytest tests/test_traces_jsonl.py tests/test_traces_migration.py tests/test_session.py -v
```

Expected: all green.

- [ ] **Step 5.7: Run full test suite to surface unrelated breakage**

```
pytest -x -q
```

Expected: any tests that called `_save` / `load` directly on `FileTraceStorage` may break — fix them inline. The proxy tests in `tests/test_proxy.py` may need their `TraceStore(...)` calls adjusted. Update them to pass `InMemoryTraceStorage()` and `hot_days=99`.

- [ ] **Step 5.8: Commit**

```
git add uncommon_route/traces.py tests/test_traces_jsonl.py
git commit -m "Schema additions, hot/cold split, feedback events in TraceStore"
```

---

## Task 6: Content Capture Parsers

**Files:**
- Create: `uncommon_route/content_capture.py`
- Test: `tests/test_content_capture.py`

Implements §7 of spec — non-streaming and streaming extractors for the three transports, plus 64 KB row truncation.

- [ ] **Step 6.1: Write failing tests**

```python
# tests/test_content_capture.py
"""Tests for per-transport content capture (extraction + SSE assembly)."""

from __future__ import annotations

import json

from uncommon_route.content_capture import (
    extract_assistant_blocks_anthropic,
    extract_assistant_blocks_openai_chat,
    extract_assistant_blocks_openai_responses,
    parse_stream_assistant_content,
    truncate_content_payload,
)


class TestExtractAnthropic:
    def test_text_only(self) -> None:
        body = json.dumps(
            {
                "content": [{"type": "text", "text": "hello"}],
                "stop_reason": "end_turn",
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_anthropic(body)
        assert text == "hello"
        assert calls == []
        assert finish == "end_turn"

    def test_tool_use(self) -> None:
        body = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "calling"},
                    {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "/a"}},
                ],
                "stop_reason": "tool_use",
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_anthropic(body)
        assert text == "calling"
        assert len(calls) == 1
        assert calls[0]["name"] == "Read"
        assert calls[0]["input"] == {"path": "/a"}
        assert finish == "tool_use"


class TestExtractOpenAIChat:
    def test_text(self) -> None:
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_openai_chat(body)
        assert text == "hi"
        assert calls == []
        assert finish == "stop"

    def test_tool_calls(self) -> None:
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc_1",
                                    "function": {
                                        "name": "Read",
                                        "arguments": '{"path":"/a"}',
                                    },
                                    "type": "function",
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ).encode("utf-8")
        text, calls, finish = extract_assistant_blocks_openai_chat(body)
        assert text == ""
        assert len(calls) == 1
        assert calls[0]["name"] == "Read"
        assert finish == "tool_calls"


class TestStreamAssemblyAnthropic:
    def test_basic_text_stream(self) -> None:
        chunks = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"m1"}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hel"}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        text, calls, finish = parse_stream_assistant_content(
            chunks, "anthropic-messages"
        )
        assert text == "hello"
        assert calls == []
        assert finish == "end_turn"


class TestStreamAssemblyOpenAIChat:
    def test_text_stream(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        text, calls, finish = parse_stream_assistant_content(chunks, "openai-chat")
        assert text == "hello"
        assert calls == []
        assert finish == "stop"


class TestTruncate:
    def test_under_cap_no_change(self) -> None:
        row = {
            "request_id": "r",
            "request_messages": [{"role": "user", "content": "hi"}],
            "response_text": "ok",
        }
        out, truncated = truncate_content_payload(row, cap_bytes=10_000)
        assert truncated is False
        assert out["response_text"] == "ok"

    def test_over_cap_drops_response_text_first(self) -> None:
        big = "x" * 100_000
        row = {
            "request_id": "r",
            "request_messages": [{"role": "user", "content": "small"}],
            "response_text": big,
            "response_tool_calls": None,
        }
        out, truncated = truncate_content_payload(row, cap_bytes=10_000)
        assert truncated is True
        assert out["response_text"] == ""
```

- [ ] **Step 6.2: Run tests to verify they fail**

```
pytest tests/test_content_capture.py -v
```

Expected: `ModuleNotFoundError: No module named 'uncommon_route.content_capture'`.

- [ ] **Step 6.3: Implement `uncommon_route/content_capture.py`**

```python
"""Per-transport content extraction.

Three transports are supported: anthropic-messages, openai-chat, openai-responses.
Each has both a non-streaming extractor (parse a JSON body) and a streaming
SSE reducer (assemble chunks).
"""

from __future__ import annotations

import json
from typing import Any


# --- Non-streaming extractors -------------------------------------------------

def extract_assistant_blocks_anthropic(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    blocks = data.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(str(b.get("text", "")))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""),
                "name": b.get("name", ""),
                "input": b.get("input", {}),
            })
    finish = str(data.get("stop_reason", ""))
    return "".join(text_parts), tool_calls, finish


def extract_assistant_blocks_openai_chat(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    choices = data.get("choices") or []
    if not choices:
        return "", [], ""
    msg = choices[0].get("message") or {}
    text_field = msg.get("content", "")
    if isinstance(text_field, list):
        text = "\n".join(
            p.get("text", "") for p in text_field
            if isinstance(p, dict) and p.get("type") == "text"
        )
    else:
        text = str(text_field) if text_field is not None else ""
    raw_calls = msg.get("tool_calls") or []
    calls: list[dict[str, Any]] = []
    for tc in raw_calls:
        fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
        try:
            args = json.loads(fn.get("arguments", "{}"))
        except Exception:
            args = fn.get("arguments", {})
        calls.append({
            "id": tc.get("id", "") if isinstance(tc, dict) else "",
            "name": fn.get("name", ""),
            "input": args,
        })
    finish = str(choices[0].get("finish_reason", ""))
    return text, calls, finish


def extract_assistant_blocks_openai_responses(
    content: bytes,
) -> tuple[str, list[dict[str, Any]], str]:
    try:
        data = json.loads(content)
    except Exception:
        return "", [], ""
    output = data.get("output") or []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                    text_parts.append(str(c.get("text", "")))
        elif item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments", "{}"))
            except Exception:
                args = item.get("arguments", {})
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "name": item.get("name", ""),
                "input": args,
            })
    finish = str(data.get("status", ""))
    return "".join(text_parts), tool_calls, finish


# --- Streaming reducers -------------------------------------------------------

def _iter_sse_events(chunks: list[bytes]) -> list[tuple[str | None, str]]:
    """Yield (event_name, data_json_str) for each SSE event in chunks."""
    buf = b"".join(chunks).decode("utf-8", errors="replace")
    out: list[tuple[str | None, str]] = []
    for raw in buf.split("\n\n"):
        ev = None
        data_lines: list[str] = []
        for line in raw.splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):].strip()
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if data_lines:
            out.append((ev, "\n".join(data_lines)))
    return out


def _stream_anthropic(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    text_parts: list[str] = []
    blocks: dict[int, dict[str, Any]] = {}
    finish = ""
    for ev, data in _iter_sse_events(chunks):
        if not data or data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        ptype = payload.get("type", ev or "")
        if ptype == "content_block_start":
            idx = payload.get("index", 0)
            blocks[idx] = dict(payload.get("content_block") or {})
            blocks[idx].setdefault("text", "")
            blocks[idx].setdefault("input", {})
        elif ptype == "content_block_delta":
            idx = payload.get("index", 0)
            delta = payload.get("delta") or {}
            block = blocks.setdefault(idx, {"type": "text", "text": ""})
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + str(delta.get("text", ""))
            elif delta.get("type") == "input_json_delta":
                block.setdefault("_partial", "")
                block["_partial"] += str(delta.get("partial_json", ""))
        elif ptype == "content_block_stop":
            idx = payload.get("index", 0)
            block = blocks.get(idx)
            if block and "_partial" in block:
                try:
                    block["input"] = json.loads(block["_partial"])
                except Exception:
                    block["input"] = block.get("_partial")
                block.pop("_partial", None)
        elif ptype == "message_delta":
            sr = (payload.get("delta") or {}).get("stop_reason")
            if sr:
                finish = str(sr)
    text = ""
    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(blocks):
        b = blocks[idx]
        if b.get("type") == "text":
            text += str(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""),
                "name": b.get("name", ""),
                "input": b.get("input", {}),
            })
    return text, tool_calls, finish


def _stream_openai_chat(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    text_parts: list[str] = []
    finish = ""
    tool_calls_by_index: dict[int, dict[str, Any]] = {}
    for _ev, data in _iter_sse_events(chunks):
        if data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        choices = payload.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        delta = ch.get("delta") or {}
        if "content" in delta and delta["content"]:
            text_parts.append(str(delta["content"]))
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_calls_by_index.setdefault(
                idx, {"id": "", "name": "", "_args": ""}
            )
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["_args"] += fn["arguments"]
        if ch.get("finish_reason"):
            finish = str(ch["finish_reason"])
    tool_calls: list[dict[str, Any]] = []
    for idx in sorted(tool_calls_by_index):
        slot = tool_calls_by_index[idx]
        try:
            args = json.loads(slot["_args"]) if slot["_args"] else {}
        except Exception:
            args = slot["_args"]
        tool_calls.append({"id": slot["id"], "name": slot["name"], "input": args})
    return "".join(text_parts), tool_calls, finish


def _stream_openai_responses(chunks: list[bytes]) -> tuple[str, list[dict[str, Any]], str]:
    # Responses API streams `response.output_text.delta` / `response.output_item.added` events.
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish = ""
    for _ev, data in _iter_sse_events(chunks):
        if data.strip() == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        ptype = payload.get("type", "")
        if ptype == "response.output_text.delta":
            text_parts.append(str(payload.get("delta", "")))
        elif ptype == "response.output_item.added":
            item = payload.get("item") or {}
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments", "{}"))
                except Exception:
                    args = item.get("arguments", {})
                tool_calls.append({
                    "id": item.get("call_id", item.get("id", "")),
                    "name": item.get("name", ""),
                    "input": args,
                })
        elif ptype == "response.completed":
            resp = payload.get("response") or {}
            finish = str(resp.get("status", "") or finish)
    return "".join(text_parts), tool_calls, finish


def parse_stream_assistant_content(
    chunks: list[bytes], transport: str
) -> tuple[str, list[dict[str, Any]], str]:
    if transport == "anthropic-messages":
        return _stream_anthropic(chunks)
    if transport == "openai-chat":
        return _stream_openai_chat(chunks)
    if transport == "openai-responses":
        return _stream_openai_responses(chunks)
    return "", [], ""


# --- Truncation ---------------------------------------------------------------

def truncate_content_payload(
    row: dict[str, Any], *, cap_bytes: int = 64 * 1024
) -> tuple[dict[str, Any], bool]:
    """Reduce the row in place until JSON-encoded size <= cap_bytes.

    Drop order: response_text → response_tool_calls → request_messages (oldest first).
    """
    out = dict(row)

    def _size() -> int:
        return len(json.dumps(out, default=str, ensure_ascii=False).encode("utf-8"))

    if _size() <= cap_bytes:
        return out, False

    if out.get("response_text"):
        out["response_text"] = ""
    if _size() <= cap_bytes:
        return out, True

    if out.get("response_tool_calls"):
        out["response_tool_calls"] = []
    if _size() <= cap_bytes:
        return out, True

    msgs = list(out.get("request_messages") or [])
    while msgs and _size() > cap_bytes:
        msgs.pop(0)
        out["request_messages"] = msgs
    return out, True
```

- [ ] **Step 6.4: Run tests to verify they pass**

```
pytest tests/test_content_capture.py -v
```

Expected: all green.

- [ ] **Step 6.5: Commit**

```
git add uncommon_route/content_capture.py tests/test_content_capture.py
git commit -m "Add per-transport content extractors and SSE reducers"
```

---

## Task 7: Wire `derive_session_id_v2` Into Proxy Request Flow

**Files:**
- Modify: `uncommon_route/proxy.py:797-804` (`_resolve_session_id` and a new helper near it)

The legacy `_resolve_session_id` returns a `session_id` (v1). We extract a richer set of fields for v2 alongside it and stash them so the trace creation site can write them.

- [ ] **Step 7.1: Add `_extract_session_v2_inputs` and the global `RecentSessions` registry near `_resolve_session_id`**

Add right after `_resolve_session_id` (`proxy.py` ~line 805):

```python
from uncommon_route.normalize import (
    hash16,
    normalize_message_text,
    normalize_messages_to_hashes,
)
from uncommon_route.session import RecentSessions, derive_session_id_v2

# Process-wide registry. Tests can override via the env vars referenced below.
_SESSION_V2_REGISTRY = RecentSessions(
    capacity=int(os.environ.get("UNCOMMON_ROUTE_SESSION_TABLE_SIZE", "5000")),
    ttl_seconds=float(os.environ.get("UNCOMMON_ROUTE_SESSION_TTL_S", "21600")),
)


def _extract_session_v2_inputs(
    request: Request, body: dict
) -> dict[str, Any]:
    """Compute the inputs and outputs for derive_session_id_v2 on this request."""
    messages = body.get("messages") or []
    msg_hashes = normalize_messages_to_hashes(messages)
    first_user_v2 = ""
    for m in messages:
        if m.get("role") == "user":
            first_user_v2 = hash16(normalize_message_text(m))
            break
    system_text = ""
    sys_field = body.get("system")
    if isinstance(sys_field, str):
        system_text = sys_field
    elif isinstance(sys_field, list):
        system_text = " ".join(
            (b.get("text") or "") for b in sys_field if isinstance(b, dict) and b.get("type") == "text"
        )
    elif messages:
        for m in messages:
            if m.get("role") == "system":
                system_text = normalize_message_text(m)
                break
    system_h = hash16(system_text) if system_text else ""

    metadata_uid = ""
    md = body.get("metadata") or {}
    if isinstance(md, dict):
        metadata_uid = str(md.get("user_id", "") or "")

    prev_response = str(body.get("previous_response_id", "") or "")
    headers = {k.lower(): v for k, v in request.headers.items()}
    user_agent = headers.get("user-agent", "")

    sid_v2 = derive_session_id_v2(
        msg_hashes=msg_hashes,
        first_user_v2=first_user_v2,
        system_hash=system_h,
        metadata_uid=metadata_uid,
        prev_response=prev_response,
        registry=_SESSION_V2_REGISTRY,
        now=time.time(),
    )

    return {
        "messages_count": len(messages),
        "msg_hashes": msg_hashes,
        "first_user_hash_v2": first_user_v2,
        "system_hash": system_h,
        "metadata_user_id": metadata_uid,
        "previous_response_id": prev_response,
        "user_agent": user_agent,
        "session_id_v2": sid_v2,
    }
```

- [ ] **Step 7.2: Pass the v2 inputs through to RequestTrace at each construction site**

There are 3 call sites: `proxy.py:3333`, `proxy.py:3528`, `proxy.py:4160`. At each, we already know `body` and `request` are in scope. Right above each `_traces.record(RequestTrace(...))` call, fetch v2 inputs once if not already done in the request flow. Add to each `RequestTrace(...)` constructor call:

```python
            _traces.record(RequestTrace(
                # ... existing kwargs ...
                **_extract_session_v2_inputs(request, body),
            ))
```

(Or if there's a top-of-function place where `session_id = _resolve_session_id(request, body)` is computed, compute `v2_inputs = _extract_session_v2_inputs(request, body)` right next to it once, and reuse.)

- [ ] **Step 7.3: Add an integration test confirming v2 fields appear in trace**

Append to `tests/test_traces_jsonl.py`:

```python
import asyncio
from starlette.testclient import TestClient


def test_v2_fields_populated_via_proxy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UNCOMMON_ROUTE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNCOMMON_ROUTE_TRACE_HOT_DAYS", "99")
    # Use the lightweight proxy harness from test_stats.py (import the same
    # create_app + fakes there). For brevity, prefer a unit-style test where
    # we directly invoke _extract_session_v2_inputs with a fake Request.
    from starlette.requests import Request

    from uncommon_route.proxy import _extract_session_v2_inputs

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"user-agent", b"claude-code/1.0")],
        "path": "/v1/messages",
    }
    req = Request(scope=scope)
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {"user_id": "u_x"},
    }
    out = _extract_session_v2_inputs(req, body)
    assert out["messages_count"] == 1
    assert len(out["msg_hashes"]) == 1
    assert len(out["msg_hashes"][0]) == 16
    assert len(out["first_user_hash_v2"]) == 16
    assert out["metadata_user_id"] == "u_x"
    assert out["user_agent"] == "claude-code/1.0"
    assert len(out["session_id_v2"]) == 8
```

- [ ] **Step 7.4: Run tests**

```
pytest tests/test_traces_jsonl.py -v
```

Expected: all green.

- [ ] **Step 7.5: Run full suite**

```
pytest -x -q
```

Expected: green. If `tests/test_proxy.py` tests fail due to RequestTrace's new kwargs being optional with defaults, no fixes needed. If a test asserts on the exact set of trace fields, update it to ignore the new ones.

- [ ] **Step 7.6: Commit**

```
git add uncommon_route/proxy.py tests/test_traces_jsonl.py
git commit -m "Wire derive_session_id_v2 inputs into proxy trace flow (shadow)"
```

---

## Task 8: Wire Content Capture Into Proxy

**Files:**
- Modify: `uncommon_route/proxy.py` (capture hooks at non-streaming and streaming finalization sites)

- [ ] **Step 8.1: Add a `_capture_content` helper near `_extract_assistant_text`**

After the existing `_extract_assistant_text` function (`proxy.py:569`), add:

```python
from uncommon_route.content_capture import (
    extract_assistant_blocks_anthropic,
    extract_assistant_blocks_openai_chat,
    extract_assistant_blocks_openai_responses,
    parse_stream_assistant_content,
    truncate_content_payload,
)


def _capture_enabled() -> bool:
    return os.environ.get("UNCOMMON_ROUTE_CAPTURE_CONTENT", "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _capture_non_streaming(
    body: dict, response_content: bytes, transport: str
) -> dict[str, Any]:
    """Return the dict of cold fields to merge into the trace at record time."""
    if not _capture_enabled():
        return {}
    if transport == "anthropic-messages":
        text, calls, finish = extract_assistant_blocks_anthropic(response_content)
    elif transport == "openai-chat":
        text, calls, finish = extract_assistant_blocks_openai_chat(response_content)
    elif transport == "openai-responses":
        text, calls, finish = extract_assistant_blocks_openai_responses(
            response_content
        )
    else:
        return {}
    return _build_capture_dict(body, text, calls, finish)


def _capture_streaming(
    body: dict, stream_chunks: list[bytes], transport: str
) -> dict[str, Any]:
    if not _capture_enabled():
        return {}
    text, calls, finish = parse_stream_assistant_content(stream_chunks, transport)
    return _build_capture_dict(body, text, calls, finish)


def _build_capture_dict(
    body: dict, text: str, calls: list[dict[str, Any]], finish: str
) -> dict[str, Any]:
    sys_field = body.get("system", "")
    if isinstance(sys_field, list):
        system_text = " ".join(
            (b.get("text") or "")
            for b in sys_field
            if isinstance(b, dict) and b.get("type") == "text"
        )
    else:
        system_text = str(sys_field) if sys_field else ""
    raw_tools = body.get("tools") or body.get("customTools") or []
    raw = {
        "request_messages": list(body.get("messages") or []),
        "request_system": system_text,
        "request_tools_count": len(raw_tools) if isinstance(raw_tools, list) else 0,
        "response_text": text,
        "response_tool_calls": calls,
        "response_finish_reason": finish,
        "content_truncated": False,
    }
    truncated, was_trunc = truncate_content_payload(raw)
    truncated["content_truncated"] = was_trunc
    return truncated
```

- [ ] **Step 8.2: Hook into the trace creation sites**

At each of the 3 `_traces.record(RequestTrace(...))` call sites (`proxy.py:3333, 3528, 4160`), pass the captured cold fields. Two of these sites are post-non-streaming (with `resp.content` available) and one is post-streaming (with `stream_chunks` available).

Locate the relevant variables in scope at each site (`resp` for non-streaming, `stream_chunks` for streaming) and inject:

```python
        capture = _capture_non_streaming(body, resp.content, transport)  # or _capture_streaming
        _traces.record(RequestTrace(
            # ... existing kwargs ...
            **_extract_session_v2_inputs(request, body),
            **capture,
        ))
```

If a site doesn't have content available (error paths), pass an empty dict — content fields stay at their defaults.

- [ ] **Step 8.3: Add a unit test that capture-off leaves cold fields empty**

Append to `tests/test_traces_jsonl.py`:

```python
def test_capture_off_writes_empty_cold_fields(monkeypatch) -> None:
    from uncommon_route.proxy import _capture_non_streaming

    monkeypatch.delenv("UNCOMMON_ROUTE_CAPTURE_CONTENT", raising=False)
    out = _capture_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        b'{"content":[{"type":"text","text":"hi back"}]}',
        "anthropic-messages",
    )
    assert out == {}


def test_capture_on_populates_cold_fields(monkeypatch) -> None:
    from uncommon_route.proxy import _capture_non_streaming

    monkeypatch.setenv("UNCOMMON_ROUTE_CAPTURE_CONTENT", "1")
    out = _capture_non_streaming(
        {"messages": [{"role": "user", "content": "hi"}]},
        b'{"content":[{"type":"text","text":"hi back"}],"stop_reason":"end_turn"}',
        "anthropic-messages",
    )
    assert out["response_text"] == "hi back"
    assert out["response_finish_reason"] == "end_turn"
    assert out["request_messages"] == [{"role": "user", "content": "hi"}]
```

- [ ] **Step 8.4: Run tests**

```
pytest tests/test_traces_jsonl.py tests/test_content_capture.py -v
```

Expected: all green.

- [ ] **Step 8.5: Commit**

```
git add uncommon_route/proxy.py tests/test_traces_jsonl.py
git commit -m "Wire opt-in content capture into proxy trace flow"
```

---

## Task 9: Conversation Assembly Endpoint

**Files:**
- Modify: `uncommon_route/proxy.py` (new `handle_session_conversation`, register route)
- Test: `tests/test_conversation_endpoint.py`

Implements §10 of spec.

- [ ] **Step 9.1: Write failing tests**

```python
# tests/test_conversation_endpoint.py
"""End-to-end tests for the /v1/sessions/{id}/conversation endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest

from uncommon_route.traces import (
    FileTraceStorage,
    InMemoryTraceStorage,
    RequestTrace,
    TraceStore,
)


def _make_store(tmp_path: Path) -> TraceStore:
    storage = FileTraceStorage(base_dir=tmp_path / "traces")
    return TraceStore(storage=storage, hot_days=99, now_fn=lambda: 1_000_000.0)


def _record_turn(
    store: TraceStore,
    *,
    ts: float,
    request_id: str,
    session_id: str,
    request_messages: list[dict],
    response_text: str,
    response_tool_calls: list[dict] | None = None,
    msg_hashes: list[str] | None = None,
    decision_tier: str = "MEDIUM",
) -> None:
    store.record(
        RequestTrace(
            timestamp=ts,
            request_id=request_id,
            model="x",
            status_code=200,
            session_id=session_id,
            decision_tier=decision_tier,
            request_messages=request_messages,
            response_text=response_text,
            response_tool_calls=response_tool_calls,
            msg_hashes=msg_hashes or [],
        )
    )


def test_assemble_two_turn_conversation(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    _record_turn(
        store,
        ts=1.0,
        request_id="t1",
        session_id="s1",
        request_messages=[{"role": "user", "content": "hello"}],
        response_text="hi there",
        msg_hashes=["h_user"],
    )
    _record_turn(
        store,
        ts=2.0,
        request_id="t2",
        session_id="s1",
        request_messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "how are you"},
        ],
        response_text="great thanks",
        msg_hashes=["h_user", "h_asst1", "h_user2"],
    )

    out = _assemble_conversation(store, "s1")
    assert out is not None
    assert out["session_id"] == "s1"
    assert out["turn_count"] == 2
    assert out["content_available"] is True
    assert out["compact_breaks"] == []
    roles = [m["role"] for m in out["messages"]]
    assert roles[0] == "user"
    assert "assistant" in roles
    # Last message is the second turn's response.
    assert out["messages"][-1]["role"] == "assistant"
    assert out["messages"][-1]["text"] == "great thanks"


def test_compact_break_detected(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    _record_turn(
        store,
        ts=1.0,
        request_id="t1",
        session_id="s2",
        request_messages=[{"role": "user", "content": "old1"}],
        response_text="reply1",
        msg_hashes=["a", "b"],
    )
    _record_turn(
        store,
        ts=2.0,
        request_id="t2",
        session_id="s2",
        request_messages=[{"role": "user", "content": "compacted summary"}],
        response_text="reply2",
        msg_hashes=["c"],  # NOT a strict extension of [a,b]
    )
    out = _assemble_conversation(store, "s2")
    assert out["compact_breaks"] == [1]


def test_no_session_returns_none(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    assert _assemble_conversation(store, "nonexistent") is None


def test_content_unavailable_when_capture_was_off(tmp_path: Path) -> None:
    from uncommon_route.proxy import _assemble_conversation

    store = _make_store(tmp_path)
    store.record(
        RequestTrace(
            timestamp=1.0,
            request_id="t1",
            model="x",
            status_code=200,
            session_id="s3",
            request_messages=None,
            response_text="",
            msg_hashes=[],
        )
    )
    out = _assemble_conversation(store, "s3")
    assert out["content_available"] is False
    assert out["turn_count"] == 1
    # Still surfaces decisions.
    assert "messages" in out
```

- [ ] **Step 9.2: Run tests to verify they fail**

```
pytest tests/test_conversation_endpoint.py -v
```

Expected: `ImportError: cannot import name '_assemble_conversation'`.

- [ ] **Step 9.3: Implement `_assemble_conversation` and register the route**

Add to `uncommon_route/proxy.py` (near other `handle_*` functions, e.g. after `handle_trace_detail` ~line 3006):

```python
def _assemble_conversation(
    traces: "TraceStore", session_id: str
) -> dict[str, Any] | None:
    # 1. Pull all hot rows for this session.
    matching = [r for r in traces._records if r.session_id == session_id]
    if not matching:
        return None
    matching.sort(key=lambda r: r.timestamp)

    # 2. Pull cold fields for each turn.
    cold_by_id: dict[str, dict[str, Any]] = {}
    for t in matching:
        cold = traces.load_content(t.request_id)
        if cold is not None:
            cold_by_id[t.request_id] = cold

    has_any_content = any(
        (c.get("request_messages") or c.get("response_text"))
        for c in cold_by_id.values()
    )

    # 3. Compact-break detection from msg_hashes.
    breaks: list[int] = []
    for k in range(1, len(matching)):
        prev = list(matching[k - 1].msg_hashes or [])
        curr = list(matching[k].msg_hashes or [])
        if not prev or not curr:
            continue
        if curr[: len(prev)] != prev:
            breaks.append(k)

    if not has_any_content:
        # Surface turn-level decisions only (no message bodies).
        decisions = [
            {
                "role": "assistant",
                "text": "",
                "tool_calls": [],
                "ts": t.timestamp,
                "request_id": t.request_id,
                "decision": _trace_decision_card(t),
            }
            for t in matching
        ]
        return {
            "session_id": session_id,
            "turn_count": len(matching),
            "content_available": False,
            "compact_breaks": breaks,
            "messages": decisions,
        }

    # 4. Build backbone from the LAST turn's request_messages, then append
    # the last turn's response.
    last_turn = matching[-1]
    last_cold = cold_by_id.get(last_turn.request_id, {})
    backbone = list(last_cold.get("request_messages") or [])
    final_response = {
        "_synthetic_response": True,
        "text": last_cold.get("response_text", ""),
        "tool_calls": list(last_cold.get("response_tool_calls") or []),
        "request_id": last_turn.request_id,
        "ts": last_turn.timestamp,
    }

    # 5. Walk backbone, expand into chat messages with decisions.
    out_messages: list[dict[str, Any]] = []
    assistant_idx = 0
    for m in backbone:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            # Could carry tool_results in content blocks.
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        out_messages.append({
                            "role": "tool_result",
                            "tool_use_id": b.get("tool_use_id", ""),
                            "text": _flatten_tool_result(b.get("content", "")),
                            "from_request_id": last_turn.request_id,
                        })
                # If there's also raw text, surface it as a user msg.
                text = " ".join(
                    str(b.get("text", ""))
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if text:
                    out_messages.append({
                        "role": "user",
                        "text": text,
                        "ts": None,
                        "from_request_id": last_turn.request_id,
                    })
            else:
                out_messages.append({
                    "role": "user",
                    "text": str(content),
                    "ts": None,
                    "from_request_id": last_turn.request_id,
                })
        elif role == "assistant":
            # Pull text + tool_use blocks.
            text = ""
            calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        if b.get("type") == "text":
                            text += str(b.get("text", ""))
                        elif b.get("type") == "tool_use":
                            calls.append({
                                "id": b.get("id", ""),
                                "name": b.get("name", ""),
                                "input": b.get("input", {}),
                            })
            else:
                text = str(content)

            # Attach the routing decision for this assistant turn.
            decision_trace = (
                matching[assistant_idx]
                if assistant_idx < len(matching) - 1
                else matching[assistant_idx]
            )
            out_messages.append({
                "role": "assistant",
                "text": text,
                "tool_calls": calls,
                "ts": decision_trace.timestamp,
                "request_id": decision_trace.request_id,
                "decision": _trace_decision_card(decision_trace),
            })
            assistant_idx += 1
        elif role == "system":
            # Skip — system prompt isn't a conversation turn for UI purposes.
            continue
        elif role == "tool":
            out_messages.append({
                "role": "tool_result",
                "tool_use_id": m.get("tool_call_id", ""),
                "text": str(content),
                "from_request_id": last_turn.request_id,
            })

    # 6. Append final assistant response (last turn).
    if assistant_idx < len(matching):
        final = matching[-1]
        out_messages.append({
            "role": "assistant",
            "text": final_response["text"],
            "tool_calls": final_response["tool_calls"],
            "ts": final.timestamp,
            "request_id": final.request_id,
            "decision": _trace_decision_card(final),
        })

    return {
        "session_id": session_id,
        "turn_count": len(matching),
        "content_available": True,
        "compact_breaks": breaks,
        "messages": out_messages,
    }


def _flatten_tool_result(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(str(b.get("text", "")))
        return " ".join(parts)
    return str(content) if content else ""


def _trace_decision_card(trace: "RequestTrace") -> dict[str, Any]:
    """Subset of the trace useful for the UI as a decision card."""
    return {
        "model": trace.model,
        "decision_tier": trace.decision_tier or trace.tier,
        "served_quality": trace.served_quality,
        "capability_lane": trace.capability_lane,
        "raw_confidence": trace.raw_confidence,
        "latency_us": trace.latency_us,
        "estimated_cost": trace.estimated_cost,
        "route_reasoning": trace.route_reasoning,
        "feature_tags": list(trace.feature_tags or []),
        "constraint_tags": list(trace.constraint_tags or []),
        "hint_tags": list(trace.hint_tags or []),
        "transport": trace.transport,
        "transport_reason": trace.transport_reason,
        "attempts_payload": list(trace.attempts_payload or []),
        "fallback_reason": trace.fallback_reason,
    }
```

Add the route handler and registration:

```python
    async def handle_session_conversation(request: Request) -> JSONResponse:
        denied = _admin_auth_failure(request)
        if denied is not None:
            return denied
        session_id = str(request.path_params["session_id"]).strip()
        out = _assemble_conversation(_traces, session_id)
        if out is None:
            return JSONResponse(
                {"error": "Session not found", "session_id": session_id},
                status_code=404,
            )
        return JSONResponse(out)
```

In the routes list (`proxy.py:4902-`), add:

```python
        Route(
            "/v1/sessions/{session_id:str}/conversation",
            handle_session_conversation,
            methods=["GET"],
        ),
```

- [ ] **Step 9.4: Run tests to verify they pass**

```
pytest tests/test_conversation_endpoint.py -v
```

Expected: all green.

- [ ] **Step 9.5: Commit**

```
git add uncommon_route/proxy.py tests/test_conversation_endpoint.py
git commit -m "Add /v1/sessions/{id}/conversation assembly endpoint"
```

---

## Task 10: CLI `traces purge` Subcommand

**Files:**
- Modify: `uncommon_route/cli.py` (add subcommand)
- Test: extend `tests/test_traces_jsonl.py`

- [ ] **Step 10.1: Write failing test**

Append to `tests/test_traces_jsonl.py`:

```python
def test_cli_traces_purge_clears_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("UNCOMMON_ROUTE_DATA_DIR", str(tmp_path))
    from uncommon_route.cli import _cmd_traces_purge

    storage = FileTraceStorage(base_dir=tmp_path / "traces")
    storage.append({"request_id": "x", "timestamp": 1.0})
    assert list((tmp_path / "traces").glob("*.jsonl"))

    rc = _cmd_traces_purge()
    assert rc == 0
    assert list((tmp_path / "traces").glob("*.jsonl")) == []
```

- [ ] **Step 10.2: Run test to verify it fails**

```
pytest tests/test_traces_jsonl.py::test_cli_traces_purge_clears_directory -v
```

Expected: import error for `_cmd_traces_purge`.

- [ ] **Step 10.3: Implement subcommand**

In `uncommon_route/cli.py`, add (near other small command helpers; the existing `main()` switch must dispatch this):

```python
def _cmd_traces_purge() -> int:
    from uncommon_route.traces import FileTraceStorage
    from uncommon_route.paths import data_dir

    storage = FileTraceStorage(base_dir=data_dir() / "traces")
    storage.purge()
    print("traces purged")
    return 0
```

In `main()`, route the args `traces purge`:

```python
    if args[:2] == ["traces", "purge"]:
        return _cmd_traces_purge()
```

(Locate the existing dispatch pattern in `main()` and follow the established style — existing subcommands like `stats history`, `support bundle` follow the same pattern.)

Also update the help text in `_print_help()` to list the new command:

```
  traces purge                      Delete all stored trace JSONL files
```

- [ ] **Step 10.4: Run test**

```
pytest tests/test_traces_jsonl.py::test_cli_traces_purge_clears_directory -v
```

Expected: green.

- [ ] **Step 10.5: Commit**

```
git add uncommon_route/cli.py tests/test_traces_jsonl.py
git commit -m "Add 'traces purge' CLI subcommand"
```

---

## Task 11: Manual Smoke Test

**Files:** none (this is a manual verification step before merging)

- [ ] **Step 11.1: Start proxy with capture on**

```
UNCOMMON_ROUTE_CAPTURE_CONTENT=1 UNCOMMON_ROUTE_DATA_DIR=/tmp/ur-smoke uncommon-route serve --port 8403
```

- [ ] **Step 11.2: Send a multi-turn Claude Code request through it**

Use the existing Claude Code setup (or `curl` with a hand-crafted Anthropic Messages payload that includes tool_use / tool_result blocks).

- [ ] **Step 11.3: Inspect stored data**

```
ls -la /tmp/ur-smoke/traces/
head -1 /tmp/ur-smoke/traces/$(date -u +%Y-%m-%d).jsonl | python3 -m json.tool | head -40
```

Expected: row contains both routing metadata and `request_messages` / `response_text` fields populated.

- [ ] **Step 11.4: Hit the assembly endpoint**

```
curl -s "http://localhost:8403/v1/sessions/$(< first session id from the trace)/conversation" | python3 -m json.tool
```

Expected: ordered list of user / assistant / tool_result entries; `content_available: true`; decisions attached to assistant turns.

- [ ] **Step 11.5: Verify capture-off path**

Stop the proxy, restart **without** `UNCOMMON_ROUTE_CAPTURE_CONTENT`, send another request, hit the same endpoint for the new session: `content_available: false`, message bodies empty, decisions still surfaced.

- [ ] **Step 11.6: Verify migration**

Drop a small synthetic legacy `traces.json` into `~/.uncommon-route/`, restart the proxy, confirm the file becomes `traces.json.bak` and `traces/YYYY-MM-DD.jsonl` files appear with the expected content.

- [ ] **Step 11.7: Note results in PR description**

PR description should list which smoke checks passed.

---

## Self-Review Checklist

Run this before requesting review:

- [ ] All §2 in-scope items have a task: schema (T5), v2 algorithm (T2), JSONL storage (T3), opt-in switch (T8), assembly endpoint (T9), migration (T4), tests (every task).
- [ ] No placeholders (`TBD`, `add appropriate`, etc.) in any step.
- [ ] Function names consistent across tasks: `derive_session_id_v2`, `_extract_session_v2_inputs`, `_assemble_conversation`, `_capture_non_streaming`, `_capture_streaming`.
- [ ] `RequestTrace` field names in T5 match what T7/T8 read/write: `messages_count`, `msg_hashes`, `first_user_hash_v2`, `system_hash`, `metadata_user_id`, `previous_response_id`, `user_agent`, `session_id_v2`, `request_messages`, `request_system`, `request_tools_count`, `response_text`, `response_tool_calls`, `response_finish_reason`, `content_truncated`.
- [ ] `COLD_FIELDS` constant in T5 matches the fields T9 reads via `load_content`.
- [ ] No frontend changes (per spec §2 out-of-scope).
