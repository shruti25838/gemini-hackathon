"""Two-dimensional geometry in room-local metres. Standard library only.

Everything is seen from above. The room occupies the rectangle from (0, 0) to
(width_m, depth_m).

Walls are traversed clockwise, and an opening's offset is measured from the
start of its wall:

    wall_n   (0, 0) -> (W, 0)    direction +x,  inward normal +y
    wall_e   (W, 0) -> (W, D)    direction +y,  inward normal -x
    wall_s   (W, D) -> (0, D)    direction -x,  inward normal -y
    wall_w   (0, D) -> (0, 0)    direction -y,  inward normal +x

Overlap tests use the separating axis theorem, which is exact for convex
polygons. Every shape here is convex: rectangles, and circular sectors of at
most 180 degrees approximated by a fan of points.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]
Polygon = list[Point]

# Distances below this are treated as zero. Room geometry is measured in
# metres from a photo, so a tenth of a millimetre is noise, not a clearance.
EPSILON = 1e-4


# --------------------------------------------------------------------------
# Basic vector helpers
# --------------------------------------------------------------------------


def subtract(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def length(a: Point) -> float:
    return math.hypot(a[0], a[1])


def normalise(a: Point) -> Point:
    mag = length(a)
    if mag < EPSILON:
        return (0.0, 0.0)
    return (a[0] / mag, a[1] / mag)


def rotate(point: Point, degrees: float) -> Point:
    radians = math.radians(degrees)
    cos_t, sin_t = math.cos(radians), math.sin(radians)
    return (
        point[0] * cos_t - point[1] * sin_t,
        point[0] * sin_t + point[1] * cos_t,
    )


# --------------------------------------------------------------------------
# Shapes
# --------------------------------------------------------------------------


def rect_corners(
    centre_x: float,
    centre_y: float,
    width: float,
    depth: float,
    rotation_deg: float = 0.0,
) -> Polygon:
    """Four corners of a rectangle, rotated about its own centre."""
    half_w, half_d = width / 2.0, depth / 2.0
    local = [
        (-half_w, -half_d),
        (half_w, -half_d),
        (half_w, half_d),
        (-half_w, half_d),
    ]
    return [
        (centre_x + rx, centre_y + ry)
        for rx, ry in (rotate(corner, rotation_deg) for corner in local)
    ]


def room_polygon(width_m: float, depth_m: float) -> Polygon:
    return [(0.0, 0.0), (width_m, 0.0), (width_m, depth_m), (0.0, depth_m)]


def wall_endpoints(wall_id: str, width_m: float, depth_m: float) -> tuple[Point, Point]:
    """Start and end of a wall, traversed clockwise."""
    if wall_id == "wall_n":
        return (0.0, 0.0), (width_m, 0.0)
    if wall_id == "wall_e":
        return (width_m, 0.0), (width_m, depth_m)
    if wall_id == "wall_s":
        return (width_m, depth_m), (0.0, depth_m)
    if wall_id == "wall_w":
        return (0.0, depth_m), (0.0, 0.0)
    raise ValueError(f"unknown wall id: {wall_id}")


def wall_length(wall_id: str, width_m: float, depth_m: float) -> float:
    start, end = wall_endpoints(wall_id, width_m, depth_m)
    return length(subtract(end, start))


def inward_normal(wall_id: str) -> Point:
    """Unit vector pointing from the wall into the room."""
    return {
        "wall_n": (0.0, 1.0),
        "wall_e": (-1.0, 0.0),
        "wall_s": (0.0, -1.0),
        "wall_w": (1.0, 0.0),
    }[wall_id]


def point_on_wall(wall_id: str, offset_m: float, width_m: float, depth_m: float) -> Point:
    """A point at `offset_m` along a wall, measured from the wall's start."""
    start, end = wall_endpoints(wall_id, width_m, depth_m)
    direction = normalise(subtract(end, start))
    return (start[0] + direction[0] * offset_m, start[1] + direction[1] * offset_m)


# --------------------------------------------------------------------------
# Door swing
# --------------------------------------------------------------------------


def door_hinge_point(
    wall_id: str,
    offset_m: float,
    opening_width_m: float,
    hinge_side: str,
    room_width_m: float,
    room_depth_m: float,
) -> Point:
    """Where the door is hinged.

    hinge_side is relative to the wall's own direction of travel:
      "left"  the end of the opening nearer the wall's start (offset_m)
      "right" the end nearer offset_m + opening_width_m
    """
    if hinge_side == "right":
        return point_on_wall(wall_id, offset_m + opening_width_m, room_width_m, room_depth_m)
    return point_on_wall(wall_id, offset_m, room_width_m, room_depth_m)


def door_leaf_segment(
    hinge: Point,
    closed_direction: Point,
    sweep_toward: Point,
    door_width_m: float,
    angle_deg: float,
) -> tuple[Point, Point]:
    """The door leaf as a line segment at a given opening angle.

    At 0 degrees it lies flat along the wall in `closed_direction`. It rotates
    toward `sweep_toward`, which is the inward normal for an inward door.
    """
    closed = normalise(closed_direction)
    toward = normalise(sweep_toward)
    # Rotating `closed` by +angle should move it toward `toward`. The sign of
    # the 2D cross product tells us which way that is.
    cross = closed[0] * toward[1] - closed[1] * toward[0]
    signed = angle_deg if cross >= 0 else -angle_deg
    tip = rotate(closed, signed)
    return hinge, (hinge[0] + tip[0] * door_width_m, hinge[1] + tip[1] * door_width_m)


def door_swing_polygon(
    hinge: Point,
    closed_direction: Point,
    sweep_toward: Point,
    door_width_m: float,
    max_angle_deg: float,
    steps: int = 7,
) -> Polygon:
    """The area the door sweeps, as a convex polygon.

    A circular sector of at most 180 degrees is convex, so the separating axis
    theorem is exact against it. The arc is approximated by `steps` points,
    which slightly under-covers the true arc between them; the clearance
    margin applied by the caller more than absorbs that.
    """
    steps = max(2, steps)
    polygon: Polygon = [hinge]
    for index in range(steps + 1):
        angle = max_angle_deg * index / steps
        _, tip = door_leaf_segment(
            hinge, closed_direction, sweep_toward, door_width_m, angle
        )
        polygon.append(tip)
    return polygon


def door_angles(max_angle_deg: float, steps: int) -> list[float]:
    """Intermediate angles to test, including fully closed and fully open."""
    steps = max(2, steps)
    return [max_angle_deg * index / steps for index in range(steps + 1)]


# --------------------------------------------------------------------------
# Separating axis theorem
# --------------------------------------------------------------------------


def _axes(polygon: Sequence[Point]) -> list[Point]:
    """One outward normal per edge, which is all SAT needs."""
    axes: list[Point] = []
    count = len(polygon)
    for index in range(count):
        current = polygon[index]
        following = polygon[(index + 1) % count]
        edge = subtract(following, current)
        if length(edge) < EPSILON:
            continue
        axes.append(normalise((-edge[1], edge[0])))
    return axes


def _project(polygon: Sequence[Point], axis: Point) -> tuple[float, float]:
    values = [dot(point, axis) for point in polygon]
    return min(values), max(values)


def polygons_overlap(a: Sequence[Point], b: Sequence[Point], margin: float = 0.0) -> bool:
    """True when two convex polygons overlap, after inflating by `margin`.

    The margin is the clearance the caller requires. Touching exactly is not
    an overlap; the epsilon keeps a shared edge from reading as a collision.
    """
    for axis in _axes(a) + _axes(b):
        a_min, a_max = _project(a, axis)
        b_min, b_max = _project(b, axis)
        if a_max + margin <= b_min + EPSILON or b_max + margin <= a_min + EPSILON:
            return False
    return True


def overlap_depth(a: Sequence[Point], b: Sequence[Point]) -> float:
    """How far two overlapping polygons interpenetrate. Zero when apart."""
    smallest = float("inf")
    for axis in _axes(a) + _axes(b):
        a_min, a_max = _project(a, axis)
        b_min, b_max = _project(b, axis)
        if a_max <= b_min or b_max <= a_min:
            return 0.0
        smallest = min(smallest, min(a_max - b_min, b_max - a_min))
    return 0.0 if smallest is float("inf") else smallest


# --------------------------------------------------------------------------
# Distance and containment
# --------------------------------------------------------------------------


def point_segment_distance(point: Point, start: Point, end: Point) -> float:
    segment = subtract(end, start)
    segment_length_sq = dot(segment, segment)
    if segment_length_sq < EPSILON * EPSILON:
        return length(subtract(point, start))
    t = max(0.0, min(1.0, dot(subtract(point, start), segment) / segment_length_sq))
    projection = (start[0] + segment[0] * t, start[1] + segment[1] * t)
    return length(subtract(point, projection))


def segment_distance(a1: Point, a2: Point, b1: Point, b2: Point) -> float:
    return min(
        point_segment_distance(a1, b1, b2),
        point_segment_distance(a2, b1, b2),
        point_segment_distance(b1, a1, a2),
        point_segment_distance(b2, a1, a2),
    )


def polygon_distance(a: Sequence[Point], b: Sequence[Point]) -> float:
    """Shortest gap between two convex polygons. Zero when they overlap."""
    if polygons_overlap(a, b):
        return 0.0
    shortest = float("inf")
    for i in range(len(a)):
        a1, a2 = a[i], a[(i + 1) % len(a)]
        for j in range(len(b)):
            b1, b2 = b[j], b[(j + 1) % len(b)]
            shortest = min(shortest, segment_distance(a1, a2, b1, b2))
    return shortest


def point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    """Ray casting. A point exactly on the boundary counts as inside."""
    x, y = point
    count = len(polygon)
    for index in range(count):
        current = polygon[index]
        following = polygon[(index + 1) % count]
        if point_segment_distance(point, current, following) < EPSILON:
            return True

    inside = False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            crossing_x = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < crossing_x:
                inside = not inside
        j = i
    return inside


def polygon_contains(outer: Sequence[Point], inner: Iterable[Point]) -> bool:
    """Every point of `inner` lies inside `outer`."""
    return all(point_in_polygon(point, outer) for point in inner)


def polygon_inside_room(
    polygon: Sequence[Point], room_width_m: float, room_depth_m: float, margin: float = 0.0
) -> bool:
    return all(
        -EPSILON + margin <= x <= room_width_m - margin + EPSILON
        and -EPSILON + margin <= y <= room_depth_m - margin + EPSILON
        for x, y in polygon
    )


def distance_to_room_edge(
    polygon: Sequence[Point], room_width_m: float, room_depth_m: float
) -> float:
    """Smallest gap between a footprint and any wall. Negative when outside."""
    gaps: list[float] = []
    for x, y in polygon:
        gaps.extend([x, y, room_width_m - x, room_depth_m - y])
    return min(gaps) if gaps else 0.0
