"""ActivityEvent: one line in the activity panel. Owner B, written by everyone."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from contracts.common import (
    EthyneonModel,
    EventSource,
    Revision,
    SourceLabel,
    utc_now,
)

# The named event types from the PRD and the brief. `type` stays a plain string
# so a lane can add one without a contract change, but these are the agreed set
# and emit_event warns on anything outside it.
KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session_created",
        "waiting_for_measurement",
        "room_committed",
        "scene_committed",
        "door_swing_overlap",
        "fit_checked",
        "variant_verified",
        "offer_cached",
        "cart_needs_review",
        "cart_written",
        "checkout_checks_passed",
        "checkout_opened",
        "payment_unconfirmed",
        "order_confirmed",
        "recovery_started",
        "recovery_finished",
        "workflow_step_finished",
    }
)


class RevisionRefs(EthyneonModel):
    """Which revisions this event refers to, so a late event is never applied
    to a newer state."""

    room_revision: Optional[Revision] = None
    scene_revision: Optional[Revision] = None


class ActivityEvent(EthyneonModel):
    event_id: str
    session_id: str
    ts: datetime = Field(default_factory=utc_now)
    source: EventSource
    type: str = Field(min_length=1)
    step_id: Optional[str] = None
    message: str
    revision_refs: RevisionRefs = Field(default_factory=RevisionRefs)
    payload: dict = Field(default_factory=dict)
    source_label: SourceLabel = "live"
    safe_to_display: bool = Field(
        default=True,
        description="Lanes show message to users only when the producer marks it safe.",
    )
