"""RoomSnapshot: raw capture evidence. Owner B, used by A and C."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from contracts.common import (
    Confidence,
    EthyneonModel,
    EvidenceStatus,
    Metres,
    Rect2D,
    SourceLabel,
    utc_now,
)

MediaKind = Literal["photo", "video_clip", "frame"]


class MediaRef(EthyneonModel):
    """A reference to captured media. EXIF and GPS are stripped in the browser
    before upload; metadata_stripped records that it happened."""

    media_id: str
    kind: MediaKind
    uri: str = Field(description="Session-scoped reference, not a public URL.")
    width_px: Optional[int] = Field(default=None, ge=1)
    height_px: Optional[int] = Field(default=None, ge=1)
    captured_at: Optional[datetime] = None
    metadata_stripped: bool = False
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ObservedObject(EthyneonModel):
    """Something the model or the user spotted in the photo."""

    object_id: str
    label: str
    confidence: Optional[Confidence] = None
    bbox: Optional[Rect2D] = None
    keep: Optional[bool] = Field(
        default=None, description="Whether the shopper wants to keep it. None means not asked."
    )


class MeasuredAnchor(EthyneonModel):
    """A real length the user confirmed, used to set the scale of the model."""

    anchor_id: str
    description: str
    length_m: Metres
    evidence_status: EvidenceStatus
    confirmed_by_user: bool = False


class RoomSnapshot(EthyneonModel):
    snapshot_id: str
    session_id: str
    media: list[MediaRef] = Field(min_length=1)
    source_device: str
    captured_at: datetime
    received_at: datetime = Field(default_factory=utc_now)
    observed_objects: list[ObservedObject] = Field(default_factory=list)
    measured_anchors: list[MeasuredAnchor] = Field(default_factory=list)
    units: Literal["m"] = "m"
    evidence_status: EvidenceStatus = "estimated"
    source: SourceLabel = "live"
    consent_given: bool = Field(
        default=False, description="Consent screen shown and accepted before analysis."
    )
