"""Function signatures between lanes, as Protocol stubs.

Each Protocol is owned by its provider. Callers code against the stub and the
fixtures; the provider writes the real implementation. Nothing here imports a
lane package, so this file stays safe for everyone to read.

Three rules this file encodes:
  - Agents never write shared state. A and the models return proposals.
  - Only C commits revisions, through commit_revision.
  - Shopify is the only product source, and a CatalogOffer must exist for any
    product before it appears in a scene or a cart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Literal, Optional, Protocol, Sequence, runtime_checkable

from pydantic import Field

from contracts.activity_event import ActivityEvent
from contracts.cart_review import ApprovalRecord, CartReview
from contracts.catalog_offer import CatalogOffer
from contracts.checkout_audit import CheckoutAudit
from contracts.common import (
    Confidence,
    EthyneonModel,
    FitResultValue,
    Metres,
    Money,
    Pose,
    Rect2D,
    Revision,
    utc_now,
)
from contracts.design_brief import DesignBrief
from contracts.fit_report import FitReport
from contracts.placement_review import PlacementReview
from contracts.recovery_run import RecoveryActionName, RecoveryRun
from contracts.room_spec import RoomSpec
from contracts.scene_revision import SceneRevision, ScenePatch
from contracts.workflow_plan import WorkflowPlan

# ==========================================================================
# Supporting types that are not contracts in their own right.
# The Command shape is defined by A here and built by B.
# ==========================================================================


class Command(EthyneonModel):
    """A spoken or typed request from the shopper."""

    command_id: str
    session_id: str
    text: str
    transcript_source: Literal["voice", "typed", "mock"] = "typed"
    selected_object_id: Optional[str] = None
    frame_refs: list[str] = Field(default_factory=list)
    tap_point: Optional[Pose] = None
    room_revision: Revision
    scene_revision: Revision


class GateAnswer(EthyneonModel):
    """One Decision 1 answer. The code reads confidence; it never assumes."""

    gate: str
    question: str
    answer: str
    confidence: Confidence
    threshold: Confidence
    passed: bool
    source: Literal["live", "mock", "local_stand_in"] = "mock"


class GateAnswers(EthyneonModel):
    """The set of gate answers for one decision point."""

    gate_set: str
    answers: list[GateAnswer] = Field(default_factory=list)
    all_passed: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class RoomAnalysis(EthyneonModel):
    """What analyze_room returns: the gates plus a draft, never a committed room."""

    analysis_id: str
    session_id: str
    gates: GateAnswers
    room_spec_draft: Optional[RoomSpec] = Field(
        default=None, description="None when a gate rejected the photo."
    )
    confidence: Optional[Confidence] = None
    evidence_refs: list[str] = Field(default_factory=list)
    source: Literal["live", "mock"] = "mock"


class FieldError(EthyneonModel):
    """One field-level validation failure."""

    field_path: str
    code: str
    message: str


class ValidationResult(EthyneonModel):
    """C calls this before every commit. An invalid draft never replaces a
    valid room; the previous room is kept."""

    ok: bool
    errors: list[FieldError] = Field(default_factory=list)
    warnings: list[FieldError] = Field(default_factory=list)


class InputRequest(EthyneonModel):
    """A question back to the shopper, for an ambiguous reference or a
    missing measurement."""

    interaction_id: str
    prompt: str
    expects: Literal["measurement", "tap", "choice", "confirmation"]
    options: list[str] = Field(default_factory=list)
    frame_ref: Optional[str] = None


class CommandResult(EthyneonModel):
    """handle_command answers accepted, pending or needs_input."""

    status: Literal["accepted", "pending", "needs_input", "rejected"]
    patch: Optional[ScenePatch] = Field(
        default=None, description="Set when status is accepted. C still validates it."
    )
    plan_id: Optional[str] = Field(default=None, description="Set when status is pending.")
    input_request: Optional[InputRequest] = Field(
        default=None, description="Set when status is needs_input."
    )
    message: Optional[str] = None


class RecoveryAction(EthyneonModel):
    """One action chosen from the registered recovery list."""

    action: RecoveryActionName
    rationale: str
    parameters: dict = Field(default_factory=dict)
    chosen_by: Literal["agent", "fallback"] = "fallback"


class SearchRequirements(EthyneonModel):
    """What search_catalog filters on."""

    query: Optional[str] = None
    category: Optional[str] = None
    max_price: Optional[Money] = None
    min_price: Optional[Money] = None
    require_available: bool = True
    require_dimensions: bool = False
    max_width_m: Optional[Metres] = None
    max_depth_m: Optional[Metres] = None
    surface: Literal["storefront_api", "shop_mcp", "any"] = "any"


class ProposedPlacement(EthyneonModel):
    """The placement check_fit is asked about."""

    object_id: str
    pose: Pose
    footprint: Rect2D
    height_m: Optional[Metres] = None
    offer_ref: Optional[str] = None
    replaces_object_id: Optional[str] = None


class BudgetLine(EthyneonModel):
    variant_id: str
    quantity: int = Field(ge=1)
    unit_price: Money


class BudgetResult(EthyneonModel):
    """Exact subtotal in minor units, and whether it is within budget."""

    items_subtotal: Money
    budget: Optional[Money] = None
    within_budget: Optional[bool] = None
    note: str = (
        "Shipping and tax are added at checkout and are not included in this subtotal."
    )


class CommitConflict(EthyneonModel):
    """Returned instead of a revision when the base is stale. Carries the
    latest revision so the caller can rebase."""

    code: Literal["stale_base_revision"] = "stale_base_revision"
    message: str
    latest_revision: Revision
    retryable: bool = True


class CommitResult(EthyneonModel):
    """Either the new revision or a conflict. A repeated action_id returns the
    first result, with replayed set to True."""

    committed: bool
    kind: Literal["room", "scene"]
    revision: Optional[Revision] = None
    room_spec: Optional[RoomSpec] = None
    scene: Optional[SceneRevision] = None
    conflict: Optional[CommitConflict] = None
    validation: Optional[ValidationResult] = None
    replayed: bool = Field(
        default=False, description="True when this is the stored result of a repeated action_id."
    )


class AuditInputs(EthyneonModel):
    """The minimal evidence packet for the checkout audit.

    Deliberately excludes the buyer's contact details, credentials, the private
    checkout link and any payment data.
    """

    session_id: str
    scene_revision: Revision
    brief_revision: Revision
    approved_lines: list[BudgetLine] = Field(default_factory=list)
    merchandise_subtotal: Money
    budget: Optional[Money] = None
    hard_constraints: list[str] = Field(default_factory=list)
    fit_status: FitResultValue = "unknown"
    live_cart_lines: list[BudgetLine] = Field(default_factory=list)
    approval_fingerprint: str
    cart_fingerprint: str


# ==========================================================================
# Lane A provides these, in ai/
# ==========================================================================


@runtime_checkable
class AnalyzeRoom(Protocol):
    def __call__(self, image: bytes, session_id: str) -> RoomAnalysis: ...


@runtime_checkable
class ValidateRoomSpec(Protocol):
    def __call__(self, draft: RoomSpec) -> ValidationResult: ...


@runtime_checkable
class HandleCommand(Protocol):
    def __call__(
        self, session_id: str, command: Command, revisions: dict[str, Revision]
    ) -> CommandResult: ...


@runtime_checkable
class PlanWorkflow(Protocol):
    def __call__(
        self, brief: DesignBrief, room_spec: RoomSpec, scene: SceneRevision
    ) -> WorkflowPlan: ...


@runtime_checkable
class RunWorkflow(Protocol):
    def __call__(self, plan_id: str) -> Iterable[ActivityEvent]: ...


@runtime_checkable
class RunCheckoutGates(Protocol):
    def __call__(self, evidence_packet: AuditInputs) -> GateAnswers: ...


@runtime_checkable
class RunRecovery(Protocol):
    def __call__(self, recovery_run: RecoveryRun) -> RecoveryAction: ...


@runtime_checkable
class ReviewPlacement(Protocol):
    """Argon proposes, Carbon tests. Both return proposals only."""

    def __call__(
        self, room_spec: RoomSpec, scene: SceneRevision, placement: ProposedPlacement
    ) -> PlacementReview: ...


# ==========================================================================
# Lane C provides these, in backend/ and fit/
# ==========================================================================


@runtime_checkable
class SearchCatalog(Protocol):
    def __call__(
        self, requirements: SearchRequirements, limit: int = 10
    ) -> list[CatalogOffer]: ...


@runtime_checkable
class GetOffer(Protocol):
    """A fresh read of one variant. Never served from cache."""

    def __call__(self, variant_id: str) -> CatalogOffer: ...


@runtime_checkable
class CheckFit(Protocol):
    """Pure geometry. Runs identically on the server and in the sandboxes."""

    def __call__(
        self,
        room_spec: RoomSpec,
        scene: SceneRevision,
        placement: ProposedPlacement,
        offers: Optional[Sequence[CatalogOffer]] = None,
    ) -> FitReport: ...


@runtime_checkable
class ComputeBudget(Protocol):
    def __call__(
        self, lines: Sequence[BudgetLine], budget: Optional[Money] = None
    ) -> BudgetResult: ...


@runtime_checkable
class CommitRevision(Protocol):
    def __call__(
        self,
        kind: Literal["room", "scene"],
        patch: dict,
        base_revision: Revision,
        action_id: str,
    ) -> CommitResult: ...


@runtime_checkable
class EmitEvent(Protocol):
    def __call__(self, activity_event: ActivityEvent) -> None: ...


@runtime_checkable
class BuildAuditInputs(Protocol):
    def __call__(self, cart_review: CartReview) -> AuditInputs: ...


@runtime_checkable
class BuildCartReview(Protocol):
    def __call__(
        self, session_id: str, shopper_id: str, selections: Sequence[BudgetLine]
    ) -> CartReview: ...


@runtime_checkable
class ConfirmCart(Protocol):
    """Refreshes price and stock, records the approval, writes the Shopify cart
    once. If anything changed, the approval is voided and a new review is
    returned instead.
    """

    def __call__(
        self, review_id: str, base_revision: Revision, action_id: str
    ) -> CartReview: ...


@runtime_checkable
class ContinueCheckout(Protocol):
    """Reconciles the live cart, runs the audit, and returns the hosted
    checkout URL only when both halves pass."""

    def __call__(self, review_id: str, action_id: str) -> CheckoutAudit: ...


@runtime_checkable
class GetApproval(Protocol):
    def __call__(self, approval_id: str) -> Optional[ApprovalRecord]: ...
