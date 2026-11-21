"""Ethyneon Person-A evaluation: gates, room geometry, door motion, and fit vs a one-call baseline.

Usage:
  python -m eval.run_eval eval/data/cases.json [--held-out-only] [--skip-baseline]

cases.json (see cases.example.json): real rooms photographed and tape-measured by people outside
the team. Ground truth geometry -> the solver gives the TRUE fit for each probe desk position.
We then compare:
  baseline  : one Gemini 4 Neon call with the photo (+ door clip) and the question "does it fit?"
  ethyneon  : Decision 1 gate -> Neon RoomSpec -> one confirmed measurement -> Neon door clip -> solver
The dangerous error is a false "fits" (desk actually hits the door), so we report it separately.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import statistics
import sys
import time
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend import gates, gemini, solver, vision  # noqa: E402
from backend.models import NEON  # noqa: E402
from contracts.schemas import DoorMotion  # noqa: E402

DESK = {"w": 1.4, "d": 0.7, "h": 0.75}


class BaselineAnswer(BaseModel):
    verdict: Literal["fits", "no_fit"]
    reason: str


def truth_room(t: dict) -> dict:
    d = t["door"]
    return {"revision": 1, "width_m": {"value_m": t["width_m"]}, "depth_m": {"value_m": t["depth_m"]},
            "height_m": {"value_m": t.get("height_m", 2.5)}, "walls": [], "existing_items": [],
            "openings": [{"id": "door1", "wall": d["wall"], "type": "door", "offset_m": d["offset_m"], "width_m": d["width_m"],
                          "motion": {"hinge_side": d["hinge_side"], "swing": d["swing"]}}]}


def desk_at(room: dict, wall: str, along: float) -> dict:
    x, z, rot = solver._against_wall(room, wall, along, DESK["w"], DESK["d"])
    return {"key": "desk", "category": "desk", **DESK, "x": x, "z": z, "rot": rot}


def fit_label(room: dict, wall: str, along: float) -> str:
    return "no_fit" if solver.check(room, [desk_at(room, wall, along)])["status"] == "no_fit" else "fits"


def load(path: Path) -> tuple[bytes, str]:
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def run_case(c: dict, skip_baseline: bool) -> dict:
    out = {"id": c["id"], "held_out": c.get("held_out", False)}
    img, mime = load(ROOT / c["photo"])
    t = c["truth"]
    troom = truth_room(t)

    # 1. photo gate
    g = gates.room_photo(img, mime)
    out["gate"] = {"passed": g.passed, "answers": g.answers, "latency_ms": g.latency_ms}
    lab = c.get("labels", {})
    out["gate_correct"] = {k: (g.answers.get(k, {}).get("noul", 0) >= 0.5) == v for k, v in lab.items() if k in g.answers}

    # 2. RoomSpec + anchoring
    t0 = time.time()
    spec, _ = vision.photo_to_roomspec(img, mime)
    out["roomspec_ms"] = int((time.time() - t0) * 1000)
    out["width_err_raw"] = abs(spec.width_m.value_m - t["width_m"])
    out["depth_err_raw"] = abs(spec.depth_m.value_m - t["depth_m"])
    anchored = vision.anchor_measurement(spec, "width_m", t["width_m"])
    out["depth_err_anchored"] = abs(anchored.depth_m.value_m - t["depth_m"])
    doors = [o for o in anchored.openings if o.type == "door" and o.wall == t["door"]["wall"]]
    out["door_found"] = bool(doors)
    if doors:
        out["door_offset_err"] = abs(doors[0].offset_m - t["door"]["offset_m"])

    # 3. door clip
    if c.get("door_clip") and doors:
        vid, vmime = load(ROOT / c["door_clip"])
        dd, _ = vision.clip_to_door_motion(gemini.video_part(vid, vmime), "")
        out["hinge_correct"] = dd.hinge_side == t["door"]["hinge_side"]
        out["swing_correct"] = dd.swing == t["door"]["swing"]
        anchored, problem = vision.apply_door(anchored, dd, doors[0].id)
        out["door_problem"] = problem or None

    # 4. fit probes: truth vs ethyneon vs baseline
    sroom = anchored.model_dump()
    probes = []
    for along in c.get("desk_probe_along", []):
        truth = fit_label(troom, t["door"]["wall"], along)
        st = solver.check(sroom, [desk_at(sroom, t["door"]["wall"], along)])["status"]
        eth = {"no_fit": "no_fit", "unknown": "unknown"}.get(st, "fits")
        p = {"along_m": along, "truth": truth, "ethyneon": eth}
        if not skip_baseline:
            parts = [gemini.image_part(img, mime)]
            if c.get("door_clip"):
                parts.append(gemini.video_part(*load(ROOT / c["door_clip"])))
            parts.append(gemini.text_part(
                f"The {t['door']['wall']} wall is {t['width_m'] if t['door']['wall'] in ('back','front') else t['depth_m']} m long. "
                f"I want a {DESK['w']} x {DESK['d']} m desk against the {t['door']['wall']} wall, its centre {along} m from the "
                f"wall's start (the left end when facing it from inside). Will it fit without blocking the door's swing?"))
            b, _ = gemini.generate_structured(NEON, parts, BaselineAnswer, thinking="high")
            p["baseline"] = b.verdict
        probes.append(p)
    out["probes"] = probes
    return out


def summarize(results: list[dict]) -> str:
    def rate(xs):
        xs = [x for x in xs if x is not None]
        return f"{sum(xs)}/{len(xs)}" if xs else "n/a"

    probes = [p for r in results for p in r["probes"]]
    lines = [f"Cases: {len(results)} (held-out: {sum(r['held_out'] for r in results)}), fit probes: {len(probes)}", ""]
    med = lambda k: f"{statistics.median([r[k] for r in results if k in r]):.2f} m" if any(k in r for r in results) else "n/a"
    lines += ["| Metric | Result |", "|---|---|",
              f"| Width error, raw photo estimate (median) | {med('width_err_raw')} |",
              f"| Depth error, raw (median) | {med('depth_err_raw')} |",
              f"| Depth error after 1 confirmed measurement (median) | {med('depth_err_anchored')} |",
              f"| Door found on correct wall | {rate([r['door_found'] for r in results])} |",
              f"| Hinge side correct (clip) | {rate([r.get('hinge_correct') for r in results])} |",
              f"| Swing direction correct (clip) | {rate([r.get('swing_correct') for r in results])} |",
              f"| Photo gate labels correct | {rate([v for r in results for v in r['gate_correct'].values()])} |", ""]
    for sysname in ("ethyneon", "baseline"):
        ps = [p for p in probes if sysname in p]
        if not ps:
            continue
        correct = sum(p[sysname] == p["truth"] for p in ps)
        false_fit = sum(p[sysname] == "fits" and p["truth"] == "no_fit" for p in ps)
        unknown = sum(p[sysname] == "unknown" for p in ps)
        lines.append(f"**{sysname}**: {correct}/{len(ps)} fit verdicts correct, {false_fit} false 'fits' "
                     f"(desk would hit the door), {unknown} honest 'unknown'")
    fails = [(r["id"], p) for r in results for p in r["probes"] if p.get("ethyneon") not in (p["truth"], "unknown")]
    if fails:
        lines += ["", "Ethyneon failures to discuss:"] + [f"- {cid}: {p}" for cid, p in fails[:5]]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("--held-out-only", action="store_true")
    ap.add_argument("--skip-baseline", action="store_true")
    a = ap.parse_args()
    cases = json.loads(Path(a.cases).read_text())["cases"]
    if a.held_out_only:
        cases = [c for c in cases if c.get("held_out")]
    results = []
    for c in cases:
        print(f"-> {c['id']}", file=sys.stderr)
        try:
            results.append(run_case(c, a.skip_baseline))
        except Exception as e:
            print(f"   failed: {type(e).__name__}: {e}", file=sys.stderr)
            results.append({"id": c["id"], "held_out": c.get("held_out", False), "error": str(e), "probes": [], "gate_correct": {},
                            "door_found": None})
    outdir = ROOT / "eval" / "results"
    outdir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (outdir / f"{stamp}.json").write_text(json.dumps(results, indent=1, default=str))
    report = summarize(results)
    (outdir / f"{stamp}.md").write_text(report)
    print(report)


if __name__ == "__main__":
    main()
