"""Opt-in pseudonymous telemetry for routing quality improvement.

Single-file module. All network calls in _send_batch().
Telemetry failures never degrade routing — silent skip on any error.

See TELEMETRY.md for what is collected and privacy details.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("uncommon-route.telemetry")

_BUFFER_FLUSH_THRESHOLD = 50
_ENDPOINT_URL = "https://telemetry.uncommonroute.dev/v1/collect"  # Phase 1 placeholder


# ─── Record Schema ───

@dataclass
class TelemetryRecord:
    schema_version: int = 1
    client_version: str = ""
    platform: str = ""
    timestamp_day: str = ""

    # Routing decision
    predicted_tier: int = -1
    routed_model: str = ""
    confidence: float = 0.0
    routing_method: str = ""

    # Context (metadata only)
    message_count: int = 0
    has_tools: bool = False
    tool_count: int = 0

    # Outcome (filled in Stage 2)
    outcome: str = ""
    outcome_reason: str | None = None
    user_feedback: str | None = None
    final_tier: int = -1
    final_model: str = ""
    cascaded: bool = False
    cascade_from_tier: int | None = None

    # Embedding (noised, private only)
    embedding: list[float] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Remove None embedding to save space
        if d["embedding"] is None:
            del d["embedding"]
        return d


# ─── Opt-In Logic ───

def _config_dir() -> Path:
    from uncommon_route.paths import data_dir
    return data_dir()


def _config_path() -> Path:
    return _config_dir() / "config.json"


def _load_config() -> dict[str, Any]:
    path = _config_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _save_config(config: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))


def is_enabled() -> bool:
    """Check if telemetry is enabled, following precedence rules."""
    # Layer 1: DO_NOT_TRACK (universal standard, always wins)
    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return False

    # Layer 2: Explicit env var
    env = os.environ.get("UNCOMMON_ROUTE_TELEMETRY", "").strip().lower()
    if env in ("on", "true", "1", "yes"):
        return True
    if env in ("off", "false", "0", "no"):
        return False

    # Layer 3: Config file (explicit user choice — respected even in non-TTY)
    config = _load_config()
    telemetry_config = config.get("telemetry", {})
    if isinstance(telemetry_config, dict) and "enabled" in telemetry_config:
        return bool(telemetry_config["enabled"])

    # Layer 4: Non-interactive → off (only affects auto-prompt, not explicit config)
    if not sys.stdin.isatty() or os.environ.get("CI"):
        return False

    # Layer 5: Not yet decided
    return False


def prompt_if_needed() -> bool:
    """Show opt-in prompt if user hasn't decided yet. Returns enabled state."""
    # Skip if already decided
    config = _load_config()
    if "telemetry" in config:
        return config.get("telemetry", {}).get("enabled", False)

    # Skip if non-interactive
    if not sys.stdin.isatty() or os.environ.get("CI"):
        return False

    # Skip if env var is set
    env = os.environ.get("UNCOMMON_ROUTE_TELEMETRY", "").strip()
    if env:
        return env.lower() in ("on", "true", "1", "yes")

    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return False

    # Prompt
    print("""
────────────────────────────────────────────────────
Help improve UncommonRoute's routing accuracy by
sharing pseudonymous routing metadata?

  ✓ Routing predictions, model selections, outcomes
  ✗ NO prompts, responses, API keys, or personal info

  Details: https://github.com/CommonstackAI/UncommonRoute/blob/main/TELEMETRY.md
────────────────────────────────────────────────────""")
    try:
        answer = input("Share pseudonymous routing data? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    enabled = answer in ("y", "yes")
    config["telemetry"] = {"enabled": enabled}
    _save_config(config)

    if enabled:
        print("  Telemetry enabled. Disable anytime: uncommon-route telemetry disable")
    else:
        print("  Telemetry disabled. Enable anytime: uncommon-route telemetry enable")

    return enabled


def enable() -> None:
    config = _load_config()
    config["telemetry"] = {"enabled": True}
    _save_config(config)


def disable() -> None:
    """Disable telemetry and discard any pending unsent records."""
    config = _load_config()
    config["telemetry"] = {"enabled": False}
    _save_config(config)
    # Discard pending buffer (right to withdraw)
    buf = _buffer_path()
    if buf.exists():
        buf.unlink()


def status() -> dict[str, Any]:
    enabled = is_enabled()
    buf = _buffer_path()
    pending = 0
    if buf.exists():
        try:
            pending = sum(1 for line in buf.read_text().splitlines() if line.strip())
        except Exception:
            pass
    sent_log = _sent_log_path()
    total_sent = 0
    if sent_log.exists():
        try:
            total_sent = sum(1 for line in sent_log.read_text().splitlines() if line.strip())
        except Exception:
            pass
    return {
        "enabled": enabled,
        "pending_records": pending,
        "total_sent": total_sent,
    }


# ─── Embedding Noise ───

def prepare_embedding(raw: np.ndarray, text_token_count: int) -> list[float] | None:
    """Add Gaussian noise to embedding for privacy. Skip short messages."""
    if text_token_count < 20:
        return None
    noised = raw + np.random.normal(0, 0.02, size=len(raw))
    norm = np.linalg.norm(noised)
    if norm > 0:
        noised = noised / norm
    return noised.tolist()


# ─── Buffer + Transmission ───

def _buffer_path() -> Path:
    return _config_dir() / "telemetry_buffer.jsonl"


def _sent_log_path() -> Path:
    return _config_dir() / "telemetry_sent.jsonl"


def buffer_record(record: TelemetryRecord) -> None:
    """Append a completed record to the local buffer."""
    if not is_enabled():
        return
    try:
        buf = _buffer_path()
        buf.parent.mkdir(parents=True, exist_ok=True)
        with open(buf, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        # NO auto-flush here — flush only on explicit command or shutdown
        # This keeps telemetry off the hot routing path (no blocking network calls)
    except Exception as e:
        logger.debug("Telemetry buffer failed: %s", e)


def flush() -> int:
    """Send buffered records to the collection endpoint. Returns count sent."""
    buf = _buffer_path()
    if not buf.exists():
        return 0
    try:
        records_text = buf.read_text().strip()
        if not records_text:
            return 0
        lines = records_text.splitlines()
        success = _send_batch(lines)
        if success:
            # Write to sent log (audit trail for successfully sent records)
            sent = _sent_log_path()
            with open(sent, "a", encoding="utf-8") as f:
                for line in lines:
                    f.write(line + "\n")
            # Clear buffer
            buf.unlink(missing_ok=True)
            return len(lines)
    except Exception as e:
        logger.debug("Telemetry flush failed: %s", e)
    return 0


def _send_batch(lines: list[str]) -> bool:
    """POST a batch of JSONL records to the collection endpoint.

    Returns True if the server accepted the batch. This is the ONLY
    function that makes network calls.
    """
    try:
        import httpx
        payload = "\n".join(lines)
        resp = httpx.post(
            _ENDPOINT_URL,
            content=payload.encode("utf-8"),
            headers={"Content-Type": "application/jsonl"},
            timeout=10.0,
        )
        return resp.status_code in (200, 201, 202, 204)
    except Exception as e:
        logger.debug("Telemetry send failed: %s", e)
        return False


def get_sent_records() -> list[dict[str, Any]]:
    """Read the sent log for `telemetry show-sent`."""
    sent = _sent_log_path()
    if not sent.exists():
        return []
    records = []
    for line in sent.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records
