"""Whole journey on fixtures, no network: photo -> room -> command -> plan -> layout -> review.
Run: python scripts/e2e_mock.py   (sets MOCK_AI=1 and ETHYNEON_FIXTURE_CATALOG=1)"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.update(MOCK_AI="1", ETHYNEON_FIXTURE_CATALOG="1", GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", "unused"))
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app  # noqa: E402

c = TestClient(app)
sid = c.post("/api/session").json()["id"]
ok = lambda cond, msg: print(("PASS " if cond else "FAIL ") + msg) or cond  # noqa: E731
results = []
r = c.post(f"/api/session/{sid}/photo", files={"file": ("r.jpg", b"\xff\xd8mock", "image/jpeg")}).json()
results.append(ok(r.get("accepted") and r["room"]["walls"][0]["id"].startswith("wall_"), "photo -> validated RoomSpec (contract wall ids)"))
c.post(f"/api/session/{sid}/measure", json={"field": "width_m", "value_m": 3.2})
c.post(f"/api/session/{sid}/door/manual", json={"opening_id": "door1", "hinge_side": "left", "swing": "in"})
r = c.post(f"/api/session/{sid}/command", data={"text": "build it"}).json()
time.sleep(0.5)
plan_ev = [e for e in c.get(f"/api/session/{sid}/events").json() if e["step"] == "planner"]
results.append(ok(r.get("status") == "running" and plan_ev and "MOCK_AI" in plan_ev[-1]["detail"], "command -> plan labeled mock"))
t0 = time.time()
while time.time() - t0 < 15:
    st = c.get(f"/api/session/{sid}/state").json()
    evs = c.get(f"/api/session/{sid}/events").json()
    if any(e["step"] == "agent_review" for e in evs):
        break
    time.sleep(0.2)
results.append(ok(st["fit"] and st["fit"]["status"] in ("fits", "tight"), f"layout committed, fit={st['fit'] and st['fit']['status']}"))
results.append(ok(not any(x.endswith("sandbox") for x in st["fit"]["checked_by"]), "mock never claims a sandbox ran"))
rv = c.post(f"/api/session/{sid}/review").json()
results.append(ok(rv.get("within_budget") and rv["lines"], f"review: {len(rv.get('lines', []))} lines, ${rv.get('subtotal_minor', 0) / 100:.2f}"))
results.append(ok(c.post(f"/api/session/{sid}/approve").status_code == 409, "fixture catalog cannot create a real cart"))
print("ALL PASS" if all(results) else "SOME FAILED")
sys.exit(0 if all(results) else 1)
