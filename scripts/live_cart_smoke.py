"""Create ONE real cart on the development store, then stop.

This is the only script in the project that writes to a live store. It needs
an explicit flag every time:

    python scripts/live_cart_smoke.py --yes-write-to-my-dev-store

Without that flag it runs a dry rehearsal: it picks the variant, prints what
it would do, and writes nothing.

What it does, in order:
  1. Checks both cart-write gates and refuses unless they are open.
  2. Picks one in-stock demo variant from your dev store.
  3. Builds a cart review and approves it.
  4. Writes the cart once, reads it back, and reconciles it line by line.
  5. Runs the deterministic checks and the Decision 1 audit.
  6. Prints the hosted checkout link to this terminal, once.

What it deliberately does not do:
  - It does not pay for anything, and it touches no payment method.
  - It never calls a Shop MCP purchase tool. Shop MCP is not used at all here.
  - It prints no token, and only the last 6 characters of the cart ID.
  - It writes nothing to any file or log. The checkout link goes to stdout
    and nowhere else, so do not redirect this script's output to a file.

You open the link yourself and test with the Shopify test gateway.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.common import Money  # noqa: E402
from contracts.interfaces import BudgetLine, SearchRequirements  # noqa: E402
from backend.cart_write import CartWriteRefused, check_cart_writes_allowed  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.db import Database  # noqa: E402
from backend.services import build_services  # noqa: E402

GO_AHEAD = "--yes-write-to-my-dev-store"


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def cart_suffix(cart_id: str) -> str:
    """Only the tail, so a full cart ID never lands in a scrollback."""
    return cart_id.rsplit("/", 1)[-1][-6:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        GO_AHEAD,
        dest="go_ahead",
        action="store_true",
        help="Actually create a cart on the live development store.",
    )
    parser.add_argument(
        "--query", default="desk", help="What to look for in the catalog."
    )
    parser.add_argument(
        "--variant-id", default=None, help="Use this exact variant instead of searching."
    )
    args = parser.parse_args()

    settings = get_settings()

    rule("1. Safety gates")
    print(f"  store              {settings.shopify_store_domain or '(not configured)'}")
    print(f"  allowlist          {settings.cart_store_allowlist}")
    print(f"  LIVE_CART_WRITES   {'on' if settings.live_cart_writes else 'off'}")
    print(f"  MOCK_COMMERCE      {'on' if settings.mock_commerce else 'off'}")

    if settings.mock_commerce:
        print()
        print("  MOCK_COMMERCE is on, so there is no live store to write to.")
        print("  Set MOCK_COMMERCE=0 in .env first.")
        return 2

    try:
        check_cart_writes_allowed(settings)
    except CartWriteRefused as refused:
        print()
        print(f"  REFUSED: {refused.message}")
        print("  Set LIVE_CART_WRITES=1 in .env, and make sure the store is")
        print("  on ALLOWED_CART_STORE_DOMAINS. Nothing was written.")
        return 2

    print("  both gates open")

    if not args.go_ahead:
        print()
        print(f"  Dry rehearsal: no {GO_AHEAD} flag, so nothing will be written.")

    services = build_services(settings, Database(":memory:"))

    rule("2. Pick one in-stock variant")
    if args.variant_id:
        offer = services.catalog.get_offer(args.variant_id)
    else:
        offers = services.catalog.search_catalog(
            SearchRequirements(
                query=args.query, surface="storefront_api", require_available=True
            ),
            limit=50,
        )
        shoppable = [o for o in offers if o.shoppable and o.availability.available]
        if not shoppable:
            print(f"  No in-stock shoppable variant matched {args.query!r}.")
            return 1
        offer = shoppable[0]

    if not offer.shoppable:
        print("  That variant is not shoppable. Refusing.")
        return 1

    print(f"  title        {offer.title[:70]}")
    print(f"  variant      ...{offer.variant_id.rsplit('/', 1)[-1][-6:]}")
    print(f"  price        {offer.price.amount_minor} {offer.price.currency} (minor units)")
    print(f"  available    {offer.availability.available}")
    print(f"  dimensions   {offer.dimension_source}")

    rule("3. Review and approve")
    session_id = f"sess_smoke_{uuid.uuid4().hex[:8]}"
    services.db.create_session(session_id, "shopper_smoke")

    review = services.carts.build_cart_review(
        session_id,
        "shopper_smoke",
        [
            BudgetLine(
                variant_id=offer.variant_id,
                quantity=1,
                unit_price=Money(amount_minor=0, currency=offer.price.currency),
            )
        ],
        scene_revision=0,
        brief_revision=0,
        budget=Money(amount_minor=1_000_000, currency=offer.price.currency),
    )
    approved = services.carts.confirm_cart(
        review.review_id, 0, str(uuid.uuid4()), current_scene_revision=0
    )
    print(f"  lines        {len(approved.lines)}")
    print(
        f"  subtotal     {approved.merchandise_subtotal.amount_minor} "
        f"{approved.merchandise_subtotal.currency} (minor units)"
    )
    print(f"  status       {approved.status}")

    if not args.go_ahead:
        rule("Stopping here")
        print(f"  This was a rehearsal. Nothing was written to the store.")
        print(f"  Re-run with {GO_AHEAD} to create the cart.")
        return 0

    rule("4. Write the cart once, then read it back")
    written, reconciliation = services.carts.write_cart_once(approved)

    print(f"  cart         ...{cart_suffix(written.cart_reference.cart_id)}")
    print(f"  write        {written.cart_reference.write_outcome}")
    print(f"  reconciled   {reconciliation.summary()}")
    for name, passed in sorted(reconciliation.checks.items()):
        print(f"    {'pass' if passed else 'FAIL'}  {name}")

    if not reconciliation.matches:
        print()
        print("  The cart does not match the approval, so no link is released.")
        return 1

    rule("5. Deterministic checks and the Decision 1 audit")
    audit, checkout_url = services.checkout.continue_checkout(
        written.review_id,
        str(uuid.uuid4()),
        current_scene_revision=0,
        fit_status="fits",
    )
    for check in audit.deterministic_results:
        print(f"    {'pass' if check.passed else 'FAIL'}  {check.name}")
    for answer in audit.decision_answers:
        print(
            f"    {answer.question:32} {answer.answer:14} "
            f"confidence {answer.confidence:.2f}  [{answer.source}]"
        )
    print(f"  outcome      {audit.outcome}")
    print(f"  payment      {services.db.get_cart_review(written.review_id)['payment_state']}")

    if not checkout_url:
        print()
        print("  The audit did not release a link. Nothing further to do.")
        return 1

    rule("6. Your hosted test checkout")
    print("  Open this in a browser. It is printed here and nowhere else:")
    print()
    print(f"    {checkout_url}")
    print()
    print("  Shopify test gateway: card 1 is approved, card 2 is declined.")
    print("  This app never sees the card. Payment stays 'payment_unconfirmed'")
    print("  until a read-only order lookup confirms it, which is off by default.")
    print()
    print("  Do not paste this link into a ticket, a chat or a log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
