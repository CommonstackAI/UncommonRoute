# Conversation Persistence — Design Spec

**Date:** 2026-04-27
**Branch:** `feat/conversation-persistence`
**Scope:** Backend only. Frontend follow-up is a separate PR.

## 1. Goal

Make the dashboard's "EXPLAIN NEW" page able to render a complete agent conversation — every user message, every assistant reply, every tool call, every tool result — with the routing decision attached to each assistant turn.

Today this is impossible because:

- `~/.uncommon-route/traces.json` stores only routing metadata. The most content-bearing field is `prompt_preview` (the **last** user message, truncated to 80 chars).
- The current `derive_session_id` (SHA-256 of the first user message) breaks under `/compact`, sliding-window truncation, multimodal content blocks, and identical first-message collisions across users.

Two independent concerns must be solved together because both touch the persistence layer:

- **Track A — `session_id` v2.** Make "one Claude Code / Codex conversation = one session" robust.
- **Track B — Content capture.** Persist enough request/response data to reconstruct the conversation.

## 2. Scope

**In scope (this branch, backend only):**

- New `RequestTrace` fields supporting v2 session derivation (small, structured).
- A `derive_session_id_v2` shadow implementation alongside the existing `derive_session_id`.
- Storage layer migration from JSON full-rewrite to daily JSONL append-only, with a Claude-Code-style hot/cold memory model.
- An opt-in switch that controls whether content fields are populated in each row.
- A new HTTP endpoint that returns a fully-assembled conversation.
- A one-time migration that splits the existing `traces.json` into the new daily JSONL files.
- Tests covering the v2 algorithm, the capture hook for all three transports, the storage migration, and the assembly endpoint.

**Out of scope:**

- Any frontend changes (`ExplainerNew.tsx` etc).
- Replacing `derive_session_id` as the default. v2 runs in shadow mode in this branch.
- SQLite or any other database engine. JSONL is sufficient.

## 3. Storage Layout

**Single store, single file family.** One row per turn, all fields (routing metadata, v2 fields, optional content) live on the same row.

| Path | Format | Write | Retention |
|---|---|---|---|
| `~/.uncommon-route/traces/YYYY-MM-DD.jsonl` | append-only JSONL, one row per turn | every request | 14 days, configurable, one-shot purge |

Why merge what was previously two stores:

- Content capture is gated by a switch on the **field level** (whether `request_messages` / `response_*` are populated), not on the **file level**. The privacy semantics are the same; the implementation is simpler.
- Switching `traces.json` from JSON full-rewrite to daily JSONL append-only is a worthwhile change in its own right — current `FileTraceStorage.save` rewrites the whole file every request (`traces.py:141-147`), which is wasteful and gets worse as the file grows.
- One write path, one read path, one retention policy, one CLI purge command.
- Conversation assembly becomes a single-store query (filter today + maybe yesterday's JSONL by `session_id`), not a join across two stores.

## 4. Memory Model — Hot / Cold Field Split

The reason "store everything in one place" doesn't blow up memory is that **content fields never enter memory**, mirroring how Claude Code holds only the active session in memory and reads other sessions from disk on demand.

### Hot fields (kept in memory)

All current `RequestTrace` fields plus the v2 additions in §5. These are small, structured, and queried on the request hot path (`latest_for_session`, `find`, `recent`).

### Cold fields (disk only)

Loaded only when `/v1/sessions/{id}/conversation` (or future endpoints) explicitly request them:

- `request_messages`
- `request_system`
- `response_text`
- `response_tool_calls`
- `response_finish_reason`

### Loader behavior

- **Startup:** `TraceStore._load` opens the most recent N days of JSONL (`UNCOMMON_ROUTE_TRACE_HOT_DAYS`, default 2), reads each row, and **drops cold fields before storing in `self._records`**. Older days are not loaded — they exist on disk for cold queries only.
- **Append:** every request writes a complete row (with or without content depending on the capture switch) to today's JSONL. The in-memory record is the row minus cold fields.
- **Cold read:** `TraceStore.load_content(request_id)` opens the JSONL for that request's date, scans for the matching row, returns the cold fields. Used by the assembly endpoint.

### Memory estimate

Average hot row ≈ 2–3 KB (dominated by `attempts_payload`, `candidate_scores_payload`, `routing_features_payload`). Two days × 5K req/day × 3 KB ≈ **30 MB**. Independent of whether content capture is on.

## 5. Track A — `RequestTrace` Schema Additions

Added to the dataclass in `uncommon_route/traces.py` and to `_trace_payload`. All small, structured, hot-field-eligible. Default-on.

```python
messages_count: int = 0
msg_hashes: list[str] | None = None      # SHA-256[:16] per normalized message position
first_user_hash_v2: str = ""             # normalized first user msg (multimodal-safe) hash
system_hash: str = ""                    # normalized system prompt hash
metadata_user_id: str = ""               # body.metadata.user_id
previous_response_id: str = ""           # OpenAI Responses API previous_response_id
user_agent: str = ""                     # request header
session_id_v2: str = ""                  # output of derive_session_id_v2 (shadow)
```

`session_id` (existing) keeps its semantics for now — cache keys and composition checkpoints continue to use it. `session_id_v2` is informational. After 1–2 days of shadow data we evaluate and decide whether to swap.

### Normalization rules

A "normalized" message text:

1. If `content` is a string, use it verbatim.
2. If `content` is a list of blocks (Anthropic / Responses style):
   - For `type == "text"`: append `text` field.
   - For `type == "tool_use"`: append `f"[tool_use:{name}({arguments_json_canonical})]"`.
   - For `type == "tool_result"`: append the inner content (recursively flattened).
   - Skip image/binary blocks; emit `[image]` placeholder.
3. Strip leading/trailing whitespace, normalize internal whitespace runs to single space.
4. SHA-256, take first 16 hex chars.

Same normalization for system prompt.

## 6. Track A — `derive_session_id_v2`

New module function in `uncommon_route/session.py`. Not invoked by the routing path; computed and recorded alongside the existing `session_id`.

### State

In-process `_RecentSessions` registry, kept in memory only:

- Capacity: 5,000 entries (configurable via `UNCOMMON_ROUTE_SESSION_TABLE_SIZE`)
- TTL: 6 hours (configurable via `UNCOMMON_ROUTE_SESSION_TTL_S`, seconds)
- Trie indexed on hash positions, with leaf nodes pointing at session records
- Each session record holds: `session_id`, `msg_hashes`, `last_seen_ts`
- Reverse map: upstream `response.id` → `session_id` (for OpenAI `previous_response_id` lookups)
- Eviction: TTL pass on every insert; if still over capacity, evict the oldest `last_seen_ts` records until under cap (LRU by access)

### Algorithm

```
inputs:
  msg_hashes      = list[str]   # normalized hash per message position
  first_user_v2   = str
  system_hash     = str
  metadata_uid    = str         # may be ""
  prev_response   = str         # may be ""

1. If prev_response and lookup(_response_id_index, prev_response) hits:
     return that session_id, extend session record

2. Walk the trie. Find the longest existing entry whose msg_hashes is
   a strict prefix of the incoming msg_hashes.
     - if found: return that session_id, replace its msg_hashes with the new (longer) list
     - else: continue

3. Generate new id from sha256(first_user_v2 || system_hash || metadata_uid)[:8].
   Insert into trie. Return new id.

4. After every insert/update, evict entries older than TTL.
```

### Why it works

- Agent loops always extend the prefix → step 2 hits.
- `/compact` rewrites the prefix → step 2 misses → new id (correctly: a compact restart is a new conversation backbone, but step 1 may rescue it via `previous_response_id` for OpenAI).
- Hash collisions on first user msg get demerged by including `system_hash` and `metadata_user_id` in the seed for new ids.
- Process restart loses the table; first request after restart starts a fresh session, but subsequent agent turns rejoin via prefix matching.

## 7. Track B — Content Fields

### Opt-in switch

Environment variable `UNCOMMON_ROUTE_CAPTURE_CONTENT`. Defaults to `0` (off). When set to `1` / `true` / `yes`, the writer populates content fields. When off, content fields are written as empty strings / empty arrays — same row schema, just no payload.

### Content fields on the JSONL row

```jsonc
{
  // ... routing metadata + v2 fields (always populated) ...

  // content (populated only when capture switch on):
  "request_messages": [/* full normalized messages array */],
  "request_system": "...",
  "request_tools_count": 28,
  "response_text": "...",
  "response_tool_calls": [/* tool_use blocks */],
  "response_finish_reason": "tool_use",
  "content_truncated": false   // true when payload exceeded 64 KB cap
}
```

Single row cap: 64 KB after JSON serialization. Over-cap rows get `content_truncated: true`, oversize fields elided in priority order: `response_text` → `response_tool_calls` → `request_messages` (oldest first).

### Capture hook

A single `_capture_content(request_id, body, response_payload, transport)` helper called after the trace is recorded, wrapped in `try/except`. Failure must never affect the request.

**Non-streaming path** (`proxy.py:4516`-ish):
- Body already in `resp.content`. Reuse the existing `_extract_assistant_text` shape; add `_extract_assistant_blocks` to also pull `tool_use` blocks. Per-transport parsers needed (3 formats).

**Streaming path** (`proxy.py:4489-4502`, `sse_passthrough`):
- All chunks are already buffered in `stream_chunks` for usage parsing.
- Add `parse_stream_assistant_content(stream_chunks, transport)` that returns `(text, tool_calls, finish_reason)`. Three implementations: `anthropic-messages`, `openai-chat`, `openai-responses`. Each is a small SSE event-name reducer.

## 8. Storage Layer Implementation

### `FileTraceStorage` rewrite

Replace the JSON full-rewrite with a daily JSONL append-only writer:

- `append(record: dict)`: open `traces/YYYY-MM-DD.jsonl` (where `YYYY-MM-DD` is derived from `record["timestamp"]`) in append mode, write one line, fsync, close.
- `load_recent_days(days: int) -> Iterator[dict]`: yield rows from the last `days` JSONL files in timestamp order.
- `load_for_request(request_id: str, ts: float) -> dict | None`: open the JSONL file for that timestamp's date and return the matching row (full content included), or scan adjacent days if not found there.
- `purge()`: rm the entire `traces/` directory.

### `TraceStore` changes

- `__init__` calls `_load` which uses `storage.load_recent_days(UNCOMMON_ROUTE_TRACE_HOT_DAYS)` and drops cold fields when constructing `self._records`.
- `record(trace)` calls `storage.append(payload)` with the full payload including content fields.
- `record_feedback(request_id, ...)` does not mutate any file. It appends a feedback event row (see §9).
- New `load_content(request_id) -> dict | None` for the assembly endpoint.

### File rotation and retention

- Daily file: `traces/YYYY-MM-DD.jsonl`.
- Retention: on each append, in 1% of calls, scan the directory and `unlink` files whose date suffix is older than `UNCOMMON_ROUTE_TRACE_RETENTION_DAYS` (default `14`).
- New CLI subcommand: `uncommon-route traces purge` clears the entire directory immediately.
- The 20K row count cap (`MAX_TRACES`) becomes informational only — daily rotation makes age-based retention the real bound.

## 9. Feedback as Event Sourcing

Current `TraceStore.record_feedback` mutates an existing record in place (`traces.py:189-212`). Append-only JSONL can't update a row, so feedback becomes its own row type:

```jsonc
{
  "type": "feedback",
  "request_id": "...",
  "timestamp": ...,
  "feedback_signal": "...",
  "feedback_ok": ...,
  "feedback_action": "...",
  "feedback_from_tier": "...",
  "feedback_to_tier": "...",
  "feedback_reason": "..."
}
```

Trace rows get an implicit `"type": "trace"` (default if the field is absent — backwards compatible).

### Loader merge

When `_load` reads JSONL files, it builds a `dict[request_id, RequestTrace]`. For each row:
- `type == "trace"` (or absent): instantiate or replace the trace.
- `type == "feedback"`: look up the trace in the dict and overlay the feedback fields.

Result: in-memory state is identical to today, just reconstructed from a stream instead of a snapshot.

## 10. Conversation Assembly Endpoint

```
GET /v1/sessions/{session_id}/conversation
```

Auth: same admin auth as `/v1/traces`.

Response:

```jsonc
{
  "session_id": "5438cbe4",
  "turn_count": 10,
  "content_available": true,
  "compact_breaks": [3],          // turn indices where prefix matching detected a reset
  "messages": [
    {
      "role": "user",
      "text": "...",
      "ts": 1777266100.0,
      "from_request_id": "abc123"
    },
    {
      "role": "assistant",
      "text": "...",
      "tool_calls": [/* tool_use blocks */],
      "ts": 1777266101.5,
      "request_id": "abc123",
      "decision": {
        // full routing decision card sourced from the trace:
        // model, decision_tier, served_quality, capability_lane,
        // route_reasoning, attempts_payload, etc.
      }
    },
    {
      "role": "tool_result",
      "tool_use_id": "...",
      "text": "...",
      "from_request_id": "..."
    }
    // ...
  ]
}
```

### Assembly algorithm

```
1. From the in-memory hot index, fetch all traces with this session_id, sorted by timestamp.
2. If none: 404.
3. For each turn, call storage.load_for_request(request_id, ts) to fetch the cold fields.
   If ALL turn cold fields are missing/empty → content_available = false, return turn list +
   decisions only (no message bodies).
4. Build the backbone:
     backbone = last_turn.request_messages
     append last_turn.response (text + tool_calls) as final assistant
5. Walk backbone. For each assistant message at position i (counting only
   assistant role), attach turns[i].decision (turn-by-turn alignment).
6. Detect compact breaks using msg_hashes recorded in each trace
   (so detection works even if content capture was off for some turns):
     for k in 1..N-1:
       prev = turns[k-1].msg_hashes
       curr = turns[k].msg_hashes
       if curr[:len(prev)] != prev:
         compact_breaks.append(k)
7. Convert backbone messages into the response shape, expanding tool_use /
   tool_result blocks into separate ordered entries.
```

When `content_available = false`, the endpoint still returns turn-level metadata — the frontend can degrade to the current per-turn explainer view.

## 11. Migration

`traces.json` is the only persistent state being changed. On first run after this branch ships, the loader detects the legacy file and migrates:

```
1. If ~/.uncommon-route/traces.json exists and traces/ does not:
   a. Read legacy JSON array.
   b. Group rows by date (UTC-day from timestamp).
   c. For each date: create traces/YYYY-MM-DD.jsonl, write each row as one line.
      - Existing rows have neither v2 fields nor content fields; they're written
        as-is. Defaults fill in via dataclass instantiation later.
   d. Rename traces.json to traces.json.bak and leave it alone.
2. Subsequent runs: skip the check, proceed with normal JSONL load.
```

The migration is idempotent: `traces/` directory existing is the signal that migration already happened. The `.bak` file is never read by the new code; users can delete it manually after verifying.

## 12. Defaults Locked In

| Choice | Default |
|---|---|
| Content capture default | **off** (opt-in via `UNCOMMON_ROUTE_CAPTURE_CONTENT`) |
| Trace retention | **14 days**, configurable |
| Hot-load days on startup | **2 days**, configurable |
| `session_id_v2` rollout | **shadow mode** in this branch; swap decision in a follow-up |
| Endpoint assembly | **backend-side**; frontend gets ready-to-render shape |
| Tool-call redaction toggle | **not added now**; keep complexity low |

## 13. PR Phasing

**This branch (PR 1):**

- All Track A schema additions and v2 algorithm.
- Storage layer rewrite (JSON → daily JSONL, hot/cold split, feedback events, migration).
- All Track B capture (off by default, controlled by env var).
- Assembly endpoint.
- Tests (see §14).
- Zero frontend changes. The pre-existing dashboard WIP on this branch is left untouched.

**Follow-up (PR 2):**

- `ExplainerNew.tsx` switches to chat-style rendering when `content_available`.
- Tool-loop turn folding (consecutive `tool-result-followup` turns sharing one user prompt collapse into one user bubble + an expandable tool-step group).
- Falls back to current per-turn metadata when content unavailable.

## 14. Test Plan

### Unit

- `tests/test_session.py`:
  - `derive_session_id_v2`: prefix-extension hits, fresh-conversation miss, TTL eviction, capacity eviction, multimodal content normalization, tool_use / tool_result normalization, `previous_response_id` index hit.
  - Confirm legacy `derive_session_id` remains byte-identical.
- New `tests/test_content_capture.py`:
  - Stream-chunk reducers for each of the three transports (assembled text + tool_calls match expected from canned SSE fixtures).
  - 64 KB row truncation behavior.
  - JSONL row schema (with and without content).
- `tests/test_traces.py` (extend existing):
  - JSONL append/load round-trip.
  - Hot-load drops cold fields; `load_content` retrieves them.
  - Feedback event overlays the corresponding trace correctly when interleaved with later trace rows.
  - One-time migration from legacy `traces.json` produces the expected daily JSONL files; legacy file gets renamed `.bak`; second run is a no-op.
  - Loading a JSONL written without v2 / content fields populates them with defaults — no crash.

### Integration

- Spin up the proxy with `UNCOMMON_ROUTE_CAPTURE_CONTENT=1`, fire a synthetic Claude-Code-style multi-turn tool loop through the proxy harness, assert `/v1/sessions/{id}/conversation` returns the correct backbone with `compact_breaks=[]`.
- Same harness with a `/compact` simulated mid-conversation: assert `compact_breaks=[k]` at the right index.
- Capture off: assert `content_available=false` and message bodies are empty in the assembly response.

### Manual smoke

- Run real Claude Code session through the local proxy with capture on. Verify resulting conversation JSON looks correct in `curl /v1/sessions/.../conversation`.

## 15. Open Questions for Implementation Plan

These are deferred to the writing-plans phase, not the spec:

- Exact module placement: does `derive_session_id_v2` live in `session.py` or a new `session_v2.py`?
- Do we expose `?include_content=1` on `/v1/traces/{request_id}` for power users, or keep the assembly endpoint as the only path to content?
- Do we want a CLI status command (`uncommon-route traces status`) showing capture on/off, file count, oldest day, total size?
