"""Event types and EventManager for inter-agent communication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class EventType(StrEnum):
    TASK_START = "task.start"
    TASK_COMPLETE = "task.ok"
    TASK_ERROR = "task.fail"
    TASK_CANCEL = "task.cancel"
    STEP_START = "step.start"
    STEP_END = "step.ok"
    STEP_FAIL = "step.fail"
    HUMAN_NEEDED = "human.needed"


class Actors(StrEnum):
    SYSTEM = "system"
    PLANNER = "planner"
    NAVIGATOR = "navigator"
    EXECUTOR = "executor"


class AgentEvent:
    """Represents a single event emitted during agent execution."""

    def __init__(
        self,
        event_type: EventType,
        actor: Actors,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.event_type = event_type
        self.actor = actor
        self.data: dict[str, Any] = data or {}

    def __repr__(self) -> str:
        return f"AgentEvent(type={self.event_type}, actor={self.actor}, data={self.data})"


# Callback type: sync or async, receives AgentEvent
EventCallback = Callable[[AgentEvent], Any]


class EventManager:
    """Pub/sub event manager for agents."""

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[EventCallback]] = {}

    def subscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """Subscribe *callback* to *event_type*. Duplicates are ignored."""
        bucket = self._subscribers.setdefault(event_type, [])
        if callback not in bucket:
            bucket.append(callback)

    def unsubscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """Remove *callback* from *event_type* subscribers."""
        if event_type in self._subscribers:
            self._subscribers[event_type] = [
                cb for cb in self._subscribers[event_type] if cb is not callback
            ]

    def clear(self, event_type: EventType | None = None) -> None:
        """Remove all subscribers for *event_type*, or all if None."""
        if event_type is None:
            self._subscribers.clear()
        else:
            self._subscribers.pop(event_type, None)

    async def emit(
        self, event_type: EventType, actor: Actors, data: dict[str, Any] | None = None
    ) -> None:
        """Emit an event and call all subscribers (sync or async)."""
        event = AgentEvent(event_type, actor, data)
        callbacks = list(self._subscribers.get(event_type, []))
        logger.debug("event.emit", event_type=event_type.value, actor=actor.value)
        for cb in callbacks:
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("event.callback_error", callback=cb)
