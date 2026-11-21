"""Product search reads the store we can actually sell from.

Studio's discovery was Shop MCP, which searches the public Shop catalog across
every merchant. A shopper could search, place a product in the room, reach
checkout, and only there be told it is view-only. With a Storefront store
configured, search and variant lookups go to that store instead.

Two invariants carry most of the weight here:

  with a store configured, Shop MCP is never asked for a search, and
  without one, nothing changes at all.

Everything runs against a fake transport. No network, no credentials.
"""

import asyncio
import json
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.connections import ShopifyConnection
from backend.errors import ConnectionError
from backend.scene import SceneStudio
from backend.storefront import (
    CatalogConnection,
    StorefrontConnection,
    money_minor,
    variant_listing_url,
)

TOKEN = "test-backend-token"
STORE = "ethyneon.myshopify.com"
STORE_TOKEN = "storefront-token-never-logged"
OTHER = "mygarvee.myshopify.com"
VARIANT = "gid://shopify/ProductVariant/49000000000001"
PRODUCT = "gid://shopify/Product/9000000000001"


def storefront_product(product_id=PRODUCT, variant_id=VARIANT, amount="329.00", available=True):
    return {
        "id": product_id,
        "title": "Walnut side table",
        "description": "<p>Solid <b>walnut</b>, 21.65&quot; high.</p>",
        "onlineStoreUrl": f"https://{STORE}/products/walnut-side-table",
        "featuredImage": {"url": "https://cdn.shopify.com/a.jpg"},
        "media": {"nodes": [{"image": {"url": "https://cdn.shopify.com/b.jpg"}}]},
        "variants": {"nodes": [{
            "id": variant_id,
            "availableForSale": available,
            "price": {"amount": amount, "currencyCode": "USD"},
            "selectedOptions": [{"name": "Finish", "value": "Walnut"}],
            "image": {"url": "https://cdn.shopify.com/c.jpg"},
        }]},
    }


class FakeStore:
    """The dev store's GraphQL endpoint. Records every request it receives."""

    def __init__(self, products=None, errors=None, node=None):
        self.products = [storefront_product()] if products is None else products
        self.errors = errors
        self.node = node
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        self.bodies.append(body)
        if self.errors:
            return httpx.Response(200, json={"errors": self.errors})
        query = body["query"]
        if "StoreSearch" in query:
            return httpx.Response(200, json={"data": {"products": {"nodes": self.products}}})
        if "StoreProduct" in query:
            return httpx.Response(200, json={"data": {"product": self.products[0] if self.products else None}})
        if "StoreVariant" in query:
            return httpx.Response(200, json={"data": {"node": self.node}})
        return httpx.Response(200, json={"data": {}})

    def connection(self, domain=STORE, token=STORE_TOKEN) -> StorefrontConnection:
        return StorefrontConnection(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), domain, token)


def run(coroutine):
    return asyncio.run(coroutine)


# ==========================================================================
# Money, the part that must agree with the purchase server to the cent
# ==========================================================================


class MoneyTests(unittest.TestCase):
    def test_decimal_strings_become_integer_minor_units(self):
        self.assertEqual(money_minor({"amount": "329.00", "currencyCode": "USD"}),
                         {"amount": 32900, "currency": "USD"})
        self.assertEqual(money_minor({"amount": "0.99", "currencyCode": "USD"})["amount"], 99)
        self.assertEqual(money_minor({"amount": "1000", "currencyCode": "USD"})["amount"], 100000)
        self.assertEqual(money_minor({"amount": "12.5", "currencyCode": "USD"})["amount"], 1250)

    def test_the_value_never_passes_through_a_float(self):
        """0.1 + 0.2 arithmetic on prices is how carts end up a cent apart."""
        self.assertEqual(money_minor({"amount": "1234567.89", "currencyCode": "USD"})["amount"],
                         123456789)

    def test_a_missing_or_unparseable_price_is_none(self):
        self.assertIsNone(money_minor(None))
        self.assertIsNone(money_minor({}))
        self.assertIsNone(money_minor({"amount": "not a number"}))

    def test_the_currency_comes_from_the_store(self):
        self.assertEqual(money_minor({"amount": "10.00", "currencyCode": "GBP"})["currency"], "GBP")


class ListingUrlTests(unittest.TestCase):
    def test_the_link_points_at_the_exact_variant(self):
        self.assertEqual(variant_listing_url("https://s.test/products/x", VARIANT),
                         "https://s.test/products/x?variant=49000000000001")

    def test_an_existing_query_string_is_preserved(self):
        self.assertTrue(variant_listing_url("https://s.test/p?a=1", VARIANT).endswith("&variant=49000000000001"))

    def test_no_product_url_gives_no_link(self):
        self.assertIsNone(variant_listing_url(None, VARIANT))


# ==========================================================================
# The search itself
# ==========================================================================


class SearchTests(unittest.TestCase):
    def test_it_queries_the_configured_store_over_graphql(self):
        store = FakeStore()
        run(store.connection().search_catalog({"query": "side table", "pagination": {"limit": 4}}))
        request = store.requests[0]
        self.assertEqual(request.url.host, STORE)
        self.assertEqual(request.url.path, "/api/2026-10/graphql.json")
        self.assertEqual(store.bodies[0]["variables"], {"query": "side table", "first": 4})

    def test_the_token_goes_in_the_header_and_not_the_body(self):
        store = FakeStore()
        run(store.connection().search_catalog({"query": "desk"}))
        self.assertEqual(store.requests[0].headers["X-Shopify-Storefront-Access-Token"], STORE_TOKEN)
        self.assertNotIn(STORE_TOKEN, store.requests[0].content.decode())

    def test_results_match_the_shape_the_rest_of_studio_consumes(self):
        store = FakeStore()
        document = run(store.connection().search_catalog({"query": "side table"}))
        product = document["products"][0]
        variant = product["variants"][0]
        self.assertEqual(product["id"], PRODUCT)
        self.assertEqual(product["title"], "Walnut side table")
        self.assertEqual(variant["id"], VARIANT)
        self.assertEqual(variant["seller"], {"domain": STORE})
        self.assertEqual(variant["price"], {"amount": 32900, "currency": "USD"})
        self.assertEqual(variant["availability"], {"available": True})
        self.assertEqual(variant["options"], [{"name": "Finish", "label": "Walnut"}])
        self.assertEqual(product["media"][0], {"type": "image", "url": "https://cdn.shopify.com/a.jpg"})

    def test_catalog_offer_can_read_the_result_unchanged(self):
        """The whole point of matching the shape: no consumer needs editing."""
        from backend.studio import catalog_offer

        store = FakeStore()
        document = run(store.connection().search_catalog({"query": "side table"}))
        product = document["products"][0]
        offer = catalog_offer(product, product["variants"][0])
        self.assertEqual(offer["merchant"], STORE)
        self.assertEqual(offer["price"]["amount"], 32900)
        self.assertEqual(offer["variant_id"], VARIANT)

    def test_html_is_stripped_from_the_description(self):
        store = FakeStore()
        document = run(store.connection().search_catalog({"query": "x"}))
        self.assertEqual(document["products"][0]["description"], 'Solid walnut , 21.65" high.')

    def test_the_description_matches_what_the_model_pipeline_produces(self):
        """Both strip tags to a space, so `<b>walnut</b>,` keeps a space before
        the comma. Cosmetic, but the two must agree: dimension parsing reads
        this text, and a difference would make the same listing measure
        differently depending on which path fetched it."""
        from backend.product_models import ProductModelConnection
        from backend.storefront import plain_text

        for raw in ('<p>Solid <b>walnut</b>, 21.65&quot; high.</p>', "", "<br>x", "a &amp; b"):
            self.assertEqual(plain_text(raw), ProductModelConnection._description(raw))

    def test_the_seller_is_always_the_configured_store(self):
        """Set by us from configuration, never read from the payload, so a
        surprising response cannot put another merchant in front of a shopper."""
        lying = storefront_product()
        lying["variants"]["nodes"][0]["seller"] = {"domain": OTHER}
        store = FakeStore(products=[lying])
        document = run(store.connection().search_catalog({"query": "x"}))
        self.assertEqual(document["products"][0]["variants"][0]["seller"], {"domain": STORE})
        self.assertNotIn(OTHER, json.dumps(document))

    def test_a_variant_without_a_price_is_dropped(self):
        priceless = storefront_product()
        priceless["variants"]["nodes"][0]["price"] = None
        store = FakeStore(products=[priceless])
        self.assertEqual(run(store.connection().search_catalog({"query": "x"}))["products"], [])

    def test_an_out_of_stock_variant_is_returned_and_marked(self):
        store = FakeStore(products=[storefront_product(available=False)])
        document = run(store.connection().search_catalog({"query": "x"}))
        self.assertFalse(document["products"][0]["variants"][0]["availability"]["available"])

    def test_an_empty_query_is_refused_before_any_request(self):
        store = FakeStore()
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection().search_catalog({"query": "   "}))
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(store.requests, [])

    def test_an_unconfigured_store_refuses_rather_than_guessing(self):
        store = FakeStore()
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection(domain="", token="").search_catalog({"query": "x"}))
        self.assertEqual(caught.exception.payload["code"], "storefront_not_configured")
        self.assertEqual(store.requests, [])

    def test_graphql_errors_do_not_leak_upstream_text(self):
        store = FakeStore(errors=[{"message": f"token {STORE_TOKEN} lacks scope unauthenticated_read"}])
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection().search_catalog({"query": "x"}))
        self.assertEqual(caught.exception.payload["code"], "storefront_query_failed")
        self.assertNotIn(STORE_TOKEN, json.dumps(caught.exception.payload))
        self.assertNotIn("unauthenticated_read", json.dumps(caught.exception.payload))


class FilterTests(unittest.TestCase):
    """Filters the caller sends must be honoured or refused, never ignored.
    A price bound that is quietly dropped is a wrong answer delivered with
    full confidence."""

    def store(self):
        return FakeStore(products=[
            storefront_product(product_id="gid://shopify/Product/1",
                               variant_id="gid://shopify/ProductVariant/1", amount="100.00"),
            storefront_product(product_id="gid://shopify/Product/2",
                               variant_id="gid://shopify/ProductVariant/2", amount="250.00"),
            storefront_product(product_id="gid://shopify/Product/3",
                               variant_id="gid://shopify/ProductVariant/3", amount="900.00"),
        ])

    def prices(self, document):
        return [variant["price"]["amount"]
                for product in document["products"] for variant in product["variants"]]

    def test_a_minimum_price_is_applied(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "filters": {"price": {"min": 20000}}}))
        self.assertEqual(self.prices(document), [25000, 90000])

    def test_a_maximum_price_is_applied(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "filters": {"price": {"max": 25000}}}))
        self.assertEqual(self.prices(document), [10000, 25000])

    def test_both_bounds_are_inclusive(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "filters": {"price": {"min": 10000, "max": 25000}}}))
        self.assertEqual(self.prices(document), [10000, 25000])

    def test_a_bound_that_matches_nothing_returns_nothing(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "filters": {"price": {"min": 100000}}}))
        self.assertEqual(document["products"], [])

    def test_filtering_over_fetches_so_the_page_is_not_short(self):
        store = self.store()
        run(store.connection().search_catalog(
            {"query": "x", "pagination": {"limit": 3}, "filters": {"price": {"min": 1}}}))
        self.assertEqual(store.bodies[0]["variables"]["first"], 9)

    def test_no_filter_means_no_over_fetch(self):
        store = self.store()
        run(store.connection().search_catalog({"query": "x", "pagination": {"limit": 3}}))
        self.assertEqual(store.bodies[0]["variables"]["first"], 3)

    def test_the_limit_is_capped(self):
        store = self.store()
        run(store.connection().search_catalog({"query": "x", "pagination": {"limit": 9999}}))
        self.assertEqual(store.bodies[0]["variables"]["first"], 50)

    def test_a_bound_in_another_currency_is_not_compared_as_a_number(self):
        """30000 minor units is $300 or GBP 300. Comparing them silently
        returns products outside the budget the shopper set."""
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "context": {"currency": "GBP"},
             "filters": {"price": {"max": 100000}}}))
        self.assertEqual(document["products"], [])

    def test_a_matching_currency_still_filters_normally(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "context": {"currency": "USD"},
             "filters": {"price": {"max": 25000}}}))
        self.assertEqual(self.prices(document), [10000, 25000])

    def test_currency_without_a_bound_filters_nothing(self):
        store = self.store()
        document = run(store.connection().search_catalog(
            {"query": "x", "context": {"currency": "GBP"}}))
        self.assertEqual(len(document["products"]), 3)

    def test_image_search_is_refused_not_silently_ignored(self):
        store = self.store()
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection().search_catalog(
                {"query": "side table", "like": [{"image": {"url": "https://x.test/a.jpg"}}]}))
        self.assertEqual(caught.exception.payload["code"], "image_search_unsupported")
        self.assertEqual(store.requests, [], "nothing should be sent for a search we cannot run")

    def test_the_route_surfaces_the_refusal_in_the_standard_shape(self):
        store = self.store()
        config = Settings(connection_token=TOKEN, shopify_storefront_domain=STORE,
                          shopify_storefront_token=STORE_TOKEN)
        with TestClient(create_app(config, transport=httpx.MockTransport(store.handler))) as client:
            response = client.post(
                "/api/shopify/catalog/search", headers={"Authorization": f"Bearer {TOKEN}"},
                json={"query": "side table", "min_price_minor": 20000, "currency": "USD"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([variant["price"]["amount"]
                              for product in response.json()["products"]
                              for variant in product["variants"]], [25000, 90000])


class LookupTests(unittest.TestCase):
    def node(self, variant_id=VARIANT):
        return {
            "id": variant_id,
            "availableForSale": True,
            "price": {"amount": "329.00", "currencyCode": "USD"},
            "selectedOptions": [],
            "image": None,
            "product": storefront_product(variant_id=variant_id),
        }

    def test_the_exact_variant_comes_back_alone(self):
        store = FakeStore(node=self.node())
        document = run(store.connection().lookup_variant(VARIANT, {}))
        variants = document["products"][0]["variants"]
        self.assertEqual([variant["id"] for variant in variants], [VARIANT])

    def test_a_substituted_variant_is_refused(self):
        """Shopify answering with a different id must never be accepted."""
        store = FakeStore(node=self.node(variant_id="gid://shopify/ProductVariant/999"))
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection().lookup_variant(VARIANT, {}))
        self.assertEqual(caught.exception.status, 404)

    def test_a_missing_variant_is_a_404(self):
        store = FakeStore(node=None)
        with self.assertRaises(ConnectionError) as caught:
            run(store.connection().lookup_variant(VARIANT, {}))
        self.assertEqual(caught.exception.payload["code"], "shop_variant_not_found")


# ==========================================================================
# The router: which surface gets asked
# ==========================================================================


class FakeDiscovery(ShopifyConnection):
    """A Shop MCP connection that records calls instead of making them."""

    def __init__(self, found=True):
        self.calls: list[str] = []
        self.found = found

    async def search_catalog(self, catalog):
        self.calls.append("search_catalog")
        return {"source": "shop_mcp", "products": []}

    async def get_product(self, catalog):
        self.calls.append("get_product")
        return {"source": "shop_mcp", "product": {}}

    async def lookup_variant(self, variant_id, context):
        self.calls.append("lookup_variant")
        if not self.found:
            raise ConnectionError(404, "shop_variant_not_found", "no")
        return {"source": "shop_mcp", "products": [{"id": PRODUCT, "variants": []}]}


class RouterTests(unittest.TestCase):
    def test_with_a_store_configured_shop_mcp_is_never_searched(self):
        store, discovery = FakeStore(), FakeDiscovery()
        router = CatalogConnection(store.connection(), discovery)
        document = run(router.search_catalog({"query": "side table"}))
        self.assertEqual(document["source"], "storefront_api")
        self.assertEqual(discovery.calls, [])
        self.assertEqual(router.source, "storefront_api")

    def test_without_a_store_nothing_changes(self):
        """The regression guard for the existing Shop MCP behaviour."""
        store, discovery = FakeStore(), FakeDiscovery()
        router = CatalogConnection(store.connection(domain="", token=""), discovery)
        document = run(router.search_catalog({"query": "side table"}))
        self.assertEqual(document["source"], "shop_mcp")
        self.assertEqual(discovery.calls, ["search_catalog"])
        self.assertEqual(store.requests, [])
        self.assertEqual(router.source, "shop_mcp")

    def test_a_variant_the_store_does_not_have_falls_back_to_discovery(self):
        """A product that reached the room from discovery must still be
        re-readable at checkout, so it can be reported view-only instead of
        failing the whole review."""
        store, discovery = FakeStore(node=None), FakeDiscovery()
        router = CatalogConnection(store.connection(), discovery)
        document = run(router.lookup_variant(VARIANT, {}))
        self.assertEqual(document["source"], "shop_mcp")
        self.assertEqual(discovery.calls, ["lookup_variant"])

    def test_a_store_outage_is_not_masked_by_the_fallback(self):
        """A 502 means we do not know. Answering from Shop MCP instead would
        quietly price the cart off the wrong catalog."""
        store, discovery = FakeStore(errors=[{"message": "upstream"}]), FakeDiscovery()
        router = CatalogConnection(store.connection(), discovery)
        with self.assertRaises(ConnectionError) as caught:
            run(router.lookup_variant(VARIANT, {}))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(discovery.calls, [])


# ==========================================================================
# Cached "similar" suggestions
# ==========================================================================


class AlternativesTests(unittest.TestCase):
    """The cached suggestions file holds 540 products from dozens of merchants,
    and a suggestion from it can be swapped straight into the room. With a
    store configured, anything from another merchant has to go: it would be
    offered, placed, and only refused at checkout."""

    MIXED = [
        {"variant_id": "gid://shopify/ProductVariant/1", "object_type": "side_table",
         "merchant": STORE, "title": "Ours", "price": {"amount": 100, "currency": "USD"}},
        {"variant_id": "gid://shopify/ProductVariant/2", "object_type": "side_table",
         "merchant": OTHER, "title": "Theirs", "price": {"amount": 200, "currency": "USD"}},
        {"variant_id": "gid://shopify/ProductVariant/3", "object_type": "side_table",
         "merchant": STORE, "title": "Ours two", "price": {"amount": 300, "currency": "USD"}},
        {"variant_id": "gid://shopify/ProductVariant/4", "object_type": "lamp",
         "merchant": STORE, "title": "A lamp", "price": {"amount": 400, "currency": "USD"}},
    ]
    GROUPS = [{"seed_variant_id": "gid://shopify/ProductVariant/1",
               "alternatives": [
                   {"variant_id": "gid://shopify/ProductVariant/2", "merchant": OTHER, "title": "Theirs"},
                   {"variant_id": "gid://shopify/ProductVariant/3", "merchant": STORE, "title": "Ours two"},
               ]}]

    def studio(self, configured, catalog=None, groups=None):
        import tempfile
        from unittest import mock

        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "products.json").write_text(
            json.dumps(self.MIXED if catalog is None else catalog), encoding="utf-8")
        (folder / "demo_250_alternatives.json").write_text(
            json.dumps(self.GROUPS if groups is None else groups), encoding="utf-8")
        self.enterContext(mock.patch("backend.scene.CATALOG", folder / "products.json"))
        token = STORE_TOKEN if configured else ""
        domain = STORE if configured else ""
        router = CatalogConnection(FakeStore().connection(domain, token), FakeDiscovery())
        return SceneStudio(router, None)

    def test_the_store_is_reported_only_when_one_is_configured(self):
        self.assertEqual(self.studio(True).shoppable_store, STORE)
        self.assertEqual(self.studio(False).shoppable_store, "")

    def test_a_curated_group_drops_the_other_merchant(self):
        results = self.studio(True).catalog_alternatives("gid://shopify/ProductVariant/1", 10)
        self.assertEqual([item["merchant"] for item in results], [STORE])
        self.assertEqual(results[0]["variant_id"], "gid://shopify/ProductVariant/3")

    def test_a_curated_group_keeps_every_merchant_when_unconfigured(self):
        results = self.studio(False).catalog_alternatives("gid://shopify/ProductVariant/1", 10)
        self.assertEqual(sorted(item["merchant"] for item in results), sorted([OTHER, STORE]))

    def test_matching_a_seed_by_membership_still_works_after_filtering(self):
        """Lookup must search the unfiltered group, or a product whose only
        listing is another merchant's would stop finding its own group."""
        results = self.studio(True).catalog_alternatives("gid://shopify/ProductVariant/2", 10)
        self.assertEqual([item["variant_id"] for item in results], ["gid://shopify/ProductVariant/3"])

    def test_the_same_kind_fallback_drops_other_merchants(self):
        results = self.studio(True, groups=[]).catalog_alternatives("gid://shopify/ProductVariant/1", 10)
        self.assertEqual([item["variant_id"] for item in results], ["gid://shopify/ProductVariant/3"])

    def test_the_same_kind_fallback_is_unchanged_when_unconfigured(self):
        results = self.studio(False, groups=[]).catalog_alternatives("gid://shopify/ProductVariant/1", 10)
        self.assertEqual(sorted(item["variant_id"] for item in results),
                         ["gid://shopify/ProductVariant/2", "gid://shopify/ProductVariant/3"])

    def test_a_catalog_of_only_other_merchants_yields_nothing(self):
        """Honest consequence: with the real sample catalog, which has no
        dev-store products, the cached half of the similar row goes empty and
        every suggestion comes live from the store."""
        others = [{**entry, "merchant": OTHER} for entry in self.MIXED]
        studio = self.studio(True, catalog=others, groups=[])
        self.assertEqual(studio.catalog_alternatives("gid://shopify/ProductVariant/1", 10), [])


# ==========================================================================
# Through the app, the way the browser reaches it
# ==========================================================================


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.auth = {"Authorization": f"Bearer {TOKEN}"}

    def client(self, handler, **settings):
        config = Settings(connection_token=TOKEN, **settings)
        return self.enterContext(
            TestClient(create_app(config, transport=httpx.MockTransport(handler))))

    def test_the_search_route_reads_the_store_when_one_is_configured(self):
        store = FakeStore()
        client = self.client(
            store.handler, shopify_shops=(STORE,),
            shopify_storefront_domain=STORE, shopify_storefront_token=STORE_TOKEN)
        response = client.post("/api/shopify/catalog/search", headers=self.auth,
                               json={"query": "side table", "limit": 4})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "storefront_api")
        self.assertEqual(body["products"][0]["variants"][0]["seller"]["domain"], STORE)
        self.assertEqual(store.requests[0].url.host, STORE)

    def test_providers_reports_which_surface_search_uses(self):
        store = FakeStore()
        configured = self.client(
            store.handler, shopify_storefront_domain=STORE, shopify_storefront_token=STORE_TOKEN)
        reported = configured.get("/api/providers", headers=self.auth).json()["shopify_storefront"]
        self.assertEqual(reported["search_source"], "storefront_api")
        self.assertEqual(reported["store"], STORE)

    def test_providers_says_shop_mcp_when_no_store_is_configured(self):
        reported = self.client(lambda request: httpx.Response(200, json={})).get(
            "/api/providers", headers=self.auth).json()["shopify_storefront"]
        self.assertEqual(reported["search_source"], "shop_mcp")
        self.assertFalse(reported["configured"])

    def test_the_store_token_is_never_in_a_response(self):
        store = FakeStore()
        client = self.client(
            store.handler, shopify_storefront_domain=STORE, shopify_storefront_token=STORE_TOKEN)
        for path, payload in (("/api/shopify/catalog/search", {"query": "x"}),):
            body = client.post(path, headers=self.auth, json=payload).text
            self.assertNotIn(STORE_TOKEN, body)
        self.assertNotIn(STORE_TOKEN, client.get("/api/providers", headers=self.auth).text)


if __name__ == "__main__":
    unittest.main()
