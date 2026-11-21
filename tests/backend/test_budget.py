"""Money is integer minor units. Exactness is the whole point."""

from __future__ import annotations

import pytest

from contracts.common import Money
from contracts.interfaces import BudgetLine
from backend.budget import CurrencyMismatch, compute_budget, format_money

pytestmark = pytest.mark.backend


def line(amount: int, quantity: int = 1, currency: str = "USD") -> BudgetLine:
    return BudgetLine(
        variant_id=f"gid://shopify/ProductVariant/{amount}",
        quantity=quantity,
        unit_price=Money(amount_minor=amount, currency=currency),
    )


def test_subtotal_is_exact() -> None:
    result = compute_budget([line(42900), line(31900)])
    assert result.items_subtotal.amount_minor == 74800
    assert result.items_subtotal.currency == "USD"


def test_quantity_multiplies_exactly() -> None:
    result = compute_budget([line(1999, quantity=3)])
    assert result.items_subtotal.amount_minor == 5997


def test_a_price_that_would_round_badly_as_a_float_stays_exact() -> None:
    """0.1 + 0.2 is the classic float failure. In minor units it is 30."""
    result = compute_budget([line(10), line(20)])
    assert result.items_subtotal.amount_minor == 30


def test_many_odd_prices_do_not_drift() -> None:
    lines = [line(333, quantity=3) for _ in range(100)]
    result = compute_budget(lines)
    assert result.items_subtotal.amount_minor == 333 * 3 * 100


def test_the_subtotal_is_an_integer_not_a_float() -> None:
    result = compute_budget([line(42900)])
    assert isinstance(result.items_subtotal.amount_minor, int)
    assert not isinstance(result.items_subtotal.amount_minor, float)


def test_within_budget_is_inclusive_at_the_limit() -> None:
    budget = Money(amount_minor=74800, currency="USD")
    result = compute_budget([line(42900), line(31900)], budget)
    assert result.within_budget is True


def test_one_minor_unit_over_budget_is_over() -> None:
    budget = Money(amount_minor=74799, currency="USD")
    result = compute_budget([line(42900), line(31900)], budget)
    assert result.within_budget is False


def test_without_a_budget_the_verdict_is_unknown_not_true() -> None:
    result = compute_budget([line(42900)])
    assert result.within_budget is None


def test_mixed_currencies_are_an_error_not_a_conversion() -> None:
    with pytest.raises(CurrencyMismatch):
        compute_budget([line(1000, currency="USD"), line(1000, currency="EUR")])


def test_a_budget_in_another_currency_is_an_error() -> None:
    with pytest.raises(CurrencyMismatch):
        compute_budget([line(1000)], Money(amount_minor=5000, currency="EUR"))


def test_an_empty_cart_is_zero_and_within_budget() -> None:
    result = compute_budget([], Money(amount_minor=120000, currency="USD"))
    assert result.items_subtotal.amount_minor == 0
    assert result.within_budget is True


def test_the_note_always_states_that_shipping_and_tax_come_later() -> None:
    """The budget caps merchandise only, and the shopper must be told."""
    result = compute_budget([line(42900)])
    assert "Shipping and tax" in result.note
    assert "checkout" in result.note


def test_formatting_is_display_only_and_never_loses_precision() -> None:
    assert format_money(Money(amount_minor=120000, currency="USD")) == "1200.00 USD"
    assert format_money(Money(amount_minor=7299, currency="USD")) == "72.99 USD"
    assert format_money(Money(amount_minor=5, currency="USD")) == "0.05 USD"


def test_money_from_shopify_parses_decimal_strings_without_floats() -> None:
    from backend.shopify import money_from_shopify

    assert money_from_shopify({"amount": "429.00", "currencyCode": "USD"}).amount_minor == 42900
    assert money_from_shopify({"amount": "72.99", "currencyCode": "USD"}).amount_minor == 7299
    assert money_from_shopify({"amount": "0.05", "currencyCode": "USD"}).amount_minor == 5
    assert money_from_shopify({"amount": "1200", "currencyCode": "USD"}).amount_minor == 120000


def test_money_from_shopify_handles_a_single_decimal_place() -> None:
    from backend.shopify import money_from_shopify

    # "72.9" means 72.90, not 72.09.
    assert money_from_shopify({"amount": "72.9", "currencyCode": "USD"}).amount_minor == 7290
