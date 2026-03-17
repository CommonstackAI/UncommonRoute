"""Session identity utilities.

Provides ``derive_session_id`` for cache key generation and
composition checkpoint tracking.  Routing no longer uses sticky
sessions — every request is routed independently by the pool scorer.
"""

from __future__ import annotations

import hashlib
from typing import Any


def derive_session_id(messages: list[dict[str, Any]]) -> str | None:
    """Derive a session ID from the first user message (SHA-256 prefix).

    Used for cache key grouping and composition checkpoints, not routing.
    """
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            text = content if isinstance(content, str) else str(content)
            return hashlib.sha256(text.encode()).hexdigest()[:8]
    return None
