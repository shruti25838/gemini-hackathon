"""validate_room_spec and scene patch validation. Server side, always.

A draft from a model is a proposal. Nothing reaches the database until it
passes through here, so a bad draft can never replace a good room.

This is a real validator, not a placeholder: finite dimensions, supported
room-size bounds, known wall and opening types, openings inside their wall,
and free zones inside the room.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from contracts.interfaces import FieldError, ValidationResult
from contracts.room_spec import (
    MAX_ROOM_DIMENSION_M,
    MAX_ROOM_HEIGHT_M,
    MIN_ROOM_DIMENSION_M,
    MIN_ROOM_HEIGHT_M,
    RoomSpec,
)
from contracts.scene_revision import ALLOWED_SCENE_ACTIONS, ScenePatch
from fit.geometry import point_in_polygon, room_polygon, wall_length

WALL_IDS = ("wall_n", "wall_e", "wall_s", "wall_w")


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def validate_room_spec(draft: RoomSpec) -> ValidationResult:
    """Check a RoomSpec draft. Field paths point at the offending value."""
    errors: list[FieldError] = []
    warnings: list[FieldError] = []

    dims = draft.dimensions_m
    width = dims.width_m.value_m
    depth = dims.depth_m.value_m
    height = dims.height_m.value_m

    # -- finite and in bounds --------------------------------------------
    for axis, value in (("width_m", width), ("depth_m", depth), ("height_m", height)):
        if not _finite(value):
            errors.append(
                FieldError(
                    field_path=f"dimensions_m.{axis}.value_m",
                    code="not_finite",
                    message=f"{axis} must be a finite number, got {value!r}",
                )
            )

    for axis, value in (("width_m", width), ("depth_m", depth)):
        if _finite(value) and not (MIN_ROOM_DIMENSION_M <= value <= MAX_ROOM_DIMENSION_M):
            errors.append(
                FieldError(
                    field_path=f"dimensions_m.{axis}.value_m",
                    code="out_of_supported_bounds",
                    message=(
                        f"{axis} is {value} m; supported range is "
                        f"{MIN_ROOM_DIMENSION_M} to {MAX_ROOM_DIMENSION_M} m"
                    ),
                )
            )

    if _finite(height) and not (MIN_ROOM_HEIGHT_M <= height <= MAX_ROOM_HEIGHT_M):
        errors.append(
            FieldError(
                field_path="dimensions_m.height_m.value_m",
                code="out_of_supported_bounds",
                message=(
                    f"height_m is {height} m; supported range is "
                    f"{MIN_ROOM_HEIGHT_M} to {MAX_ROOM_HEIGHT_M} m"
                ),
            )
        )

    # -- walls -------------------------------------------------------------
    wall_ids = [wall.wall_id for wall in draft.walls]
    if sorted(wall_ids) != sorted(WALL_IDS):
        errors.append(
            FieldError(
                field_path="walls",
                code="wall_set_invalid",
                message=f"expected exactly {list(WALL_IDS)}, got {wall_ids}",
            )
        )

    if errors:
        # Later checks depend on the dimensions and walls being sane.
        return ValidationResult(ok=False, errors=errors, warnings=warnings)

    for index, wall in enumerate(draft.walls):
        expected = wall_length(wall.wall_id, width, depth)
        if abs(wall.length_m - expected) > 0.01:
            warnings.append(
                FieldError(
                    field_path=f"walls.{index}.length_m",
                    code="wall_length_mismatch",
                    message=(
                        f"{wall.wall_id} is {wall.length_m} m but the room "
                        f"dimensions imply {expected} m"
                    ),
                )
            )

    # -- openings ----------------------------------------------------------
    seen_openings: set[str] = set()
    for index, opening in enumerate(draft.openings):
        path = f"openings.{index}"

        if opening.opening_id in seen_openings:
            errors.append(
                FieldError(
                    field_path=f"{path}.opening_id",
                    code="duplicate_opening_id",
                    message=f"opening_id {opening.opening_id} appears more than once",
                )
            )
        seen_openings.add(opening.opening_id)

        if opening.wall_id not in WALL_IDS:
            errors.append(
                FieldError(
                    field_path=f"{path}.wall_id",
                    code="unknown_wall",
                    message=f"{opening.wall_id} is not a known wall",
                )
            )
            continue

        available = wall_length(opening.wall_id, width, depth)
        if opening.offset_m + opening.width_m > available + 1e-6:
            errors.append(
                FieldError(
                    field_path=f"{path}.offset_m",
                    code="opening_outside_wall",
                    message=(
                        f"{opening.opening_id} runs from {opening.offset_m} m to "
                        f"{opening.offset_m + opening.width_m} m along {opening.wall_id}, "
                        f"which is only {available} m long"
                    ),
                )
            )

        if opening.height_m > height + 1e-6:
            errors.append(
                FieldError(
                    field_path=f"{path}.height_m",
                    code="opening_taller_than_room",
                    message=(
                        f"{opening.opening_id} is {opening.height_m} m tall in a "
                        f"{height} m room"
                    ),
                )
            )

        if opening.sill_m is not None and opening.sill_m + opening.height_m > height + 1e-6:
            errors.append(
                FieldError(
                    field_path=f"{path}.sill_m",
                    code="opening_above_ceiling",
                    message=f"{opening.opening_id} would extend above the ceiling",
                )
            )

        if opening.type == "door" and opening.hinge is None:
            warnings.append(
                FieldError(
                    field_path=f"{path}.hinge",
                    code="door_without_hinge",
                    message=(
                        f"{opening.opening_id} is a door with no hinge, so every "
                        f"fit check against it will return unknown"
                    ),
                )
            )

    # -- free zones --------------------------------------------------------
    bounds = room_polygon(width, depth)
    for index, zone in enumerate(draft.free_zones):
        outside = [
            (point.x_m, point.y_m)
            for point in zone.area.points
            if not point_in_polygon((point.x_m, point.y_m), bounds)
        ]
        if outside:
            errors.append(
                FieldError(
                    field_path=f"free_zones.{index}.area",
                    code="zone_outside_room",
                    message=(
                        f"{zone.zone_id} has {len(outside)} point(s) outside the "
                        f"{width} by {depth} m room, first at {outside[0]}"
                    ),
                )
            )

    # -- existing items ----------------------------------------------------
    for index, item in enumerate(draft.existing_items):
        if item.footprint is None:
            continue
        half_w = item.footprint.width_m / 2.0
        half_d = item.footprint.depth_m / 2.0
        if (
            item.footprint.x_m - half_w < -1e-6
            or item.footprint.y_m - half_d < -1e-6
            or item.footprint.x_m + half_w > width + 1e-6
            or item.footprint.y_m + half_d > depth + 1e-6
        ):
            warnings.append(
                FieldError(
                    field_path=f"existing_items.{index}.footprint",
                    code="item_outside_room",
                    message=f"{item.item_id} sits partly outside the room",
                )
            )

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def preserve_confirmed_values(previous: Optional[RoomSpec], draft: RoomSpec) -> RoomSpec:
    """A later estimate never overwrites a value the user confirmed.

    The integration doc states this as a hard rule. It matters because a new
    photo produces fresh estimates for everything, including the one wall the
    shopper already measured by hand.
    """
    if previous is None:
        return draft

    merged = draft.model_copy(deep=True)
    for axis in ("width_m", "depth_m", "height_m"):
        old = getattr(previous.dimensions_m, axis)
        new = getattr(merged.dimensions_m, axis)
        if old.evidence_status == "confirmed" and new.evidence_status != "confirmed":
            setattr(merged.dimensions_m, axis, old)

    if (
        previous.scale_reference is not None
        and previous.scale_reference.confirmed
        and (merged.scale_reference is None or not merged.scale_reference.confirmed)
    ):
        merged.scale_reference = previous.scale_reference

    previous_openings = {o.opening_id: o for o in previous.openings}
    for index, opening in enumerate(merged.openings):
        old_opening = previous_openings.get(opening.opening_id)
        if old_opening is None:
            continue
        if old_opening.evidence_status == "confirmed" and opening.evidence_status != "confirmed":
            merged.openings[index] = old_opening
        elif (
            old_opening.hinge is not None
            and old_opening.hinge.evidence_status == "confirmed"
            and (opening.hinge is None or opening.hinge.evidence_status != "confirmed")
        ):
            merged.openings[index].hinge = old_opening.hinge

    return merged


def validate_scene_patch(
    patch: ScenePatch,
    *,
    known_object_ids: set[str],
    known_variant_ids: set[str],
    known_asset_ids: set[str],
    room_width_m: float,
    room_depth_m: float,
) -> ValidationResult:
    """Scene patches are allowlisted, and every ID in them must be real.

    This is what stops a model inventing an operation, a product, or a
    position outside the room.
    """
    errors: list[FieldError] = []

    for index, action in enumerate(patch.actions):
        path = f"actions.{index}"

        if action.type not in ALLOWED_SCENE_ACTIONS:
            errors.append(
                FieldError(
                    field_path=f"{path}.type",
                    code="action_not_allowed",
                    message=f"{action.type} is not a registered scene action",
                )
            )
            continue

        payload: dict[str, Any] = action.payload or {}

        if action.type in ("move_object", "rotate_object", "remove_object", "lock_object",
                           "unlock_object", "swap_variant"):
            if not action.object_id:
                errors.append(
                    FieldError(
                        field_path=f"{path}.object_id",
                        code="object_id_required",
                        message=f"{action.type} needs an object_id",
                    )
                )
            elif action.object_id not in known_object_ids:
                errors.append(
                    FieldError(
                        field_path=f"{path}.object_id",
                        code="unknown_object",
                        message=f"{action.object_id} is not in the current scene",
                    )
                )

        if action.type in ("add_object", "swap_variant"):
            variant_id = payload.get("variant_id")
            if not variant_id:
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.variant_id",
                        code="variant_required",
                        message=(
                            f"{action.type} needs a variant_id; Shopify is the only "
                            f"product source"
                        ),
                    )
                )
            elif variant_id not in known_variant_ids:
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.variant_id",
                        code="unverified_variant",
                        message=(
                            f"{variant_id} has no verified CatalogOffer, so it cannot "
                            f"enter the scene"
                        ),
                    )
                )
            asset_id = payload.get("asset_id")
            if asset_id and asset_id not in known_asset_ids:
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.asset_id",
                        code="unknown_asset",
                        message=f"{asset_id} is not a known asset",
                    )
                )

        if action.type in ("add_object", "move_object"):
            x, y = payload.get("x_m"), payload.get("y_m")
            for name, value in (("x_m", x), ("y_m", y)):
                if value is None:
                    errors.append(
                        FieldError(
                            field_path=f"{path}.payload.{name}",
                            code="position_required",
                            message=f"{action.type} needs {name}",
                        )
                    )
                elif not _finite(value):
                    errors.append(
                        FieldError(
                            field_path=f"{path}.payload.{name}",
                            code="not_finite",
                            message=f"{name} must be a finite number, got {value!r}",
                        )
                    )
            if _finite(x) and not (0.0 <= x <= room_width_m):
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.x_m",
                        code="outside_room",
                        message=f"x_m {x} is outside the room width {room_width_m}",
                    )
                )
            if _finite(y) and not (0.0 <= y <= room_depth_m):
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.y_m",
                        code="outside_room",
                        message=f"y_m {y} is outside the room depth {room_depth_m}",
                    )
                )

        if action.type == "set_wall_color":
            if payload.get("wall_id") not in WALL_IDS:
                errors.append(
                    FieldError(
                        field_path=f"{path}.payload.wall_id",
                        code="unknown_wall",
                        message=f"{payload.get('wall_id')!r} is not a known wall",
                    )
                )

    return ValidationResult(ok=not errors, errors=errors, warnings=[])
