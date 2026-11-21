"""Placeholders so Karishma's modules import. Lane A never writes state.

Out of scope for this phase: the real SessionFacade and catalog shim. These
exist only so `ai.karishma` is importable, and they refuse rather than guess.

The one rule they encode now, which is the rule that matters: `commit_scene`
and `set_room` return a proposal and write nothing. Only our
`backend.revisions.commit_revision` writes shared state.
"""

from __future__ import annotations

from typing import Any, Optional


class LaneANotWired(NotImplementedError):
    """Reached a part of her code the adapter has not connected yet."""


class _Shop:
    """Stands in for her `backend.shop`. Our CatalogService replaces it later."""

    @staticmethod
    def fetch_catalog(*_args: Any, **_kwargs: Any):
        raise LaneANotWired(
            "backend.shop.fetch_catalog is not wired. Use our "
            "CatalogService.search_catalog, which carries store, shoppable and "
            "dimension_source that her CatalogOffer has no field for."
        )

    @staticmethod
    def live() -> bool:
        return False

    @staticmethod
    def fixture_mode() -> bool:
        return True


shop = _Shop()


class Session:
    """Read-mostly stand-in. Writes are refused, not silently dropped.

    A no-op `commit_scene` would be worse than an error: her workflow would
    believe the scene had been committed and carry on from a state that does
    not exist.
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.room = None
        self.brief = None
        self.offers = []
        self.scenes = []
        self.notes = []
        self.recovery_runs = []
        self.pending_input = None
        self.plan = None
        self.fit = None
        self.room_revision = 0
        self.scale_reference = None
        self.openings = []

    @property
    def scene(self):
        return None

    def emit(self, step: str, status: str, detail: str = "", **data: Any):
        raise LaneANotWired("event emission is not wired; use our EventWriter")

    def commit_scene(self, scene: Any, base_rev: Optional[int] = None):
        """Lane A proposes. It does not commit."""
        raise LaneANotWired(
            "lane A does not write state. Return the scene as a proposal and "
            "let our commit_revision write it."
        )

    def set_room(self, room: Any):
        """Lane A proposes. It does not commit."""
        raise LaneANotWired(
            "lane A does not write state. Return the RoomSpec as a draft and "
            "let our commit_revision write it."
        )


class StaleRevision(RuntimeError):
    """Her name for our 409."""
