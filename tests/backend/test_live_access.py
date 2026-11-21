"""Live checks against the real Shopify store, Gemini and Shop MCP.

Deselected by default. The main suite is deterministic and offline; these
need credentials and network:

    pytest -m network

They skip rather than fail when a credential is missing, so a teammate
without keys still gets a green run. No secret value is ever asserted on or
printed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ai.model_ids import MODEL_IDS

pytestmark = pytest.mark.network

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_env_file() -> dict[str, str]:
    """Read .env without exporting it.

    load_dotenv() would copy every value into os.environ at import time, and
    pytest imports this module even when these tests are deselected. That
    leaked a real API key into the offline suite and made two stand-in tests
    silently call the live Decisions API. Reading into a local dict keeps the
    rest of the suite hermetic.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_ENV = _read_env_file()


def _setting(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name) or _ENV.get(name)
        if value:
            return value
    return default


API_KEY = _setting("GOOGLE_API_KEY", "GEMINI_API_KEY")
STORE = _setting("SHOPIFY_STORE_DOMAIN")
STOREFRONT_TOKEN = _setting(
    "SHOPIFY_STOREFRONT_PRIVATE_TOKEN", "SHOPIFY_STOREFRONT_TOKEN"
)
API_VERSION = _setting("SHOPIFY_API_VERSION", default="2026-10")

needs_gemini = pytest.mark.skipif(not API_KEY, reason="no Gemini API key configured")
needs_store = pytest.mark.skipif(
    not (STORE and STOREFRONT_TOKEN), reason="no Shopify store configured"
)


# ==========================================================================
# Shopify Storefront
# ==========================================================================


@pytest.fixture(scope="module")
def storefront():
    from backend.shopify import ShopifyStorefront

    return ShopifyStorefront(STORE, STOREFRONT_TOKEN, API_VERSION)


@needs_store
def test_the_token_is_a_storefront_token(storefront) -> None:
    data = storefront.execute("{ shop { name } }")
    assert data["shop"]["name"]


@needs_store
def test_the_live_catalog_returns_shoppable_offers(storefront) -> None:
    offers = storefront.search_products(first=50)
    assert offers, "no offers came back from the live store"
    assert all(o.shoppable for o in offers)
    assert all(o.store.api_surface == "storefront_api" for o in offers)


@needs_store
def test_no_shopify_sample_product_survives_normalisation(storefront) -> None:
    """Snowboards, ski wax and gift cards never reach the shoppable catalog."""
    from backend.shopify import is_sample_product

    offers = storefront.search_products(first=50)
    for offer in offers:
        assert not is_sample_product(offer.title), f"sample leaked through: {offer.title}"


@needs_store
def test_the_live_store_has_seeded_furniture_with_dimensions(storefront) -> None:
    offers = storefront.search_products(first=50)
    with_dimensions = [
        o for o in offers if o.dimensions and o.dimensions.has_footprint
    ]
    assert with_dimensions, "no live product has a usable footprint"
    assert any(o.dimension_source == "metafield" for o in with_dimensions)


@needs_store
def test_live_prices_are_integer_minor_units(storefront) -> None:
    offers = storefront.search_products(first=20)
    for offer in offers:
        assert isinstance(offer.price.amount_minor, int)
        assert offer.price.amount_minor > 0
        assert len(offer.price.currency) == 3


@needs_store
def test_a_fresh_single_variant_read_works(storefront) -> None:
    # Fetch widely: the first page of this store is entirely Shopify sample
    # data, which normalisation drops, so a small page comes back empty.
    offers = storefront.search_products(first=50)
    assert offers, "no non-sample products in the first 50"
    fresh = storefront.get_variant(offers[0].variant_id)
    assert fresh.variant_id == offers[0].variant_id
    assert fresh.freshness.source_label == "live"


# ==========================================================================
# Gemini and the Decisions API
# ==========================================================================


@needs_gemini
@pytest.mark.parametrize(
    "model",
    [
        MODEL_IDS["frontier_fast"],
        MODEL_IDS["fast_workhorse"],
        MODEL_IDS["frontier_argon"],
        MODEL_IDS["frontier_deep"],
        MODEL_IDS["decisions"],
        MODEL_IDS["live_voice_vision"],
    ],
)
def test_every_prd_model_id_is_visible_to_this_key(model: str) -> None:
    from google import genai

    client = genai.Client(api_key=API_KEY)
    names = {m.name.removeprefix("models/") for m in client.models.list()}
    assert model in names


@needs_gemini
def test_the_decisions_api_answers_over_plain_http() -> None:
    """google-genai 2.33.0 has no .decisions client, so this is the real path."""
    from contracts.common import Money
    from contracts.interfaces import AuditInputs, BudgetLine
    from backend.decisions import DecisionsClient, run_checkout_gates

    lines = [
        BudgetLine(
            variant_id="gid://shopify/ProductVariant/1",
            quantity=1,
            unit_price=Money(amount_minor=42900, currency="USD"),
        )
    ]
    packet = AuditInputs(
        session_id="sess_live",
        scene_revision=7,
        brief_revision=2,
        approved_lines=lines,
        merchandise_subtotal=Money(amount_minor=42900, currency="USD"),
        budget=Money(amount_minor=120000, currency="USD"),
        hard_constraints=["keep_door_clear"],
        fit_status="fits",
        live_cart_lines=lines,
        approval_fingerprint="sha256:a",
        cart_fingerprint="sha256:b",
    )
    answers = run_checkout_gates(packet, client=DecisionsClient(API_KEY))

    assert {a.source for a in answers.answers} == {"live"}
    assert answers.all_passed is True
    assert all(0.0 <= a.confidence <= 1.0 for a in answers.answers)


@needs_gemini
def test_the_decisions_api_refuses_a_mismatched_cart() -> None:
    """The gate must notice when the live cart is not what was approved."""
    from contracts.common import Money
    from contracts.interfaces import AuditInputs, BudgetLine
    from backend.decisions import DecisionsClient, run_checkout_gates

    def line(variant: str) -> BudgetLine:
        return BudgetLine(
            variant_id=variant,
            quantity=1,
            unit_price=Money(amount_minor=42900, currency="USD"),
        )

    packet = AuditInputs(
        session_id="sess_live",
        scene_revision=7,
        brief_revision=2,
        approved_lines=[line("gid://shopify/ProductVariant/walnut")],
        merchandise_subtotal=Money(amount_minor=42900, currency="USD"),
        budget=Money(amount_minor=120000, currency="USD"),
        hard_constraints=["keep_door_clear"],
        fit_status="fits",
        live_cart_lines=[line("gid://shopify/ProductVariant/oak")],
        approval_fingerprint="sha256:a",
        cart_fingerprint="sha256:b",
    )
    answers = run_checkout_gates(packet, client=DecisionsClient(API_KEY))
    assert answers.all_passed is False


@needs_gemini
def test_managed_agents_are_reachable_on_this_account() -> None:
    from google import genai

    client = genai.Client(api_key=API_KEY)
    assert isinstance(list(client.agents.list()), list)


# ==========================================================================
# Shop MCP
# ==========================================================================


def test_shop_mcp_discovery_is_live_and_never_shoppable() -> None:
    from backend.catalog import normalise_shop_mcp
    from backend.shop_mcp import ShopMcpClient, ShopMcpError

    try:
        result = ShopMcpClient().search_catalog("small writing desk", limit=3)
    except ShopMcpError as exc:
        pytest.skip(f"Shop MCP unavailable: {exc}")

    offers = normalise_shop_mcp(result.structured_content)
    assert offers
    for offer in offers:
        assert offer.shoppable is False
        assert offer.store.api_surface == "shop_mcp"
        assert offer.price.amount_minor > 0
        assert "<" not in offer.title


def test_shop_mcp_advertises_the_ttl_we_cache_on() -> None:
    from backend.shop_mcp import ShopMcpClient, ShopMcpError

    try:
        body = ShopMcpClient().list_tools()
    except ShopMcpError as exc:
        pytest.skip(f"Shop MCP unavailable: {exc}")

    result = body["result"]
    assert result["ttlMs"] > 0
    assert len(result["tools"]) == 22
