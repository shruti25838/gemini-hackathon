"""Thin Shopify Storefront layer (Person C owns the deep version).

Live path: Storefront GraphQL with SHOPIFY_STORE_DOMAIN + SHOPIFY_STOREFRONT_TOKEN.
Dimensions come from product metafields custom.width_cm / depth_cm / height_cm
(expose them to the Storefront API in the metafield definition).

Fixture path: only when ETHYNEON_FIXTURE_CATALOG=1. Every offer is labeled
source="fixture" and the UI shows "Fixture catalog - not live"; carts are disabled.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from contracts.schemas import CatalogOffer, Dims

API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-10")
FIXTURE = Path(__file__).resolve().parent.parent / "eval" / "data" / "fixture_catalog.json"

CATEGORY_TYPES = {
    "desk": ["desk"], "chair": ["chair"], "monitor": ["monitor", "display"],
    "lighting": ["light", "lamp", "led"], "storage": ["drawer", "storage", "cabinet"],
}


class ShopError(RuntimeError):
    pass


def live() -> bool:
    return bool(os.getenv("SHOPIFY_STORE_DOMAIN") and os.getenv("SHOPIFY_STOREFRONT_TOKEN"))


def fixture_mode() -> bool:
    return os.getenv("ETHYNEON_FIXTURE_CATALOG") == "1"


def _gql(query: str, variables: dict) -> dict:
    url = f"https://{os.environ['SHOPIFY_STORE_DOMAIN']}/api/{API_VERSION}/graphql.json"
    r = httpx.post(url, json={"query": query, "variables": variables}, timeout=15,
                   headers={"X-Shopify-Storefront-Access-Token": os.environ["SHOPIFY_STOREFRONT_TOKEN"]})
    if r.status_code == 429:
        raise ShopError("Shopify rate limited")
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise ShopError(str(body["errors"])[:300])
    return body["data"]


PRODUCTS_Q = """
query($first:Int!){ products(first:$first){ nodes{
  id title productType tags onlineStoreUrl description
  featuredImage{ url }
  w: metafield(namespace:"custom", key:"width_cm"){ value }
  d: metafield(namespace:"custom", key:"depth_cm"){ value }
  h: metafield(namespace:"custom", key:"height_cm"){ value }
  variants(first:10){ nodes{ id title availableForSale price{ amount currencyCode } image{ url } } }
}}}"""


def _category(title: str, ptype: str) -> str:
    t = f"{ptype} {title}".lower()
    for cat, words in CATEGORY_TYPES.items():
        if any(w in t for w in words):
            return cat
    return "other"


def _dims(p) -> Dims | None:
    try:
        return Dims(w=float(p["w"]["value"]) / 100, d=float(p["d"]["value"]) / 100, h=float(p["h"]["value"]) / 100)
    except (TypeError, KeyError, ValueError):
        return None


def fetch_catalog() -> tuple[list[CatalogOffer], dict[str, str]]:
    """Returns (offers, descriptions by product id). Descriptions go to the injection gate only."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if live():
        data = _gql(PRODUCTS_Q, {"first": 60})
        offers, descs = [], {}
        for p in data["products"]["nodes"]:
            descs[p["id"]] = p.get("description") or ""
            colors = [t.split(":", 1)[1] for t in p.get("tags", []) if t.lower().startswith("color:")]
            for v in p["variants"]["nodes"]:
                offers.append(CatalogOffer(
                    product_id=p["id"], variant_id=v["id"], title=p["title"], variant_title=v["title"],
                    category=_category(p["title"], p.get("productType", "")),
                    price_minor=round(float(v["price"]["amount"]) * 100), currency=v["price"]["currencyCode"],
                    available=v["availableForSale"], image_url=(v.get("image") or p.get("featuredImage") or {}).get("url"),
                    url=p.get("onlineStoreUrl"), dims_m=_dims(p), colors=colors, source="live", fetched_at=now))
        return offers, descs
    if fixture_mode():
        raw = json.loads(FIXTURE.read_text())
        offers = [CatalogOffer(**{**o, "source": "fixture", "fetched_at": now}) for o in raw["offers"]]
        return offers, raw.get("descriptions", {})
    raise ShopError("Shopify is not configured (set SHOPIFY_STORE_DOMAIN and SHOPIFY_STOREFRONT_TOKEN)")


CART_Q = """
mutation($lines:[CartLineInput!]!){ cartCreate(input:{lines:$lines}){
  cart{ id checkoutUrl cost{ subtotalAmount{ amount currencyCode } }
        lines(first:50){ nodes{ quantity merchandise{ ... on ProductVariant{ id price{ amount } } } } } }
  userErrors{ field message } } }"""


def create_cart(lines: list[dict]) -> dict:
    if not live():
        raise ShopError("Cart disabled: catalog is not live")
    data = _gql(CART_Q, {"lines": [{"merchandiseId": l["variant_id"], "quantity": l["quantity"]} for l in lines]})
    res = data["cartCreate"]
    if res["userErrors"]:
        raise ShopError("; ".join(e["message"] for e in res["userErrors"]))
    c = res["cart"]
    return {
        "cart_id": c["id"], "checkout_url": c["checkoutUrl"],
        "subtotal_minor": round(float(c["cost"]["subtotalAmount"]["amount"]) * 100),
        "currency": c["cost"]["subtotalAmount"]["currencyCode"],
        "lines": [{"variant_id": n["merchandise"]["id"], "quantity": n["quantity"],
                   "price_minor": round(float(n["merchandise"]["price"]["amount"]) * 100)} for n in c["lines"]["nodes"]],
    }
