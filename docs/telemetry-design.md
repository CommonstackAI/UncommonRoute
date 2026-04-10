# UncommonRoute Telemetry Design (v2)

> **Status**: Design Draft v2 (post-Codex review) | Date: 2026-04-10

## Purpose

Collect **pseudonymous** routing outcomes from opt-in users to improve routing accuracy. Current bottleneck: 487 labeled training samples caps accuracy at 73.6%. Target: 5000+ samples to unlock classifier and MF improvements.

## Principles

1. **Opt-in, default off** — user must explicitly say yes
2. **No content, ever** — never collect prompts, responses, or conversation text
3. **Pseudonymous, not "anonymous"** — we are honest: soft signals + embeddings are not truly anonymous. We treat the data as pseudonymous and apply corresponding safeguards.
4. **Private collection, public aggregates** — raw records go to a private endpoint; only aggregated, de-identified statistics are published publicly
5. **Verifiable** — single-file module, local audit log, noised embeddings
6. **Revocable** — disable anytime, data deletion available

---

## What We Collect

| Field | Type | Example | Purpose |
|---|---|---|---|
| `schema_version` | int | `1` | Forward compatibility |
| `client_version` | string | `"0.5.0"` | Track version-specific issues |
| `platform` | string | `"darwin"` | OS distribution |
| `timestamp_day` | string | `"2026-04-10"` | Day only — reduces fingerprinting |
| `predicted_tier` | int | `0` | 0=low, 1=mid, 2=mid_high, 3=high |
| `routed_model` | string | `"deepseek-chat"` | Which model was selected |
| `confidence` | float | `0.82` | Router confidence score |
| `routing_method` | string | `"direct"` | direct / conservative / cascaded |
| `final_tier` | int | `0` | Tier actually used (after cascade, if any) |
| `final_model` | string | `"deepseek-chat"` | Model actually used (after cascade) |
| `message_count` | int | `6` | Conversation depth |
| `has_tools` | bool | `true` | Whether tools are present |
| `tool_count` | int | `3` | Number of tool-related messages |
| `outcome` | string | `"success"` | success / http_error / timeout / empty_response |
| `outcome_reason` | string? | `"4xx"` | Coarse failure reason if not success |
| `user_feedback` | string? | `"ok"` | ok / weak / strong / null |
| `cascaded` | bool | `false` | Whether a cascade fallback was triggered |
| `cascade_from_tier` | int? | `null` | Original tier before cascade |
| `embedding` | float[384]? | `[0.012, ...]` | Noised embedding, **NEVER published publicly** |

### Removed from v1 design:
- ~~`install_id`~~ — removed entirely. No persistent identifier. Records are fire-and-forget with no cross-session linking.

---

## What We NEVER Collect

| Never Collect | Why |
|---|---|
| Prompt text | User's proprietary content |
| Response text | Model output may contain sensitive info |
| Full conversation | Privacy violation |
| API keys | Security risk |
| IP address | PII (server does not log IPs) |
| File paths | Reveals project structure |
| Exact timestamps | Fingerprinting vector |
| User email/identity | Not needed |
| System prompt content | May contain business logic |
| Persistent user/install ID | Enables tracking — removed in v2 |

---

## Privacy Measures

### Data classification: Pseudonymous

We do NOT call this data "anonymous." Per GDPR Recital 26, data is anonymous only if re-identification is not reasonably possible. Our embedding vectors preserve semantic similarity, which means a motivated attacker with a candidate corpus could potentially match records. We therefore:

1. Treat the data as **pseudonymous** under GDPR
2. Apply privacy protections accordingly
3. Never publish raw records publicly

### Embedding Privacy

**Threat model:** An attacker with access to raw embedding vectors and a candidate corpus of known prompts could find nearest-neighbor matches.

**Mitigations (defense in depth):**

| Layer | Measure | Effect |
|---|---|---|
| 1. Noise | Add N(0, 0.02) Gaussian noise + re-normalize | Degrades exact nearest-neighbor matching |
| 2. Short-message skip | Skip embedding for messages < 20 tokens | Small candidate space = higher risk |
| 3. Private storage | Raw embeddings go to private endpoint only | Not publicly accessible |
| 4. Public = aggregates only | Public dataset contains metadata + outcomes, NOT embeddings | Eliminates public attack surface |
| 5. Retention limit | Embeddings deleted after model retraining (max 90 days) | Limits exposure window |

```python
def _prepare_embedding(raw: np.ndarray, text_tokens: int) -> list[float] | None:
    if text_tokens < 20:
        return None  # skip short messages
    noised = raw + np.random.normal(0, 0.02, size=len(raw))
    noised = noised / np.linalg.norm(noised)
    return noised.tolist()
```

### No Persistent Identifier

v1 design had `install_id`. **Removed.** Each record is independent with no cross-session linking. This means:
- Cannot track a user across sessions
- Cannot build a user profile from multiple records
- "Right to erasure" is moot — there's nothing to link records to a person

Trade-off: We lose the ability to group records from the same conversation. Accepted — privacy > analytics convenience.

---

## Opt-In UX

### Precedence rules (highest to lowest):

```
1. DO_NOT_TRACK=1             → off (universal standard, always wins)
2. UNCOMMON_ROUTE_TELEMETRY   → on/off (explicit per-tool setting)
3. Config file                → on/off (persisted choice)
4. Interactive prompt         → ask once, persist to config
5. No TTY / CI=true / Docker  → off (never prompt non-interactive)
```

### Interactive prompt (first run only, if layers 1-4 are unset):

```
────────────────────────────────────────────────────
Help improve UncommonRoute's routing accuracy by
sharing anonymous routing metadata?

  ✓ Routing predictions, model selections, outcomes
  ✗ NO prompts, responses, API keys, or personal info

  Raw data goes to a private server (not published).
  Only aggregated statistics are made public.

  Details: https://github.com/CommonstackAI/UncommonRoute/blob/main/TELEMETRY.md
────────────────────────────────────────────────────
Share routing data? [y/N]:
```

### Headless / Container / Read-only $HOME:

| Environment | Behavior |
|---|---|
| No TTY (piped, Docker, systemd) | Auto-off, no prompt |
| `CI=true` | Auto-off |
| `$HOME` read-only | Telemetry disabled (can't write config/buffer) |
| Docker with volume mount | Works if `~/.uncommon-route/` is writable |

### CLI commands:

```bash
uncommon-route telemetry status      # Show current state + pending count
uncommon-route telemetry enable      # Opt in
uncommon-route telemetry disable     # Opt out + flush pending
uncommon-route telemetry show-sent   # Show successfully sent records only
uncommon-route telemetry flush       # Send pending records now
```

---

## Record Lifecycle

A telemetry record goes through two stages, solving the "outcome is known later" problem:

### Stage 1: Routing (immediate)

When `route()` returns a decision:

```python
record = TelemetryRecord(
    predicted_tier=decision.tier_id,
    routed_model=decision.model,
    confidence=decision.confidence,
    routing_method=decision.method,
    message_count=msg_count,
    has_tools=has_tools,
    tool_count=tool_count,
    embedding=_prepare_embedding(query_vec, token_count),
    # outcome fields are EMPTY at this stage
)
# Store in memory buffer, keyed by request_id (local only)
_pending_records[request_id] = record
```

### Stage 2: Outcome (async, when response comes back)

When the upstream response is received OR user gives feedback:

```python
# Update the pending record with outcome
record = _pending_records.pop(request_id, None)
if record:
    record.outcome = "success"  # or http_error / timeout / empty_response
    record.outcome_reason = None  # or "429" / "connection_refused"
    record.user_feedback = feedback  # ok / weak / strong / None
    record.final_tier = final_tier  # after cascade
    record.final_model = final_model
    record.cascaded = was_cascaded
    record.cascade_from_tier = original_tier if was_cascaded else None

    # NOW write to buffer file
    _append_to_buffer(record)
```

### Stage 3: Transmission (batched)

```python
# Every 50 completed records OR on `telemetry flush`:
records = _read_buffer()
success = _send_batch(records)  # POST to private endpoint
if success:
    _clear_buffer()
    _append_to_sent_log(records)  # audit log = successfully sent only
```

**Key fix from v1:** The audit log (`show-sent`) only contains records that were **actually transmitted successfully**, not pre-send records.

---

## Data Flow

```
User's machine                            Private server
─────────────                            ──────────────

  route() → Stage 1 record (no outcome)
       │
  response received → Stage 2 (add outcome)
       │
  buffer file (local only)
       │
  batch of 50 → POST to endpoint  ──────►  Private collection
                                            (Cloudflare Worker + R2)
                                                │
                                            Validate schema
                                            Strip unexpected fields
                                            Store in private bucket
                                                │
                                            Periodic aggregation
                                                │
                                                ▼
                                            Public aggregates
                                            (no embeddings, no raw records)
                                            - Tier distribution stats
                                            - Model usage stats
                                            - Accuracy metrics by version
```

### Phase 1: Cloudflare Worker

- Simple POST endpoint accepting JSONL batches
- Validates schema, rejects malformed records
- Stores in R2 bucket (private)
- **No auth required from client** — endpoint accepts anonymous POST
- Rate limited per IP (server-side, IPs not stored)
- Cost: effectively zero at our volumes

### Why NOT GitHub Issues (changed from v1):

Codex review identified critical issues with GitHub-based collection:
1. Requires auth token → leaks identity
2. Raw data public before validation → privacy risk
3. Abuse surface (poisoning, spam)

Cloudflare Worker solves all three: anonymous POST, private storage, schema validation before storage.

### Public data (aggregates only):

Periodically publish to `CommonstackAI/uncommonroute-telemetry`:
- Tier distribution histograms
- Model usage percentages
- Accuracy metrics by client version
- **NO raw records, NO embeddings**

---

## Data Record Schema

```python
@dataclass
class TelemetryRecord:
    # Schema
    schema_version: int = 1

    # Client (no persistent ID)
    client_version: str = ""
    platform: str = ""            # darwin / linux / win32

    # Routing decision
    predicted_tier: int = -1      # 0=low, 1=mid, 2=mid_high, 3=high
    routed_model: str = ""
    confidence: float = 0.0
    routing_method: str = ""      # direct / conservative / cascaded

    # Context (metadata only)
    message_count: int = 0
    has_tools: bool = False
    tool_count: int = 0

    # Outcome (filled in Stage 2)
    outcome: str = ""             # success / http_error / timeout / empty_response
    outcome_reason: str | None = None  # coarse failure reason
    user_feedback: str | None = None   # ok / weak / strong
    final_tier: int = -1          # tier actually used (after cascade)
    final_model: str = ""         # model actually used
    cascaded: bool = False        # was cascade triggered?
    cascade_from_tier: int | None = None  # original tier before cascade

    # Embedding (noised, private only, never published)
    embedding: list[float] | None = None

    # Time
    timestamp_day: str = ""       # "2026-04-10" (day only)
```

---

## Training Data Utility

The collected data provides **soft labels, not gold labels**. This is explicit:

| Signal | What it tells us | How to use |
|---|---|---|
| `outcome="success"` + no retry | Routing was probably adequate | Weak positive label |
| `outcome="http_error"` | Model couldn't handle it | Negative: tier was too low |
| `user_feedback="weak"` | User says model wasn't good enough | Strong negative: needed higher tier |
| `user_feedback="strong"` | User says could've been cheaper | Positive: tier could be lower |
| `cascaded=true` | First model failed, had to escalate | Negative: original tier was wrong |
| `user_feedback="ok"` | User confirms routing was fine | Strong positive label |

**Training approach:** Contextual bandit / reward modeling, not supervised classification on gold labels. The reward function combines outcome signals into a scalar:

```python
reward = 0.0
if outcome == "success" and not cascaded:
    reward = 0.5  # weak positive
if user_feedback == "ok":
    reward = 1.0  # strong positive
if user_feedback == "weak" or cascaded:
    reward = -0.5  # routing was wrong
if outcome in ("http_error", "timeout"):
    reward = -1.0  # hard failure
```

This maps naturally to the BaRP (Bandit-feedback Routing with Preferences, 2025) framework.

---

## Legal Compliance

### Classification: Pseudonymous Data

We do **not** claim anonymity. The data is **pseudonymous** under GDPR because:
- Noised embeddings preserve semantic similarity (potential re-identification vector)
- Even without install_id, repeated patterns from the same user COULD be grouped by an attacker

### GDPR Requirements

| Requirement | How We Meet It |
|---|---|
| Lawful basis | Explicit opt-in consent (affirmative action, not pre-ticked) |
| Data minimization | Only routing metadata + noised embeddings |
| Transparency | TELEMETRY.md + local audit log |
| Right to withdraw | `telemetry disable` at any time |
| Right to erasure | No persistent ID to link records; on request, we delete records matching provided criteria from private store |
| Data controller | Commonstack AI (contact: privacy@commonstack.ai) |
| Retention period | Raw records: max 90 days. Aggregates: indefinite. |
| Cross-border | Private endpoint in US (Cloudflare). TELEMETRY.md discloses this. |

### CCPA

| Requirement | Status |
|---|---|
| "Sale" of data | No sale — data used only for routing quality improvement |
| Right to know | TELEMETRY.md documents all collected fields |
| Right to delete | Honored on request (email privacy@commonstack.ai) |
| Opt-out | Opt-in by default — no opt-out needed |

### Schema Version Policy

If we ever expand the collected fields:
1. Bump `schema_version`
2. Update TELEMETRY.md
3. Re-prompt ALL users for consent (don't silently expand scope)
4. Existing consent does NOT carry over to new schema versions

---

## Implementation Plan

### Files:

| File | Responsibility |
|---|---|
| `uncommon_route/telemetry.py` | Single-file module: opt-in logic, record building, buffering, sending |
| `TELEMETRY.md` | Public disclosure document (repo root) |
| `uncommon_route/cli.py` (modify) | Add `telemetry` subcommand |
| `uncommon_route/v2_lifecycle.py` (modify) | Stage 1 + Stage 2 hooks |
| `uncommon_route/proxy.py` (modify) | Stage 2 outcome recording after response |

### Implementation rules:

1. `telemetry.py` must be a **single file** — no dynamic imports, no obfuscation
2. All network calls in **one function** (`_send_batch()`)
3. Telemetry failures **never degrade routing** — silent skip on any error
4. Buffer writes are **after outcome** (Stage 2), not after routing (Stage 1)
5. Audit log (`show-sent`) reflects **successfully transmitted records only**
6. `TELEMETRY.md` is source of truth — code must match doc
7. CI tests verify emitted fields match TELEMETRY.md schema (prevent drift)

---

## Volume Projections

| Opt-in users | Requests/day/user | Days to 5000 records |
|---|---|---|
| 10 | 50 | 10 days |
| 20 | 50 | 5 days |
| 50 | 50 | 2 days |

At 5000 records with outcome signals:
- Train contextual bandit reward model → expected +5-8pp
- Retrain embedding classifier with soft labels → expected +3-5pp
- Combined target: **80-85% accuracy** (from current 73.6%)
