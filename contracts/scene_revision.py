"""SceneRevision: the committed 3D scene. Owner B; A and B propose, C commits.

Revisions only increase. Undo creates a new revision that copies an older one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from contracts.common import (
    ColorSpec,
    EthyneonModel,
    FitResultValue,
    Metres,
    Pose,
    Rect2D,
    Revision,
    SourceLabel,
    utc_now,
)

# Only these action types may appear in a scene patch. commit_revision rejects
# anything else, so a model cannot invent an operation.
ALLOWED_SCENE_ACTIONS: frozenset[str] = frozenset(
    {
        "add_object",
        "move_object",
        "rotate_object",
        "remove_object",
        "swap_variant",
        "lock_object",
        "unlock_object",
        "set_material",
        "set_light",
        "apply_room_spec",
        "set_wall_color",
        "set_room_dimension",
        "set_opening",
    }
)

SceneActionType = Literal[
    "add_object",
    "move_object",
    "rotate_object",
    "remove_object",
    "swap_variant",
    "lock_object",
    "unlock_object",
    "set_material",
    "set_light",
    "apply_room_spec",
    "set_wall_color",
    "set_room_dimension",
    "set_opening",
]


class VariantSelection(EthyneonModel):
    """The exact Shopify variant behind a placed object. Product and variant
    IDs stay separate, and offer_ref points at the verified CatalogOffer."""

    product_id: str
    variant_id: str
    store_domain: str
    offer_ref: str = Field(description="offer_id of the verified CatalogOffer.")


class ObjectPlacement(EthyneonModel):
    """One object standing on the floor of the room."""

    object_id: str
    label: str
    pose: Pose
    footprint: Rect2D
    height_m: Optional[Metres] = None
    locked: bool = False
    variant: Optional[VariantSelection] = Field(
        default=None,
        description="None for the shopper's own furniture. Required for anything shoppable.",
    )
    asset_id: Optional[str] = Field(
        default=None, description="Stand-in 3D asset used to represent the product."
    )
    asset_is_representative: bool = Field(
        default=True,
        description="Labelled on screen: the geometry stands in for the real product.",
    )
    fit_result: FitResultValue = "unknown"
    fit_report_ref: Optional[str] = None


class MaterialState(EthyneonModel):
    wall_colors: dict[str, ColorSpec] = Field(default_factory=dict)
    floor_color: Optional[ColorSpec] = None
    overrides: dict[str, str] = Field(default_factory=dict)


class LightState(EthyneonModel):
    preset: Literal["daylight", "evening", "rgb", "neutral"] = "daylight"
    intensity: float = Field(default=1.0, ge=0.0, le=4.0)
    color_temp_k: Optional[int] = Field(default=None, ge=1000, le=12000)


class SceneAction(EthyneonModel):
    """One entry in a scene patch."""

    type: SceneActionType
    object_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)


class ScenePatch(EthyneonModel):
    """What a lane proposes. Only C's commit_revision turns this into a
    SceneRevision, after validating every action against the allowlist, the
    known product and asset IDs, and the room bounds."""

    actions: list[SceneAction] = Field(min_length=1)
    base_revision: Revision
    action_id: str = Field(description="Client-made UUID. A repeat returns the first result.")
    origin: Literal["user", "agent", "recovery"] = "user"


class SceneRevision(EthyneonModel):
    scene_revision: Revision
    parent_revision: Optional[Revision] = Field(
        default=None, description="None only for the first revision of a session."
    )
    session_id: str
    room_revision: Revision
    placements: list[ObjectPlacement] = Field(default_factory=list)
    material_state: MaterialState = Field(default_factory=MaterialState)
    light_state: LightState = Field(default_factory=LightState)
    selected_variants: list[VariantSelection] = Field(default_factory=list)
    fit_status: FitResultValue = "unknown"
    action_id: Optional[str] = None
    undo_of: Optional[Revision] = Field(
        default=None, description="Set when this revision restores an older one."
    )
    source: SourceLabel = "generated"
    created_at: datetime = Field(default_factory=utc_now)
