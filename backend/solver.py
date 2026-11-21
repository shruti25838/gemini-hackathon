"""Ethyneon fit solver. Standard library only.

The same file runs on the server (authoritative) and is mounted into the
Argon and Carbon Managed Agent sandboxes, so every sandbox claim can be
re-checked by the server with identical code.

CLI:
  python solver.py check  input.json   -> FitReport JSON
  python solver.py search input.json   -> {"placements": [...], "report": {...}, "rejected": [...]}
  python solver.py probe  input.json   -> door swing checked at 15-degree steps

input.json = {"room": RoomSpec, "placements": [Placement...], "products": [CatalogOffer...],
              "brief": DesignBrief}
All units metres. Footprints are axis-aligned (rot in 0/90/180/270).
"""
from __future__ import annotations

import json
import math
import sys

VERSION = "solver-1.2"
TIGHT_GAP_M = 0.15     # below this gap (but no overlap) => "tight"
DOOR_CLEAR_M = 0.05    # tolerance around the swept door area
EPS = 1e-6


# ---------- geometry ----------
def footprint(p: dict) -> tuple[float, float, float, float]:
    """(x0, z0, x1, z1) of an axis-aligned footprint centred at (x, z)."""
    w, d = (p["d"], p["w"]) if p.get("rot", 0) in (90, 270) else (p["w"], p["d"])
    return (p["x"] - w / 2, p["z"] - d / 2, p["x"] + w / 2, p["z"] + d / 2)


def rect_gap(a, b) -> float:
    """Distance between rects; negative => overlap depth (min axis)."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dz = max(b[1] - a[3], a[1] - b[3])
    if dx < 0 and dz < 0:
        return max(dx, dz)  # both negative -> overlapping; report smaller penetration
    return math.hypot(max(dx, 0), max(dz, 0))


def _dim(room, key) -> float:
    v = room[key]
    return v["value_m"] if isinstance(v, dict) else float(v)


def door_geometry(room: dict, opening: dict):
    """Hinge point, inward unit normal, along-wall unit vector toward the latch, radius."""
    W, D = _dim(room, "width_m"), _dim(room, "depth_m")
    wall, off, width = opening["wall"], opening["offset_m"], opening["width_m"]
    # start point of the wall, direction along it, inward normal
    spec = {
        "back": ((0.0, 0.0), (1, 0), (0, 1)),
        "right": ((W, 0.0), (0, 1), (-1, 0)),
        "front": ((0.0, D), (1, 0), (0, -1)),
        "left": ((0.0, 0.0), (0, 1), (1, 0)),
    }[wall]
    (sx, sz), (ax, az), (nx, nz) = spec
    a = (sx + ax * off, sz + az * off)                  # opening start
    b = (sx + ax * (off + width), sz + az * (off + width))  # opening end
    motion = opening.get("motion") or {}
    hinge_side = motion.get("hinge_side", "left")
    # "left" as seen from inside facing the wall. Facing the wall means looking
    # along -n; left-hand direction is then rot90 of that view.
    view = (-nx, -nz)
    left_dir = (view[1], -view[0])  # rotate view -90deg (x,z with y up: left of view)
    # choose the end of the opening that lies further in left_dir
    pa = a[0] * left_dir[0] + a[1] * left_dir[1]
    pb = b[0] * left_dir[0] + b[1] * left_dir[1]
    hinge, latch = (a, b) if (pa > pb) == (hinge_side == "left") else (b, a)
    along = ((latch[0] - hinge[0]) / width, (latch[1] - hinge[1]) / width)
    return hinge, (nx, nz), along, width


def door_swept_points(room: dict, opening: dict, step_deg: float = 5.0, angle_max: float | None = None):
    """Sample points covering the swept area of an inward-swinging door."""
    motion = opening.get("motion") or {}
    if motion.get("swing", "in") != "in":
        return []
    hinge, n, along, r = door_geometry(room, opening)
    amax = angle_max if angle_max is not None else motion.get("max_angle_deg", 90)
    pts = []
    steps = max(1, int(amax / step_deg))
    for i in range(steps + 1):
        t = math.radians(min(amax, i * step_deg))
        dx = along[0] * math.cos(t) + n[0] * math.sin(t)
        dz = along[1] * math.cos(t) + n[1] * math.sin(t)
        for k in range(1, 11):
            rr = r * k / 10
            pts.append((hinge[0] + dx * rr, hinge[1] + dz * rr))
    return pts


def point_rect_gap(p, rect) -> float:
    dx = max(rect[0] - p[0], 0, p[0] - rect[2])
    dz = max(rect[1] - p[1], 0, p[1] - rect[3])
    if dx == 0 and dz == 0:
        return -min(p[0] - rect[0], rect[2] - p[0], p[1] - rect[1], rect[3] - p[1])
    return math.hypot(dx, dz)


# ---------- check ----------
def check(room: dict, placements: list[dict], scene_rev: int = 0, angle_max: float | None = None) -> dict:
    W, D = _dim(room, "width_m"), _dim(room, "depth_m")
    collisions, unknowns = [], []
    gaps = []
    rects = {}
    for p in placements:
        if p.get("w") is None or p.get("d") is None:
            unknowns.append(f"{p['key']}: product dimensions not documented")
            continue
        r = footprint(p)
        rects[p["key"]] = r
        oob = []
        if r[0] < -EPS: oob.append("left")
        if r[1] < -EPS: oob.append("back")
        if r[2] > W + EPS: oob.append("right")
        if r[3] > D + EPS: oob.append("front")
        for wid in oob:
            collisions.append({"a": p["key"], "b": f"wall:{wid}", "kind": "out_of_bounds",
                               "detail": f"{p['key']} extends past the {wid} wall"})

    keys = list(rects)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            g = rect_gap(rects[keys[i]], rects[keys[j]])
            gaps.append(g)
            if g < -EPS:
                collisions.append({"a": keys[i], "b": keys[j], "kind": "overlap",
                                   "detail": f"footprints overlap by {abs(g):.2f} m"})

    for o in room.get("openings", []):
        if o["type"] != "door":
            continue
        if not o.get("motion"):
            near = [k for k, r in rects.items() if _near_opening(room, o, r)]
            if near:
                unknowns.append(f"door {o['id']}: hinge/swing unknown; affects {', '.join(near)}")
            continue
        pts = door_swept_points(room, o, angle_max=angle_max)
        for k, r in rects.items():
            worst = min((point_rect_gap(pt, r) for pt in pts), default=9e9)
            if pts:
                gaps.append(worst)
            if worst < DOOR_CLEAR_M:
                collisions.append({"a": k, "b": f"door:{o['id']}", "kind": "door_swing",
                                   "detail": f"{k} is inside the swing of door {o['id']}"})

    pos_gaps = [g for g in gaps if g >= 0]
    min_gap = round(min(pos_gaps), 3) if pos_gaps else None
    if collisions:
        status = "no_fit"
    elif unknowns:
        status = "unknown"
    elif min_gap is not None and min_gap < TIGHT_GAP_M:
        status = "tight"
    else:
        status = "fits"
    return {"scene_rev": scene_rev, "room_revision": room.get("revision", 1), "status": status,
            "collisions": collisions, "min_gap_m": min_gap, "unknowns": unknowns,
            "solver_version": VERSION, "checked_by": ["server"]}


def _near_opening(room, o, rect, margin=1.0) -> bool:
    hinge, n, along, r = door_geometry(room, {**o, "motion": {"hinge_side": "left", "swing": "in"}})
    cx = hinge[0] + along[0] * r / 2 + n[0] * r / 2
    cz = hinge[1] + along[1] * r / 2 + n[1] * r / 2
    return point_rect_gap((cx, cz), rect) < r / 2 + margin


# ---------- search ----------
WALL_ORDER = ["back", "left", "right", "front"]


def _against_wall(room, wall, along_pos, w, d):
    """Centre of a w x d footprint with its back against `wall` at `along_pos`."""
    W, D = _dim(room, "width_m"), _dim(room, "depth_m")
    if wall == "back":
        return along_pos, d / 2, 0
    if wall == "front":
        return along_pos, D - d / 2, 180
    if wall == "left":
        return d / 2, along_pos, 90
    return W - d / 2, along_pos, 270


def search(room: dict, fixed: list[dict], items: list[dict], preferred_wall: str | None = None,
           step: float = 0.1) -> dict:
    """Greedy placement of `items` (largest first) against walls, avoiding collisions.

    Returns placements (fixed + placed), the final report and rejected candidates.
    """
    W, D = _dim(room, "width_m"), _dim(room, "depth_m")
    placed = list(fixed)
    rejected = []
    walls = ([preferred_wall] if preferred_wall else []) + [w for w in WALL_ORDER if w != preferred_wall]
    for item in sorted(items, key=lambda p: -(p.get("w") or 0) * (p.get("d") or 0)):
        if item.get("w") is None or item.get("d") is None:
            rejected.append({"key": item["key"], "reason": "dimensions unknown"})
            continue
        best = None
        for wall in walls:
            length = W if wall in ("back", "front") else D
            n = int(length / step)
            for i in range(n + 1):
                pos = i * step
                x, z, rot = _against_wall(room, wall, pos, item["w"], item["d"])
                cand = {**item, "x": round(x, 3), "z": round(z, 3), "rot": rot}
                rep = check(room, placed + [cand])
                if rep["status"] in ("fits", "tight"):
                    score = (0 if wall == walls[0] else 1, 0 if rep["status"] == "fits" else 1,
                             -(rep["min_gap_m"] or 0))
                    if best is None or score < best[0]:
                        best = (score, cand)
                elif len(rejected) < 40:
                    rejected.append({"key": item["key"], "wall": wall, "pos": round(pos, 2),
                                     "reason": rep["collisions"][0]["detail"] if rep["collisions"] else rep["status"]})
            if best and best[0][0] == 0:
                break
        if best:
            placed.append(best[1])
        else:
            rejected.append({"key": item["key"], "reason": "no collision-free position found"})
    return {"placements": placed, "report": check(room, placed), "rejected": rejected,
            "solver_version": VERSION}


def probe(room: dict, placements: list[dict]) -> dict:
    """Independent test: check the door swing at intermediate angles."""
    out = []
    for a in range(15, 181, 15):
        rep = check(room, placements, angle_max=a)
        out.append({"angle_deg": a, "status": rep["status"],
                    "collisions": [c for c in rep["collisions"] if c["kind"] == "door_swing"]})
    return {"solver_version": VERSION, "angles": out}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    data = json.load(open(path))
    room, placements = data["room"], data.get("placements", [])
    if cmd == "check":
        res = check(room, placements)
    elif cmd == "search":
        fixed = [p for p in placements if p.get("locked")]
        items = [p for p in placements if not p.get("locked")]
        res = search(room, fixed, items, (data.get("brief") or {}).get("preferred_wall"))
    elif cmd == "probe":
        res = probe(room, placements)
    else:
        print(f"unknown command {cmd}")
        return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
