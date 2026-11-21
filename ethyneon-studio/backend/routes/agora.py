from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from backend.agora_tokens import issue_agora_tokens
from backend.errors import ConnectionError


router = APIRouter(tags=["Agora"])


@router.get("/agora/token")
def agora_token(
    request: Request,
    response: Response,
    channel: Annotated[str, Query(min_length=1, max_length=64)],
    uid: Annotated[int, Query(ge=1, le=2**32 - 1)],
) -> dict[str, str | int]:
    if len(channel.encode("utf-8")) > 64 or not channel.strip() or not channel.isprintable():
        raise ConnectionError(422, "invalid_agora_channel", "Provide a valid Agora channel name.")
    response.headers["Cache-Control"] = "no-store"
    return issue_agora_tokens(request.app.state.settings, channel, uid)
