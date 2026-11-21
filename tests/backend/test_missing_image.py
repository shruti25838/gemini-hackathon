"""A product with no image, all the way through lane C.

Plenty of real products have no image, and several of the seeded demo ones do
not either. A missing image must be a null field, never an exception and
never an empty string standing in for a URL.

Lane B decides what to draw in its place. Lane C's job is to report honestly
that there is nothing.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from contracts.catalog_offer import CatalogOffer
from contracts.common import Money
from contracts.interfaces import BudgetLine, SearchRequirements
from backend.catalog import normalise_shop_mcp
from backend.services import Services
from backend.shopify import ShopifyStorefront

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.backend, pytest.mark.commerce]

DEV_STORE = "dev.myshopify.com"


def storefront() -> ShopifyStorefront:
    return ShopifyStorefront(DEV_STORE, "placeholder-never-sent", "2026-10")


def product_without_images(**overrides) -> dict:
    product = {
        "id": "gid://shopify/Product/500",
        "title": "Plain Wall Shelf",
        "handle": "plain-wall-shelf",
        "productType": "Shelf",
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/500",
                        "title": "Default",
                        "availableForSale": True,
                        "price": {"amount": "39.00", "currencyCode": "USD"},
                        "selectedOptions": [],
                    }
                }
            ]
        },
    }
    product.update(overrides)
    return product


# ==========================================================================
# Storefront normalisation
# ==========================================================================


def test_a_product_with_no_image_at_all_normalises_cleanly() -> None:
    offers = storefront()._offers_from_product(product_without_images())
    assert len(offers) == 1
    assert offers[0].image_url is None
    assert offers[0].shoppable is True


def test_an_explicit_null_image_is_handled() -> None:
    product = product_without_images(featuredImage=None)
    product["variants"]["edges"][0]["node"]["image"] = None
    offers = storefront()._offers_from_product(product)
    assert offers[0].image_url is None


def test_an_image_object_with_no_url_is_treated_as_no_image() -> None:
    """Shopify can return the object with a null url."""
    product = product_without_images(featuredImage={"url": None})
    product["variants"]["edges"][0]["node"]["image"] = {"url": None}
    offers = storefront()._offers_from_product(product)
    assert offers[0].image_url is None


def test_the_variant_image_wins_when_present() -> None:
    product = product_without_images(featuredImage={"url": "https://cdn/product.jpg"})
    product["variants"]["edges"][0]["node"]["image"] = {"url": "https://cdn/variant.jpg"}
    offers = storefront()._offers_from_product(product)
    assert offers[0].image_url == "https://cdn/variant.jpg"


def test_the_product_image_is_the_fallback() -> None:
    product = product_without_images(featuredImage={"url": "https://cdn/product.jpg"})
    offers = storefront()._offers_from_product(product)
    assert offers[0].image_url == "https://cdn/product.jpg"


# ==========================================================================
# Shop MCP normalisation
# ==========================================================================


def mcp_product(**overrides) -> dict:
    product = {
        "id": "gid://shopify/p/noimage",
        "title": "Desk with no photo",
        "variants": [
            {
                "id": "gid://shopify/ProductVariant/700",
                "title": "Desk with no photo",
                "url": "https://example.com/p",
                "seller_product_id": "gid://shopify/Product/700",
                "price": {"amount": 4500, "currency": "USD"},
                "availability": {"available": True},
                "options": [],
                "seller": {"domain": "other.myshopify.com", "name": "Other"},
            }
        ],
    }
    product.update(overrides)
    return {"products": [product]}


def test_a_discovery_result_with_no_media_has_no_image() -> None:
    offers = normalise_shop_mcp(mcp_product())
    assert len(offers) == 1
    assert offers[0].image_url is None


def test_media_with_no_usable_url_gives_no_image() -> None:
    offers = normalise_shop_mcp(mcp_product(media=[{"type": "image"}]))
    assert offers[0].image_url is None


def test_non_image_media_is_not_used_as_an_image() -> None:
    offers = normalise_shop_mcp(
        mcp_product(media=[{"type": "video", "url": "https://cdn/clip.mp4"}])
    )
    assert offers[0].image_url is None


def test_the_first_real_image_is_used() -> None:
    offers = normalise_shop_mcp(
        mcp_product(
            media=[
                {"type": "video", "url": "https://cdn/clip.mp4"},
                {"type": "image", "url": "https://cdn/photo.jpg"},
            ]
        )
    )
    assert offers[0].image_url == "https://cdn/photo.jpg"


# ==========================================================================
# The contract and the fixtures
# ==========================================================================


def test_the_contract_allows_a_missing_image() -> None:
    assert CatalogOffer.model_fields["image_url"].default is None


def test_an_offer_with_no_image_round_trips() -> None:
    offers = storefront()._offers_from_product(product_without_images())
    payload = offers[0].model_dump(mode="json")
    assert payload["image_url"] is None
    assert CatalogOffer.model_validate(payload).image_url is None


def test_the_fixture_catalog_includes_products_with_no_image() -> None:
    """So the mock exercises this path too, not only the live store."""
    raw = json.loads(
        (REPO_ROOT / "fixtures" / "catalog_offers.sample.json").read_text(encoding="utf-8")
    )
    offers = [CatalogOffer.model_validate(item) for item in raw]
    without = [o for o in offers if o.image_url is None]
    assert without, "no fixture offer exercises the missing-image path"
    assert any(o.shoppable for o in without), "and at least one must be buyable"


# ==========================================================================
# The money path, with no image anywhere
# ==========================================================================


def imageless_shoppable_variant(services: Services) -> str:
    offers = services.catalog.search_catalog(SearchRequirements(), limit=200)
    candidates = [
        o
        for o in offers
        if o.shoppable and o.image_url is None and o.availability.available
    ]
    assert candidates, "the mock catalog has no buyable product without an image"
    return candidates[0].variant_id


def test_a_product_with_no_image_can_be_searched(services: Services) -> None:
    variant_id = imageless_shoppable_variant(services)
    assert variant_id


def test_a_product_with_no_image_can_be_read_fresh(services: Services) -> None:
    offer = services.catalog.get_offer(imageless_shoppable_variant(services))
    assert offer.image_url is None
    assert offer.price.amount_minor > 0


def test_a_product_with_no_image_can_be_reviewed_and_approved(
    services: Services, session_id: str
) -> None:
    variant_id = imageless_shoppable_variant(services)
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        [
            BudgetLine(
                variant_id=variant_id,
                quantity=1,
                unit_price=Money(amount_minor=0, currency="USD"),
            )
        ],
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=1_000_000, currency="USD"),
    )
    assert len(review.lines) == 1

    approved = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    assert approved.status == "approved"


def test_a_product_with_no_image_passes_the_checkout_audit(
    services: Services, session_id: str
) -> None:
    variant_id = imageless_shoppable_variant(services)
    review = services.carts.build_cart_review(
        session_id,
        "shopper_test",
        [
            BudgetLine(
                variant_id=variant_id,
                quantity=1,
                unit_price=Money(amount_minor=0, currency="USD"),
            )
        ],
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=1_000_000, currency="USD"),
    )
    approved = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    audit, _url = services.checkout.continue_checkout(
        approved.review_id, str(uuid.uuid4()), current_scene_revision=0, fit_status="fits"
    )
    assert audit.outcome == "ready"


def test_the_catalog_route_serves_an_imageless_product_as_null(
    services: Services, monkeypatch
) -> None:
    """Null, not an empty string, so a client can tell there is no image."""
    import backend.services as services_module
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setattr(services_module, "_services", services)
    body = TestClient(app).get("/api/catalog/search", params={"limit": 50}).json()

    without = [o for o in body["offers"] if o["image_url"] is None]
    assert without, "the route returned no imageless offers to check"
    for offer in without:
        assert offer["image_url"] is None
        assert offer["image_url"] != ""


def test_a_missing_image_never_affects_the_fit_result(services: Services) -> None:
    """Images are not geometry. A product without one still fit-checks."""
    from contracts.common import Pose
    from fit.analysis import ProposedPlacement, analyze_fit
    from tests.fit.test_engine import empty_scene, make_room

    offers = services.catalog.search_catalog(
        SearchRequirements(require_dimensions=True), limit=200
    )
    candidates = [o for o in offers if o.image_url is None and o.dimensions]
    assert candidates, "no imageless product with dimensions in the mock catalog"
    offer = candidates[0]

    analysis = analyze_fit(
        make_room(),
        empty_scene(),
        ProposedPlacement(
            object_id="obj_x", pose=Pose(x_m=2.0, y_m=2.0), offer_ref=offer.offer_id
        ),
        offers=[offer],
    )
    assert analysis.report.result in ("fits", "tight", "does_not_fit")
    assert analysis.report.result != "unknown"
