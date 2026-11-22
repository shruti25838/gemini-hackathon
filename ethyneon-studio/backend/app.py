from contextlib import asynccontextmanager
import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
import httpx

from backend.config import Settings
from backend.connections import GeminiConnection, ShopifyConnection
from backend.errors import ConnectionError
from backend.product_models import ProductModelConnection
from backend.room import RoomStudio
from backend.room_chat import RoomChat
from backend.voice import VoiceRelay
from backend.routes.voice import router as voice_router
from backend.scene import SceneStudio
from backend.checkout import CheckoutStudio
from backend.routes.scene import router as scene_router
from backend.routes.samples import router as samples_router
from backend.room_planner import PlacementPlanner
from backend.routes.agora import router as agora_router
from backend.routes.room import check_room_capture, get_room, get_room_plan, revise_room, router as room_router, scan_room, start_room_plan
from backend.routes.room_chat import router as room_chat_router
from backend.routes.studio import STUDIO_ASSETS, router as studio_router, studio_command
from backend.schemas import (
    CatalogProductRequest,
    CatalogSearchRequest,
    CatalogVariantRequest,
    GeminiDecisionRequest,
    GeminiInteractionRequest,
    ManagedRunRequest,
)
from backend.studio import ProductStudio


bearer = HTTPBearer(auto_error=False)


def require_backend(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    expected = request.app.state.settings.connection_token
    if not expected:
        raise ConnectionError(
            503, "connection_not_configured", "Configure ETHYNEON_CONNECTION_TOKEN on the server.",
        )
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise ConnectionError(401, "unauthorized", "An Ethyneon backend token is required.")


api = APIRouter(prefix="/api", dependencies=[Depends(require_backend)])
api.include_router(agora_router)
api.add_api_route("/studio/command", studio_command, methods=["POST"], tags=["Design Studio"])
api.add_api_route("/room/capture/check", check_room_capture, methods=["POST"], tags=["Room Studio"])
api.add_api_route("/room/scan", scan_room, methods=["POST"], tags=["Room Studio"])
api.add_api_route("/room/{room_id}", get_room, methods=["GET"], tags=["Room Studio"])
api.add_api_route("/room/{room_id}/revise", revise_room, methods=["POST"], tags=["Room Studio"])
api.add_api_route("/room/{room_id}/plan", start_room_plan, methods=["POST"], status_code=202, tags=["Room Studio"])
api.add_api_route("/room/{room_id}/plan/{plan_id}", get_room_plan, methods=["GET"], tags=["Room Studio"])


@api.get("/providers")
def providers(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "gemini": {"configured": bool(settings.gemini_api_key)},
        "agora": {"configured": bool(settings.agora_app_id and settings.agora_app_certificate)},
        "shopify_mcp": {"configured": True, "stores": list(settings.shopify_shops)},
    }


@api.post("/shopify/catalog/search")
async def search_catalog(body: CatalogSearchRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.shopify.search_catalog(body.shop_catalog())


@api.post("/shopify/catalog/product")
async def get_product(body: CatalogProductRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.shopify.get_product(body.shop_catalog())


@api.post("/shopify/catalog/variant")
async def get_variant(body: CatalogVariantRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.shopify.lookup_variant(body.variant_id, body.shop_context())


@api.post("/shopify/catalog/variant/model")
async def get_variant_model(body: CatalogVariantRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.product_models.generate(body.variant_id, body.shop_context())


@api.post("/gemini/interactions")
async def gemini_interaction(body: GeminiInteractionRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.gemini.interact(body.model_dump(exclude_none=True))


@api.get("/gemini/interactions/{interaction_id}")
async def gemini_interaction_status(interaction_id: str, request: Request) -> dict[str, Any]:
    return await request.app.state.gemini.get_interaction(interaction_id)


@api.post("/gemini/decisions")
async def gemini_decision(body: GeminiDecisionRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.gemini.decide(body.model_dump(exclude_none=True))


@api.post("/gemini/managed-runs")
async def gemini_managed_run(body: ManagedRunRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.gemini.start_managed_run(**body.model_dump())


def create_app(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), transport=transport) as http:
            app.state.gemini = GeminiConnection(http, config.gemini_api_key)
            app.state.shopify = ShopifyConnection(http, config.shopify_shops)
            app.state.product_models = ProductModelConnection(http, app.state.shopify, app.state.gemini, config)
            app.state.studio = ProductStudio(app.state.shopify, app.state.gemini, app.state.product_models)
            app.state.rooms = RoomStudio(app.state.gemini, product_models=app.state.product_models)
            app.state.room_planner = PlacementPlanner(app.state.gemini, app.state.rooms)
            app.state.scenes = SceneStudio(app.state.shopify, app.state.product_models)
            app.state.checkout = CheckoutStudio(app.state.shopify, app.state.gemini, app.state.scenes)
            app.state.room_chat = RoomChat(app.state.rooms, app.state.room_planner, app.state.shopify,
                                           app.state.checkout, app.state.scenes.path)
            app.state.voice = VoiceRelay(config, app.state.room_chat)
            yield

    app = FastAPI(title="Ethyneon Connection Layer", version="3.0.0", lifespan=lifespan)
    app.state.settings = config
    app.include_router(api)
    app.include_router(scene_router)
    app.include_router(samples_router)
    app.include_router(voice_router)
    app.include_router(room_chat_router)
    app.include_router(room_router)
    app.include_router(studio_router)
    app.mount("/studio-assets", StaticFiles(directory=STUDIO_ASSETS), name="studio-assets")

    @app.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/studio/room")

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(ConnectionError)
    async def connection_error(request: Request, exc: ConnectionError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(exc.payload, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({
            "code": "invalid_request",
            "message": "The request did not match the connection contract.",
            "retryable": False,
            "details": {"issues": [{"path": error["loc"], "type": error["type"]} for error in exc.errors()]},
        }, status_code=422)

    return app


app = create_app()
