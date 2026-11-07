"""In-process event broadcast for the live UI (server-sent events).

The worker runs in a thread, while SSE subscribers wait on asyncio queues, so publish()
hands each event to the subscriber's event loop with call_soon_threadsafe.
Events are hints to refresh: the database stays the source of truth, so a dropped
event only means the UI updates on its next poll.
"""

import asyncio
import threading
from datetime import datetime, timezone

_subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
_lock = threading.Lock()


def subscribe() -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
    sub = (asyncio.get_running_loop(), asyncio.Queue(maxsize=200))
    with _lock:
        _subscribers.add(sub)
    return sub


def unsubscribe(sub) -> None:
    with _lock:
        _subscribers.discard(sub)


def publish(event_type: str, **data) -> None:
    event = {"type": event_type, "at": datetime.now(timezone.utc).isoformat(), **data}
    with _lock:
        subs = list(_subscribers)
    for loop, queue in subs:
        try:
            loop.call_soon_threadsafe(_offer, queue, event)
        except RuntimeError:  # loop already closed
            unsubscribe((loop, queue))


def _offer(queue: asyncio.Queue, event: dict) -> None:
    if queue.full():  # slow client: drop the oldest rather than block the worker
        queue.get_nowait()
    queue.put_nowait(event)
