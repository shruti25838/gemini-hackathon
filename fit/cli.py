"""Command line for the fit engine, for use from a sandbox.

    python -m fit check --input case.json [--output result.json]

Exit codes:

    0   a result of any kind, including does_not_fit and unknown
    2   malformed input, with a JSON error on stderr
    1   an unexpected internal failure, also as JSON

A bad input never produces a traceback. The caller is usually an agent in a
sandbox, and a stack trace is neither actionable nor safe to feed back into a
model, so every input problem is reported as
{"error": {"code", "message", "field_path"}}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from fit import SOLVER_VERSION


class InputError(Exception):
    """A problem with the caller's JSON. Always carries a field path."""

    def __init__(self, code: str, message: str, field_path: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_path = field_path

    def as_json(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "field_path": self.field_path,
            },
            "solver_version": SOLVER_VERSION,
        }


def _require(payload: dict[str, Any], key: str, path: str = "") -> Any:
    if key not in payload or payload[key] is None:
        raise InputError(
            "missing_field",
            f"{key} is required",
            f"{path}.{key}" if path else key,
        )
    return payload[key]


def load_case(raw: str, source: str = "input") -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(
            "invalid_json",
            f"{source} is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            "",
        ) from exc
    if not isinstance(payload, dict):
        raise InputError("invalid_json", f"{source} must be a JSON object", "")
    return payload


def build_inputs(payload: dict[str, Any]):
    """Turn the case JSON into the engine's arguments.

    Every validation failure becomes an InputError naming the field, so the
    caller can fix it without reading Python.
    """
    from pydantic import ValidationError

    from contracts.catalog_offer import CatalogOffer
    from contracts.common import Pose, Rect2D
    from contracts.room_spec import RoomSpec
    from contracts.scene_revision import ObjectPlacement, SceneRevision
    from fit.analysis import ProposedPlacement

    def parse(model, data, path):
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(p) for p in first.get("loc", ()))
            raise InputError(
                "invalid_field",
                f"{path}: {first.get('msg', 'is invalid')}",
                f"{path}.{location}" if location else path,
            ) from exc

    room_spec = parse(RoomSpec, _require(payload, "room_spec"), "room_spec")

    scene_data = payload.get("scene")
    if scene_data is None:
        placements = payload.get("placements") or []
        if not isinstance(placements, list):
            raise InputError("invalid_field", "placements must be a list", "placements")
        scene = SceneRevision(
            scene_revision=int(payload.get("scene_revision", 0)),
            session_id=str(payload.get("session_id", "sandbox")),
            room_revision=room_spec.room_revision,
            placements=[
                parse(ObjectPlacement, item, f"placements.{index}")
                for index, item in enumerate(placements)
            ],
        )
    else:
        scene = parse(SceneRevision, scene_data, "scene")

    placement_data = _require(payload, "placement")
    if not isinstance(placement_data, dict):
        raise InputError("invalid_field", "placement must be an object", "placement")

    pose = parse(Pose, _require(placement_data, "pose", "placement"), "placement.pose")

    footprint = None
    if placement_data.get("footprint") is not None:
        footprint = parse(Rect2D, placement_data["footprint"], "placement.footprint")
    elif payload.get("product") is not None:
        product = payload["product"]
        if not isinstance(product, dict):
            raise InputError("invalid_field", "product must be an object", "product")
        width = product.get("width_m")
        depth = product.get("depth_m")
        if width is not None and depth is not None:
            footprint = parse(
                Rect2D,
                {
                    "x_m": pose.x_m,
                    "y_m": pose.y_m,
                    "width_m": width,
                    "depth_m": depth,
                    "rotation_deg": pose.rotation_deg,
                },
                "product",
            )

    offers = [
        parse(CatalogOffer, item, f"offers.{index}")
        for index, item in enumerate(payload.get("offers") or [])
    ]

    placement = ProposedPlacement(
        object_id=str(_require(placement_data, "object_id", "placement")),
        pose=pose,
        footprint=footprint,
        height_m=placement_data.get("height_m"),
        offer_ref=placement_data.get("offer_ref"),
        replaces_object_id=placement_data.get("replaces_object_id"),
        category=placement_data.get("category"),
        pullback_m=placement_data.get("pullback_m"),
        drawer_extension_m=placement_data.get("drawer_extension_m"),
        facing_deg=placement_data.get("facing_deg"),
    )

    options = payload.get("options") or {}
    if not isinstance(options, dict):
        raise InputError("invalid_field", "options must be an object", "options")

    def number(key: str, default: float) -> float:
        value = options.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise InputError(
                "invalid_field", f"{key} must be a number", f"options.{key}"
            ) from None

    from fit import (
        DEFAULT_CLEARANCE_MARGIN_M,
        DEFAULT_DOOR_SWING_STEPS,
        DEFAULT_MIN_WALKWAY_WIDTH_M,
    )

    return (
        room_spec,
        scene,
        placement,
        offers,
        {
            "clearance_margin_m": number("clearance_margin_m", DEFAULT_CLEARANCE_MARGIN_M),
            "min_walkway_width_m": number("min_walkway_width_m", DEFAULT_MIN_WALKWAY_WIDTH_M),
            "door_swing_steps": int(number("door_swing_steps", DEFAULT_DOOR_SWING_STEPS)),
        },
    )


def run_case(payload: dict[str, Any]) -> dict[str, Any]:
    """The whole job, from parsed JSON to the output document."""
    from fit.analysis import analyze_fit

    room_spec, scene, placement, offers, options = build_inputs(payload)
    analysis = analyze_fit(room_spec, scene, placement, offers or None, **options)
    return analysis.as_dict()


def dump(document: dict[str, Any]) -> str:
    """Deterministic: the same input must give byte-identical output."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fit",
        description="Ethyneon fit engine. Pure geometry, no network.",
    )
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="Check one placement.")
    check.add_argument("--input", required=True, help="Case JSON file, or - for stdin.")
    check.add_argument("--output", default=None, help="Where to write the result JSON.")

    subparsers.add_parser("version", help="Print the solver version.")

    args = parser.parse_args(argv)

    if args.command == "version":
        print(SOLVER_VERSION)
        return 0

    if args.command != "check":
        parser.print_help()
        return 2

    try:
        if args.input == "-":
            raw = sys.stdin.read()
            source = "stdin"
        else:
            path = Path(args.input)
            if not path.is_file():
                raise InputError(
                    "input_not_found", f"no such file: {args.input}", "--input"
                )
            raw = path.read_text(encoding="utf-8")
            source = str(path)

        document = run_case(load_case(raw, source))

    except InputError as bad_input:
        # Never a traceback. The caller is often an agent.
        print(dump(bad_input.as_json()), file=sys.stderr, end="")
        return 2
    except Exception as unexpected:  # noqa: BLE001
        print(
            dump(
                {
                    "error": {
                        "code": "internal_error",
                        "message": f"{type(unexpected).__name__}: {unexpected}",
                        "field_path": "",
                    },
                    "solver_version": SOLVER_VERSION,
                }
            ),
            file=sys.stderr,
            end="",
        )
        return 1

    rendered = dump(document)
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        print(
            f"{document['fit_report']['result']}  ->  {args.output}",
            file=sys.stderr,
        )
    else:
        print(rendered, end="")
    return 0
