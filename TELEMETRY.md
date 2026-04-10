# UncommonRoute Telemetry

UncommonRoute can optionally collect pseudonymous routing metadata to improve routing accuracy. **This is opt-in and disabled by default.**

## What We Collect

- Routing predictions (which tier and model were selected)
- Routing confidence score
- Routing outcome (success, failure, retry)
- User feedback signal (ok, weak, strong) — if you provide it
- Conversation metadata: message count, whether tools are present, tool count (best-effort; may be 0 in some code paths)
- A noised embedding vector (384-dim, with Gaussian noise added for privacy)
- Client version, platform (darwin/linux/win32), day-level timestamp

## What We NEVER Collect

- Your prompts or responses
- API keys or credentials
- File paths or project names
- IP addresses (our server does not log them)
- Personal identity information
- System prompt content
- Exact timestamps (day only)
- Any persistent user identifier

## How to Control It

```bash
# Check status
uncommon-route telemetry status

# Enable
uncommon-route telemetry enable

# Disable (discards unsent data)
uncommon-route telemetry disable

# See what was sent
uncommon-route telemetry show-sent

# Environment variables (override config):
export UNCOMMON_ROUTE_TELEMETRY=off
export DO_NOT_TRACK=1  # universal standard
```

## Privacy Details

- **Data classification:** Pseudonymous (not anonymous). We add noise to embeddings but cannot guarantee they are fully de-identifiable.
- **No persistent identifier:** Each record is independent. We cannot link records to a specific user.
- **Deletion:** Because we store no identifier, we cannot locate or delete specific user records after submission. If this is unacceptable, do not opt in. All raw records are automatically purged after 90 days.
- **Raw data is private:** Individual records go to a private collection server. They are never published publicly.
- **Public aggregates only:** We periodically publish aggregated statistics (tier distributions, accuracy metrics by version). No individual records or embeddings are ever made public.
- **Source code:** The telemetry module is a single file (`uncommon_route/telemetry.py`) that you can audit in 5 minutes.
- **Local audit log:** Every successfully sent record is also saved locally. Run `uncommon-route telemetry show-sent` to inspect.

## Schema Version Policy

If we ever expand the collected fields, we will:
1. Bump the schema version
2. Update this document
3. Re-prompt all users for consent

Existing consent does NOT carry over to new schema versions.
