"""Money. Integer minor units throughout, never a float.

A float is wrong for money and the error compounds across lines. Everything
here is integer arithmetic, and a mixed-currency cart is an error rather than
something to coerce.
"""

from __future__ import annotations

from typing import Optional, Sequence

from contracts.common import Money
from contracts.interfaces import BudgetLine, BudgetResult

SHIPPING_AND_TAX_NOTE = (
    "Shipping and tax are added at checkout and are not included in this subtotal."
)


class CurrencyMismatch(ValueError):
    """Two amounts in different currencies were combined."""


def line_subtotal(line: BudgetLine) -> Money:
    return Money(
        amount_minor=line.unit_price.amount_minor * line.quantity,
        currency=line.unit_price.currency,
    )


def add(a: Money, b: Money) -> Money:
    if a.currency != b.currency:
        raise CurrencyMismatch(f"cannot add {a.currency} to {b.currency}")
    return Money(amount_minor=a.amount_minor + b.amount_minor, currency=a.currency)


def compute_budget(
    lines: Sequence[BudgetLine], budget: Optional[Money] = None
) -> BudgetResult:
    """Exact subtotal in minor units, and whether it is within budget.

    The budget caps the merchandise subtotal only. Shipping and tax are
    unknown until checkout and are never guessed at.
    """
    if not lines:
        currency = budget.currency if budget else "USD"
        return BudgetResult(
            items_subtotal=Money(amount_minor=0, currency=currency),
            budget=budget,
            within_budget=True if budget else None,
            note=SHIPPING_AND_TAX_NOTE,
        )

    currencies = {line.unit_price.currency for line in lines}
    if len(currencies) > 1:
        raise CurrencyMismatch(f"cart lines mix currencies: {sorted(currencies)}")
    currency = currencies.pop()

    total = sum(line.unit_price.amount_minor * line.quantity for line in lines)
    subtotal = Money(amount_minor=total, currency=currency)

    within: Optional[bool] = None
    if budget is not None:
        if budget.currency != currency:
            raise CurrencyMismatch(
                f"budget is {budget.currency} but the lines are {currency}"
            )
        within = subtotal.amount_minor <= budget.amount_minor

    return BudgetResult(
        items_subtotal=subtotal,
        budget=budget,
        within_budget=within,
        note=SHIPPING_AND_TAX_NOTE,
    )


def format_money(money: Money) -> str:
    """Display only. Never feed this back into arithmetic."""
    major, minor = divmod(money.amount_minor, 100)
    return f"{major}.{minor:02d} {money.currency}"
