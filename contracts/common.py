"""Shared primitives for every Ethyneon contract.

Conventions fixed by the team integration contract:
  - Lengths are metres, as numbers (3.2, never "3.2 m").
  - Money is integer minor units plus an ISO 4217 currency code. Never floats.
  - One room-local coordinate system in metres. Opening offsets are measured
    from the start of their wall.
  - Timestamps are ISO 8601, UTC.
  - Revisions are integers that only increase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------
# Enumerations shared across lanes
# --------------------------------------------------------------------------

# Evidence status. User-confirmed values are never overwritten by later estimates.
EvidenceStatus = Literal["estimated", "confirmed", "unknown", "sampled_from_photo"]

# Fit result. "unknown" whenever dimensions, hinge or swing direction are missing.
FitResultValue = Literal["fits", "tight", "does_not_fit", "unknown"]

# Everything shown to the user carries one of these where it applies.
SourceLabel = Literal["live", "cached", "preset", "generated", "inferred", "mock"]

# Stable wall IDs, set once by the RoomSpec and never renamed.
WallId = Literal["wall_n", "wall_e", "wall_s", "wall_w"]

# Which lane or subsystem emitted an activity event.
EventSource = Literal["ai", "room", "commerce", "app"]

# Where a CatalogOffer's dimensions came from. Anything parsed out of listing
# text is inferred and caps any fit result at tentative.
DimensionSource = Literal["metafield", "listing_text_inferred", "unknown"]

# Which commerce API produced an offer.
ApiSurface = Literal["storefront_api", "shop_mcp", "mock"]

# Payment and checkout states. "paid" is never inferred from the handoff alone.
PaymentState = Literal[
    "cart_ready",
    "checkout_audit_pending",
    "needs_review",
    "checkout_opened",
    "payment_unconfirmed",
    "order_confirmed",
]

Metres = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Revision = Annotated[int, Field(ge=0)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


def utc_now() -> datetime:
    """Timezone-aware UTC now, for default timestamps."""
    return datetime.now(timezone.utc)


class EthyneonModel(BaseModel):
    """Base for every contract model.

    extra="forbid" is deliberate: a typo in a field name from another lane
    should fail loudly at the boundary instead of being silently dropped.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


class Money(EthyneonModel):
    """Integer minor units plus an ISO 4217 code. 120000 + USD means $1,200.00."""

    amount_minor: int = Field(description="Integer minor units. Never a float.")
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217, uppercase.")

    @field_validator("currency")
    @classmethod
    def _upper_alpha(cls, v: str) -> str:
        if not v.isalpha():
            raise ValueError("currency must be three letters, ISO 4217")
        return v.upper()


# --------------------------------------------------------------------------
# Geometry, all in room-local metres
# --------------------------------------------------------------------------


class Point2D(EthyneonModel):
    """A point on the floor plane, in room-local metres."""

    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)


class Rect2D(EthyneonModel):
    """An axis-aligned footprint plus a rotation about its own centre."""

    x_m: float = Field(allow_inf_nan=False, description="Centre x, room-local metres.")
    y_m: float = Field(allow_inf_nan=False, description="Centre y, room-local metres.")
    width_m: Metres
    depth_m: Metres
    rotation_deg: float = Field(default=0.0, allow_inf_nan=False)


class Polygon2D(EthyneonModel):
    """A closed polygon on the floor plane. At least three points."""

    points: list[Point2D] = Field(min_length=3)


class Pose(EthyneonModel):
    """Where an object sits on the floor and how it is turned."""

    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)
    rotation_deg: float = Field(default=0.0, allow_inf_nan=False)


class Measurement(EthyneonModel):
    """One length with its provenance, so estimates never masquerade as facts."""

    value_m: Metres
    evidence_status: EvidenceStatus
    source: SourceLabel
    confidence: Optional[Confidence] = None


class ColorSpec(EthyneonModel):
    """A surface colour and where it came from. Browser pixel sampling is
    labelled sampled_from_photo because lighting can affect it."""

    hex: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    evidence_status: EvidenceStatus
    source: SourceLabel


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ErrorResponse(EthyneonModel):
    """Every failed call returns exactly this shape."""

    code: str
    message: str
    retryable: bool
    details: dict = Field(default_factory=dict)
