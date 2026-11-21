"""Neon-generated workflow plans, validated and executed by code.

Neon proposes a DAG over REGISTERED ops only. validate_plan() rejects unknown ops,
bad args, missing deps and cycles, then inserts mandatory steps. execute() runs the
DAG layer by layer, emits activity events, and pauses on needs_input so the run can be
resumed after the shopper answers.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from contracts.schemas import Placement, PlanStep, SceneRevision, WorkflowPlan

from . import agents, gates, gemini, shop, solver
from .models import NEON
from .state import Session

import os

# "background" (default): server solver commits instantly, Argon/Carbon review runs after.
# "inline": wait for the full sandbox loop before committing. "off": server solver only.
AGENT_REVIEW = os.getenv("ETHYNEON_AGENT_REVIEW", "background")


class NeedsInput(Exception):
    def __init__(self, request: dict):
        super().__init__(request.get("prompt", "input needed"))
        self.request = request


class PlanError(ValueError):
    pass


# ---------- op argument schemas ----------
class NoArgs(BaseModel):
    pass


class StyleArgs(BaseModel):
    wall_id: Optional[Literal["back", "right", "front", "left", "all"]] = None
    color_hex: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    lighting: Optional[Literal["daylight", "evening", "rgb"]] = None
    accent_hex: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class SwapArgs(BaseModel):
    category: Literal["desk", "chair", "monitor", "lighting", "storage"]
    finish: str


class DiscoverArgs(BaseModel):
    categories: list[Literal["desk", "chair", "monitor", "lighting", "storage"]] = []


OP_DOCS = {
    "inspect_room_motion": ("Check door hinge/swing evidence for doors near planned furniture.", NoArgs),
    "confirm_measurement_if_needed": ("Pause and ask the shopper for one real measurement if none is confirmed.", NoArgs),
    "discover_shopify_variants": ("Fetch verified Shopify offers and pick variants within budget and style.", DiscoverArgs),
    "plan_and_test_layout": ("Argon proposes placements in a sandbox, Carbon tests them in another, server verifies.", NoArgs),
    "apply_style_edit": ("Change wall color, lighting or accent color. No product change.", StyleArgs),
    "swap_finish": ("Swap a selected product to another verified variant/finish of the same category.", SwapArgs),
    "commit_scene_preview": ("Commit the verified result as a new scene revision.", NoArgs),
}


# ---------- planning ----------
class DraftStep(BaseModel):
    id: str
    op: Literal[tuple(OP_DOCS)]  # type: ignore[valid-type]
    after: list[str]
    args_json: str = Field(description="JSON object of arguments for the op, '{}' if none")


class DraftPlan(BaseModel):
    goal: str
    steps: list[DraftStep]
    rationale: str


PLANNER_SYSTEM = "You are Ethyneon's workflow planner. Build the smallest DAG of registered operations that " \
    "achieves the shopper's request given the room evidence. Independent steps must not depend on each other " \
    "so they can run in parallel. Operations:\n" + "\n".join(f"- {k}: {v[0]} Args: {v[1].model_json_schema().get('properties', {})}" for k, v in OP_DOCS.items())


def room_summary(s: Session) -> str:
    r = s.room
    if not r:
        return "No room model yet."
    doors = [f"{o.id} on {o.wall} wall, motion={'known' if o.motion else 'unknown'}" for o in r.openings if o.type == "door"]
    confirmed = [k for k in ("width_m", "depth_m", "height_m") if getattr(r, k).source == "confirmed"]
    sel = [f"{p.category}:{p.key}" for p in (s.scene.placements if s.scene else [])]
    return (f"Room {r.width_m.value_m}x{r.depth_m.value_m} m, confirmed dims: {confirmed or 'none'}; doors: {doors or 'none'}; "
            f"existing items: {[i.label for i in r.existing_items]}; current scene items: {sel or 'none'}.")


def propose_plan(s: Session, request: str, route: str) -> tuple[WorkflowPlan, dict]:
    import json

    prompt = (f"Shopper request: {request}\nRouted as: {route}\nBrief: {s.brief.model_dump_json()}\n"
              f"Room evidence: {room_summary(s)}")
    try:
        d, meta = gemini.generate_structured(NEON, prompt, DraftPlan, system=PLANNER_SYSTEM, thinking="medium")
        steps = []
        for st in d.steps:
            try:
                args = json.loads(st.args_json or "{}")
            except json.JSONDecodeError:
                args = {}
            steps.append(PlanStep(id=st.id, op=st.op, after=st.after, args=args))
        plan = WorkflowPlan(plan_id=uuid.uuid4().hex[:8], goal=d.goal, steps=steps)
        meta["rationale"] = d.rationale
        meta["source"] = "neon"
    except gemini.GeminiError as e:
        plan, meta = template_plan(route), {"source": "template", "error": str(e)}
    return validate_plan(s, plan), meta


def template_plan(route: str) -> WorkflowPlan:
    """Deterministic fallback when Neon is unavailable (labeled in the UI)."""
    T = {
        "style_edit": [("style", "apply_style_edit", [], {}), ("commit", "commit_scene_preview", ["style"], {})],
        "product_search": [("room", "inspect_room_motion", [], {}), ("shop", "discover_shopify_variants", [], {}),
                           ("scale", "confirm_measurement_if_needed", ["room"], {}),
                           ("plan", "plan_and_test_layout", ["shop", "scale"], {}),
                           ("commit", "commit_scene_preview", ["plan"], {})],
    }
    steps = T.get(route, T["product_search"])
    return WorkflowPlan(plan_id=uuid.uuid4().hex[:8], goal=route,
                        steps=[PlanStep(id=i, op=o, after=a, args=g) for i, o, a, g in steps])


def validate_plan(s: Session, plan: WorkflowPlan) -> WorkflowPlan:
    ids = [st.id for st in plan.steps]
    if len(ids) != len(set(ids)):
        raise PlanError("duplicate step ids")
    if not plan.steps:
        raise PlanError("empty plan")
    for st in plan.steps:
        if st.op not in OP_DOCS:
            raise PlanError(f"unregistered op {st.op}")
        try:
            st.args = OP_DOCS[st.op][1](**st.args).model_dump(exclude_none=True)
        except ValidationError as e:
            raise PlanError(f"bad args for {st.id}/{st.op}: {e.errors()[0]['msg']}")
        missing = [a for a in st.after if a not in ids]
        if missing:
            raise PlanError(f"{st.id} depends on unknown steps {missing}")
    _toposort(plan.steps)  # raises on cycle

    ops = {st.op for st in plan.steps}
    by_op = {st.op: st for st in plan.steps}
    # Mandatory: any layout work is preceded by a scale check and door inspection.
    if "plan_and_test_layout" in ops:
        lay = by_op["plan_and_test_layout"]
        for op, sid in (("confirm_measurement_if_needed", "_scale"), ("inspect_room_motion", "_door"),
                        ("discover_shopify_variants", "_shop")):
            if op not in ops:
                plan.steps.insert(0, PlanStep(id=sid, op=op, after=[], args={}))
                ids.append(sid)
            dep = by_op.get(op, plan.steps[0]).id
            if dep not in lay.after:
                lay.after.append(dep)
    # Mandatory: every plan that changes the scene ends with a commit after all other steps.
    if not ops & {"commit_scene_preview"}:
        plan.steps.append(PlanStep(id="_commit", op="commit_scene_preview", after=[], args={}))
    commit = next(st for st in plan.steps if st.op == "commit_scene_preview")
    commit.after = [st.id for st in plan.steps if st is not commit]
    _toposort(plan.steps)
    plan.base_room_revision = s.room.revision if s.room else 0
    plan.base_scene_rev = s.scene.rev if s.scene else 0
    return plan


def _toposort(steps: list[PlanStep]) -> list[list[PlanStep]]:
    remaining = {st.id: st for st in steps}
    done: set[str] = set()
    layers = []
    while remaining:
        ready = [st for st in remaining.values() if all(a in done for a in st.after)]
        if not ready:
            raise PlanError(f"cycle among {list(remaining)}")
        layers.append(ready)
        for st in ready:
            done.add(st.id)
            del remaining[st.id]
    return layers


# ---------- ops ----------
def _working_scene(s: Session, ctx: dict) -> SceneRevision:
    if "scene" not in ctx:
        base = s.scene or SceneRevision(rev=0, room_revision=s.room.revision if s.room else 0)
        ctx["scene"] = base.model_copy(deep=True)
    return ctx["scene"]


def op_inspect_room_motion(s: Session, args: dict, ctx: dict):
    doors = [o for o in s.room.openings if o.type == "door"] if s.room else []
    unknown = [o.id for o in doors if not o.motion]
    if unknown and s.brief.keep_door_clear:
        s.emit("inspect_room_motion", "warn",
               f"Door swing for {', '.join(unknown)} is unknown. Record the door opening, or fit near it stays 'unknown'.")
    else:
        s.emit("inspect_room_motion", "ok", f"{len(doors)} door(s); motion known for {len(doors) - len(unknown)}")


def op_confirm_measurement_if_needed(s: Session, args: dict, ctx: dict):
    r = s.room
    if r and not any(getattr(r, k).source == "confirmed" for k in ("width_m", "depth_m", "height_m")):
        raise NeedsInput({"kind": "measurement", "field": "width_m", "estimate": r.width_m.value_m,
                          "prompt": f"I estimated the back wall at {r.width_m.value_m:.2f} m. What is its real length?"})
    s.emit("confirm_measurement_if_needed", "ok", "Scale anchored by a confirmed measurement")


def _style_score(offer, style: list[str]) -> int:
    text = " ".join([offer.title, offer.variant_title] + offer.colors).lower()
    return sum(1 for w in style if w.lower() in text)


def op_discover_shopify_variants(s: Session, args: dict, ctx: dict):
    offers, descs = shop.fetch_catalog()
    flagged = gates.injection({k: v for k, v in descs.items() if v})
    bad = [k for k, f in flagged.items() if f]
    if bad:
        s.emit("injection_gate", "warn", f"Ignored text in {len(bad)} product description(s) that addressed the AI",
               product_ids=bad)
    cats = args.get("categories") or s.brief.categories
    owned = {i.label.lower() for i in (s.room.existing_items if s.room else []) if i.keep}
    pool = [o for o in offers if o.available and o.currency == s.brief.currency and o.category in cats]
    # one per category, best style match then cheapest; then downgrade until within budget
    choice = {}
    for c in cats:
        # when fit matters, prefer products whose dimensions are documented (unknown dims => unknown fit)
        cands = sorted([o for o in pool if o.category == c],
                       key=lambda o: (o.dims_m is None and c != "lighting", -_style_score(o, s.brief.style), o.price_minor))
        if cands:
            choice[c] = cands
    pick = {c: 0 for c in choice}

    def total():
        return sum(choice[c][i].price_minor for c, i in pick.items())

    while total() > s.brief.budget_minor:
        # downgrade the category whose next-cheaper option saves the most
        best = None
        for c, i in pick.items():
            cheaper = [j for j in range(len(choice[c])) if choice[c][j].price_minor < choice[c][i].price_minor]
            if cheaper:
                j = min(cheaper, key=lambda j: choice[c][j].price_minor)
                save = choice[c][i].price_minor - choice[c][j].price_minor
                if not best or save > best[2]:
                    best = (c, j, save)
        if not best:
            raise NeedsInput({"kind": "budget", "prompt": f"The cheapest setup is ${total() / 100:.0f}, above your "
                              f"${s.brief.budget_minor / 100:.0f} budget. Raise the budget or drop a category?"})
        pick[best[0]] = best[1]
    selected = [choice[c][i] for c, i in pick.items()]
    for o in offers:
        s.offers[o.variant_id] = o
    ctx["selected"] = selected
    missing = [c for c in cats if c not in choice]
    s.emit("discover_shopify_variants", "ok",
           f"{len(selected)} verified variants, items subtotal ${total() / 100:.2f}"
           + (f"; nothing available for {missing}" if missing else "")
           + (" [FIXTURE CATALOG - NOT LIVE]" if offers and offers[0].source == "fixture" else ""),
           variant_ids=[o.variant_id for o in selected], subtotal_minor=total(), owned=sorted(owned))


def _placements_for(s: Session, selected) -> tuple[list[dict], list[dict]]:
    fixed = [Placement(key=i.id, category=i.label, x=i.x, z=i.z, w=i.w, d=i.d, h=i.h, locked=True).model_dump()
             for i in s.room.existing_items if i.keep]
    items = []
    for o in selected:
        if o.category in ("lighting",):
            continue  # lighting is a scene setting + cart line, not a floor footprint
        d = o.dims_m
        items.append({"key": o.variant_id, "variant_id": o.variant_id, "category": o.category,
                      "x": 0.0, "z": 0.0, "w": d.w if d else None, "d": d.d if d else None, "h": d.h if d else 0.75,
                      "rot": 0, "locked": False})
    return fixed, items


def op_plan_and_test_layout(s: Session, args: dict, ctx: dict):
    selected = ctx.get("selected")
    if selected is None:
        sc = _working_scene(s, ctx)
        selected = [s.offers[p.variant_id] for p in sc.placements if p.variant_id in s.offers]
    fixed, items = _placements_for(s, selected)
    room = s.room.model_dump()
    if AGENT_REVIEW == "inline":
        res = agents.propose_and_test(room, fixed, items, s.brief.model_dump(), s.emit)
    else:
        # Fast path: server solver now; Argon/Carbon sandbox review runs after commit (see agent_review).
        res = solver.search(room, fixed, items, s.brief.preferred_wall)
        res["report"]["checked_by"] = ["server"]
        s.emit("plan_and_test_layout", "ok", f"Server solver placed {len(items)} items: {res['report']['status']}"
               + ("; sandbox review starts after commit" if AGENT_REVIEW == "background" else ""),
               report=res["report"], rejected=len(res["rejected"]))
        ctx["agent_review"] = AGENT_REVIEW == "background"
    sc = _working_scene(s, ctx)
    sc.placements = [Placement(**{k: v for k, v in p.items() if k in Placement.model_fields and v is not None})
                     for p in res["placements"] if p.get("w") is not None]
    ctx["fit"] = res["report"]
    ctx["layout"] = res
    # lighting has no floor footprint; products without documented dims can't be placed but stay visible
    no_dims = [o for o in selected if o.category != "lighting" and o.dims_m is None]
    for o in no_dims:
        s.emit("plan_and_test_layout", "warn", f"{o.title} has no documented dimensions: shown in the list, "
               "not placed in 3D, fit unknown")
        ctx.setdefault("fit_unknowns", []).append(f"{o.variant_id}: dimensions not documented")
    s.extra_variant_ids = [o.variant_id for o in selected if o.category == "lighting"] + [o.variant_id for o in no_dims]


def op_apply_style_edit(s: Session, args: dict, ctx: dict):
    sc = _working_scene(s, ctx)
    if args.get("color_hex"):
        walls = ["back", "right", "front", "left"] if args.get("wall_id") in (None, "all") else [args["wall_id"]]
        for w in walls:
            sc.wall_colors[w] = args["color_hex"]
    if args.get("lighting"):
        sc.lighting = args["lighting"]
    if args.get("accent_hex"):
        sc.accent_hex = args["accent_hex"]
    s.emit("apply_style_edit", "ok", f"Style updated: {args}")


def op_swap_finish(s: Session, args: dict, ctx: dict):
    sc = _working_scene(s, ctx)
    cur = next((p for p in sc.placements if p.category == args["category"] and p.variant_id), None)
    if not cur:
        raise NeedsInput({"kind": "target", "prompt": f"There is no {args['category']} in the design yet. Add one first?"})
    old = s.offers.get(cur.variant_id)
    finish = args["finish"].lower()
    cands = [o for o in s.offers.values() if o.category == args["category"] and o.available
             and finish in " ".join([o.title, o.variant_title] + o.colors).lower()]
    if not cands:
        raise NeedsInput({"kind": "finish", "prompt": f"No verified {finish} {args['category']} in the store. Pick another finish?"})
    new = min(cands, key=lambda o: abs(o.price_minor - (old.price_minor if old else 0)))
    dims_changed = bool(old and old.dims_m != new.dims_m)
    cur.key = cur.variant_id = new.variant_id
    if new.dims_m:
        cur.w, cur.d, cur.h = new.dims_m.w, new.dims_m.d, new.dims_m.h
    s.emit("swap_finish", "ok", f"{new.title} / {new.variant_title} verified at ${new.price_minor / 100:.2f}",
           variant_id=new.variant_id, dims_changed=dims_changed)
    if dims_changed:
        rep = solver.check(s.room.model_dump(), [p.model_dump() for p in sc.placements])
        if rep["status"] == "no_fit":
            s.emit("swap_finish", "warn", "New dimensions conflict; re-planning that item")
            op_plan_and_test_layout(s, {}, ctx)


def op_commit_scene_preview(s: Session, args: dict, ctx: dict):
    sc = _working_scene(s, ctx)
    rep = solver.check(s.room.model_dump(), [p.model_dump() for p in sc.placements])
    if ctx.get("fit"):
        rep["checked_by"] = ctx["fit"].get("checked_by", rep["checked_by"])
    if ctx.get("fit_unknowns"):
        rep["unknowns"] += ctx["fit_unknowns"]
        if rep["status"] != "no_fit":
            rep["status"] = "unknown"
    committed = s.commit_scene(sc, base_rev=ctx["base_scene_rev"])
    from contracts.schemas import FitReport
    rep["scene_rev"] = committed.rev
    s.fit = FitReport(**rep)
    ctx["committed_rev"] = committed.rev
    s.emit("commit_scene_preview", "ok", f"Scene revision {committed.rev} committed; fit: {rep['status']}",
           rev=committed.rev, fit=rep)
    if ctx.get("agent_review"):
        import threading
        threading.Thread(target=agent_review, args=(s, committed.rev), daemon=True).start()


def _same_layout(a: list[dict], b: list[dict], tol: float = 0.05) -> bool:
    ka = {p["key"]: p for p in a if not p.get("locked")}
    kb = {p["key"]: p for p in b if not p.get("locked")}
    return ka.keys() == kb.keys() and all(
        abs(ka[k]["x"] - kb[k]["x"]) <= tol and abs(ka[k]["z"] - kb[k]["z"]) <= tol and ka[k].get("rot", 0) == kb[k].get("rot", 0)
        for k in ka)


def agent_review(s: Session, rev: int):
    """Background: Argon proposes, Carbon tests, server verifies. Never commits on its own."""
    try:
        sc = next(x for x in s.scenes if x.rev == rev)
        current = [p.model_dump() for p in sc.placements]
        fixed = [p for p in current if p.get("locked")]
        items = [p for p in current if not p.get("locked")]
        if not items:
            return
        res = agents.propose_and_test(s.room.model_dump(), fixed, items, s.brief.model_dump(), s.emit)
        if not s.scene or s.scene.rev != rev:
            s.emit("agent_review", "info", f"Sandbox review for rev {rev} arrived after newer edits; discarded")
            return
        sandboxes = [c for c in res["report"]["checked_by"] if c.endswith("sandbox")]
        if _same_layout(current, res["placements"]):
            if sandboxes and s.fit and s.fit.scene_rev == rev:
                s.fit.checked_by = list(dict.fromkeys(s.fit.checked_by + sandboxes))
            s.emit("agent_review", "ok", f"Sandbox review agrees with rev {rev}" + (f" ({', '.join(sandboxes)})" if sandboxes else " (agents unavailable; server only)"))
        elif res["report"]["status"] in ("fits", "tight"):
            s.last_alternative = {"base_rev": rev, "title": "Argon suggests a different layout", "requested": current,
                                  "conflicts": [], "alternative": res["placements"], "report": res["report"],
                                  "style": res.get("style_assessment")}
            s.emit("agent_review", "needs_input", "Argon proposed a different layout that passes every check. Use it?",
                   checked_by=res["report"]["checked_by"])
        else:
            s.emit("agent_review", "warn", f"Sandbox proposal did not pass server checks ({res['report']['status']}); kept current layout")
    except Exception as e:
        s.emit("agent_review", "warn", f"Sandbox review failed: {type(e).__name__}: {str(e)[:200]}")


OPS: dict[str, Callable] = {
    "inspect_room_motion": op_inspect_room_motion,
    "confirm_measurement_if_needed": op_confirm_measurement_if_needed,
    "discover_shopify_variants": op_discover_shopify_variants,
    "plan_and_test_layout": op_plan_and_test_layout,
    "apply_style_edit": op_apply_style_edit,
    "swap_finish": op_swap_finish,
    "commit_scene_preview": op_commit_scene_preview,
}


# ---------- execution ----------
def execute(s: Session, plan: WorkflowPlan, ctx: Optional[dict] = None) -> dict:
    """Run (or resume) a validated plan. Returns {"status": done|needs_input|error, ...}."""
    ctx = ctx if ctx is not None else {"done": set(), "base_scene_rev": plan.base_scene_rev}
    s.plan = plan
    try:
        for layer in _toposort(plan.steps):
            todo = [st for st in layer if st.id not in ctx["done"]]
            if not todo:
                continue
            for st in todo:
                s.emit(st.op, "started", f"step {st.id}", step_id=st.id)
            if len(todo) == 1:
                OPS[todo[0].op](s, todo[0].args, ctx)
            else:
                with ThreadPoolExecutor(len(todo)) as ex:
                    futs = [ex.submit(OPS[st.op], s, st.args, ctx) for st in todo]
                    for f in futs:
                        f.result()
            ctx["done"].update(st.id for st in todo)
    except NeedsInput as ni:
        s.pending_input = {"plan": plan, "ctx": ctx, "request": ni.request}
        s.emit("workflow", "needs_input", ni.request["prompt"], request=ni.request)
        return {"status": "needs_input", "request": ni.request}
    except Exception as e:  # typed failure surfaced; last verified scene is untouched
        s.emit("workflow", "error", f"{type(e).__name__}: {str(e)[:300]}")
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    s.pending_input = None
    return {"status": "done", "rev": ctx.get("committed_rev")}


def resume(s: Session) -> dict:
    if not s.pending_input:
        return {"status": "idle"}
    p = s.pending_input
    s.emit("workflow", "info", "Resuming with the new input")
    return execute(s, p["plan"], p["ctx"])
