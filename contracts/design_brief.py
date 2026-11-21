"""DesignBrief: what the shopper asked for. Owner A, used by C."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from contracts.common import (
    EthyneonModel,
    EvidenceStatus,
    Money,
    Revision,
    utc_now,
)


class RequiredItem(EthyneonModel):
    """Something the shopper said they need, before any product is chosen."""

    item_id: str
    label: str
    quantity: int = Field(default=1, ge=1)
    category: Optional[str] = None
    notes: Optional[str] = None


class Preferences(EthyneonModel):
    """Soft wishes. These rank feasible options; they never override a
    measured constraint."""

    finish: list[str] = Field(default_factory=list)
    lighting: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    free_text: Optional[str] = None


class HardConstraint(EthyneonModel):
    """A requirement that must hold. Relaxing one needs the shopper to say so."""

    constraint_id: str
    kind: str = Field(description="For example keep_door_clear or min_walkway_width.")
    value: Optional[float] = None
    unit: Optional[str] = None
    description: str
    evidence_status: EvidenceStatus = "confirmed"


class DesignBrief(EthyneonModel):
    brief_id: str
    session_id: str
    brief_revision: Revision = 0
    room_use: str = Field(description="For example dual_monitor_gaming_corner.")
    locked_object_ids: list[str] = Field(
        default_factory=list, description="Items the shopper is keeping."
    )
    merchandise_budget: Money = Field(
        description="Maximum merchandise subtotal. Shipping and tax are added at checkout."
    )
    budget_applies_to: str = Field(
        default="merchandise_subtotal",
        description="Stated plainly, because the shopper must know what the cap covers.",
    )
    required_items: list[RequiredItem] = Field(default_factory=list)
    preferences: Preferences = Field(default_factory=Preferences)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
