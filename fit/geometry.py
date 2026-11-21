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


# --------------------------------------------------------------------------
# Richer output: intersections, closest pairs, corridors
# --------------------------------------------------------------------------


def polygon_intersection(subject: Sequence[Point], clip: Sequence[Point]) -> Polygon:
    """Sutherland-Hodgman clipping. Exact for the convex shapes used here.

    Returns the overlapping area as a polygon, or an empty list when the two
    do not overlap. Lane B draws this, so it has to be the real region and
    not a bounding box.
    """
    def inside(point: Point, edge_a: Point, edge_b: Point) -> bool:
        return (edge_b[0] - edge_a[0]) * (point[1] - edge_a[1]) - (
            edge_b[1] - edge_a[1]
        ) * (point[0] - edge_a[0]) >= -EPSILON

    def intersect(p1: Point, p2: Point, edge_a: Point, edge_b: Point) -> Point:
        x1, y1 = p1
        x2, y2 = p2
        x3, y3 = edge_a
        x4, y4 = edge_b
        denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denominator) < EPSILON:
            return p2
        a = x1 * y2 - y1 * x2
        b = x3 * y4 - y3 * x4
        return (
            (a * (x3 - x4) - (x1 - x2) * b) / denominator,
            (a * (y3 - y4) - (y1 - y2) * b) / denominator,
        )

    output: list[Point] = list(_counter_clockwise(subject))
    clip_polygon = list(_counter_clockwise(clip))

    for index in range(len(clip_polygon)):
        if not output:
            return []
        edge_a = clip_polygon[index]
        edge_b = clip_polygon[(index + 1) % len(clip_polygon)]
        current_input = output
        output = []
        for i in range(len(current_input)):
            current = current_input[i]
            previous = current_input[i - 1]
            if inside(current, edge_a, edge_b):
                if not inside(previous, edge_a, edge_b):
                    output.append(intersect(previous, current, edge_a, edge_b))
                output.append(current)
            elif inside(previous, edge_a, edge_b):
                output.append(intersect(previous, current, edge_a, edge_b))

    return [_round_point(p) for p in output]


def _signed_area(polygon: Sequence[Point]) -> float:
    total = 0.0
    for index in range(len(polygon)):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % len(polygon)]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _counter_clockwise(polygon: Sequence[Point]) -> Sequence[Point]:
    return polygon if _signed_area(polygon) >= 0 else list(reversed(polygon))


def polygon_area(polygon: Sequence[Point]) -> float:
    return abs(_signed_area(polygon))


def _round_point(point: Point, places: int = 4) -> Point:
    """Round for output stability. Same input must give identical bytes."""
    return (round(point[0] + 0.0, places), round(point[1] + 0.0, places))


def round_polygon(polygon: Sequence[Point], places: int = 4) -> Polygon:
    return [_round_point(p, places) for p in polygon]


def closest_points(a: Sequence[Point], b: Sequence[Point]) -> tuple[Point, Point, float]:
    """The nearest pair of points between two polygons, and the gap.

    Reported so a reader can see where a clearance was measured rather than
    being handed a bare number.
    """
    best: tuple[Point, Point, float] = ((0.0, 0.0), (0.0, 0.0), float("inf"))
    for i in range(len(a)):
        a1, a2 = a[i], a[(i + 1) % len(a)]
        for j in range(len(b)):
            b1, b2 = b[j], b[(j + 1) % len(b)]
            for point, start, end in (
                (a1, b1, b2),
                (a2, b1, b2),
                (b1, a1, a2),
                (b2, a1, a2),
            ):
                distance = point_segment_distance(point, start, end)
                if distance < best[2]:
                    other = _closest_point_on_segment(point, start, end)
                    pair = (point, other) if point in (a1, a2) else (other, point)
                    best = (_round_point(pair[0]), _round_point(pair[1]), distance)
    return best


def _closest_point_on_segment(point: Point, start: Point, end: Point) -> Point:
    segment = subtract(end, start)
    length_sq = dot(segment, segment)
    if length_sq < EPSILON * EPSILON:
        return start
    t = max(0.0, min(1.0, dot(subtract(point, start), segment) / length_sq))
    return (start[0] + segment[0] * t, start[1] + segment[1] * t)


def corridor_width_profile(
    corridor: Sequence[Point], obstacles: Sequence[Sequence[Point]], samples: int = 9
) -> list[dict]:
    """How wide the corridor stays along its length, and where it narrows.

    The corridor is treated as a straight run between its two short ends. At
    each sample the free width is the corridor width minus whatever intrudes
    from either side.
    """
    if len(corridor) < 3:
        return []

    xs = [p[0] for p in corridor]
    ys = [p[1] for p in corridor]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width_x = max_x - min_x
    width_y = max_y - min_y
    runs_along_y = width_y >= width_x
    full_width = width_x if runs_along_y else width_y

    # Evenly spaced samples alone can step straight over a narrow obstacle:
    # a 0.4 m box between 0.44 m samples is invisible. So the sample set also
    # includes every obstacle's leading and trailing edge, and the midpoint
    # between them, which is where a narrowing is actually deepest.
    samples = max(2, samples)
    along_min, along_max = (min_y, max_y) if runs_along_y else (min_x, max_x)
    positions: set[float] = {
        along_min + (along_max - along_min) * (index / samples)
        for index in range(samples + 1)
    }
    for obstacle in obstacles:
        if not obstacle:
            continue
        coordinates = [p[1] for p in obstacle] if runs_along_y else [p[0] for p in obstacle]
        low, high = min(coordinates), max(coordinates)
        for position in (low, high, (low + high) / 2.0):
            if along_min - EPSILON <= position <= along_max + EPSILON:
                positions.add(min(max(position, along_min), along_max))

    profile: list[dict] = []
    for position in sorted(positions):
        t = 0.0 if along_max == along_min else (position - along_min) / (along_max - along_min)
        if runs_along_y:
            y = position
            slice_polygon = [
                (min_x, y - EPSILON),
                (max_x, y - EPSILON),
                (max_x, y + EPSILON),
                (min_x, y + EPSILON),
            ]
        else:
            x = position
            slice_polygon = [
                (x - EPSILON, min_y),
                (x + EPSILON, min_y),
                (x + EPSILON, max_y),
                (x - EPSILON, max_y),
            ]

        blocked = 0.0
        for obstacle in obstacles:
            overlap = polygon_intersection(slice_polygon, obstacle)
            if not overlap:
                continue
            if runs_along_y:
                span = max(p[0] for p in overlap) - min(p[0] for p in overlap)
            else:
                span = max(p[1] for p in overlap) - min(p[1] for p in overlap)
            blocked = max(blocked, span)

        at = position
        profile.append(
            {
                "at_m": round(at, 4),
                "axis": "y" if runs_along_y else "x",
                "free_width_m": round(max(0.0, full_width - blocked), 4),
            }
        )
    return profile
