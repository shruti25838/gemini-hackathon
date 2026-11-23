"""Which header the Storefront client authenticates with, and when it switches.

Shopify accepts exactly one authentication method per request, so the client
sends one header and falls back to the other. The fallback used to trigger on
401 only. Against our development store on the 2026-10 endpoint, a public
token sent under the private header comes back **403**, so the fallback never
ran: every catalog read failed with "Storefront returned HTTP 403" while
/api/health still reported commerce as live, because health reports how the
app is configured and not whether the store answers.

Offline. No network: the transport is replaced with a recorder.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.shopify import ShopifyError, ShopifyStorefront

pytestmark = pytest.mark.commerce

SHOP = {"data": {"shop": {"name": "ethyneon"}}}


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class Recorder:
    """Answers by header, and remembers every header it was asked with."""

    def __init__(self, by_header: dict[str, FakeResponse]) -> None:
        self.by_header = by_header
        self.seen: list[str] = []

    def __call__(self, auth_header: str, query: str, variables: dict[str, Any]):
        self.seen.append(auth_header)
        return self.by_header.get(auth_header, FakeResponse(403))


def client_with(recorder: Recorder) -> ShopifyStorefront:
    storefront = ShopifyStorefront("ethyneon.myshopify.com", "token-not-a-real-one")
    storefront._post = recorder  # type: ignore[assignment]
    return storefront


# ==========================================================================
# The regression this file exists for
# ==========================================================================


@pytest.mark.parametrize("wrong_header_status", [401, 403])
def test_a_public_token_falls_back_whichever_refusal_shopify_sends(
    wrong_header_status: int,
) -> None:
    recorder = Recorder({
        ShopifyStorefront.PRIVATE_HEADER: FakeResponse(wrong_header_status),
        ShopifyStorefront.PUBLIC_HEADER: FakeResponse(200, SHOP),
    })
    storefront = client_with(recorder)

    assert storefront.execute("{ shop { name } }") == SHOP["data"]
    assert recorder.seen == [
        ShopifyStorefront.PRIVATE_HEADER,
        ShopifyStorefront.PUBLIC_HEADER,
    ]


def test_403_is_treated_as_a_wrong_header() -> None:
    """The exact status our development store returns."""
    assert 403 in ShopifyStorefront.WRONG_HEADER_STATUSES
    assert 401 in ShopifyStorefront.WRONG_HEADER_STATUSES


def test_the_switch_is_remembered_so_later_calls_do_not_retry() -> None:
    recorder = Recorder({
        ShopifyStorefront.PRIVATE_HEADER: FakeResponse(403),
        ShopifyStorefront.PUBLIC_HEADER: FakeResponse(200, SHOP),
    })
    storefront = client_with(recorder)

    storefront.execute("{ shop { name } }")
    storefront.execute("{ shop { name } }")

    assert recorder.seen == [
        ShopifyStorefront.PRIVATE_HEADER,
        ShopifyStorefront.PUBLIC_HEADER,
        ShopifyStorefront.PUBLIC_HEADER,
    ]


# ==========================================================================
# The fallback stays narrow
# ==========================================================================


def test_a_private_token_never_retries() -> None:
    recorder = Recorder({ShopifyStorefront.PRIVATE_HEADER: FakeResponse(200, SHOP)})
    storefront = client_with(recorder)

    assert storefront.execute("{ shop { name } }") == SHOP["data"]
    assert recorder.seen == [ShopifyStorefront.PRIVATE_HEADER]


def test_a_genuine_refusal_of_both_headers_still_raises() -> None:
    recorder = Recorder({
        ShopifyStorefront.PRIVATE_HEADER: FakeResponse(403),
        ShopifyStorefront.PUBLIC_HEADER: FakeResponse(403),
    })
    storefront = client_with(recorder)

    with pytest.raises(ShopifyError) as refused:
        storefront.execute("{ shop { name } }")

    assert "403" in str(refused.value)
    assert refused.value.retryable is False


def test_a_server_error_is_not_a_header_problem() -> None:
    """500 must not burn the fallback: the header was fine."""
    recorder = Recorder({ShopifyStorefront.PRIVATE_HEADER: FakeResponse(500)})
    storefront = client_with(recorder)

    with pytest.raises(ShopifyError) as failed:
        storefront.execute("{ shop { name } }")

    assert recorder.seen == [ShopifyStorefront.PRIVATE_HEADER]
    assert failed.value.retryable is True


def test_the_token_never_appears_in_an_error() -> None:
    recorder = Recorder({
        ShopifyStorefront.PRIVATE_HEADER: FakeResponse(403),
        ShopifyStorefront.PUBLIC_HEADER: FakeResponse(403),
    })
    storefront = client_with(recorder)

    with pytest.raises(ShopifyError) as refused:
        storefront.execute("{ shop { name } }")

    assert "token-not-a-real-one" not in str(refused.value)
