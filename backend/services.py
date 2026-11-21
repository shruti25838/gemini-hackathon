"""One place that builds and holds the backend services.

Keeps construction out of the route handlers, and gives the tests a single
seam: build_services(settings, db) with an in-memory database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from backend.cart import CartService
from backend.catalog import CatalogService
from backend.checkout import CheckoutService
from backend.config import Settings, get_settings
from backend.db import Database
from backend.decisions import DecisionsClient
from backend.events import EventBus, EventWriter
from backend.revisions import RevisionStore


@dataclass
class Services:
    settings: Settings
    db: Database
    bus: EventBus
    events: EventWriter
    revisions: RevisionStore
    catalog: CatalogService
    carts: CartService
    checkout: CheckoutService

    def close(self) -> None:
        self.db.close()


def build_services(
    settings: Optional[Settings] = None,
    db: Optional[Database] = None,
    *,
    bus: Optional[EventBus] = None,
) -> Services:
    settings = settings or get_settings()
    db = db or Database(settings.db_path)
    bus = bus or EventBus()

    events = EventWriter(db, bus)
    revisions = RevisionStore(db, events)
    catalog = CatalogService(settings, db)
    carts = CartService(
        db, catalog, events, approval_ttl_seconds=settings.approval_ttl_seconds
    )

    # Decision 1. Without a key the labelled local stand-in answers instead,
    # and every answer it produces is marked local_stand_in.
    decisions = (
        DecisionsClient(
            settings.api_key,
            model=settings.gemini_decisions_model,
            base_url=settings.gemini_decisions_base_url,
        )
        if settings.api_key
        else None
    )

    checkout = CheckoutService(
        db,
        carts,
        events,
        decisions=decisions,
        audit_ttl_seconds=settings.checkout_audit_ttl_seconds,
        use_stand_in=True,
    )

    return Services(
        settings=settings,
        db=db,
        bus=bus,
        events=events,
        revisions=revisions,
        catalog=catalog,
        carts=carts,
        checkout=checkout,
    )


_services: Optional[Services] = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = build_services()
    return _services


def reset_services() -> None:
    global _services
    if _services is not None:
        _services.close()
    _services = None
