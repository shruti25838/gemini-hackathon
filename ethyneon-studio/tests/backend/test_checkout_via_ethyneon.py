"""Studio's checkout goes through the purchase server, or nowhere.

Before this, `checkout.py` built `https://{merchant}/cart/{variant}:1` from
Shop MCP's `seller.domain`. Those are real, working Shopify cart permalinks
to arbitrary third-party merchants, so a shopper could pay a stranger's store
from the demo. The first test here is the one that would have caught it.

Everything runs against a fake transport. No network, no credentials, and no
test dependency beyond what the app already installs.
"""

import asyncio
import json
import logging
import unittest
from pathlib import Path
from typing import Any

import httpx

from backend.checkout import CheckoutStudio
from backend.ethyneon_client import EthyneonClient, PurchaseServerUnavailable, refusal_reason

STUDIO = Path(__file__).resolve().parents[2]
DEV_STORE = "ethyneon.myshopify.com"
OTHER_STORE = "mygarvee.myshopify.com"
OUR_VARIANT = "gid://shopify/ProductVariant/49000000000001"
OTHER_VARIANT = "gid://shopify/ProductVariant/54307265806649"
CHECKOUT_URL = "https://" + DEV_STORE + "/cart/c/abcdef123456"


# ==========================================================================
# A fake purchase server
# ==========================================================================


class FakeServer:
    """Stands in for our lane C backend. Records every path it is asked for."""

    def __init__(
        self,
        purchasable=(OUR_VARIANT,),
        audit_outcome="ready",
        checkout_url=CHECKOUT_URL,
        review_error=None,
    ):
        self.calls: list[str] = []
        self.purchasable = purchasable
        self.audit_outcome = audit_outcome
        self.checkout_url = checkout_url
        self.review_error = review_error
        self.sent: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        body = json.loads(request.content or b"{}")
        self.sent.append({"path": path, "body": body})

        if path == "/api/session":
            return httpx.Response(200, json={"session_id": "sess_fake"})

        if path == "/api/cart/review":
            if self.review_error:
                return httpx.Response(400, json=self.review_error)
            wanted = [s["variant_id"] for s in body.get("selections", [])]
            lines = [{"variant_id": v, "quantity": 1} for v in wanted if v in self.purchasable]
            if not lines:
                return httpx.Response(400, json={
                    "code": "no_shoppable_lines",
                    "message": "None of the selected variants can be bought.",
                    "retryable": False, "details": {},
                })
            return httpx.Response(200, json={
                "review_id": "cart_fake", "lines": lines, "scene_revision": 0,
                "store": {"store_domain": DEV_STORE, "api_surface": "storefront_api"},
                "merchandise_subtotal": {"amount_minor": 42900, "currency": "USD"},
            })

        if path == "/api/cart/confirm":
            return httpx.Response(200, json={"review_id": "cart_fake", "status": "approved"})

        if path == "/api/checkout/continue":
            ready = self.audit_outcome == "ready"
            return httpx.Response(200, json={
                "audit": {"outcome": self.audit_outcome},
                "checkout_url": self.checkout_url if ready else None,
            })

        return httpx.Response(404, json={
            "code": "not_found", "message": "no route", "retryable": False, "details": {}})

    def client(self) -> EthyneonClient:
        """A real EthyneonClient whose transport is this fake, so the client's
        own parsing and error handling are exercised, not stubbed out."""
        transport = httpx.MockTransport(self.handler)
        client = EthyneonClient("http://purchase.test", "token-never-sent-anywhere")

        async def routed(method, path, payload=None):
            async with httpx.AsyncClient(transport=transport) as http:
                response = await http.request(
                    method, "http://purchase.test" + path,
                    headers=client._headers(), json=payload)
            body = response.json()
            body.setdefault("_status_code", response.status_code)
            return body

        client._request = routed
        return client


def cart_line(variant_id: str, title: str = "Desk") -> dict[str, Any]:
    return {
        "product_id": "p_" + variant_id[-4:], "title": title, "variant_id": variant_id,
        "verified": True, "available": True, "price_changed": False, "problem": None,
        "price_now": {"amount": 42900, "currency": "USD"},
        "merchant": DEV_STORE if variant_id == OUR_VARIANT else OTHER_STORE,
        "listing_url": "https://example.test/p",
    }


def studio(server) -> CheckoutStudio:
    client = server.client() if server else EthyneonClient("", "")
    return CheckoutStudio(shopify=None, gemini=None, scenes=None, ethyneon=client)


def purchase(server, lines, status="ready", confirm=True) -> dict[str, Any]:
    return asyncio.run(studio(server)._purchase(lines, status, confirm))


def as_text(result: dict[str, Any]) -> str:
    return json.dumps(result, default=sorted)


# ==========================================================================
# The permalink is gone
# ==========================================================================


class PermalinkTests(unittest.TestCase):
    def test_the_source_builds_no_merchant_cart_url(self):
        """The exact pattern that made third-party purchase links."""
        source = (STUDIO / "backend" / "checkout.py").read_text(encoding="utf-8")
        building = [
            text for text in source.splitlines()
            if "/cart/" in text and 'f"' in text and not text.strip().startswith("#")
        ]
        self.assertEqual(building, [], "a cart permalink is still being built")
        self.assertNotIn("store['merchant']}/cart/", source)
        self.assertNotIn('{store["merchant"]}/cart/', source)

    def test_no_response_ever_contains_a_third_party_cart_link(self):
        server = FakeServer()
        result = purchase(server, [cart_line(OUR_VARIANT), cart_line(OTHER_VARIANT)])
        self.assertNotIn(OTHER_STORE, as_text(result))
        for store in result["stores"]:
            self.assertEqual(store["cart_url"], CHECKOUT_URL)
            self.assertTrue(store["cart_url"].startswith("https://" + DEV_STORE))

    def test_every_link_comes_from_the_purchase_server_verbatim(self):
        """Studio must not rebuild, rewrite or append to the URL it is given."""
        server = FakeServer(checkout_url="https://" + DEV_STORE + "/checkouts/cn/XYZ")
        result = purchase(server, [cart_line(OUR_VARIANT)])
        self.assertEqual(result["stores"][0]["cart_url"], "https://" + DEV_STORE + "/checkouts/cn/XYZ")


# ==========================================================================
# Only the connected dev store, everything else view-only
# ==========================================================================


class ViewOnlyTests(unittest.TestCase):
    def test_a_non_dev_store_variant_is_refused_and_view_only(self):
        server = FakeServer(purchasable=(OUR_VARIANT,))
        result = purchase(server, [cart_line(OUR_VARIANT), cart_line(OTHER_VARIANT, "Garvee desk")])
        self.assertIn(OUR_VARIANT, result["purchasable_variants"])
        self.assertNotIn(OTHER_VARIANT, result["purchasable_variants"])
        self.assertIn("view-only", result["view_only_reason"])

    def test_a_cart_of_only_other_merchants_yields_no_store(self):
        server = FakeServer(purchasable=())
        result = purchase(server, [cart_line(OTHER_VARIANT)])
        self.assertEqual(result["stores"], [])
        self.assertEqual(result["purchasable_variants"], set())

    def test_at_most_one_store_is_returned(self):
        server = FakeServer(purchasable=(OUR_VARIANT,))
        result = purchase(server, [cart_line(OUR_VARIANT), cart_line(OTHER_VARIANT)])
        self.assertLessEqual(len(result["stores"]), 1)
        self.assertEqual(result["stores"][0]["merchant"], DEV_STORE)

    def test_variant_ids_cross_unchanged(self):
        """Both sides speak the same Shopify GID. Any rewriting here would
        send the purchase server a variant the shopper did not pick."""
        server = FakeServer()
        purchase(server, [cart_line(OUR_VARIANT), cart_line(OTHER_VARIANT)])
        review = next(c for c in server.sent if c["path"] == "/api/cart/review")
        self.assertEqual(
            [s["variant_id"] for s in review["body"]["selections"]],
            [OUR_VARIANT, OTHER_VARIANT])
        self.assertTrue(all(s["quantity"] == 1 for s in review["body"]["selections"]))

    def test_the_refusal_reason_is_plain_words(self):
        self.assertIn("view-only", refusal_reason({"code": "no_shoppable_lines", "message": "x"}))
        self.assertIn("disabled", refusal_reason({"code": "live_cart_writes_refused", "message": "x"}))
        self.assertEqual(refusal_reason({"code": "weird", "message": "Upstream said no."}),
                         "Upstream said no.")


# ==========================================================================
# Approval is explicit
# ==========================================================================


class ApprovalTests(unittest.TestCase):
    def test_review_never_calls_confirm(self):
        """An approval the shopper did not give is worse than no cart."""
        server = FakeServer()
        purchase(server, [cart_line(OUR_VARIANT)], confirm=False)
        self.assertIn("/api/cart/review", server.calls)
        self.assertNotIn("/api/cart/confirm", server.calls)
        self.assertNotIn("/api/checkout/continue", server.calls)

    def test_review_returns_no_link(self):
        server = FakeServer()
        result = purchase(server, [cart_line(OUR_VARIANT)], confirm=False)
        self.assertEqual(result["stores"], [])
        self.assertIn("Confirm", result["message"])
        self.assertNotIn("/cart/", as_text(result))

    def test_review_still_reports_what_could_be_bought(self):
        server = FakeServer()
        result = purchase(server, [cart_line(OUR_VARIANT), cart_line(OTHER_VARIANT)], confirm=False)
        self.assertEqual(result["purchasable_variants"], {OUR_VARIANT})

    def test_confirm_walks_both_routes_in_order(self):
        server = FakeServer()
        result = purchase(server, [cart_line(OUR_VARIANT)], confirm=True)
        self.assertEqual(server.calls, [
            "/api/session", "/api/cart/review", "/api/cart/confirm", "/api/checkout/continue"])
        self.assertEqual(result["stores"][0]["cart_url"], CHECKOUT_URL)

    def test_each_write_carries_a_distinct_action_id(self):
        server = FakeServer()
        purchase(server, [cart_line(OUR_VARIANT)], confirm=True)
        ids = [call["body"]["action_id"] for call in server.sent if "action_id" in call["body"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)


# ==========================================================================
# No link before our audit returns ready
# ==========================================================================


class AuditGateTests(unittest.TestCase):
    def test_no_link_when_the_studio_audit_has_not_passed(self):
        server = FakeServer()
        result = purchase(server, [cart_line(OUR_VARIANT)], status="needs_review", confirm=True)
        self.assertEqual(result["stores"], [])
        self.assertNotIn("/api/cart/confirm", server.calls)

    def test_no_link_when_the_purchase_server_audit_is_not_ready(self):
        server = FakeServer(audit_outcome="blocked")
        result = purchase(server, [cart_line(OUR_VARIANT)])
        self.assertEqual(result["stores"], [])
        self.assertIn("blocked", result["message"])

    def test_no_link_when_cart_writes_are_disabled(self):
        """LIVE_CART_WRITES off on our side: ready audit, but no URL."""
        server = FakeServer(audit_outcome="ready", checkout_url=None)
        result = purchase(server, [cart_line(OUR_VARIANT)])
        self.assertEqual(result["stores"], [])
        self.assertIn("cart writes are disabled", result["message"])

    def test_a_blocked_audit_is_not_reported_as_disabled_writes(self):
        """Two different problems; naming the wrong one misdirects the fix."""
        server = FakeServer(audit_outcome="blocked")
        result = purchase(server, [cart_line(OUR_VARIANT)])
        self.assertNotIn("cart writes are disabled", result["message"])


# ==========================================================================
# Not configured, and unreachable
# ==========================================================================


class UnavailableTests(unittest.TestCase):
    def test_unset_api_base_gives_the_not_configured_response(self):
        result = purchase(None, [cart_line(OUR_VARIANT)])
        self.assertEqual(result["stores"], [])
        self.assertIn("not configured", result["message"].lower())
        self.assertIn("ETHYNEON_API_BASE", result["message"])

    def test_unset_api_base_never_falls_back_to_the_old_behaviour(self):
        result = purchase(None, [cart_line(OUR_VARIANT)])
        self.assertNotIn("/cart/", as_text(result))

    def test_the_client_knows_it_is_unconfigured(self):
        self.assertFalse(EthyneonClient("", "").configured)
        self.assertTrue(EthyneonClient("http://purchase.test", "").configured)

    def test_an_unconfigured_client_raises_rather_than_guessing(self):
        with self.assertRaises(PurchaseServerUnavailable) as caught:
            asyncio.run(EthyneonClient("", "").cart_review("s", []))
        self.assertEqual(caught.exception.code, "purchase_server_not_configured")

    def test_an_unreachable_server_yields_no_link(self):
        def refuse(request):
            raise httpx.ConnectError("connection refused")

        client = EthyneonClient("http://purchase.test", "")
        transport = httpx.MockTransport(refuse)

        async def routed(method, path, payload=None):
            try:
                async with httpx.AsyncClient(transport=transport) as http:
                    await http.request(method, "http://purchase.test" + path, json=payload)
            except Exception as error:
                raise PurchaseServerUnavailable(
                    "The purchase server at http://purchase.test did not answer.") from error

        client._request = routed
        result = asyncio.run(
            CheckoutStudio(None, None, None, client)._purchase([cart_line(OUR_VARIANT)], "ready", True))
        self.assertEqual(result["stores"], [])
        self.assertIn("unavailable", result["message"].lower())

    def test_a_server_refusal_becomes_a_plain_reason(self):
        server = FakeServer(review_error={
            "code": "live_cart_writes_refused",
            "message": "Live cart writes are not permitted: LIVE_CART_WRITES is off.",
            "retryable": False, "details": {}})
        result = purchase(server, [cart_line(OUR_VARIANT)])
        self.assertEqual(result["stores"], [])
        self.assertIn("disabled", result["message"])


# ==========================================================================
# Nothing secret reaches the logs
# ==========================================================================


class LoggingTests(unittest.TestCase):
    def capture(self):
        records: list[str] = []

        class Sink(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        return records, Sink()

    def test_the_checkout_url_and_token_are_never_logged(self):
        records, sink = self.capture()
        root = logging.getLogger()
        previous = root.level
        root.addHandler(sink)
        root.setLevel(logging.DEBUG)
        try:
            server = FakeServer()
            result = purchase(server, [cart_line(OUR_VARIANT)])
        finally:
            root.removeHandler(sink)
            root.setLevel(previous)

        self.assertEqual(result["stores"][0]["cart_url"], CHECKOUT_URL)
        logged = "\n".join(records)
        self.assertNotIn(CHECKOUT_URL, logged)
        self.assertNotIn("/cart/c/", logged)
        self.assertNotIn("token-never-sent-anywhere", logged)

    def test_the_purchase_client_writes_no_logs_at_all(self):
        source = (STUDIO / "backend" / "ethyneon_client.py").read_text(encoding="utf-8")
        for call in ("logging.", "logger", "print("):
            self.assertNotIn(call, source, call + " appears in the purchase client")

    def test_the_token_is_sent_as_a_header_and_nowhere_else(self):
        server = FakeServer()
        purchase(server, [cart_line(OUR_VARIANT)])
        for call in server.sent:
            self.assertNotIn("token-never-sent-anywhere", json.dumps(call["body"]))


if __name__ == "__main__":
    unittest.main()
