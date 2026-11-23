"""Ethyneon FastAPI app. Lane C owns this file and every route except two.

Route ownership, from the team integration contract:
  C owns backend/app.py and every route except these two
  A owns backend/routes/live_ws.py
  B owns backend/routes/agora.py
C registers both. Nobody else edits them.

Every write carries base_revision and a client action_id. A stale base returns
409 with the latest revision. A repeated action_id returns the first result.
Every failure returns {code, message, retryable, details}.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from contracts.common import Money
from contracts.interfaces import BudgetLine, Command, SearchRequirements
from backend.cart import CartError
from backend.checkout import CheckoutError
from backend.config import get_settings
from backend.errors import (
    EthyneonError,
    NotImplementedYet,
    ethyneon_error_handler,
    unhandled_error_handler,
)
from backend.lane_a import (
    get_lane_a,
    install_lane_a_from_settings,
    lane_a_delegation,
    lane_a_is_stubbed,
    lane_a_name,
)
from backend.services import Services, get_services

API_VERSION = "0.2.0"
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "fixtures"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    mocks = settings.active_mocks()
    print(f"Ethyneon {API_VERSION} on {settings.host}:{settings.port}")
    print(f"  mocks on: {', '.join(mocks) if mocks else 'none'}")
    print(f"  commerce: {'live dev store' if settings.commerce_is_live else 'MOCK_COMMERCE'}")
    print(f"  shop mcp: {'enabled' if settings.shop_mcp else 'disabled'} (discovery only)")
    print("  cart writes: disabled in Phase 1")
    print(f"  lane A: {install_lane_a_from_settings(settings)}")
    yield


app = FastAPI(
    title="Ethyneon",
    version=API_VERSION,
    description="Room photo to editable 3D room to verified Shopify cart to hosted test checkout.",
    lifespan=lifespan,
)

app.add_exception_handler(EthyneonError, ethyneon_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def services() -> Services:
    return get_services()


def _redact_checkout_url(cart: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Keep the private checkout link out of the general state endpoint.

    The URL is a bearer link to a hosted checkout. /api/state is a broad
    read, so the link is withheld here and released only by
    POST /api/checkout/continue, after a passing audit bound to this exact
    approval and cart. The field stays present and null, so the contract is
    unchanged and a client can tell the difference between "no cart" and
    "cart exists, link withheld".
    """
    if not cart:
        return cart
    reference = cart.get("cart_reference")
    if isinstance(reference, dict) and reference.get("checkout_url"):
        cart = {**cart, "cart_reference": {**reference, "checkout_url": None}}
    return cart


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------


class SessionRequest(BaseModel):
    shopper_id: Optional[str] = None


class AnalyzeRequest(BaseModel):
    session_id: str
    media_ref: Optional[str] = None
    consent_given: bool = False


class RoomConfirmRequest(BaseModel):
    session_id: str
    room_spec: dict[str, Any]
    base_revision: int
    action_id: str


class CommandRequest(BaseModel):
    session_id: str
    text: str
    selected_object_id: Optional[str] = None
    base_revision: int = 0
    action_id: str


class ScenePatchRequest(BaseModel):
    session_id: str
    patch: dict[str, Any]
    base_revision: int
    action_id: str


class InputRespondRequest(BaseModel):
    session_id: str
    interaction_id: str
    value: Any = None


class SelectionItem(BaseModel):
    variant_id: str
    quantity: int = Field(default=1, ge=1)


class CartReviewRequest(BaseModel):
    session_id: str
    selections: list[SelectionItem] = Field(min_length=1)
    budget_minor: Optional[int] = None
    currency: str = "USD"


class CartConfirmRequest(BaseModel):
    session_id: str
    review_id: str
    base_revision: int
    action_id: str


class CheckoutContinueRequest(BaseModel):
    session_id: str
    review_id: str
    action_id: str
    fit_status: str = "unknown"


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@app.get("/api/health")
async def health(svc: Services = Depends(services)) -> dict[str, Any]:
    # Reads the settings this app instance is actually using, not the ambient
    # process config, so what it reports is what is really in force.
    settings = svc.settings
    return {
        "status": "ok",
        "version": API_VERSION,
        "mocks_on": settings.active_mocks(),
        "commerce": "live" if settings.commerce_is_live else "mock",
        "shop_mcp": "enabled" if settings.shop_mcp else "disabled",
        "cart_writes": "disabled",
        "lane_a": lane_a_name(),
        "lane_a_is_stub": lane_a_is_stubbed(),
        "lane_a_functions": lane_a_delegation(),
        "phase": "1 (lane C core)",
    }


# --------------------------------------------------------------------------
# Session and room
# --------------------------------------------------------------------------


@app.post("/api/session")
async def create_session(
    body: SessionRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    shopper_id = body.shopper_id or f"shopper_{uuid.uuid4().hex[:12]}"
    session = svc.db.create_session(session_id, shopper_id)
    svc.events.emit_simple(session_id, "app", "session_created", "Session started.")
    return {
        "session_id": session_id,
        "shopper_id": shopper_id,
        "room_revision": session["room_revision"],
        "scene_revision": session["scene_revision"],
        "brief_revision": session["brief_revision"],
    }


@app.post("/api/room/analyze")
async def analyze_room(
    body: AnalyzeRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    """Calls A's analyze_room. While MOCK_AI is on, serves A's fixture.

    The draft is never committed here. It goes back to the client, which
    corrects it and posts it to /api/room/confirm.
    """
    if not body.consent_given:
        raise EthyneonError(
            code="consent_required",
            message="Consent must be given before a photo is analysed.",
            retryable=False,
        )

    lane_a = get_lane_a()
    analysis = lane_a.analyze_room(b"", body.session_id)

    # The draft is validated before it goes back, but it is never committed
    # here. Only POST /api/room/confirm writes.
    validation = None
    if analysis.room_spec_draft is not None:
        validation = lane_a.validate_room_spec(analysis.room_spec_draft).model_dump(
            mode="json"
        )

    label = "mock" if lane_a_is_stubbed() else "live"
    svc.events.emit_simple(
        body.session_id,
        "ai",
        "waiting_for_measurement",
        "Draft room ready. Confirm one real measurement to set the scale.",
        source_label=label,
    )
    return {
        "analysis": analysis.model_dump(mode="json"),
        "validation": validation,
        "source_label": label,
        "lane_a_is_stub": lane_a_is_stubbed(),
    }


@app.post("/api/room/confirm")
async def confirm_room(
    body: RoomConfirmRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    result = svc.revisions.commit_revision(
        "room",
        body.room_spec,
        body.base_revision,
        body.action_id,
        session_id=body.session_id,
    )
    if result.conflict is not None:
        raise EthyneonError(
            code=result.conflict.code,
            message=result.conflict.message,
            retryable=True,
            status_code=409,
            details={"latest_revision": result.conflict.latest_revision},
        )
    if not result.committed:
        raise EthyneonError(
            code="room_invalid",
            message="The room was rejected. The previous room is still in use.",
            retryable=False,
            status_code=422,
            details={
                "errors": [e.model_dump() for e in (result.validation.errors if result.validation else [])]
            },
        )
    return result.model_dump(mode="json")


@app.get("/api/state")
async def get_state(
    session_id: str,
    since_revision: Optional[int] = Query(default=None),
    svc: Services = Depends(services),
) -> dict[str, Any]:
    """Everything a reconnecting client needs."""
    session = svc.db.get_session(session_id)
    if session is None:
        raise EthyneonError(
            code="unknown_session",
            message=f"No session {session_id}.",
            retryable=False,
            status_code=404,
        )

    room = svc.revisions.current_room(session_id)
    scene = svc.revisions.current_scene(session_id)
    cart = _redact_checkout_url(svc.db.latest_cart_review(session_id))

    return {
        "session_id": session_id,
        "room_revision": session["room_revision"],
        "scene_revision": session["scene_revision"],
        "room_spec": room.model_dump(mode="json") if room else None,
        "scene": scene.model_dump(mode="json") if scene else None,
        "cart_review": cart,
        "events": svc.db.events_since(session_id, since_revision),
        "mocks_on": get_settings().active_mocks(),
    }


# --------------------------------------------------------------------------
# Commands and scene edits
# --------------------------------------------------------------------------


@app.post("/api/command")
async def post_command(
    body: CommandRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    session = svc.db.get_session(body.session_id)
    if session is None:
        raise EthyneonError(
            code="unknown_session", message="No such session.", retryable=False, status_code=404
        )

    command = Command(
        command_id=body.action_id,
        session_id=body.session_id,
        text=body.text,
        transcript_source="typed",
        selected_object_id=body.selected_object_id,
        room_revision=session["room_revision"],
        scene_revision=session["scene_revision"],
    )
    result = get_lane_a().handle_command(
        body.session_id,
        command,
        {
            "room_revision": session["room_revision"],
            "scene_revision": session["scene_revision"],
        },
    )
    # A proposal only. C's commit_revision remains the single writer, so the
    # client posts any accepted patch to /api/scene/patch.
    return {
        "result": result.model_dump(mode="json"),
        "source_label": "mock" if lane_a_is_stubbed() else "live",
        "lane_a_is_stub": lane_a_is_stubbed(),
    }


@app.post("/api/scene/patch")
async def patch_scene(
    body: ScenePatchRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    """A manual drag or drop. check_fit runs before anything is committed."""
    room = svc.revisions.current_room(body.session_id)
    if room is None:
        raise EthyneonError(
            code="no_room_yet",
            message="Confirm the room before placing anything in it.",
            retryable=False,
        )

    # "Verified" means exactly what the shoppable rule says: this store, this
    # exact variant, available for sale. The default surface is "any", which
    # includes Shop MCP discovery results from other merchants, and those must
    # never become a scene placement that reads as purchasable.
    known_variants = {
        offer.variant_id
        for offer in svc.catalog.search_catalog(
            SearchRequirements(surface="storefront_api", require_available=True),
            limit=200,
        )
    }
    known_assets = {
        "asset_desk_rect",
        "asset_monitor_generic",
        "asset_chair_generic",
        "asset_lamp_generic",
        "asset_shelf_generic",
        "asset_rug_generic",
        "asset_box_generic",
    }

    result = svc.revisions.commit_revision(
        "scene",
        body.patch,
        body.base_revision,
        body.action_id,
        session_id=body.session_id,
        known_variant_ids=known_variants,
        known_asset_ids=known_assets,
    )

    if result.conflict is not None:
        raise EthyneonError(
            code=result.conflict.code,
            message=result.conflict.message,
            retryable=True,
            status_code=409,
            details={"latest_revision": result.conflict.latest_revision},
        )
    if not result.committed:
        raise EthyneonError(
            code="patch_rejected",
            message="The scene patch was rejected.",
            retryable=False,
            status_code=422,
            details={
                "errors": [
                    e.model_dump()
                    for e in (result.validation.errors if result.validation else [])
                ]
            },
        )
    return result.model_dump(mode="json")


@app.post("/api/input/respond")
async def respond_to_input(
    body: InputRespondRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    """Answers a paused workflow. A resumes the same run in Phase 2."""
    raise NotImplementedYet("POST /api/input/respond", "Phase 2")


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


@app.get("/api/catalog/search")
async def search_catalog_route(
    query: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    surface: str = Query(default="any", pattern="^(any|storefront_api|shop_mcp)$"),
    require_dimensions: bool = False,
    svc: Services = Depends(services),
) -> dict[str, Any]:
    requirements = SearchRequirements(
        query=query,
        require_dimensions=require_dimensions,
        surface=surface,  # type: ignore[arg-type]
    )
    offers = svc.catalog.search_catalog(requirements, limit=limit)
    return {
        "offers": [o.model_dump(mode="json") for o in offers],
        "shoppable_count": sum(1 for o in offers if o.shoppable),
        "discovery_count": sum(1 for o in offers if not o.shoppable),
        "discovery_note": (
            "Discovery results come from other merchants and can never be added "
            "to the cart."
        ),
        "degraded": svc.catalog.last_discovery_error,
    }


# --------------------------------------------------------------------------
# Cart and checkout
# --------------------------------------------------------------------------


@app.post("/api/cart/review")
async def review_cart(
    body: CartReviewRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    session = svc.db.get_session(body.session_id)
    if session is None:
        raise EthyneonError(
            code="unknown_session", message="No such session.", retryable=False, status_code=404
        )

    selections = [
        BudgetLine(
            variant_id=item.variant_id,
            quantity=item.quantity,
            # Price is read live; this is only a placeholder the server ignores.
            unit_price=Money(amount_minor=0, currency=body.currency),
        )
        for item in body.selections
    ]
    review = svc.carts.build_cart_review(
        body.session_id,
        session["shopper_id"],
        selections,
        scene_revision=session["scene_revision"],
        brief_revision=session["brief_revision"],
        budget=(
            Money(amount_minor=body.budget_minor, currency=body.currency)
            if body.budget_minor is not None
            else None
        ),
    )
    return review.model_dump(mode="json")


@app.post("/api/cart/confirm")
async def confirm_cart(
    body: CartConfirmRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    session = svc.db.get_session(body.session_id)
    if session is None:
        raise EthyneonError(
            code="unknown_session", message="No such session.", retryable=False, status_code=404
        )
    review = svc.carts.confirm_cart(
        body.review_id,
        body.base_revision,
        body.action_id,
        current_scene_revision=session["scene_revision"],
    )
    return review.model_dump(mode="json")


@app.post("/api/checkout/continue")
async def continue_checkout(
    body: CheckoutContinueRequest, svc: Services = Depends(services)
) -> dict[str, Any]:
    session = svc.db.get_session(body.session_id)
    if session is None:
        raise EthyneonError(
            code="unknown_session", message="No such session.", retryable=False, status_code=404
        )

    audit, checkout_url = svc.checkout.continue_checkout(
        body.review_id,
        body.action_id,
        current_scene_revision=session["scene_revision"],
        fit_status=body.fit_status,
    )
    return {
        "audit": audit.model_dump(mode="json"),
        "checkout_url": checkout_url,
        "payment_state": "checkout_opened" if checkout_url else "needs_review",
        "note": (
            "Opening the hosted checkout is not payment. The order is confirmed "
            "only by a read-only order lookup or a verified Shopify order event."
        ),
    }


# --------------------------------------------------------------------------
# WebSockets
# --------------------------------------------------------------------------


@app.websocket("/ws/events")
async def events_socket(websocket: WebSocket, session_id: str = "") -> None:
    """Streams ActivityEvent messages and scene-revision notices from C to B.

    Events are written to SQLite before they are pushed here, so a client that
    misses a push still finds them through GET /api/state?since_revision.
    """
    await websocket.accept()
    svc = get_services()
    queue = await svc.bus.subscribe(session_id)
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session_id,
                "message": "Connected. Reconnect with GET /api/state?since_revision after a drop.",
            }
        )
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
                continue
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        await svc.bus.unsubscribe(session_id, queue)


def register_lane_routes(application: FastAPI) -> None:
    """C registers A's and B's route files.

    A missing module is fine; a module that exists but fails to import is
    reported rather than swallowed, because a silent pass would hide a broken
    lane behind a server that looks healthy.
    """
    import importlib.util

    for module_name, owner in (("live_ws", "A"), ("agora", "B")):
        qualified = f"backend.routes.{module_name}"
        if importlib.util.find_spec(qualified) is None:
            print(f"  lane {owner} routes: {module_name}.py not present yet, skipping")
            continue
        try:
            module = importlib.import_module(qualified)
            application.include_router(module.router)
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            print(f"  lane {owner} routes: {qualified} failed to load: {exc!r}")


register_lane_routes(app)
