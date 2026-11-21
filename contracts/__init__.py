"""Shared data formats for Ethyneon.

One file per contract. These change only through a pull request that the other
two lane owners approve: adding a field needs one approval, renaming or
removing one needs both.

CONTRACT_MODELS is the registry the schema generator and the contract tests
both walk, so a new contract is wired in by adding exactly one line here.
"""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from contracts.activity_event import ActivityEvent
from contracts.cart_review import CartReview
from contracts.catalog_offer import CatalogOffer
from contracts.checkout_audit import CheckoutAudit
from contracts.design_brief import DesignBrief
from contracts.fit_report import FitReport
from contracts.placement_review import PlacementReview
from contracts.recovery_run import RecoveryRun
from contracts.room_snapshot import RoomSnapshot
from contracts.room_spec import RoomSpec
from contracts.scene_revision import SceneRevision
from contracts.workflow_plan import WorkflowPlan

# contract name -> model. The fixture file for each is fixtures/<snake>.sample.json
CONTRACT_MODELS: dict[str, Type[BaseModel]] = {
    "RoomSnapshot": RoomSnapshot,
    "RoomSpec": RoomSpec,
    "DesignBrief": DesignBrief,
    "WorkflowPlan": WorkflowPlan,
    "PlacementReview": PlacementReview,
    "SceneRevision": SceneRevision,
    "CatalogOffer": CatalogOffer,
    "FitReport": FitReport,
    "CartReview": CartReview,
    "CheckoutAudit": CheckoutAudit,
    "RecoveryRun": RecoveryRun,
    "ActivityEvent": ActivityEvent,
}

# Who owns each contract, from the team integration doc. A change outside your
# lane needs the owner's approval.
CONTRACT_OWNERS: dict[str, str] = {
    "RoomSnapshot": "B",
    "RoomSpec": "A",
    "DesignBrief": "A",
    "WorkflowPlan": "A",
    "PlacementReview": "A",
    "SceneRevision": "B",
    "CatalogOffer": "C",
    "FitReport": "C",
    "CartReview": "C",
    "CheckoutAudit": "C",
    "RecoveryRun": "A",
    "ActivityEvent": "B",
}

__all__ = [
    "CONTRACT_MODELS",
    "CONTRACT_OWNERS",
    "ActivityEvent",
    "CartReview",
    "CatalogOffer",
    "CheckoutAudit",
    "DesignBrief",
    "FitReport",
    "PlacementReview",
    "RecoveryRun",
    "RoomSnapshot",
    "RoomSpec",
    "SceneRevision",
    "WorkflowPlan",
]
