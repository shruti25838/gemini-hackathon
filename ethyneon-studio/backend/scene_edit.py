"""Shopper edits from the Design view, with deterministic guardrails, written back to geometricconfig.json.

Guardrails reject a pose that would put a product outside the walls, inside another product or a fixture
(wardrobe, pillar, heater), or in the area a door sweeps or a passage needs. They run on the same metre frame the
viewer draws: origin at the wall_n / wall_w floor corner, +x along wall_n, +z toward wall_s, yaw about Y.
"""

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.connections import SHOPIFY_VARIANT_ID
from backend.schemas import RequestBody


TOLERANCE_M = 0.01
PASSAGE_CLEARANCE_M = 0.6
# A replacement must fit the space the current product occupies: up to 10% or 5 cm larger on each side.
REPLACE_GROWTH = 1.10
REPLACE_SLACK_M = 0.05


class Point(RequestBody):
    # Wide bounds on purpose: a drag past the walls should reach the guardrail and get its reason back.
    x: float = Field(ge=-100, le=100, allow_inf_nan=False)
    z: float = Field(ge=-100, le=100, allow_inf_nan=False)


class Replacement(RequestBody):
    variant_id: str = Field(pattern=SHOPIFY_VARIANT_ID)
    title: str = Field(min_length=1, max_length=300)
    price: dict[str, Any] | None = None
    listing_url: str | None = Field(default=None, max_length=2000)


class NewProduct(Replacement):
    size: dict[str, float] | None = None


class SceneEdit(RequestBody):
    base_etag: str = Field(pattern=r"^[0-9a-f]{16}$")
    action_id: str = Field(pattern=r"^[0-9a-fA-F-]{8,64}$")
    op: Literal["move", "rotate", "swap", "replace", "remove", "add"]
    product_id: str = Field(min_length=1, max_length=120)
    other_id: str | None = Field(default=None, max_length=120)
    position: Point | None = None
    yaw: float | None = Field(default=None, ge=-720, le=720, allow_inf_nan=False)
    replacement: Replacement | None = None
    new_product: NewProduct | None = None

    @model_validator(mode="after")
    def needs_inputs(self) -> "SceneEdit":
        needed = {"move": self.position, "rotate": self.yaw, "swap": self.other_id, "replace": self.replacement,
                  "add": self.new_product}
        if self.op in needed and needed[self.op] is None:
            raise ValueError(f"The {self.op} edit is missing its input.")
        return self


def _box(label: str, x: float, z: float, width: float, depth: float, yaw_deg: float = 0.0,
         y: float = 0.0, height: float = 3.0) -> dict[str, Any]:
    return {"label": label, "x": x, "z": z, "hw": width / 2, "hd": depth / 2, "yaw": math.radians(yaw_deg),
            "y0": y, "y1": y + height}


def item_box(item: dict[str, Any]) -> dict[str, Any]:
    position, rotation, size = item["pose"]["position"], item["pose"]["rotation"], item["size"]
    return _box(item["label"], position["x"], position["z"], size["width"], size["depth"], rotation["yaw"],
                position["y"], size["height"])


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Oriented footprints intersect (separating-axis test) and their height ranges overlap."""
    if first["y1"] <= second["y0"] + TOLERANCE_M or second["y1"] <= first["y0"] + TOLERANCE_M:
        return False

    def axes(box):
        c, s = math.cos(box["yaw"]), math.sin(box["yaw"])
        return ((c, -s, box["hw"]), (s, c, box["hd"]))

    first_axes, second_axes = axes(first), axes(second)
    dx, dz = first["x"] - second["x"], first["z"] - second["z"]
    for ax, az, _ in (*first_axes, *second_axes):
        reach = sum(abs(ax * ux + az * uz) * extent for ux, uz, extent in (*first_axes, *second_axes))
        if abs(dx * ax + dz * az) >= reach - TOLERANCE_M:
            return False
    return True


def corners(box: dict[str, Any]) -> list[tuple[float, float]]:
    c, s = math.cos(box["yaw"]), math.sin(box["yaw"])
    return [(box["x"] + sx * box["hw"] * c + sz * box["hd"] * s, box["z"] - sx * box["hw"] * s + sz * box["hd"] * c)
            for sx in (-1, 1) for sz in (-1, 1)]


def opening_zone(opening: dict[str, Any], width: float, depth: float) -> dict[str, Any] | None:
    """The floor area a door sweeps (a square of its width) or a passage needs to stay walkable."""
    if opening["type"] == "window":
        return None
    reach = opening["width_m"] if opening["type"] == "door" and opening.get("swing") not in {"outward", "sliding"} \
        else PASSAGE_CLEARANCE_M
    along = opening["offset_m"] + opening["width_m"] / 2
    name = "door swing" if opening["type"] == "door" else "doorway"
    label = f"the {name} of {opening['id'].replace('_', ' ')}"
    return {
        "wall_n": _box(label, along, reach / 2, opening["width_m"], reach),
        "wall_s": _box(label, along, depth - reach / 2, opening["width_m"], reach),
        "wall_w": _box(label, reach / 2, along, reach, opening["width_m"]),
        "wall_e": _box(label, width - reach / 2, along, reach, opening["width_m"]),
    }[opening["wall"]]


def placement_problems(scene: dict[str, Any], product_ids: list[str]) -> list[str]:
    width, depth, height = (scene["dimensions"][axis]["value_m"] for axis in ("width", "depth", "height"))
    products = {item["id"]: item for item in scene["placed_products"]}
    obstacles = [item_box(item) for item in scene["existing_items"]]
    obstacles += [zone for opening in scene["openings"] if (zone := opening_zone(opening, width, depth))]
    problems = []
    for product_id in product_ids:
        box = item_box(products[product_id])
        if any(x < -TOLERANCE_M or x > width + TOLERANCE_M or z < -TOLERANCE_M or z > depth + TOLERANCE_M
               for x, z in corners(box)) or box["y1"] > height + TOLERANCE_M:
            problems.append(f"{box['label']} would extend outside the room.")
        others = [item_box(item) for item_id, item in products.items() if item_id not in product_ids]
        others += [item_box(products[other]) for other in product_ids if other != product_id]
        for other in [*obstacles, *others]:
            if overlaps(box, other):
                problems.append(f"{box['label']} would overlap {other['label']}.")
    return list(dict.fromkeys(problems))


def replacement_problem(label: str, new_label: str, old: dict[str, float], new: dict[str, float]) -> str | None:
    """Refuse swapping in a product bigger than the space it replaces, even where it would not hit anything yet."""
    cm = lambda size: " × ".join(f"{size[axis] * 100:.0f}" for axis in ("width", "depth", "height"))  # noqa: E731
    too_big = [axis for axis in ("width", "depth", "height") if new[axis] > old[axis] * REPLACE_GROWTH + REPLACE_SLACK_M]
    if not too_big:
        return None
    return (f"Not possible: {new_label} ({cm(new)} cm) is bigger than the space of {label} ({cm(old)} cm) "
            f"in {', '.join(too_big)}. Choose a smaller alternative or move things first.")


def replace_product(item: dict[str, Any], replacement: Replacement, size: dict[str, float] | None) -> None:
    item.update(variant_id=replacement.variant_id, label=replacement.title[:120],
                price=replacement.price, listing_url=replacement.listing_url)
    item.pop("model", None)
    if size:
        item["size"] = size


def add_product(config: dict[str, Any], edit: SceneEdit, size: dict[str, float], scene_of) -> list[str]:
    """Place a new product at the first spot that passes every guardrail, scanning from the room's centre."""
    from uuid import uuid4

    new = edit.new_product
    item = {"id": edit.product_id if edit.product_id != "new" else f"product_{uuid4().hex[:12]}",
            "label": new.title[:120], "variant_id": new.variant_id, "price": new.price, "listing_url": new.listing_url,
            "size": size, "pose": {"position": {"x": 0, "y": 0, "z": 0}, "rotation": {"yaw": 0, "pitch": 0, "roll": 0}}}
    config.setdefault("placed_products", []).append(item)
    scene = scene_of(config)
    width, depth = scene["dimensions"]["width"]["value_m"], scene["dimensions"]["depth"]["value_m"]
    step = 0.25
    spots = [(x * step, z * step) for x in range(1, int(width / step)) for z in range(1, int(depth / step))]
    spots.sort(key=lambda spot: (spot[0] - width / 2) ** 2 + (spot[1] - depth / 2) ** 2)
    for yaw in (0, 90):
        for x, z in spots:
            item["pose"]["position"].update({"x": round(x, 3), "z": round(z, 3)})
            item["pose"]["rotation"]["yaw"] = yaw
            if not placement_problems(scene_of(config), [item["id"]]):
                return [item["id"]]
    config["placed_products"].remove(item)
    raise ValueError(f"There is no free floor space that fits {new.title[:60]}.")


def apply_edit(config: dict[str, Any], edit: SceneEdit, replacement_size: dict[str, float] | None) -> list[str]:
    """Change the raw config in place and return the ids of products whose placement must be checked."""
    products = {item.get("id"): item for item in config.get("placed_products") or [] if isinstance(item, dict)}
    item = products.get(edit.product_id)
    if item is None:
        raise KeyError(edit.product_id)
    pose = item.setdefault("pose", {})
    position = pose.setdefault("position", {"x": 0, "y": 0, "z": 0})
    rotation = pose.setdefault("rotation", {"yaw": 0, "pitch": 0, "roll": 0})
    if edit.op == "move":
        position["x"], position["z"] = round(edit.position.x, 3), round(edit.position.z, 3)
        return [edit.product_id]
    if edit.op == "rotate":
        rotation["yaw"] = round(((edit.yaw + 180) % 360) - 180, 1)
        return [edit.product_id]
    if edit.op == "swap":
        other = products.get(edit.other_id)
        if other is None or other is item:
            raise KeyError(edit.other_id)
        other_pose = other.setdefault("pose", {})
        other_position = other_pose.setdefault("position", {"x": 0, "y": 0, "z": 0})
        other_rotation = other_pose.setdefault("rotation", {"yaw": 0, "pitch": 0, "roll": 0})
        for key in ("x", "z"):
            position[key], other_position[key] = other_position.get(key, 0), position.get(key, 0)
        rotation["yaw"], other_rotation["yaw"] = other_rotation.get("yaw", 0), rotation.get("yaw", 0)
        return [edit.product_id, edit.other_id]
    if edit.op == "replace":
        replace_product(item, edit.replacement, replacement_size)
        return [edit.product_id]
    config["placed_products"] = [entry for entry in config["placed_products"] if entry is not item]
    return []
