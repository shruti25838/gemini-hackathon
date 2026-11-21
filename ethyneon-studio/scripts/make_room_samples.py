"""Build multi-photo samples of plain, empty rooms from CC0 Poly Haven interior panoramas.

Each set holds 2 to 5 ordinary camera views of the same real room, taken from the panorama's viewpoint at
evenly spaced headings. Download the tonemapped JPG of each asset from https://polyhaven.com/hdris (CC0),
then run:

    .venv/bin/python scripts/make_room_samples.py <panorama_dir> samples/rooms
"""

import json
import math
from pathlib import Path
import sys

import numpy as np
from PIL import Image


WIDTH, HEIGHT = 1024, 768
FOV_DEG = 100          # roughly a phone's ultra-wide lens
PITCH_DEG = -12        # tilt down a little so the floor line is in frame
# Plain, empty rooms only: walls, floor, doors, windows, and fixed fittings, no loose furniture.
# The folder name is the shopper's intended use for the empty room.
ROOMS = [
    # folder name, Poly Haven asset, intended use, photo counts, first heading in degrees
    ("kitchen", "small_empty_house", "kitchen", (2, 3, 4, 5), 0),
    ("gaming-room", "small_empty_room_1", "gaming_room", (2, 3, 4, 5), 0),
    ("living-room", "lebombo", "living_room", (2, 3, 4, 5), 0),
    ("working-room", "unfinished_office", "office", (2, 3, 4, 5), 0),
    ("bedroom", "small_empty_room_3", "bedroom", (2, 3, 4, 5), 0),
    ("study-room", "small_empty_room_2", "office", (2, 3, 4, 5), 0),
    ("guest-room", "small_empty_room_4", "bedroom", (2, 3, 4, 5), 0),
]


def perspective(panorama: np.ndarray, yaw_deg: float) -> Image.Image:
    """Render a pinhole view of an equirectangular panorama with bilinear sampling."""
    rows, cols = panorama.shape[:2]
    focal = (WIDTH / 2) / math.tan(math.radians(FOV_DEG) / 2)
    u, v = np.meshgrid(np.arange(WIDTH) - WIDTH / 2 + 0.5, np.arange(HEIGHT) - HEIGHT / 2 + 0.5)
    rays = np.stack([u / focal, -v / focal, np.ones_like(u)], axis=-1)
    rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
    pitch, yaw = math.radians(PITCH_DEG), math.radians(yaw_deg)
    tilt = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]])
    turn = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    rays = rays @ (turn @ tilt).T
    longitude = np.arctan2(rays[..., 0], rays[..., 2])
    latitude = np.arcsin(np.clip(rays[..., 1], -1, 1))
    x = (longitude / (2 * math.pi) + 0.5) * cols - 0.5
    y = (0.5 - latitude / math.pi) * rows - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    x0, x1 = x0 % cols, (x0 + 1) % cols
    y0, y1 = np.clip(y0, 0, rows - 1), np.clip(y0 + 1, 0, rows - 1)
    top = panorama[y0, x0] * (1 - fx) + panorama[y0, x1] * fx
    bottom = panorama[y1, x0] * (1 - fx) + panorama[y1, x1] * fx
    return Image.fromarray(np.clip(top * (1 - fy) + bottom * fy, 0, 255).astype(np.uint8))


def main(panorama_dir: Path, out_dir: Path) -> None:
    manifest = []
    for name, asset, room_type, counts, first in ROOMS:
        source = panorama_dir / f"{asset}.jpg"
        image = Image.open(source).convert("RGB")
        image.thumbnail((4096, 2048))
        panorama = np.asarray(image, dtype=np.float32)
        for count in counts:
            folder = out_dir / f"{name}-{count}-photos"
            folder.mkdir(parents=True, exist_ok=True)
            headings = [(first + index * 360 / count) % 360 for index in range(count)]
            for index, heading in enumerate(headings, 1):
                perspective(panorama, heading).save(folder / f"photo-{index}.jpg", quality=84, optimize=True)
            manifest.append({
                "folder": folder.name, "intended_use": room_type, "plain_room": True, "photos": count, "headings_deg": headings,
                "fov_deg": FOV_DEG, "pitch_deg": PITCH_DEG,
                "source": f"https://polyhaven.com/a/{asset}", "license": "CC0 1.0",
            })
            print(folder.name)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
