import re
import time

from agora_agent import generate_client_token

from backend.config import Settings
from backend.errors import ConnectionError


TOKEN_TTL_SECONDS = 15 * 60
AGORA_CREDENTIAL = re.compile(r"[0-9a-fA-F]{32}")


def issue_agora_tokens(settings: Settings, channel: str, uid: int) -> dict[str, str | int]:
    if not settings.agora_app_id or not settings.agora_app_certificate:
        raise ConnectionError(
            503, "agora_not_configured", "Configure AGORA_APP_ID and AGORA_APP_CERTIFICATE on the server.",
        )
    if not all(AGORA_CREDENTIAL.fullmatch(value) for value in (settings.agora_app_id, settings.agora_app_certificate)):
        raise ConnectionError(
            503, "agora_invalid_credentials", "Agora requires a 32-character App ID and App Certificate.",
        )

    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    token = generate_client_token(
        app_id=settings.agora_app_id,
        app_certificate=settings.agora_app_certificate,
        channel_name=channel,
        uid=uid,
        token_expire=TOKEN_TTL_SECONDS,
        privilege_expire=TOKEN_TTL_SECONDS,
    )
    return {
        "appId": settings.agora_app_id,
        "channel": channel,
        "uid": uid,
        "rtcToken": token,
        "rtmToken": token,
        "expireAt": expires_at,
    }
