from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path as PathParam, Request
from fastapi.responses import FileResponse

from backend.room import RoomCaptureRequest, RoomRevisionRequest, RoomScanRequest
from backend.room_capture import CaptureReview
from backend.room_planner import PlanRequest, PlanReview
from backend.routes.studio import STUDIO_ASSETS, require_local_studio


RoomId = Annotated[str, PathParam(pattern=r"^[0-9a-f]{32}$")]
router = APIRouter(prefix="/studio/room", include_in_schema=False, dependencies=[Depends(require_local_studio)])


@router.get("")
def room_page() -> FileResponse:
    return FileResponse(STUDIO_ASSETS / "room.html")


async def scan_room(body: RoomScanRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.rooms.scan(body)


async def check_room_capture(body: RoomCaptureRequest, request: Request) -> CaptureReview:
    return await request.app.state.rooms.check_capture(body)


def get_room(room_id: RoomId, request: Request) -> dict[str, Any]:
    return request.app.state.rooms.get(room_id)


async def revise_room(room_id: RoomId, body: RoomRevisionRequest, request: Request) -> dict[str, Any]:
    return await request.app.state.rooms.revise(room_id, body)


async def start_room_plan(room_id: RoomId, body: PlanRequest, request: Request) -> PlanReview:
    return await request.app.state.room_planner.start(room_id, body)


async def get_room_plan(room_id: RoomId, plan_id: RoomId, request: Request) -> PlanReview:
    return await request.app.state.room_planner.get(room_id, plan_id)


# Same handlers on the loopback studio router and, in app.py, on the bearer-protected /api router.
router.add_api_route("/capture/check", check_room_capture, methods=["POST"])
router.add_api_route("/scan", scan_room, methods=["POST"])
router.add_api_route("/{room_id}", get_room, methods=["GET"])
router.add_api_route("/{room_id}/revise", revise_room, methods=["POST"])
router.add_api_route("/{room_id}/plan", start_room_plan, methods=["POST"], status_code=202)
router.add_api_route("/{room_id}/plan/{plan_id}", get_room_plan, methods=["GET"])
