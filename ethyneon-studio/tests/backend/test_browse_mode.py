"""Browse mode: look at every merchant on Shopify, buy from one store only.

Two separate things have to be true, and they are tested separately on
purpose.

The first is routing. Browse has to reach Shop MCP with the shop filter
dropped, or "browse all Shopify" quietly means "browse ours" as soon as the
development store is named in SHOPIFY_SHOPS. Our-store mode has to keep
reading the Storefront API and has to be exactly what it was.

The second is the refusal, and it is the one that matters. Browse cards
carry no add, review or checkout control, but a missing button guards
nothing: the scene and checkout routes take a variant ID from whoever posts
one. So the refusal is tested at the server, in both modes, against the
store's own answer rather than against the merchant a result claims.

Everything here runs on fake transports. No credentials, no network.
"""

import asyncio
import json
import re
import subprocess
import unittest
from pathlib import Path

import httpx

from backend.checkout import CheckoutRequest, CheckoutStudio
from backend.connections import NO_SHOPPABLE_STORE_REASON, VIEW_ONLY_REASON, ShopifyConnection
from backend.errors import ConnectionError
from backend.scene import SceneStudio
from backend.scene_edit import SceneEdit
from backend.storefront import CatalogConnection, StorefrontConnection
from backend.studio import ProductStudio, StudioCommand, catalog_offer


REPO_ROOT = Path(__file__).resolve().parents[3]
STUDIO_ROOT = Path(__file__).resolve().parents[2]
STATIC = STUDIO_ROOT / "backend" / "static"

STORE = "ethyneon.myshopify.com"
STORE_TOKEN = "storefront-token-never-logged"
OTHER = "someone-else.myshopify.com"
OURS = "gid://shopify/ProductVariant/49000000000001"
THEIRS = "gid://shopify/ProductVariant/77000000000002"
OUR_PRODUCT = "gid://shopify/Product/9000000000001"
THEIR_PRODUCT = "gid://shopify/Product/9000000000002"


def run(coroutine):
    return asyncio.run(coroutine)


# ==========================================================================
# Fakes
# ==========================================================================


class FakeStore:
    """The development store's Storefront API. It knows exactly one variant."""

    def __init__(self):
        self.queries: list[str] = []

    def _product(self):
        return {
            "id": OUR_PRODUCT,
            "title": "Walnut Gaming Desk",
            "description": "Solid walnut, 140 cm wide.",
            "onlineStoreUrl": f"https://{STORE}/products/walnut-gaming-desk",
            "featuredImage": {"url": "https://cdn.shopify.com/a.jpg"},
            "media": {"nodes": []},
            "variants": {"nodes": [{
                "id": OURS,
                "availableForSale": True,
                "price": {"amount": "329.00", "currencyCode": "USD"},
                "selectedOptions": [{"name": "Finish", "value": "Walnut"}],
                "image": {"url": "https://cdn.shopify.com/a.jpg"},
            }]},
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = re.search(r"query (\w+)", body["query"]).group(1)
        self.queries.append(name)
        if name == "StoreSearch":
            return httpx.Response(200, json={"data": {"products": {"nodes": [self._product()]}}})
        if name == "StoreProduct":
            return httpx.Response(200, json={"data": {"product": self._product()}})
        # StoreVariant. Only our own variant exists here; anything else is a
        # product this store does not sell, which is the whole question.
        node = body["variables"]["id"]
        if node != OURS:
            return httpx.Response(200, json={"data": {"node": None}})
        variant = self._product()["variants"]["nodes"][0]
        return httpx.Response(200, json={"data": {"node": {**variant, "product": self._product()}}})

    def connection(self, domain=STORE, token=STORE_TOKEN) -> StorefrontConnection:
        return StorefrontConnection(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), domain, token)


class FakeShop:
    """Shop MCP. Records the shop filter every call carried."""

    def __init__(self):
        self.filters: list[list[str]] = []
        self.tools: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        params = body["params"]
        catalog = params["arguments"]["catalog"]
        self.tools.append(params["name"])
        self.filters.append((catalog.get("filters") or {}).get("shops", []))
        product = {
            "id": THEIR_PRODUCT,
            "title": "Oak Standing Desk",
            "variants": [{
                "id": THEIRS,
                "seller": {"domain": OTHER},
                "price": {"amount": 42900, "currency": "USD"},
                "availability": {"available": True},
                "url": f"https://{OTHER}/products/oak-standing-desk?variant=2",
                "options": [{"name": "Finish", "label": "Oak"}],
                "media": [{"type": "image", "url": "https://cdn.shopify.com/oak.jpg"}],
            }],
        }
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"structuredContent": {"products": [product]}},
        })

    def connection(self, shops=()) -> ShopifyConnection:
        return ShopifyConnection(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), tuple(shops))


def router(configured=True, shops=(STORE,)) -> tuple[CatalogConnection, FakeStore, FakeShop]:
    store, shop = FakeStore(), FakeShop()
    connection = CatalogConnection(
        store.connection(STORE if configured else "", STORE_TOKEN if configured else ""),
        shop.connection(shops),
    )
    return connection, store, shop


class FakeGemini:
    """Answers the intent question with a fixed search, and audits as ready."""

    def __init__(self, value="desk"):
        self.value = value

    async def interact(self, payload, **kwargs):
        return {"steps": [{"type": "model_output", "content": [
            {"type": "text", "text": json.dumps({"action": "search", "value": self.value})}]}]}

    async def decide(self, payload):
        return {"answers": {
            "purchase_scope_matches": {"noul": 1.0},
            "required_constraints_resolved": {"noul": 1.0},
            "fits_brief": {"noul": 1.0},
            "next_action": {"choice": "ready", "confidence": 1.0},
        }}


def studio(connection) -> ProductStudio:
    return ProductStudio(connection, FakeGemini(), models=None)


# ==========================================================================
# Routing: the switch picks the source
# ==========================================================================


class ModeRoutingTests(unittest.TestCase):
    def test_the_mode_defaults_to_browse(self):
        self.assertEqual(StudioCommand(command="gaming desk").mode, "browse")

    def test_browse_reaches_shop_mcp_even_with_a_store_configured(self):
        connection, store, shop = router()
        result = run(studio(connection).run(StudioCommand(command="desk", mode="browse")))
        self.assertEqual(shop.tools, ["search_catalog"])
        self.assertEqual(store.queries, [], "the development store must not be searched in browse mode")
        self.assertEqual(result["mode"], "browse")

    def test_browse_drops_the_shop_filter(self):
        """With the store named in SHOPIFY_SHOPS, a filtered browse would
        answer with that one store and "browse all" would mean "browse ours"."""
        connection, _, shop = router(shops=(STORE,))
        self.assertEqual(connection.discovery.shops, (STORE,))
        run(studio(connection).run(StudioCommand(command="desk", mode="browse")))
        self.assertEqual(shop.filters, [[]], "browse must not carry a shops filter")

    def test_discovery_keeps_its_filter_for_everything_else(self):
        connection, _, shop = router(configured=False, shops=(STORE,))
        run(connection.search_catalog({"query": "desk"}))
        self.assertEqual(shop.filters, [[STORE]])

    def test_our_store_mode_reads_the_storefront_and_never_shop_mcp(self):
        """The regression guard: our-store mode is exactly what it was."""
        connection, store, shop = router()
        result = run(studio(connection).run(StudioCommand(command="desk", mode="store")))
        self.assertEqual(store.queries, ["StoreSearch"])
        self.assertEqual(shop.tools, [])
        self.assertEqual(result["mode"], "store")
        self.assertTrue(result["shoppable"])

    def test_browse_keeps_the_read_only_tool_allowlist(self):
        connection, _, _ = router()
        self.assertIsInstance(connection.browse, ShopifyConnection)
        self.assertEqual(connection.browse.shops, ())
        tools = ShopifyConnection.search_catalog.__annotations__
        self.assertIn("catalog", tools)
        source = (STUDIO_ROOT / "backend" / "connections.py").read_text(encoding="utf-8")
        allowlist = re.search(r"ShopTool = Literal\[([^\]]*)\]", source).group(1)
        self.assertEqual(
            sorted(re.findall(r'"([a-z_]+)"', allowlist)),
            ["get_product", "lookup_catalog", "search_catalog"],
            "browse must not widen the Shop MCP tool allowlist",
        )

    def test_an_unconfigured_studio_still_browses(self):
        connection, store, shop = router(configured=False, shops=())
        run(studio(connection).run(StudioCommand(command="desk", mode="browse")))
        self.assertEqual(shop.tools, ["search_catalog"])
        self.assertEqual(store.queries, [])


# ==========================================================================
# A browse result can never be bought
# ==========================================================================


class ViewOnlyResultTests(unittest.TestCase):
    def offers(self, mode, **kwargs):
        connection, _, _ = router(**kwargs)
        return run(studio(connection).run(StudioCommand(command="desk", mode=mode)))

    def test_every_browse_offer_is_marked_view_only(self):
        result = self.offers("browse")
        self.assertFalse(result["shoppable"])
        self.assertTrue(result["results"])
        for offer in result["results"]:
            self.assertFalse(offer["shoppable"])
            self.assertEqual(offer["view_only_reason"], VIEW_ONLY_REASON)

    def test_every_browse_offer_names_the_merchant_and_links_to_them(self):
        for offer in self.offers("browse")["results"]:
            self.assertEqual(offer["merchant"], OTHER)
            self.assertTrue(offer["listing_url"].startswith(f"https://{OTHER}/products/"))

    def test_no_browse_offer_carries_a_purchase_link(self):
        """The old Studio built https://{merchant}/cart/{variant}:1 out of
        seller.domain, which was a real purchase link to a stranger's shop."""
        for offer in self.offers("browse")["results"]:
            blob = json.dumps(offer)
            self.assertNotIn("/cart", blob)
            self.assertNotIn("checkout", blob)
            for key in offer:
                self.assertNotIn(key, {"cart_url", "checkout_url", "cart", "stores"})

    def test_our_store_offers_stay_shoppable(self):
        result = self.offers("store")
        self.assertTrue(result["results"])
        for offer in result["results"]:
            self.assertTrue(offer["shoppable"])
            self.assertIsNone(offer["view_only_reason"])
            self.assertEqual(offer["merchant"], STORE)

    def test_without_a_store_even_our_store_mode_is_honest(self):
        """Nothing is configured to sell from, so nothing is labelled buyable."""
        result = self.offers("store", configured=False, shops=())
        for offer in result["results"]:
            self.assertFalse(offer["shoppable"])

    def test_an_offer_defaults_to_shoppable_only_when_told_so(self):
        offer = catalog_offer({"title": "T"}, {"id": OURS, "seller": {"domain": STORE}}, shoppable=False)
        self.assertFalse(offer["shoppable"])
        self.assertEqual(offer["view_only_reason"], VIEW_ONLY_REASON)


# ==========================================================================
# The server refuses, in both modes
# ==========================================================================


MINIMAL_ROOM = {
    "revision": 3,
    "category": "office",
    "dimensions": {"width": {"value_m": 4.0}, "depth": {"value_m": 3.0}, "height": {"value_m": 2.5}},
    "openings": [],
    "existing_items": [],
    "placed_products": [],
}


class ShoppableGuardTests(unittest.TestCase):
    def test_the_store_is_the_only_authority(self):
        connection, store, shop = router()
        with self.assertRaises(ConnectionError) as caught:
            run(connection.require_shoppable(THEIRS, {}))
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["code"], "variant_view_only")
        self.assertEqual(caught.exception.payload["message"], VIEW_ONLY_REASON)
        self.assertEqual(shop.tools, [], "Shop MCP must not get a vote on what the store sells")

    def test_a_variant_the_store_sells_is_returned(self):
        connection, _, _ = router()
        document = run(connection.require_shoppable(OURS, {}))
        self.assertEqual(document["products"][0]["variants"][0]["id"], OURS)

    def test_with_no_store_configured_nothing_is_shoppable(self):
        connection, _, _ = router(configured=False)
        with self.assertRaises(ConnectionError) as caught:
            run(connection.require_shoppable(OURS, {}))
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.payload["message"], NO_SHOPPABLE_STORE_REASON)

    def test_shop_mcp_on_its_own_refuses_everything(self):
        shop = FakeShop().connection()
        with self.assertRaises(ConnectionError) as caught:
            run(shop.require_shoppable(OURS, {}))
        self.assertEqual(caught.exception.status, 409)

    def test_a_store_outage_is_not_read_as_view_only(self):
        """A 502 means we do not know. Calling it view-only would be a guess,
        and calling it shoppable would be a worse one."""
        def dead(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        connection = CatalogConnection(
            StorefrontConnection(
                httpx.AsyncClient(transport=httpx.MockTransport(dead)), STORE, STORE_TOKEN),
            FakeShop().connection(),
        )
        with self.assertRaises(ConnectionError) as caught:
            run(connection.require_shoppable(OURS, {}))
        self.assertEqual(caught.exception.status, 502)


class ScenePlacementTests(unittest.TestCase):
    """Putting a product in the room is the first step of buying it."""

    def scene(self, configured=True):
        import tempfile

        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = folder / "geometricconfig.json"
        path.write_text(json.dumps(MINIMAL_ROOM), encoding="utf-8")
        connection, store, shop = router(configured=configured)
        return SceneStudio(connection, None, path=path, cache_dir=folder / "cache"), store, shop

    ACTION_IDS = {"add": "aaaaaaaa-0000-0000-0000-000000000001",
                  "replace": "bbbbbbbb-0000-0000-0000-000000000002"}

    def edit(self, scenes, op, variant_id):
        etag = scenes.read()[0]
        body = {"base_etag": etag, "action_id": self.ACTION_IDS[op],
                "op": op, "product_id": "new" if op == "add" else "product_1"}
        key = "new_product" if op == "add" else "replacement"
        body[key] = {"variant_id": variant_id, "title": "A desk"}
        return run(scenes.edit(SceneEdit(**body)))

    def test_add_refuses_a_variant_the_store_does_not_sell(self):
        scenes, _, _ = self.scene()
        with self.assertRaises(ConnectionError) as caught:
            self.edit(scenes, "add", THEIRS)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["message"], VIEW_ONLY_REASON)

    def test_replace_refuses_a_variant_the_store_does_not_sell(self):
        scenes, _, _ = self.scene()
        with self.assertRaises(ConnectionError) as caught:
            self.edit(scenes, "replace", THEIRS)
        self.assertEqual(caught.exception.status, 422)

    def test_a_refused_add_changes_nothing_on_disk(self):
        scenes, _, _ = self.scene()
        before = scenes.path.read_bytes()
        with self.assertRaises(ConnectionError):
            self.edit(scenes, "add", THEIRS)
        self.assertEqual(scenes.path.read_bytes(), before)

    def test_add_accepts_a_variant_the_store_does_sell(self):
        """The guard has to let the real thing through, or it proves nothing."""
        scenes, store, _ = self.scene()
        body = self.edit(scenes, "add", OURS)
        placed = body["scene"]["placed_products"]
        self.assertEqual([item["variant_id"] for item in placed], [OURS])
        self.assertIn("StoreVariant", store.queries)

    def test_with_no_store_configured_nothing_can_be_placed(self):
        scenes, _, _ = self.scene(configured=False)
        with self.assertRaises(ConnectionError) as caught:
            self.edit(scenes, "add", OURS)
        self.assertEqual(caught.exception.status, 409)


class CheckoutGuardTests(unittest.TestCase):
    """The purchase server is never asked to confirm a cart it should refuse."""

    def checkout(self, variant_id):
        import tempfile

        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = folder / "geometricconfig.json"
        path.write_text(json.dumps({**MINIMAL_ROOM, "placed_products": [
            {"id": "product_1", "label": "A desk", "variant_id": variant_id,
             "price": {"amount": 32900, "currency": "USD"},
             "size": {"width": 1.4, "depth": 0.6, "height": 0.75},
             "pose": {"position": {"x": 2.0, "y": 0, "z": 1.5}}},
        ]}), encoding="utf-8")
        connection, _, _ = router()
        scenes = SceneStudio(connection, None, path=path, cache_dir=folder / "cache")
        studio = CheckoutStudio(connection, FakeGemini(), scenes, ethyneon=None)
        self.asked = []
        original = studio._purchase

        async def spy(lines, status, confirm=False):
            self.asked.append({"status": status, "confirm": confirm})
            return await original(lines, status, confirm)

        studio._purchase = spy
        etag = scenes.read()[0]
        return run(studio.review(CheckoutRequest(base_etag=etag, confirm=True)))

    def named_check(self, result):
        return next(c for c in result["checks"] if c["name"] == "Sold on the connected store")

    def test_a_browse_line_fails_the_store_check(self):
        result = self.checkout(THEIRS)
        check = self.named_check(result)
        self.assertFalse(check["ok"])
        self.assertIn(VIEW_ONLY_REASON, check["detail"])
        self.assertFalse(result["lines"][0]["sold_here"])
        self.assertNotEqual(result["status"], "ready")

    def test_a_browse_line_is_never_sent_to_the_confirm_call(self):
        self.checkout(THEIRS)
        self.assertEqual([call["confirm"] for call in self.asked], [False])

    def test_an_own_store_line_passes_the_check_and_may_confirm(self):
        result = self.checkout(OURS)
        self.assertTrue(self.named_check(result)["ok"])
        self.assertTrue(result["lines"][0]["sold_here"])
        self.assertEqual([call["confirm"] for call in self.asked], [True])

    def test_no_checkout_url_is_ever_produced_here(self):
        for variant_id in (THEIRS, OURS):
            with self.subTest(variant=variant_id):
                result = self.checkout(variant_id)
                self.assertEqual(result["stores"], [])
                self.assertNotIn("checkout_url", json.dumps(result))


# ==========================================================================
# The front end says so too
# ==========================================================================


class BrowseFrontEndTests(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "studio.html").read_text(encoding="utf-8")
        self.js = (STATIC / "studio.js").read_text(encoding="utf-8")

    def test_the_mode_switch_is_on_the_page_with_both_options(self):
        self.assertIn('name="mode" value="browse"', self.html)
        self.assertIn('name="mode" value="store"', self.html)
        self.assertIn("Browse all Shopify", self.html)
        self.assertIn("Our store", self.html)

    def test_browse_is_the_checked_default(self):
        self.assertIn('value="browse" checked', self.html)
        self.assertNotIn('value="store" checked', self.html)

    def test_the_attribution_line_is_on_the_page(self):
        self.assertIn(VIEW_ONLY_REASON, self.html)

    def test_a_view_only_card_names_the_merchant(self):
        self.assertIn('"View only, sold by "', self.js)
        self.assertIn("link.textContent = offer.merchant", self.js)

    def test_the_mode_is_sent_with_every_command(self):
        self.assertIn("mode: currentMode()", self.js)

    def test_the_front_end_builds_no_cart_or_checkout_control(self):
        """Comments are allowed to name the thing. Code is not."""
        code = chr(10).join(re.sub(r"//.*$", "", line) for line in self.js.splitlines()).lower()
        self.assertNotIn("/cart", code)
        self.assertNotIn("checkout", code)
        self.assertNotIn("purchase", code)
        for word in ("Add to room", "Add to cart", "Buy", "Checkout"):
            self.assertNotIn(word, self.html)


# ==========================================================================
# Merchant-derived data stays out of git
# ==========================================================================


class MerchantDataStaysUntrackedTests(unittest.TestCase):
    """The model cache and the sample catalogues are built from other
    merchants' product photography and listing data, and this repository
    records no licence covering them. Browse mode fills that cache from more
    merchants, so the rule gets a test rather than a comment."""

    MERCHANT_DATA = re.compile(
        r"(^|/)\.cache/"
        r"|^ethyneon-studio/samples/models/"
        r"|^ethyneon-studio/samples/catalog/"
    )
    SHOULD_BE_IGNORED = (
        "ethyneon-studio/.cache/product-models/abc.json",
        "ethyneon-studio/samples/models/49000000000001.glb",
        "ethyneon-studio/samples/models/49000000000001.json",
        "ethyneon-studio/samples/catalog/products.json",
        "ethyneon-studio/samples/catalog/demo_250_alternatives.json",
    )

    def test_no_merchant_derived_file_is_tracked(self):
        out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True)
        tracked = [line for line in out.stdout.splitlines() if self.MERCHANT_DATA.search(line)]
        self.assertEqual(tracked, [], f"merchant-derived files are tracked: {tracked}")

    def test_gitignore_covers_each_of_those_paths(self):
        for path in self.SHOULD_BE_IGNORED:
            with self.subTest(path=path):
                result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path],
                                        cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f".gitignore does not cover {path}")

    def test_the_committed_room_photos_are_still_allowed(self):
        """samples/rooms is the CC0 set, and the exclusion must not eat it."""
        result = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "ethyneon-studio/samples/rooms/manifest.json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, "the CC0 room sets must stay committable")


if __name__ == "__main__":
    unittest.main()
