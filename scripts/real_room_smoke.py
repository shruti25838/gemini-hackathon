"""Run one real room photo through Karishma's analyze_room.

    python scripts/real_room_smoke.py --photo room.jpg                 # mocked
    python scripts/real_room_smoke.py --photo room.jpg --use-real-gemini

Without `--use-real-gemini` it runs against her offline fixture and calls
nothing. With the flag it uses the key already in your environment.

It prints the validation result and the opening fields, and nothing else. No
key, no prompt, no raw model output, no room photo path beyond the name you
typed. The opening fields are the point: they are what our fit engine needs,
and a missing hinge or swing direction is the difference between a real
answer and `unknown`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photo", required=True, help="Path to a room photo.")
    parser.add_argument(
        "--use-real-gemini",
        action="store_true",
        help="Call the live model. Without this, her offline fixture answers.",
    )
    parser.add_argument("--session-id", default="sess_smoke")
    args = parser.parse_args()

    photo = Path(args.photo)
    if not photo.is_file():
        print(f"No such photo: {args.photo}")
        return 2

    # Her gemini module reads MOCK_AI from the environment at call time.
    if args.use_real_gemini:
        os.environ["MOCK_AI"] = "0"
    else:
        os.environ["MOCK_AI"] = "1"

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    from ai.karishma.gemini import mock_ai
    from ai.karishma_adapter.provider import RealLaneA
    from backend.validation import validate_room_spec

    rule("1. Mode")
    live = args.use_real_gemini and not mock_ai()
    has_key = bool(
        (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip()
    )
    print(f"  photo        {photo.name}  ({photo.stat().st_size} bytes)")
    print(f"  model        {'live Gemini' if live else 'offline fixture'}")
    print(f"  api key      {'present' if has_key else 'not configured'}")
    if args.use_real_gemini and not has_key:
        print()
        print("  --use-real-gemini was passed but no key is configured.")
        return 2

    rule("2. analyze_room")
    try:
        analysis = RealLaneA().analyze_room(photo.read_bytes(), args.session_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        return 1

    from ai.karishma_adapter.provider import GATE_SET_ERROR

    errored = analysis.gates.gate_set == GATE_SET_ERROR
    if errored:
        status = "ERROR  (the gate could not run; this is NOT a verdict on the photo)"
    elif analysis.gates.all_passed:
        status = "PASSED"
    else:
        status = "REJECTED  (the gate ran and turned the photo down)"

    print(f"  gate status  {status}")
    print(f"  gate set     {analysis.gates.gate_set}")
    print(f"  source       {analysis.source}")
    print()
    print("  raw gate answers:")
    for answer in analysis.gates.answers:
        print(
            f"    {answer.question:26} {answer.answer[:40]:42} "
            f"confidence {answer.confidence:.2f}  passed={answer.passed}"
        )
    for ref in analysis.evidence_refs:
        print(f"  note         {ref}")

    draft = analysis.room_spec_draft
    if draft is None:
        print()
        if errored:
            print("  No RoomSpec draft, because the gate never ran.")
            print("  Fix the gate, do not retake the photo.")
        else:
            print("  No RoomSpec draft: the gate turned this photo down.")
            hints = [a.answer for a in analysis.gates.answers if "rescan" in a.question]
            for hint in hints:
                print(f"    hint: {hint}")
        return 1

    rule("3. Our validator")
    validation = validate_room_spec(draft)
    print(f"  ok           {validation.ok}")
    for error in validation.errors:
        print(f"    ERROR   {error.field_path}: {error.code}")
    for warning in validation.warnings:
        print(f"    warning {warning.field_path}: {warning.code}")

    rule("4. Room")
    # Where this room came from matters more than its numbers.
    print(f"  provenance   analysed from the photo (gate passed)")
    print(f"  draft status validated draft, not a preset")
    print(f"  source label {analysis.source}")
    print(f"  evidence     {analysis.evidence_refs or 'none recorded'}")
    print()
    dims = draft.dimensions_m
    for name, measurement in (
        ("width_m", dims.width_m),
        ("depth_m", dims.depth_m),
        ("height_m", dims.height_m),
    ):
        print(f"  {name:10} {measurement.value_m:6.2f}  {measurement.evidence_status}")

    rule("5. Openings (what the fit engine needs)")
    if not draft.openings:
        print("  none detected")
    for opening in draft.openings:
        print(f"  {opening.opening_id}")
        print(f"    wall           {opening.wall_id}")
        print(f"    type           {opening.type}")
        print(f"    offset_m       {opening.offset_m:.3f}")
        print(f"    width_m        {opening.width_m:.3f}")
        print(f"    height_m       {opening.height_m:.3f}")
        print(f"    sill_m         {opening.sill_m}")
        print(f"    evidence       {opening.evidence_status}")
        if opening.hinge is None:
            if opening.type == "door":
                print("    hinge          NONE -> every fit check on this door is unknown")
            else:
                print("    hinge          not applicable")
            continue
        hinge = opening.hinge
        print(f"    hinge_side     {hinge.hinge_side}")
        print(f"    swing          {hinge.swing_direction}")
        print(f"    max angle      {hinge.swing_angle_max_deg}")
        print(f"    hinge evidence {hinge.evidence_status}")
        print(f"    motion ref     {hinge.motion_evidence_ref}")
        if hinge.hinge_side == "unknown" or hinge.swing_direction == "unknown":
            print("    -> incomplete, so the door swing check returns unknown")

    rule("Note")
    print("  This committed nothing. Lane A proposes; only /api/room/confirm")
    print("  writes, through our commit_revision.")
    return 0 if validation.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
