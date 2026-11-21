"""Review -> approval -> cart -> reconciliation + Decision 1 audit -> hosted checkout.

Approval is created only by the explicit button endpoint and is bound to the scene
revision and exact lines. Any scene commit voids it (Session.commit_scene).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from . import gates, shop
from .state import Session


class CheckoutError(RuntimeError):
    pass


def _fp(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def build_review(s: Session, extra_variant_ids: list[str] | None = None) -> dict:
    if not s.scene:
        raise CheckoutError("Nothing to review yet")
    vids = [p.variant_id for p in s.scene.placements if p.variant_id] + (extra_variant_ids or [])
    # refresh from the catalog before review (prices / stock may have moved)
    offers, _ = shop.fetch_catalog()
    fresh = {o.variant_id: o for o in offers}
    lines, problems = [], []
    for vid in dict.fromkeys(vids):
        o = fresh.get(vid)
        if not o:
            problems.append(f"{vid} no longer exists in the store")
            continue
        if not o.available:
            problems.append(f"{o.title} ({o.variant_title}) is out of stock")
            continue
        old = s.offers.get(vid)
        if old and old.price_minor != o.price_minor:
            problems.append(f"{o.title} price changed ${old.price_minor / 100:.2f} -> ${o.price_minor / 100:.2f}")
        s.offers[vid] = o
        lines.append({"variant_id": vid, "title": o.title, "variant_title": o.variant_title, "quantity": 1,
                      "price_minor": o.price_minor, "currency": o.currency, "source": o.source})
    subtotal = sum(l["price_minor"] * l["quantity"] for l in lines)
    review = {"scene_rev": s.scene.rev, "lines": lines, "subtotal_minor": subtotal, "currency": s.brief.currency,
              "budget_minor": s.brief.budget_minor, "within_budget": subtotal <= s.brief.budget_minor,
              "fit_status": s.fit.status if s.fit else "unknown", "problems": problems,
              "note": "Items subtotal only. Shipping and tax are added on Shopify's checkout page."}
    review["fingerprint"] = _fp({k: review[k] for k in ("scene_rev", "lines", "subtotal_minor")})
    s.emit("review", "ok" if not problems else "warn", f"Review ready: {len(lines)} items, ${subtotal / 100:.2f}",
           problems=problems)
    return review


def approve(s: Session, review: dict) -> dict:
    """Called only by the 'Confirm and add to cart' button."""
    if not s.scene or review.get("scene_rev") != s.scene.rev:
        raise CheckoutError("The design changed since this review. Please review again.")
    if review["problems"]:
        raise CheckoutError("Resolve the review problems first")
    s.approval = {"token": secrets.token_urlsafe(12), "scene_rev": s.scene.rev, "fingerprint": review["fingerprint"],
                  "lines": review["lines"], "subtotal_minor": review["subtotal_minor"], "approved_at": time.time()}
    s.emit("approval", "ok", f"Approved scene rev {s.scene.rev}, ${review['subtotal_minor'] / 100:.2f}")
    return {"approval_token": s.approval["token"]}


def create_cart(s: Session, token: str) -> dict:
    a = s.approval
    if not a or a["token"] != token or a["scene_rev"] != (s.scene.rev if s.scene else None):
        raise CheckoutError("No valid approval for the current design")
    if s.cart and s.cart.get("approval_token") == token:
        return s.cart  # idempotent: never create a second cart for the same approval
    cart = shop.create_cart(a["lines"])
    want = sorted((l["variant_id"], l["quantity"]) for l in a["lines"])
    got = sorted((l["variant_id"], l["quantity"]) for l in cart["lines"])
    if want != got:
        s.emit("cart", "error", "Shopify cart does not match the approved lines", want=want, got=got)
        raise CheckoutError("Cart did not match the approval")
    cart["approval_token"] = token
    s.cart = cart
    s.emit("cart", "ok", f"Shopify cart verified: {len(got)} lines, ${cart['subtotal_minor'] / 100:.2f}")
    return {k: v for k, v in cart.items() if k != "checkout_url"}


def audit_and_release(s: Session) -> dict:
    a, c = s.approval, s.cart
    if not (a and c):
        raise CheckoutError("Approve and create the cart first")
    # deterministic reconciliation
    checks = {
        "approval_current": a["scene_rev"] == (s.scene.rev if s.scene else None),
        "lines_match": sorted((l["variant_id"], l["quantity"]) for l in a["lines"])
        == sorted((l["variant_id"], l["quantity"]) for l in c["lines"]),
        "amount_matches": c["subtotal_minor"] == a["subtotal_minor"],
        "currency_matches": c["currency"] == s.brief.currency,
        "within_budget": c["subtotal_minor"] <= s.brief.budget_minor,
        "fit_resolved": bool(s.fit and s.fit.status in ("fits", "tight")),
    }
    owned = [i.label for i in (s.room.existing_items if s.room else []) if i.keep]
    packet = {"approved": {"lines": a["lines"], "subtotal_minor": a["subtotal_minor"]},
              "cart": {"lines": c["lines"], "subtotal_minor": c["subtotal_minor"], "currency": c["currency"]},
              "owned_items_to_keep": owned, "budget_minor": s.brief.budget_minor,
              "fit": s.fit.model_dump() if s.fit else None, "deterministic_checks": checks}
    g = gates.checkout_audit(packet)
    ok = all(checks.values()) and g.passed
    s.audit = {"checks": checks, "decision": g.model_dump(), "passed": ok, "cart_id": c["cart_id"],
               "approval_fingerprint": a["fingerprint"], "at": time.time()}
    s.emit("checkout_audit", "ok" if ok else "warn",
           "Checkout checks passed" if ok else f"Checkout held: {g.message or [k for k, v in checks.items() if not v]}",
           checks=checks, decision=g.answers)
    if not ok:
        return {"released": False, "audit": s.audit}
    s.emit("checkout", "info", "Checkout opened on Shopify (test mode). Payment is not confirmed until Shopify reports it.")
    return {"released": True, "checkout_url": c["checkout_url"], "audit": s.audit}
