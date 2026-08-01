import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

Handler = Callable[..., Awaitable[None]]


class EventBus:
    """Typed asyncio pub/sub bus. publish() schedules handlers as tasks."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, event_type: str, handler: Handler) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Handler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    def publish(self, event_type: str, *args: Any, **kwargs: Any) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        for handler in self._subscribers.get(event_type, []):
            self._loop.create_task(handler(*args, **kwargs))

    def subscriber_count(self, event_type: str) -> int:
        return len(self._subscribers.get(event_type, []))


bus = EventBus()
