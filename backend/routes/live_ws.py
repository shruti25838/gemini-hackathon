"""Live voice socket. Lane A owns this file. C registers it.

/ws/live?session_id= carries live audio between the browser and A's ADK Live
session, plus typed commands back. It is the one lane boundary that does not
pass through C's REST routes.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/live")
async def live_socket(websocket: WebSocket, session_id: str = "") -> None:
    """Scaffold. The ADK Live session lands in Phase 2."""
    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "type": "scaffold_notice",
                "message": "/ws/live is scaffolded. ADK Live lands in Phase 2.",
                "session_id": session_id,
                "source_label": "mock",
            }
        )
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
