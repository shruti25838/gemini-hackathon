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

from . import gemini
from ._vendor import solver
from .models import ANTIGRAVITY_AGENT, ARGON, CARBON

SOLVER_SRC = (Path(__file__).resolve().parent / "_vendor" / "solver.py").read_text()
AGENT_TIMEOUT_S = float(os.getenv("AGENT_TIMEOUT_S", "120"))
AGENT_MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "120000"))
MAX_REPAIRS = int(os.getenv("AGENT_MAX_REPAIRS", "1"))
CARBON_TIMEOUT_S = float(os.getenv("CARBON_TIMEOUT_S", "240"))
CARBON_MAX_TOKENS = int(os.getenv("CARBON_MAX_TOKENS", "60000"))


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
   return a layout that `solver.py check` reports as does_not_fit.
Only use product keys present in input.json. Do not invent products.
Your FINAL message must be ONLY this JSON object (not the solver's output):
{"placements":[{"key":"...","x":0.0,"z":0.0,"rot":0}], "commands_run":["..."],
 "style_assessment":"two sentences", "alternatives_considered":0, "unresolved":[]}"""

TESTER_SYSTEM = """You are an independent tester. /workspace contains solver.py and input.json with a
PROPOSED layout. Be quick and concrete:
1. `python solver.py check input.json`
2. `python solver.py probe input.json` (door swing at 15-180 degrees)
3. One tolerance test: copy input.json with every unlocked product's w and d increased by 0.03, run check on it.
Report each real problem with the exact command that reproduces it. If nothing fails, verdict=pass.
You cannot change the layout.
Your FINAL message must be ONLY this JSON object:
{"verdict":"pass|fail", "findings":[{"kind":"door_swing|overlap|out_of_bounds|clearance|tolerance|access",
 "item_key":"...","other":"...","detail":"...","angle_deg":null,"command":"..."}], "commands_run":["..."]}"""


def _run_agent(model: str, system: str, task: str, payload: dict, out: type[BaseModel],
               on_event=None, previous_id: Optional[str] = None, timeout_s: Optional[float] = None,
               max_tokens: Optional[int] = None) -> tuple[BaseModel, dict]:
    timeout_s = timeout_s or AGENT_TIMEOUT_S
    if gemini.mock_ai():
        raise AgentUnavailable("MOCK_AI: sandbox agents are not called")
    env = {"sources": [
        {"type": "inline", "content": SOLVER_SRC, "target": "/workspace/solver.py"},
        {"type": "inline", "content": json.dumps(payload, indent=1), "target": "/workspace/input.json"},
    ]}
    kwargs = dict(
        agent=ANTIGRAVITY_AGENT,
        environment=env,
        agent_config={"type": "antigravity", "model": model, "max_total_tokens": max_tokens or AGENT_MAX_TOKENS},
        system_instruction=system,
        input=task,
        response_format={"type": "text", "mime_type": "application/json", "schema": gemini.inline_schema(out)},
        background=True,
    )
    if previous_id:
        kwargs["previous_interaction_id"] = previous_id
    t0 = time.time()
    try:
        it = gemini.beta_client().interactions.create(**kwargs)
        while it.status in ("in_progress", "queued"):
            if time.time() - t0 > timeout_s:
                raise AgentUnavailable(f"{model} agent timed out after {timeout_s:.0f}s")
            time.sleep(2)
            it = gemini.beta_client().interactions.get(id=it.id)
            if on_event:
                on_event("poll", it)
        if it.status != "completed":
            raise AgentUnavailable(f"{model} agent ended with status {it.status}: {getattr(it, 'errors', None)}")
        executed = _executed_commands(it)
        parsed = _coerce(out, _extract_json(it.output_text or ""), executed)
    except AgentUnavailable:
        raise
    except Exception as e:
        raise AgentUnavailable(f"{model} agent failed: {type(e).__name__}: {str(e)[:200]}") from e
    meta = {"model": model, "interaction_id": it.id, "environment_id": it.environment_id,
            "latency_ms": int((time.time() - t0) * 1000),
            "usage": it.usage.model_dump() if getattr(it, "usage", None) else None,
            "executed_in_sandbox": executed}
    return parsed, meta


def _extract_json(text: str) -> dict:
    """Agents don't enforce response_format: take the last JSON object in the final message."""
    t = text.strip()
    if "```" in t:
        blocks = [b.split("\n", 1)[-1] if b.lstrip().startswith(("json", "\n")) or "{" not in b[:5] else b
                  for b in t.split("```")[1::2]]
        t = blocks[-1] if blocks else t
    start = t.find("{")
    if start < 0:
        raise ValueError("no JSON object in agent output")
    obj, _ = json.JSONDecoder().raw_decode(t[start:])
    return obj


def _executed_commands(it) -> list[str]:
    """Commands the sandbox actually executed (from interaction steps), not self-reported ones."""
    out = []
    for st in it.steps or []:
        if st.type in ("code_execution_call", "function_call"):
            args = getattr(st, "arguments", None)
            if hasattr(args, "model_dump"):
                args = args.model_dump()
            if isinstance(args, dict):
                args = args.get("code") or args.get("command") or args.get("cmd") or json.dumps(args)[:200]
            out.append(str(args)[:200] if args else st.type)
    return out


def _coerce(out: type[BaseModel], obj: dict, executed: list[str]) -> BaseModel:
    """Accept the requested schema, or the solver's own output shape, and fill evidence from real steps."""
    try:
        m = out.model_validate(obj)
    except Exception:
        if out is Proposal and isinstance(obj.get("placements"), list):
            m = Proposal(placements=[ProposedPose(key=p["key"], x=p["x"], z=p["z"], rot=p.get("rot", 0))
                                     for p in obj["placements"] if {"key", "x", "z"} <= set(p)],
                         commands_run=[], style_assessment="", alternatives_considered=len(obj.get("rejected", [])),
                         unresolved=[])
        elif out is TestReport and ("status" in obj or "collisions" in obj or "angles" in obj):
            cols = obj.get("collisions", []) + [c for a in obj.get("angles", []) for c in a.get("collisions", [])]
            m = TestReport(verdict="fail" if cols or obj.get("status") == "does_not_fit" else "pass",
                           findings=[Finding(kind=c.get("kind", "overlap") if c.get("kind") in Finding.model_fields["kind"].annotation.__args__ else "overlap",
                                             item_key=c["a"], other=c["b"], detail=c.get("detail", ""),
                                             command="python solver.py check input.json") for c in cols],
                           commands_run=[])
        else:
            raise
    if hasattr(m, "commands_run") and executed:
        m.commands_run = executed
    return m


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


def _max_door_angle(room: dict) -> float:
    angs = [(o.get("motion") or {}).get("max_angle_deg", 90) for o in room.get("openings", []) if o["type"] == "door"]
    return max(angs) if angs else 90


def server_probe_findings(room: dict, placements: list[dict]) -> list[dict]:
    """Server-side probe. Angles beyond the door's observed opening are advisory, not failures."""
    amax = _max_door_angle(room)
    seen, out = set(), []
    for a in solver.probe(room, placements)["angles"]:
        for c in a["collisions"]:
            beyond = a["angle_deg"] > amax
            if (c["a"], beyond) in seen:
                continue
            seen.add((c["a"], beyond))
            out.append({"kind": "door_swing", "item_key": c["a"], "other": c["b"], "angle_deg": a["angle_deg"],
                        "detail": c["detail"] + (f" (only if opened past the observed {amax:.0f} deg)" if beyond else ""),
                        "command": "python solver.py probe input.json", "reproduced_by_server": True, "advisory": beyond})
    return out


def verify_findings(room: dict, placements: list[dict], findings: list[Finding]) -> list[dict]:
    """Server reproduces each tester finding with its own solver run."""
    base = solver.check(room, placements)
    amax = _max_door_angle(room)
    out = []
    for f in findings:
        reproduced = False
        beyond = f.kind == "door_swing" and f.angle_deg is not None and f.angle_deg > amax
        if f.kind in ("door_swing", "overlap", "out_of_bounds"):
            reproduced = any(c["a"] in (f.item_key, f.other) or c["b"] in (f.item_key, f.other)
                             for c in base["collisions"])
            if not reproduced and f.kind == "door_swing":
                reproduced = any(p["item_key"] == f.item_key for p in server_probe_findings(room, placements))
        out.append({**f.model_dump(), "reproduced_by_server": reproduced,
                    "advisory": beyond or f.kind in ("tolerance", "access", "clearance")})
    return out


def propose_and_test(room: dict, fixed: list[dict], items: list[dict], brief: dict, emit) -> dict:
    """Full proposal loop. `emit(step, status, detail, **data)` feeds the activity panel."""
    payload = {"room": room, "placements": fixed + items, "brief": brief}
    used_agents = True
    completed: list[str] = []
    try:
        emit("argon.propose", "started", "Argon is searching layouts in a sandbox")
        prop, pmeta = _run_agent(ARGON, PROPOSER_SYSTEM, "Propose the layout for the products in input.json.",
                                 payload, Proposal)
        placements, rej = _apply_poses(fixed + items, prop.placements)
        completed.append("argon-sandbox")
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
                                         {"room": room, "placements": placements, "brief": brief}, TestReport,
                                         timeout_s=CARBON_TIMEOUT_S, max_tokens=CARBON_MAX_TOKENS)
                test_findings = verify_findings(room, placements, test.findings)
                if "carbon-sandbox" not in completed:
                    completed.append("carbon-sandbox")
                n_rep = sum(f["reproduced_by_server"] for f in test_findings)
                emit("carbon.test", "ok" if test.verdict == "pass" else "warn",
                     f"Carbon verdict: {test.verdict}; {len(test_findings)} findings, {n_rep} reproduced by server",
                     findings=test_findings, interaction_id=tmeta["interaction_id"],
                     environment_id=tmeta["environment_id"], latency_ms=tmeta["latency_ms"])
            except AgentUnavailable as e:
                emit("carbon.test", "warn", f"{e}. Falling back to the server probe.")
                test_findings = server_probe_findings(room, placements)
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

    report["checked_by"] = ["server"] + (completed or ["local-fallback"])
    return {"placements": placements, "report": report, "findings": test_findings, "repairs": repairs,
            "style_assessment": prop.style_assessment if prop else None, "used_agents": used_agents}


def review_move(room: dict, placements: list[dict], key: str, x: float, z: float, rot: int, brief: dict, emit,
                use_agents: bool = False) -> dict:
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
    fixed = [dict(p, locked=True) for p in others]
    if use_agents:
        alt = propose_and_test(room, fixed, [item], brief, emit)
    else:  # interactive drag: answer in < 1 s with the server solver; sandbox review can run after commit
        near = dict(item, x=x, z=z, rot=rot)
        res = solver.search(room, fixed, [near], brief.get("preferred_wall"))
        res["report"]["checked_by"] = ["server"]
        alt = {"placements": res["placements"], "report": res["report"], "findings": [], "repairs": 0,
               "style_assessment": None, "used_agents": False}
        emit("move.alternative", "ok" if res["report"]["status"] in ("fits", "tight") else "warn",
             f"Checked alternative ready: {res['report']['status']}", report=res["report"])
    return {"accepted": False, "placements": requested, "report": rep, "conflicts": involved, "alternative": alt}
