import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path as PathParam
from fastapi.responses import FileResponse

from backend.errors import ConnectionError
from backend.routes.studio import require_local_studio


ROOMS = Path(__file__).resolve().parents[2] / "samples" / "rooms"
router = APIRouter(prefix="/studio/samples", include_in_schema=False, dependencies=[Depends(require_local_studio)])


@router.get("/rooms")
def sample_rooms() -> list[dict[str, Any]]:
    """Full-coverage sample room sets for the demo video stream."""
    try:
        manifest = json.loads((ROOMS / "manifest.json").read_text())
    except (FileNotFoundError, ValueError):
        return []
    return [{"folder": entry["folder"], "photos": sorted(path.name for path in (ROOMS / entry["folder"]).glob("*.jpg"))}
            for entry in manifest if (ROOMS / entry["folder"]).is_dir()]


@router.get("/rooms/{folder}/{name}")
def sample_photo(folder: Annotated[str, PathParam(pattern=r"^[a-z0-9-]+$")],
                 name: Annotated[str, PathParam(pattern=r"^photo-\d\.jpg$")]) -> FileResponse:
    path = ROOMS / folder / name
    if not path.is_file():
        raise ConnectionError(404, "sample_not_found", "That sample photo does not exist.")
    return FileResponse(path, media_type="image/jpeg")
