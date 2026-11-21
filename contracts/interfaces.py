"""Function signatures between lanes (team integration contract). Each Protocol is owned by its
provider; callers code against these stubs and the fixtures."""
from __future__ import annotations

from typing import Literal, Optional, Protocol

from pydantic import BaseModel

from .schemas import CatalogOffer, DesignBrief, FitReport, RecoveryRun, RoomSpec, SceneRevision, WorkflowPlan


class Command(BaseModel):
    """Owner: A (defined here), built by B."""
    text: Optional[str] = None
    transcript_source: Literal["typed", "voice", "live"] = "typed"
    selected_key: Optional[str] = None
    frame_ref: Optional[str] = None
    tap: Optional[dict] = None  # {"x": metres, "z": metres}
    action_id: Optional[str] = None
    base_revision: Optional[int] = None


class AIProvider(Protocol):
    """Owner: A. Implemented in ai/__init__.py."""

    def analyze_room(self, image: bytes, session_id: str) -> "RoomAnalysis": ...  # noqa: F821
    def validate_room_spec(self, draft: dict | RoomSpec) -> "ValidationResult": ...  # noqa: F821
    def handle_command(self, session, text: Optional[str] = None, audio: Optional[bytes] = None) -> "CommandResult": ...  # noqa: F821
    def plan_workflow(self, session, request: str, route: str, brief: Optional[DesignBrief] = None) -> WorkflowPlan: ...
    def run_workflow(self, session, plan: WorkflowPlan) -> dict: ...
    def run_checkout_gates(self, evidence_packet: dict) -> "GateResult": ...  # noqa: F821
    def run_recovery(self, run: RecoveryRun) -> RecoveryRun: ...


class CommerceFitProvider(Protocol):
    """Owner: C. A's full build implements thin versions in backend/ and fit/."""

    def search_catalog(self, requirements: dict, limit: int) -> list[CatalogOffer]: ...
    def get_offer(self, variant_id: str) -> CatalogOffer: ...
    def check_fit(self, room_spec: RoomSpec, scene: SceneRevision, placement: Optional[dict] = None) -> FitReport: ...
    def compute_budget(self, lines: list[dict]) -> dict: ...
    def commit_revision(self, kind: str, patch: dict, base_revision: int, action_id: str) -> dict: ...
    def emit_event(self, activity_event: dict) -> None: ...
    def build_audit_inputs(self, cart_review: dict) -> dict: ...
