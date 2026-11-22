from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import FileResponse

from backend.room_chat import RoomChatMove, RoomChatMessage, RoomChatReplacement, StartRoomChat
from backend.routes.studio import STUDIO_ASSETS, require_local_studio


ChatId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
router = APIRouter(prefix="/studio/room/chat", include_in_schema=False, dependencies=[Depends(require_local_studio)])


@router.get("")
def chat_page() -> FileResponse:
    return FileResponse(STUDIO_ASSETS / "room-chat.html")


@router.post("/sessions", status_code=201)
def start_chat(body: StartRoomChat, request: Request) -> dict[str, Any]:
    return request.app.state.room_chat.start(body)


@router.get("/sessions/{session_id}")
async def get_chat(session_id: ChatId, request: Request) -> dict[str, Any]:
    return await request.app.state.room_chat.get(session_id)


@router.post("/sessions/{session_id}/message")
async def send_chat(session_id: ChatId, body: RoomChatMessage, request: Request) -> dict[str, Any]:
    return await request.app.state.room_chat.message(session_id, body)


@router.post("/sessions/{session_id}/replace")
async def replace_chat_item(session_id: ChatId, body: RoomChatReplacement, request: Request) -> dict[str, Any]:
    return await request.app.state.room_chat.replace(session_id, body)


@router.post("/sessions/{session_id}/move")
async def move_chat_item(session_id: ChatId, body: RoomChatMove, request: Request) -> dict[str, Any]:
    return await request.app.state.room_chat.move(session_id, body)
