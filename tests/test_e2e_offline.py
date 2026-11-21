"""End-to-end wiring with model + Shopify boundaries mocked. Tests only: the app never ships mocks."""
import time

import pytest
from fastapi.testclient import TestClient

from ai import agents, gates, gemini, intake, vision, workflow
from backend import app as appmod, shop
from contracts.schemas import CatalogOffer


@pytest.fixture
def client(monkeypatch):
    def decide(parts, questions):
        a = {}
        for k, q in questions.items():
            if q["type"] == "noul":
                a[k] = {"noul": 0.05 if k == "person_visible" else 0.95, "confidence": 0.95}
            elif q["type"] == "score":
                a[k] = {"score": 2.0, "confidence": 0.9}
            else:
                want = "ready" if "ready" in q["criteria"] else "product_search"
                a[k] = {"choice": want, "confidence": 0.95, "probabilities": {}}
        return a, {"latency_ms": 3}

    def structured(model, parts, out, **kw):
        if out is vision.RoomDraft:
            return out(width_m={"value_m": 2.8, "confidence": 0.6}, depth_m={"value_m": 3.0, "confidence": 0.5},
                       height_m={"value_m": 2.5, "confidence": 0.6},
                       walls=[{"id": w, "color_hex": "#ddd", "visible": w != "wall_s"} for w in ("wall_n", "wall_e", "wall_s", "wall_w")],
                       openings=[{"wall": "wall_n", "type": "door", "offset_m": 1.9, "width_m": 0.8, "height_m": 2.0,
                                  "confidence": 0.8, "evidence": "right side"}],
                       existing_items=[{"label": "monitor", "x": 0.5, "z": 1.5, "w": 0.5, "d": 0.2, "h": 0.4, "confidence": 0.7}],
                       floor_color_hex="#8a7560", uncertainties=[]), {"latency_ms": 5}
        if out is intake.Command:
            return out(transcript="dual monitor setup under 1200 purple", intent="product_search",
                       brief_patch={"budget_usd": 1200, "style": ["purple"], "categories": ["desk", "chair", "monitor"]},
                       ambiguities=[]), {"latency_ms": 5}
        if out is workflow.DraftPlan:
            return out(goal="setup", rationale="r", steps=[
                {"id": "shop", "op": "discover_shopify_variants", "after": [], "args_json": "{}"},
                {"id": "lay", "op": "plan_and_test_layout", "after": ["shop"], "args_json": "{}"}]), {"latency_ms": 5}
        raise AssertionError(out)

    def no_agent(*a, **k):
        raise agents.AgentUnavailable("test: sandbox disabled")

    monkeypatch.setattr(gemini, "decide", decide)
    monkeypatch.setattr(gemini, "generate_structured", structured)
    monkeypatch.setattr(agents, "_run_agent", no_agent)
    monkeypatch.setattr(shop, "live", lambda: True)  # pretend live for cart; fixture data for offers
    import json
    raw = json.loads(shop.FIXTURE.read_text())
    monkeypatch.setattr(shop, "fetch_catalog", lambda: ([CatalogOffer(**o) for o in raw["offers"]], raw["descriptions"]))
    monkeypatch.setattr(shop, "create_cart", lambda lines: {
        "cart_id": "gid://shopify/Cart/1", "checkout_url": "https://example.test/checkout",
        "subtotal_minor": sum(l["price_minor"] for l in lines), "currency": "USD",
        "lines": [{"variant_id": l["variant_id"], "quantity": l["quantity"], "price_minor": l["price_minor"]} for l in lines]})
    return TestClient(appmod.app)


def wait_idle(c, sid, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        evs = c.get(f"/api/session/{sid}/events").json()
        if any(e["step"] in ("commit_scene_preview",) and e["status"] == "ok" for e in evs) or \
           any(e["status"] in ("needs_input", "error") and e["step"] == "workflow" for e in evs[-2:]):
            return evs
        time.sleep(0.1)
    raise TimeoutError([(e["step"], e["status"], e["detail"]) for e in evs])


def test_full_flow(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    r = c.post(f"/api/session/{sid}/photo", files={"file": ("r.jpg", b"\xff\xd8fake", "image/jpeg")}).json()
    assert r["accepted"]
    c.post(f"/api/session/{sid}/door/manual", json={"opening_id": "door1", "hinge_side": "left", "swing": "in"})
    r = c.post(f"/api/session/{sid}/command", data={"text": "build it"}).json()
    assert r["status"] == "running"
    evs = wait_idle(c, sid)
    # no confirmed measurement yet -> workflow must pause and ask
    assert any(e["status"] == "needs_input" and "real length" in e["detail"] for e in evs), [(e["step"], e["detail"]) for e in evs]
    c.post(f"/api/session/{sid}/measure", json={"field": "width_m", "value_m": 3.2})
    t0 = time.time()
    while time.time() - t0 < 10:
        st = c.get(f"/api/session/{sid}/state").json()
        if st["scene"] and st["scene"]["placements"] and st["fit"] and not st["pending"]:
            break
        time.sleep(0.1)
    assert st["fit"]["status"] in ("fits", "tight"), st["fit"]
    assert st["fit"]["checked_by"][0] == "server"
    # background sandbox review runs after commit; with agents unavailable it must say so, not claim a sandbox
    t0 = time.time()
    while time.time() - t0 < 10:
        evs = c.get(f"/api/session/{sid}/events").json()
        rev_ev = [e for e in evs if e["step"] == "agent_review"]
        if rev_ev:
            break
        time.sleep(0.1)
    assert rev_ev and "agents unavailable" in rev_ev[-1]["detail"]
    assert not any(x.endswith("sandbox") for x in c.get(f"/api/session/{sid}/state").json()["fit"]["checked_by"])
    assert st["subtotal_minor"] <= 120000
    keys = {p["key"] for p in st["scene"]["placements"]}
    assert "item1" in keys  # existing monitor kept and locked

    rv = c.post(f"/api/session/{sid}/review").json()
    assert rv["within_budget"] and not rv["problems"]
    cart = c.post(f"/api/session/{sid}/approve").json()
    assert "checkout_url" not in cart
    out = c.post(f"/api/session/{sid}/checkout").json()
    assert out["released"] and out["checkout_url"]


def test_cart_requires_approval_and_scene_change_voids_it(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    assert c.post(f"/api/session/{sid}/approve").status_code == 400  # no review, no cart
    c.post(f"/api/session/{sid}/photo", files={"file": ("r.jpg", b"\xff\xd8fake", "image/jpeg")})
    c.post(f"/api/session/{sid}/measure", json={"field": "width_m", "value_m": 3.2})
    c.post(f"/api/session/{sid}/door/manual", json={"opening_id": "door1", "hinge_side": "left", "swing": "in"})
    c.post(f"/api/session/{sid}/command", data={"text": "build it"})
    t0 = time.time()
    while time.time() - t0 < 10:
        st = c.get(f"/api/session/{sid}/state").json()
        if st["scene"] and st["scene"]["placements"] and st["fit"]:
            break
        time.sleep(0.1)
    c.post(f"/api/session/{sid}/review")
    # scene changes after review (undo) -> stale review cannot be approved
    c.post(f"/api/session/{sid}/undo")
    assert c.post(f"/api/session/{sid}/approve").status_code == 409


def test_drag_into_door_swing_gets_checked_alternative(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    c.post(f"/api/session/{sid}/photo", files={"file": ("r.jpg", b"\xff\xd8fake", "image/jpeg")})
    c.post(f"/api/session/{sid}/measure", json={"field": "width_m", "value_m": 3.2})
    c.post(f"/api/session/{sid}/door/manual", json={"opening_id": "door1", "hinge_side": "left", "swing": "in"})
    c.post(f"/api/session/{sid}/command", data={"text": "build it"})
    t0 = time.time()
    while time.time() - t0 < 10:
        st = c.get(f"/api/session/{sid}/state").json()
        if st["scene"] and st["scene"]["placements"] and st["fit"]:
            break
        time.sleep(0.1)
    rev = st["scene"]["rev"]
    desk = next(p for p in st["scene"]["placements"] if p["category"] == "desk")
    door = next(o for o in st["room"]["openings"] if o["type"] == "door")
    # drop the desk right inside the swing
    x = door["offset_m"] + door["width_m"] / 2
    c.post(f"/api/session/{sid}/move", json={"key": desk["key"], "x": x, "z": 0.4, "rot": 0})
    t0 = time.time()
    while time.time() - t0 < 10:
        st = c.get(f"/api/session/{sid}/state").json()
        if st["alternative"]:
            break
        time.sleep(0.1)
    alt = st["alternative"]
    assert alt and any(cf["kind"] == "door_swing" for cf in alt["conflicts"])
    assert alt["report"]["status"] in ("fits", "tight")
    assert st["scene"]["rev"] == rev  # nothing committed until the shopper accepts
    st = c.post(f"/api/session/{sid}/alternative/accept").json()
    assert st["scene"]["rev"] == rev + 1 and st["fit"]["status"] in ("fits", "tight")


def test_preset_room_is_labeled(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    r = c.post(f"/api/session/{sid}/preset").json()["room"]
    assert any("PRESET" in n for n in r["notes"])
    assert r["width_m"]["source"] == "default"  # not "confirmed": still needs a real measurement


def test_door_clip_on_room_without_door_says_so(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    c.post(f"/api/session/{sid}/preset")
    from backend import state
    s = state.get(sid)
    s.room.openings = [o for o in s.room.openings if o.type != "door"]
    r = c.post(f"/api/session/{sid}/door", files={"file": ("d.mp4", b"\x00" * 10, "video/mp4")}).json()
    assert not r["accepted"] and "no door" in r["problem"]


def test_review_buys_only_ticked_items(client):
    c = client
    sid = c.post("/api/session").json()["id"]
    c.post(f"/api/session/{sid}/photo", files={"file": ("r.jpg", b"\xff\xd8fake", "image/jpeg")})
    c.post(f"/api/session/{sid}/measure", json={"field": "width_m", "value_m": 3.2})
    c.post(f"/api/session/{sid}/door/manual", json={"opening_id": "door1", "hinge_side": "left", "swing": "in"})
    c.post(f"/api/session/{sid}/command", data={"text": "build it"})
    t0 = time.time()
    while time.time() - t0 < 10:
        st = c.get(f"/api/session/{sid}/state").json()
        if st["scene"] and st["scene"]["placements"] and st["fit"]:
            break
        time.sleep(0.1)
    chair = next(o["variant_id"] for o in st["selected"] if o["category"] == "chair")
    rv = c.post(f"/api/session/{sid}/review", json={"exclude": [chair]}).json()
    assert chair not in {l["variant_id"] for l in rv["lines"]} and rv["excluded"] == [chair]
    cart = c.post(f"/api/session/{sid}/approve").json()
    assert chair not in {l["variant_id"] for l in cart["lines"]}
    # chair is still in the design
    assert any(p["variant_id"] == chair for p in c.get(f"/api/session/{sid}/state").json()["scene"]["placements"])
