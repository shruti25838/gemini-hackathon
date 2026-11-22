from typing import Any

from typing import Annotated

from fastapi import APIRouter, Depends, Path as PathParam, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import Field

from backend.connections import SHOPIFY_VARIANT_ID
from backend.errors import ConnectionError
from backend.room_chat import review_products
from backend.scene import scene_room
from backend.scene_edit import SceneEdit
from backend.checkout import CheckoutRequest
from backend.routes.studio import STUDIO_ASSETS, require_local_studio
from backend.schemas import RequestBody


router = APIRouter(prefix="/studio/scene", include_in_schema=False, dependencies=[Depends(require_local_studio)])


class VariantBody(RequestBody):
    variant_id: str = Field(pattern=SHOPIFY_VARIANT_ID)


class StatusBody(RequestBody):
    variant_ids: list[Annotated[str, Field(pattern=SHOPIFY_VARIANT_ID)]] = Field(min_length=1, max_length=80)


class SimilarBody(VariantBody):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=6, ge=1, le=12)
    live_only: bool = False
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)


@router.get("")
def scene_page() -> FileResponse:
    return FileResponse(STUDIO_ASSETS / "scene.html")


@router.get("/config")
def scene_config(request: Request, response: Response) -> Any:
    etag, body = request.app.state.scenes.read()
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{etag}"'
    if request.headers.get("if-none-match") == f'"{etag}"':
        return Response(status_code=304, headers={"ETag": f'"{etag}"'})
    return body


@router.post("/product")
async def scene_product(body: VariantBody, request: Request) -> dict[str, Any]:
    return await request.app.state.scenes.product(body.variant_id)


@router.post("/similar")
async def scene_similar(body: SimilarBody, request: Request) -> dict[str, Any]:
    return await request.app.state.scenes.similar(
        body.variant_id, body.query, body.limit, live_only=body.live_only, cursor=body.cursor,
    )


@router.post("/models/status")
def scene_model_status(body: StatusBody, request: Request) -> dict[str, str]:
    return request.app.state.scenes.model_states(body.variant_ids)


@router.get("/models/{name}")
def scene_model_file(name: Annotated[str, PathParam(pattern=r"^[0-9]+\.glb$")], request: Request) -> Response:
    """The 3D model for one Shopify variant number, from Codex's drop folder or the generated cache."""
    model = request.app.state.scenes.model_bytes(name.removesuffix(".glb"))
    if model is None:
        raise ConnectionError(404, "model_not_found", "No 3D model is cached for that variant yet.")
    if isinstance(model, str):
        return RedirectResponse(model)
    return Response(model, media_type="model/gltf-binary", headers={"Cache-Control": "no-cache"})


@router.post("/edit")
async def scene_edit(body: SceneEdit, request: Request) -> dict[str, Any]:
    return await request.app.state.scenes.edit(body)


@router.post("/checkout")
async def scene_checkout(body: CheckoutRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.checkout.review(body)


class LinkBody(RequestBody):
    url: str = Field(min_length=12, max_length=2000)


@router.post("/import")
async def scene_import(body: LinkBody, request: Request) -> dict[str, Any]:
    """Paste a Shopify product link; returns the product with a cached 3D model."""
    return await request.app.state.scenes.product_from_link(body.url)


@router.get("/chat/{session_id}")
async def scene_for_chat(session_id: Annotated[str, PathParam(pattern=r"^[0-9a-f]{32}$")], request: Request) -> dict[str, Any]:
    """Show the exact products that a confirmation would save."""
    state = await request.app.state.room_chat.get(session_id)
    document = dict(state["document"] or {})
    proposed = document.get("proposed_products") or []
    if "dimensions" not in document or len(document["dimensions"]) < 3:
        return {"phase": state["phase"], "scene": None}
    document["placed_products"] = review_products(document)
    scene = scene_room(document)
    proposed_ids = {item.get("id") for item in proposed}
    for item in scene["placed_products"]:
        item["proposed"] = item["id"] in proposed_ids
    scene["room_id"] = f"chat-{session_id}"
    return {"phase": state["phase"], "review_id": state.get("review_id"), "scene": scene}
