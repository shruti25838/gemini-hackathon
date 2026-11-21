"""In-memory session state. Canonical state lives here, never in a model."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from contracts.schemas import (ActivityEvent, CatalogOffer, DesignBrief, FitReport, RoomSpec, SceneRevision,
                               WorkflowPlan)

log = logging.getLogger("ethyneon.events")


@dataclass
class Session:
    id: str
    room: Optional[RoomSpec] = None
    room_history: list[RoomSpec] = field(default_factory=list)
    brief: DesignBrief = field(default_factory=DesignBrief)
    offers: dict[str, CatalogOffer] = field(default_factory=dict)  # variant_id -> offer
    scenes: list[SceneRevision] = field(default_factory=list)
    fit: Optional[FitReport] = None
    plan: Optional[WorkflowPlan] = None
    pending_input: Optional[dict] = None
    approval: Optional[dict] = None
    cart: Optional[dict] = None
    audit: Optional[dict] = None
    events: list[ActivityEvent] = field(default_factory=list)
    gate_log: list[dict] = field(default_factory=list)
    extra_variant_ids: list[str] = field(default_factory=list)  # cart lines with no floor footprint (lighting)
    last_review: Optional[dict] = None
    last_alternative: Optional[dict] = None
    recovery_runs: list = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    # ----- scene -----
    @property
    def scene(self) -> Optional[SceneRevision]:
        return self.scenes[-1] if self.scenes else None

    def commit_scene(self, scene: SceneRevision, base_rev: Optional[int] = None) -> SceneRevision:
        """Commit a new revision. Stale results (built on an older base) are rejected."""
        with self.lock:
            cur = self.scene
            if base_rev is not None and cur is not None and base_rev != cur.rev:
                raise StaleRevision(f"result built on rev {base_rev}, current is {cur.rev}")
            scene.parent_rev = cur.rev if cur else None
            scene.rev = (cur.rev + 1) if cur else 1
            self.scenes.append(scene)
            self.approval = None  # any scene change voids a cart approval
            return scene

    def set_room(self, room: RoomSpec):
        with self.lock:
            if self.room:
                self.room_history.append(self.room)
            self.room = room
            self.approval = None

    # ----- activity -----
    def emit(self, step: str, status: str, detail: str = "", **data) -> ActivityEvent:
        with self.lock:
            ev = ActivityEvent(seq=len(self.events) + 1, ts=time.time(), step=step, status=status,
                               detail=detail, data=data)
            self.events.append(ev)
        log.info(json.dumps({"session_id": self.id, "step": step, "status": status, "detail": detail[:200]}))
        return ev


class StaleRevision(RuntimeError):
    pass


_SESSIONS: dict[str, Session] = {}


def new_session() -> Session:
    s = Session(id=uuid.uuid4().hex[:12])
    _SESSIONS[s.id] = s
    return s


def get(sid: str) -> Session:
    if sid not in _SESSIONS:
        raise KeyError(sid)
    return _SESSIONS[sid]
