import re
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, WebSocket
from fastapi.responses import FileResponse

from backend.agora_tokens import issue_agora_tokens
from backend.routes.studio import STUDIO_ASSETS, require_local_studio


router = APIRouter(prefix="/studio/voice", include_in_schema=False)
LOOPBACK = {"127.0.0.1", "::1"}


@router.get("", dependencies=[Depends(require_local_studio)])
def voice_page() -> FileResponse:
    return FileResponse(STUDIO_ASSETS / "voice.html")


@router.get("/agora", dependencies=[Depends(require_local_studio)])
def voice_agora_token(request: Request, channel: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")]) -> dict[str, Any]:
    """RTC token for this device to publish its camera and microphone to the room's Agora channel."""
    return issue_agora_tokens(request.app.state.settings, channel, secrets.randbelow(2**31 - 2) + 1)


@router.websocket("/live")
async def voice_live(websocket: WebSocket, room_id: str | None = None) -> None:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    local_origin = origin is None or re.fullmatch(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?", origin)
    if (websocket.client.host if websocket.client else "") not in LOOPBACK or not local_origin or \
            not re.fullmatch(r"(localhost|127\.0\.0\.1|\[::1\])(:\d+)?", host):
        await websocket.close(code=4403)
        return
    if room_id is not None and not re.fullmatch(r"[0-9a-f]{32}", room_id):
        room_id = None
    await websocket.accept()
    await websocket.app.state.voice.run(websocket, room_id)
    try:
        await websocket.close()
    except RuntimeError:
        pass
