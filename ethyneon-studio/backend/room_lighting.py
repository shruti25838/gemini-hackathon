from math import dist, radians, cos, sin
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.room import RoomSpec


def scene_geometry(spec: "RoomSpec", room: dict) -> None:
    width, depth, height = (spec.dimensions[axis].value_m for axis in ("width", "depth", "height"))
    frames = {
        "wall_n": ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
        "wall_s": ((0.0, depth), (1.0, 0.0), (0.0, -1.0)),
        "wall_w": ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0)),
        "wall_e": ((width, 0.0), (0.0, 1.0), (-1.0, 0.0)),
    }

    def point(wall_id, offset, y):
        start, along, _ = frames[wall_id]
        return {"x": start[0] + along[0] * offset, "y": y, "z": start[1] + along[1] * offset}

    for wall in room["walls"]:
        wall["start"] = point(wall["id"], 0, 0)
        wall["end"] = point(wall["id"], wall["length_m"], 0)
        wall["height_m"] = height

    windows = []
    for opening in room["openings"]:
        position = point(opening["wall"], opening["offset_m"] + opening["width_m"] / 2, opening["sill_m"])
        opening["position"] = position
        if opening["type"] == "window":
            center = {**position, "y": position["y"] + opening["height_m"] / 2}
            windows.append({"opening_id": opening["id"], "wall": opening["wall"], "center": center,
                            "area_m2": round(opening["width_m"] * opening["height_m"], 4)})

    def exposure(wall_id, location):
        if not windows:
            return None
        nx, nz = frames[wall_id][2]
        strength = 0.0
        for window in windows:
            center = window["center"]
            dx, dz = location["x"] - center["x"], location["z"] - center["z"]
            distance = max(dist(tuple(location.values()), tuple(center.values())), 0.1)
            wx, wz = frames[window["wall"]][2]
            emit = max(0.0, (wx * dx + wz * dz) / distance)
            receive = max(0.0, (-nx * dx - nz * dz) / distance)
            strength += window["area_m2"] * emit * receive / distance ** 2
        return round(strength, 5)

    # One clear candidate per observed wall for a 60 x 60 cm artwork at eye level.
    y, half = min(1.55, height / 2 + 0.45), 0.3
    candidates = []
    for wall in spec.walls:
        if not wall.observed or wall.length_m < 2 * half:
            continue
        horizontal = wall.id in {"wall_n", "wall_s"}
        start, _, normal = frames[wall.id]
        blocked = []
        for opening in spec.openings:
            if opening.wall == wall.id and opening.sill_m < y + half and opening.sill_m + opening.height_m > y - half:
                blocked.append((opening.offset_m, opening.offset_m + opening.width_m))
        for item in [*spec.existing_items, *spec.placed_products]:
            p, size = item.pose.position, item.size
            if p.y >= y + half or p.y + size.height <= y - half:
                continue
            angle = radians(item.pose.rotation.yaw)
            hx = (abs(cos(angle)) * size.width + abs(sin(angle)) * size.depth) / 2
            hz = (abs(sin(angle)) * size.width + abs(cos(angle)) * size.depth) / 2
            gap = (p.x - start[0]) * normal[0] + (p.z - start[1]) * normal[1]
            if gap <= (hz if horizontal else hx) + 0.1:
                center, extent = (p.x, hx) if horizontal else (p.z, hz)
                blocked.append((center - extent, center + extent))
        start_of_gap = 0.0
        spots = []
        for left, right in sorted([*blocked, (wall.length_m, wall.length_m)]):
            if left - start_of_gap >= 2 * half:
                position = point(wall.id, (left + start_of_gap) / 2, y)
                spots.append({"wall": wall.id, "position": position, "relative_exposure": exposure(wall.id, position)})
            start_of_gap = max(start_of_gap, right)
        if spots:
            candidates.append(min(spots, key=lambda spot: spot["relative_exposure"] or 0))
    room["lighting"] = {
        "status": "estimated" if windows else "unknown",
        "method": "window_geometry",
        "window_area_m2": round(sum(window["area_m2"] for window in windows), 4),
        "windows": windows,
        "painting_spots": sorted(candidates, key=lambda spot: spot["relative_exposure"] or 0),
    }
