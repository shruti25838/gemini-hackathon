"""emit_event: write to SQLite, then push to every /ws/events listener.

Order matters. The event is persisted first, so a client that misses the push
still finds it through GET /api/state?since_revision. A socket that has gone
away never loses an event.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from contracts.activity_event import KNOWN_EVENT_TYPES, ActivityEvent, RevisionRefs
from contracts.common import utc_now
from backend.db import Database


class EventBus:
    """Fan-out to the websocket listeners for one process."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            listeners = self._subscribers.get(session_id)
            if listeners:
                listeners.discard(queue)
                if not listeners:
                    self._subscribers.pop(session_id, None)

    def listener_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, ()))

    def publish_nowait(self, session_id: str, message: dict[str, Any]) -> None:
        """Push without awaiting, so emit_event stays callable from sync code.

        A full queue means a client is not reading. The event is already in
        SQLite, so dropping the push is safe: the client will pick it up on
        reconnect through GET /api/state.
        """
        for queue in list(self._subscribers.get(session_id, ())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass


class EventWriter:
    """The emit_event implementation behind the Protocol in interfaces.py."""

    def __init__(self, db: Database, bus: Optional[EventBus] = None) -> None:
        self.db = db
        self.bus = bus or EventBus()
        self.unknown_types_seen: list[str] = []

    def emit(self, event: ActivityEvent) -> ActivityEvent:
        if event.type not in KNOWN_EVENT_TYPES:
            # Not an error: the contract leaves `type` open so a lane can add
            # one. Recorded so it shows up in review rather than silently.
            self.unknown_types_seen.append(event.type)

        payload = event.model_dump(mode="json")
        self.db.insert_event(payload)
        self.bus.publish_nowait(event.session_id, payload)
        return event

    def emit_simple(
        self,
        session_id: str,
        source: str,
        event_type: str,
        message: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        room_revision: Optional[int] = None,
        scene_revision: Optional[int] = None,
        source_label: str = "live",
        step_id: Optional[str] = None,
        safe_to_display: bool = True,
    ) -> ActivityEvent:
        event = ActivityEvent(
            event_id=f"evt_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            ts=utc_now(),
            source=source,  # type: ignore[arg-type]
            type=event_type,
            step_id=step_id,
            message=message,
            revision_refs=RevisionRefs(
                room_revision=room_revision, scene_revision=scene_revision
            ),
            payload=payload or {},
            source_label=source_label,  # type: ignore[arg-type]
            safe_to_display=safe_to_display,
        )
        return self.emit(event)
