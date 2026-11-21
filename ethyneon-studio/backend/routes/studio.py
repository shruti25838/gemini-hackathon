from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from backend.errors import ConnectionError
from backend.studio import StudioCommand


STUDIO_ASSETS = Path(__file__).resolve().parents[1] / "static"
router = APIRouter(prefix="/studio", include_in_schema=False)


def require_local_studio(request: Request) -> None:
    peer = request.client.host if request.client else ""
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.url.netloc}"
    if (
        peer not in {"127.0.0.1", "::1"}
        or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
        or (origin and origin != expected_origin)
    ):
        raise ConnectionError(403, "studio_local_only", "Open Product Studio from this computer.")


@router.get("", dependencies=[Depends(require_local_studio)])
def studio_page() -> FileResponse:
    return FileResponse(STUDIO_ASSETS / "studio.html")


@router.post("/command", dependencies=[Depends(require_local_studio)])
async def studio_command(body: StudioCommand, request: Request) -> dict[str, Any]:
    return await request.app.state.studio.run(body)
