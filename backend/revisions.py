"""commit_revision: the only writer of shared state.

Three guarantees, in the order they are checked:

  1. A repeated action_id returns the first result, unchanged. The client may
     retry a request as often as it likes without applying it twice.
  2. A stale base_revision is refused with 409 and the latest revision, so an
     old edit can never overwrite a newer one.
  3. Everything is validated server side before it is written. An invalid
     RoomSpec never replaces a valid one; the previous room survives intact.

Revisions only increase. Undo does not rewind the counter: it writes a new
revision whose contents are copied from an older one.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from contracts.interfaces import CommitConflict, CommitResult, ValidationResult
from contracts.room_spec import RoomSpec
from contracts.scene_revision import ScenePatch, SceneRevision
from backend.db import Database
from backend.errors import EthyneonError
from backend.events import EventWriter
from backend.validation import (
    preserve_confirmed_values,
    validate_room_spec,
    validate_scene_patch,
)

Kind = Literal["room", "scene"]


class RevisionStore:
    def __init__(self, db: Database, events: EventWriter) -> None:
        self.db = db
        self.events = events

    # -- reads ------------------------------------------------------------

    def current_room(self, session_id: str) -> Optional[RoomSpec]:
        payload = self.db.get_revision("room", session_id)
        return RoomSpec.model_validate(payload) if payload else None

    def current_scene(self, session_id: str) -> Optional[SceneRevision]:
        payload = self.db.get_revision("scene", session_id)
        return SceneRevision.model_validate(payload) if payload else None

    def room_at(self, session_id: str, revision: int) -> Optional[RoomSpec]:
        payload = self.db.get_revision("room", session_id, revision)
        return RoomSpec.model_validate(payload) if payload else None

    def scene_at(self, session_id: str, revision: int) -> Optional[SceneRevision]:
        payload = self.db.get_revision("scene", session_id, revision)
        return SceneRevision.model_validate(payload) if payload else None

    # -- the write path ---------------------------------------------------

    def commit_revision(
        self,
        kind: Kind,
        patch: dict[str, Any],
        base_revision: int,
        action_id: str,
        *,
        session_id: str,
        known_variant_ids: Optional[set[str]] = None,
        known_asset_ids: Optional[set[str]] = None,
    ) -> CommitResult:
        if not action_id:
            raise EthyneonError(
                code="action_id_required",
                message="Every write carries a client-made action_id.",
                retryable=False,
            )

        # 1. Idempotency. A repeat returns the first result.
        stored = self.db.get_action(action_id)
        if stored is not None:
            result = CommitResult.model_validate(stored)
            result.replayed = True
            return result

        # 2. Staleness.
        current = self.db.current_revision(session_id, kind)
        if base_revision != current:
            conflict = CommitConflict(
                message=(
                    f"base_revision {base_revision} is behind the current "
                    f"{kind} revision {current}"
                ),
                latest_revision=current,
            )
            # A conflict is not recorded against the action_id: the client is
            # expected to rebase and retry with the same intent.
            return CommitResult(committed=False, kind=kind, conflict=conflict)

        # 3. Validation, then write.
        if kind == "room":
            result = self._commit_room(patch, base_revision, action_id, session_id)
        else:
            result = self._commit_scene(
                patch,
                base_revision,
                action_id,
                session_id,
                known_variant_ids or set(),
                known_asset_ids or set(),
            )

        if result.committed:
            self.db.record_action(action_id, session_id, kind, result.model_dump(mode="json"))
        return result

    # -- room -------------------------------------------------------------

    def _commit_room(
        self, patch: dict[str, Any], base_revision: int, action_id: str, session_id: str
    ) -> CommitResult:
        previous = self.current_room(session_id)
        next_revision = base_revision + 1

        try:
            candidate = RoomSpec.model_validate(
                {**patch, "room_revision": next_revision, "session_id": session_id}
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a field error
            return CommitResult(
                committed=False,
                kind="room",
                validation=ValidationResult(
                    ok=False,
                    errors=[
                        {
                            "field_path": "room_spec",
                            "code": "schema_invalid",
                            "message": str(exc)[:500],
                        }
                    ],
                ),
            )

        candidate = preserve_confirmed_values(previous, candidate)

        validation = validate_room_spec(candidate)
        if not validation.ok:
            # The previous room is untouched. That is the whole point.
            self.events.emit_simple(
                session_id,
                "room",
                "waiting_for_measurement",
                "The proposed room was rejected; the last valid room is still in use.",
                payload={"errors": [e.model_dump() for e in validation.errors]},
                room_revision=previous.room_revision if previous else None,
            )
            return CommitResult(committed=False, kind="room", validation=validation)

        self.db.insert_revision(
            "room",
            session_id,
            next_revision,
            candidate.model_dump(mode="json"),
            action_id,
            base_revision if previous else None,
        )
        self.events.emit_simple(
            session_id,
            "room",
            "room_committed",
            f"Room revision {next_revision} committed.",
            room_revision=next_revision,
        )
        return CommitResult(
            committed=True,
            kind="room",
            revision=next_revision,
            room_spec=candidate,
            validation=validation,
        )

    # -- scene ------------------------------------------------------------

    def _commit_scene(
        self,
        patch: dict[str, Any],
        base_revision: int,
        action_id: str,
        session_id: str,
        known_variant_ids: set[str],
        known_asset_ids: set[str],
    ) -> CommitResult:
        room = self.current_room(session_id)
        if room is None:
            raise EthyneonError(
                code="no_room_yet",
                message="A scene cannot be committed before a room exists.",
                retryable=False,
            )

        previous = self.current_scene(session_id)
        base_scene = previous or SceneRevision(
            scene_revision=0, session_id=session_id, room_revision=room.room_revision
        )

        try:
            scene_patch = ScenePatch.model_validate(
                {**patch, "base_revision": base_revision, "action_id": action_id}
            )
        except Exception as exc:  # noqa: BLE001
            return CommitResult(
                committed=False,
                kind="scene",
                validation=ValidationResult(
                    ok=False,
                    errors=[
                        {
                            "field_path": "patch",
                            "code": "schema_invalid",
                            "message": str(exc)[:500],
                        }
                    ],
                ),
            )

        validation = validate_scene_patch(
            scene_patch,
            known_object_ids={p.object_id for p in base_scene.placements},
            known_variant_ids=known_variant_ids,
            known_asset_ids=known_asset_ids,
            room_width_m=room.dimensions_m.width_m.value_m,
            room_depth_m=room.dimensions_m.depth_m.value_m,
        )
        if not validation.ok:
            return CommitResult(committed=False, kind="scene", validation=validation)

        next_revision = base_revision + 1
        updated = base_scene.model_copy(deep=True)
        updated.scene_revision = next_revision
        updated.parent_revision = base_scene.scene_revision if previous else None
        updated.room_revision = room.room_revision
        updated.action_id = action_id
        updated.undo_of = None

        for action in scene_patch.actions:
            self._apply_action(updated, action)

        self.db.insert_revision(
            "scene",
            session_id,
            next_revision,
            updated.model_dump(mode="json"),
            action_id,
            base_scene.scene_revision if previous else None,
        )
        self.events.emit_simple(
            session_id,
            "app",
            "scene_committed",
            f"Scene revision {next_revision} committed.",
            payload={"actions": [a.type for a in scene_patch.actions]},
            room_revision=room.room_revision,
            scene_revision=next_revision,
        )
        return CommitResult(
            committed=True,
            kind="scene",
            revision=next_revision,
            scene=updated,
            validation=validation,
        )

    @staticmethod
    def _apply_action(scene: SceneRevision, action: Any) -> None:
        from contracts.common import ColorSpec
        from contracts.scene_revision import ObjectPlacement, VariantSelection

        payload = action.payload or {}
        by_id = {p.object_id: p for p in scene.placements}

        if action.type == "add_object":
            from contracts.common import Pose, Rect2D

            scene.placements.append(
                ObjectPlacement(
                    object_id=payload["object_id"],
                    label=payload.get("label", payload["object_id"]),
                    pose=Pose(
                        x_m=payload["x_m"],
                        y_m=payload["y_m"],
                        rotation_deg=payload.get("rotation_deg", 0.0),
                    ),
                    footprint=Rect2D(
                        x_m=payload["x_m"],
                        y_m=payload["y_m"],
                        width_m=payload.get("width_m", 0.0),
                        depth_m=payload.get("depth_m", 0.0),
                        rotation_deg=payload.get("rotation_deg", 0.0),
                    ),
                    height_m=payload.get("height_m"),
                    variant=VariantSelection(
                        product_id=payload["product_id"],
                        variant_id=payload["variant_id"],
                        store_domain=payload["store_domain"],
                        offer_ref=payload["offer_ref"],
                    ),
                    asset_id=payload.get("asset_id"),
                    fit_result=payload.get("fit_result", "unknown"),
                    fit_report_ref=payload.get("fit_report_ref"),
                )
            )

        elif action.type == "move_object":
            placement = by_id[action.object_id]
            placement.pose.x_m = payload["x_m"]
            placement.pose.y_m = payload["y_m"]
            placement.footprint.x_m = payload["x_m"]
            placement.footprint.y_m = payload["y_m"]
            if "fit_result" in payload:
                placement.fit_result = payload["fit_result"]
            if "fit_report_ref" in payload:
                placement.fit_report_ref = payload["fit_report_ref"]

        elif action.type == "rotate_object":
            placement = by_id[action.object_id]
            placement.pose.rotation_deg = payload["rotation_deg"]
            placement.footprint.rotation_deg = payload["rotation_deg"]

        elif action.type == "remove_object":
            scene.placements = [
                p for p in scene.placements if p.object_id != action.object_id
            ]

        elif action.type == "swap_variant":
            placement = by_id[action.object_id]
            placement.variant = VariantSelection(
                product_id=payload["product_id"],
                variant_id=payload["variant_id"],
                store_domain=payload["store_domain"],
                offer_ref=payload["offer_ref"],
            )
            if "width_m" in payload:
                placement.footprint.width_m = payload["width_m"]
            if "depth_m" in payload:
                placement.footprint.depth_m = payload["depth_m"]
            placement.fit_result = payload.get("fit_result", "unknown")

        elif action.type in ("lock_object", "unlock_object"):
            by_id[action.object_id].locked = action.type == "lock_object"

        elif action.type == "set_wall_color":
            scene.material_state.wall_colors[payload["wall_id"]] = ColorSpec(
                hex=payload["hex"],
                evidence_status=payload.get("evidence_status", "confirmed"),
                source=payload.get("source", "generated"),
            )

        elif action.type == "set_light":
            if "preset" in payload:
                scene.light_state.preset = payload["preset"]
            if "intensity" in payload:
                scene.light_state.intensity = payload["intensity"]
            if "color_temp_k" in payload:
                scene.light_state.color_temp_k = payload["color_temp_k"]

        elif action.type == "set_material":
            scene.material_state.overrides[payload["target"]] = payload["material"]

        # Keep the denormalised variant list in step with the placements.
        scene.selected_variants = [p.variant for p in scene.placements if p.variant]

    # -- undo -------------------------------------------------------------

    def undo_scene(
        self, session_id: str, target_revision: int, action_id: str
    ) -> CommitResult:
        """Restore an older scene as a brand new revision.

        The counter never goes backwards, so a client that saw revision 9 can
        never be confused by a different revision 9 appearing later.
        """
        stored = self.db.get_action(action_id)
        if stored is not None:
            result = CommitResult.model_validate(stored)
            result.replayed = True
            return result

        target = self.scene_at(session_id, target_revision)
        if target is None:
            raise EthyneonError(
                code="unknown_revision",
                message=f"Scene revision {target_revision} does not exist.",
                retryable=False,
            )

        current = self.db.current_revision(session_id, "scene")
        next_revision = current + 1

        restored = target.model_copy(deep=True)
        restored.scene_revision = next_revision
        restored.parent_revision = current
        restored.undo_of = target_revision
        restored.action_id = action_id

        self.db.insert_revision(
            "scene",
            session_id,
            next_revision,
            restored.model_dump(mode="json"),
            action_id,
            current,
            undo_of=target_revision,
        )
        self.events.emit_simple(
            session_id,
            "app",
            "scene_committed",
            f"Undo: revision {next_revision} restores revision {target_revision}.",
            payload={"undo_of": target_revision},
            scene_revision=next_revision,
        )
        result = CommitResult(
            committed=True, kind="scene", revision=next_revision, scene=restored
        )
        self.db.record_action(action_id, session_id, "scene_undo", result.model_dump(mode="json"))
        return result
