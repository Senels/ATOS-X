import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, List

from loguru import logger


@dataclass
class Event:
    type: str
    payload: dict
    ts: float = time.time()


class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)

    def subscribe(self, event_type: str, handler: Callable):
        self.subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event):
        await self.queue.put(event)

    async def run(self):
        while True:
            evt = await self.queue.get()
            for handler in self.subscribers.get(evt.type, []) + self.subscribers.get("*", []):
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(evt)
                    else:
                        handler(evt)
                except Exception as e:
                    logger.exception(f"Event bus handler hatasi ({evt.type}): {e}")
            self.queue.task_done()


bus = EventBus()
