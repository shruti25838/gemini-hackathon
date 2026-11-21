"""Ethyneon FastAPI app. Lane C owns this file and every route except two.

Phase 0 declares every route from the team integration contract so the other
lanes can wire against real paths immediately. Each one answers 501 with the
standard error envelope until Phase 1 implements it, rather than returning a
plausible fake.

Route ownership, from the integration doc:
  C owns backend/app.py and every route except these two
  A owns backend/routes/live_ws.py
  B owns backend/routes/agora.py
C registers both. Nobody else edits them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.errors import (
    EthyneonError,
    NotImplementedYet,
    ethyneon_error_handler,
    unhandled_error_handler,
)

API_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    mocks = settings.active_mocks()
    print(f"Ethyneon {API_VERSION} starting on {settings.host}:{settings.port}")
    print(f"  mocks on: {', '.join(mocks) if mocks else 'none'}")
    print(f"  commerce: {'live dev store' if settings.commerce_is_live else 'MOCK_COMMERCE'}")
    print(f"  shop mcp: {'enabled' if settings.shop_mcp else 'disabled'} (discovery only)")
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


# --------------------------------------------------------------------------
# Health and status. Implemented now, because the other lanes need it to
# confirm they are talking to the right server.
# --------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": API_VERSION,
        "mocks_on": settings.active_mocks(),
        "commerce": "live" if settings.commerce_is_live else "mock",
        "shop_mcp": "enabled" if settings.shop_mcp else "disabled",
        "phase": "0 (scaffold and contracts)",
    }


# --------------------------------------------------------------------------
# Session and room
# --------------------------------------------------------------------------


@app.post("/api/session")
async def create_session() -> dict[str, Any]:
    """Creates a session and returns session_id and current revisions."""
    raise NotImplementedYet("POST /api/session", "Phase 1")


@app.post("/api/room/analyze")
async def analyze_room() -> dict[str, Any]:
    """Takes the cleaned photo, calls A's analyze_room, returns gate results,
    a RoomSpec draft and its validation."""
    raise NotImplementedYet("POST /api/room/analyze", "Phase 1")


@app.post("/api/room/confirm")
async def confirm_room() -> dict[str, Any]:
    """Corrections plus the confirmed measurement. Validates and commits a new
    room_revision."""
    raise NotImplementedYet("POST /api/room/confirm", "Phase 1")


@app.get("/api/state")
async def get_state(since_revision: Optional[int] = None) -> dict[str, Any]:
    """Current RoomSpec, SceneRevision, CartReview and recent events. Clients
    call this after a dropped socket."""
    raise NotImplementedYet("GET /api/state", "Phase 1")


# --------------------------------------------------------------------------
# Commands and scene edits
# --------------------------------------------------------------------------


@app.post("/api/command")
async def post_command() -> dict[str, Any]:
    """A spoken or typed request. A's handle_command answers accepted, pending
    or needs_input."""
    raise NotImplementedYet("POST /api/command", "Phase 1")


@app.post("/api/scene/patch")
async def patch_scene() -> dict[str, Any]:
    """A manual drag or drop. C runs check_fit and commits or rejects."""
    raise NotImplementedYet("POST /api/scene/patch", "Phase 1")


@app.post("/api/input/respond")
async def respond_to_input() -> dict[str, Any]:
    """Answers a paused workflow. A resumes the same run."""
    raise NotImplementedYet("POST /api/input/respond", "Phase 1")


# --------------------------------------------------------------------------
# Cart and checkout
# --------------------------------------------------------------------------


@app.post("/api/cart/review")
async def review_cart() -> dict[str, Any]:
    """Builds a CartReview from the selected items."""
    raise NotImplementedYet("POST /api/cart/review", "Phase 1")


@app.post("/api/cart/confirm")
async def confirm_cart() -> dict[str, Any]:
    """Records the approval and writes the Shopify cart once."""
    raise NotImplementedYet("POST /api/cart/confirm", "Phase 1")


@app.post("/api/checkout/continue")
async def continue_checkout() -> dict[str, Any]:
    """Reconciles the live cart, runs the Decision 1 audit, and returns the
    hosted checkout URL only when both halves pass."""
    raise NotImplementedYet("POST /api/checkout/continue", "Phase 1")


# --------------------------------------------------------------------------
# Catalog. Discovery and the purchasable store are deliberately separate.
# --------------------------------------------------------------------------


@app.get("/api/catalog/search")
async def search_catalog_route() -> dict[str, Any]:
    """Searches the dev store, and Shop MCP for labelled discovery results.
    A discovery result is never shoppable."""
    raise NotImplementedYet("GET /api/catalog/search", "Phase 1")


# --------------------------------------------------------------------------
# WebSockets. /ws/events is C's. /ws/live is A's, registered here.
# --------------------------------------------------------------------------


@app.websocket("/ws/events")
async def events_socket(websocket: WebSocket, session_id: str = "") -> None:
    """Streams ActivityEvent messages and scene-revision notices from C to B.

    Phase 0 accepts the connection and says so honestly, so B can build the
    reconnect path now. Phase 1 attaches the real event bus.
    """
    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "type": "scaffold_notice",
                "message": (
                    "/ws/events is scaffolded. The event bus lands in Phase 1. "
                    "Reconnect with GET /api/state?since_revision after a drop."
                ),
                "session_id": session_id,
                "source_label": "mock",
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return


def register_lane_routes(application: FastAPI) -> None:
    """C registers A's and B's route files.

    They are optional until those lanes land, so a missing module is not a
    startup failure. A module that exists but fails to import is a different
    thing entirely, and it is reported rather than swallowed: a silent pass
    here would hide a broken lane behind a server that looks healthy.
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
