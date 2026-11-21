"""Agora token route. Lane B owns this file. C registers it.

MOCK_AGORA is the default: two browser tabs share state through the events
socket with no video, and this route says plainly that the token is a mock.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from backend.config import get_settings
from backend.errors import NotImplementedYet

router = APIRouter()


@router.get("/api/agora/token")
async def agora_token(session_id: str = "", channel: str = "") -> dict[str, Any]:
    """Returns a short-lived Agora token.

    While MOCK_AGORA is on there is no real token to hand out, and a fake one
    must never look live, so the response says mock in the label.
    """
    settings = get_settings()
    if settings.mock_agora:
        return {
            "token": None,
            "app_id": None,
            "channel": channel or "ethyneon-mock",
            "session_id": session_id,
            "expires_in": 0,
            "source_label": "mock",
            "message": "MOCK_AGORA is on. No RTC token issued; use two tabs on the events socket.",
        }
    raise NotImplementedYet("GET /api/agora/token", "Phase 3")
