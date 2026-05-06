"""Async pub/sub event bus for dashboard SSE stream.

In-process fan-out from request lifecycle hooks to subscribed asyncio
clients. Each subscriber holds a bounded queue; slow consumers get
a synthetic `dropped` event rather than blocking publishers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

QUEUE_MAX = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_MAX)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        """Non-blocking fan-out. Safe to call from any async context."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.put_nowait({"type": "dropped", "reason": "queue_full"})
                except asyncio.QueueFull:
                    pass
            except Exception:
                log.exception("event publish failed")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_bus_for_tests() -> None:
    global _bus
    _bus = None
