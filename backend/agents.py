"""Managed Agent proposal / independent test / bounded repair.

Argon  (Antigravity sandbox #1): runs solver.py search, ranks for the shopper's taste,
                                 proposes placements. Also does repairs.
Carbon (Antigravity sandbox #2, fresh): independently tests the proposal with the same
                                 solver plus its own probes (door angles, dimension tolerance).
Server: re-runs solver.check / probe on every claim. Only server-verified results reach
        the scene. Agents never touch cart, approval or checkout.

If an agent is unavailable or times out, the deterministic local solver is used and
the activity panel says so.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import gemini, solver
from .models import ANTIGRAVITY_AGENT, ARGON, CARBON

SOLVER_SRC = (Path(__file__).parent / "solver.py").read_text()
AGENT_TIMEOUT_S = float(os.getenv("AGENT_TIMEOUT_S", "120"))
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "120000"))
MAX_REPAIRS = int(os.getenv("AGENT_MAX_REPAIRS", "1"))


class AgentUnavailable(RuntimeError):
    pass


# ---------- agent output contracts ----------
class ProposedPose(BaseModel):
    key: str
    x: float
    z: float
    rot: Literal[0, 90, 180, 270]


class Proposal(BaseModel):
    placements: list[ProposedPose]
    commands_run: list[str] = Field(description="exact shell commands you ran")
    style_assessment: str = Field(description="2 sentences: how this layout serves the shopper's stated taste")
    alternatives_considered: int
    unresolved: list[str]


class Finding(BaseModel):
    kind: Literal["door_swing", "overlap", "out_of_bounds", "clearance", "tolerance", "access"]
    item_key: str
    other: str
    detail: str
    angle_deg: Optional[float] = None
    command: str = Field(description="the command that reproduces this finding")


class TestReport(BaseModel):
    verdict: Literal["pass", "fail"]
    findings: list[Finding]
    commands_run: list[str]


PROPOSER_SYSTEM = """You are the layout planner for Ethyneon. /workspace contains solver.py (the
authoritative fit checker) and input.json (room, fixed items, products to place, shopper brief).
1. Run `python solver.py search input.json` to get collision-free candidates.
2. You may try other positions by editing a copy of input.json and running `python solver.py check`.
3. Prefer positions that serve the brief (style, preferred wall, keeping the door clear) but NEVER
   return a layout that `solver.py check` reports as no_fit.
Only use product keys present in input.json. Do not invent products. Return JSON only."""

TESTER_SYSTEM = """You are an independent tester. /workspace contains solver.py and input.json with a
PROPOSED layout. Try to break it:
- `python solver.py check input.json` and `python solver.py probe input.json` (door at 15-180 degrees)
- re-check with every product footprint enlarged by 3 cm (manufacturing tolerance)
- check that a 0.6 m path from the door to the desk front is not blocked
Report each real problem with the exact command that reproduces it. If nothing fails, verdict=pass.
You cannot change the layout. Return JSON only."""


def _run_agent(model: str, system: str, task: str, payload: dict, out: type[BaseModel],
               on_event=None, previous_id: Optional[str] = None) -> tuple[BaseModel, dict]:
    env = {"sources": [
        {"type": "inline", "content": SOLVER_SRC, "target": "/workspace/solver.py"},
        {"type": "inline", "content": json.dumps(payload, indent=1), "target": "/workspace/input.json"},
    ]}
    kwargs = dict(
        agent=ANTIGRAVITY_AGENT,
        environment=env,
        agent_config={"type": "antigravity", "model": model, "max_total_tokens": AGENT_MAX_TOKENS},
        system_instruction=system,
        input=task,
        response_format={"type": "text", "mime_type": "application/json", "schema": gemini.inline_schema(out)},
        response_mime_type="application/json",
        background=True,
    )
    if previous_id:
        kwargs["previous_interaction_id"] = previous_id
    t0 = time.time()
    try:
        it = gemini.beta_client().interactions.create(**kwargs)
        while it.status in ("in_progress", "queued"):
            if time.time() - t0 > AGENT_TIMEOUT_S:
                raise AgentUnavailable(f"{model} agent timed out after {AGENT_TIMEOUT_S:.0f}s (interaction {it.id})")
            time.sleep(2)
            it = gemini.beta_client().interactions.get(id=it.id)
            if on_event:
                on_event("poll", it)
        if it.status != "completed":
            raise AgentUnavailable(f"{model} agent ended with status {it.status}: {getattr(it, 'errors', None)}")
        parsed = out.model_validate_json(it.output_text or "")
    except AgentUnavailable:
        raise
    except Exception as e:
        raise AgentUnavailable(f"{model} agent failed: {type(e).__name__}: {str(e)[:200]}") from e
    meta = {"model": model, "interaction_id": it.id, "environment_id": it.environment_id,
            "latency_ms": int((time.time() - t0) * 1000),
            "usage": it.usage.model_dump() if getattr(it, "usage", None) else None}
    return parsed, meta


# ---------- orchestration ----------
def _apply_poses(placements: list[dict], poses: list[ProposedPose]) -> tuple[list[dict], list[str]]:
    by_key = {p["key"]: dict(p) for p in placements}
    rejected = []
    for pose in poses:
        if pose.key not in by_key:
            rejected.append(f"unknown key {pose.key}")
            continue
        if by_key[pose.key].get("locked"):
            rejected.append(f"{pose.key} is locked")
            continue
        by_key[pose.key].update(x=pose.x, z=pose.z, rot=pose.rot)
    return list(by_key.values()), rejected


def verify_findings(room: dict, placements: list[dict], findings: list[Finding]) -> list[dict]:
    """Server reproduces each tester finding with its own solver run."""
    base = solver.check(room, placements)
    probe = solver.probe(room, placements)
    out = []
    for f in findings:
        reproduced = False
        if f.kind in ("door_swing", "overlap", "out_of_bounds"):
            reproduced = any(c["a"] in (f.item_key, f.other) or c["b"] in (f.item_key, f.other)
                             for c in base["collisions"])
            if not reproduced and f.kind == "door_swing":
                reproduced = any(c["a"] == f.item_key for a in probe["angles"] for c in a["collisions"])
        out.append({**f.model_dump(), "reproduced_by_server": reproduced,
                    "advisory": f.kind in ("tolerance", "access", "clearance")})
    return out


def propose_and_test(room: dict, fixed: list[dict], items: list[dict], brief: dict, emit) -> dict:
    """Full proposal loop. `emit(step, status, detail, **data)` feeds the activity panel."""
    payload = {"room": room, "placements": fixed + items, "brief": brief}
    used_agents = True
    try:
        emit("argon.propose", "started", "Argon is searching layouts in a sandbox")
        prop, pmeta = _run_agent(ARGON, PROPOSER_SYSTEM, "Propose the layout for the products in input.json.",
                                 payload, Proposal)
        placements, rej = _apply_poses(fixed + items, prop.placements)
        emit("argon.propose", "ok", prop.style_assessment, interaction_id=pmeta["interaction_id"],
             environment_id=pmeta["environment_id"], commands=prop.commands_run, ignored=rej,
             latency_ms=pmeta["latency_ms"])
    except AgentUnavailable as e:
        used_agents = False
        emit("argon.propose", "warn", f"{e}. Using the deterministic solver instead.")
        res = solver.search(room, fixed, items, brief.get("preferred_wall"))
        placements, prop, pmeta = res["placements"], None, None

    report = solver.check(room, placements)
    emit("server.check", "ok" if report["status"] in ("fits", "tight") else "warn",
         f"Server re-check: {report['status']}", report=report)

    test_findings: list[dict] = []
    repairs = 0
    while True:
        if used_agents:
            try:
                emit("carbon.test", "started", "Carbon is testing the layout in a separate sandbox")
                test, tmeta = _run_agent(CARBON, TESTER_SYSTEM, "Test this proposed layout.",
                                         {"room": room, "placements": placements, "brief": brief}, TestReport)
                test_findings = verify_findings(room, placements, test.findings)
                n_rep = sum(f["reproduced_by_server"] for f in test_findings)
                emit("carbon.test", "ok" if test.verdict == "pass" else "warn",
                     f"Carbon verdict: {test.verdict}; {len(test_findings)} findings, {n_rep} reproduced by server",
                     findings=test_findings, interaction_id=tmeta["interaction_id"],
                     environment_id=tmeta["environment_id"], latency_ms=tmeta["latency_ms"])
            except AgentUnavailable as e:
                emit("carbon.test", "warn", f"{e}. Falling back to server probe.")
                pr = solver.probe(room, placements)
                test_findings = [{"kind": "door_swing", "item_key": c["a"], "other": c["b"], "detail": c["detail"],
                                  "angle_deg": a["angle_deg"], "reproduced_by_server": True, "advisory": False}
                                 for a in pr["angles"] for c in a["collisions"]]
        hard = [f for f in test_findings if f["reproduced_by_server"] and not f["advisory"]]
        if report["status"] in ("fits", "tight") and not hard:
            break
        if repairs >= MAX_REPAIRS:
            emit("repair", "needs_input", "Could not find a layout that passes every check. Which constraint can move?",
                 report=report, findings=hard)
            break
        repairs += 1
        problems = report["collisions"] + hard
        try:
            if not (used_agents and pmeta):
                raise AgentUnavailable("no proposer session")
            emit("argon.repair", "started", f"Argon repairing {len(problems)} problem(s)")
            fix, fmeta = _run_agent(ARGON, PROPOSER_SYSTEM,
                                    "The independent tester and the server found these problems. Move only the affected "
                                    "items and keep everything else. Problems:\n" + json.dumps(problems, indent=1),
                                    {"room": room, "placements": placements, "brief": brief}, Proposal,
                                    previous_id=pmeta["interaction_id"])
            placements, _ = _apply_poses(placements, fix.placements)
            emit("argon.repair", "ok", fix.style_assessment, interaction_id=fmeta["interaction_id"])
        except AgentUnavailable as e:
            emit("argon.repair", "warn", f"{e}. Repairing with deterministic solver.")
            bad = {f["item_key"] for f in problems if "item_key" in f} | {c["a"] for c in report["collisions"]}
            keep = [p for p in placements if p["key"] not in bad]
            res = solver.search(room, keep, [p for p in placements if p["key"] in bad], brief.get("preferred_wall"))
            placements = res["placements"]
        report = solver.check(room, placements)
        emit("server.check", "ok" if report["status"] in ("fits", "tight") else "warn",
             f"Server re-check after repair: {report['status']}", report=report)

    report["checked_by"] = ["server"] + (["argon-sandbox", "carbon-sandbox"] if used_agents else ["local-fallback"])
    return {"placements": placements, "report": report, "findings": test_findings, "repairs": repairs,
            "style_assessment": prop.style_assessment if prop else None, "used_agents": used_agents}


def review_move(room: dict, placements: list[dict], key: str, x: float, z: float, rot: int, brief: dict, emit) -> dict:
    """Shopper asked to move one item. Check the requested pose; on conflict, find a checked alternative."""
    requested = [dict(p, x=x, z=z, rot=rot) if p["key"] == key else p for p in placements]
    rep = solver.check(room, requested)
    involved = [c for c in rep["collisions"] if key in (c["a"], c["b"])]
    emit("move.check", "ok" if not involved else "warn",
         "Requested position is clear" if not involved else involved[0]["detail"], report=rep)
    if not involved:
        return {"accepted": True, "placements": requested, "report": rep, "alternative": None}
    others = [p for p in placements if p["key"] != key]
    item = next(p for p in placements if p["key"] == key)
    alt = propose_and_test(room, [dict(p, locked=True) for p in others], [item], brief, emit)
    return {"accepted": False, "placements": requested, "report": rep, "conflicts": involved, "alternative": alt}
