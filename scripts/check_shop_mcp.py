"""Run one real Shop MCP search and print the raw result.

  python scripts/check_shop_mcp.py
  python scripts/check_shop_mcp.py "standing desk" --limit 3
  python scripts/check_shop_mcp.py --raw           print the whole JSON body

This talks to the live public server. It is read-only: it can only call the
four allowlisted discovery tools, and the allowlist is enforced in
backend/shop_mcp.py before any request is built.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.shop_mcp import (  # noqa: E402
    ALL_KNOWN_SERVER_TOOLS,
    ALLOWED_TOOLS,
    PURCHASE_AND_PAYMENT_TOOLS,
    ShopMcpClient,
    ShopMcpError,
    ShopMcpToolNotAllowed,
    assert_tool_allowed,
)


def minor_units(price: dict | None) -> str:
    if not price:
        return "no price"
    return f"{price.get('amount')} {price.get('currency')} (minor units)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="small writing desk")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--raw", action="store_true", help="Print the full JSON result.")
    parser.add_argument(
        "--skip-tools-list", action="store_true", help="Go straight to the search."
    )
    args = parser.parse_args()

    client = ShopMcpClient()
    print(f"endpoint: {client.endpoint}")
    print(f"protocol: {client.protocol_version}")
    print(f"allowlist: {', '.join(sorted(ALLOWED_TOOLS))}")
    print()

    # Prove the guard rail works before touching the network.
    blocked = "complete_checkout"
    try:
        assert_tool_allowed(blocked)
    except ShopMcpToolNotAllowed as exc:
        print(f"guard rail: {blocked} refused as expected")
        print(f"  {exc}")
    else:
        print(f"GUARD RAIL BROKEN: {blocked} was allowed")
        return 2
    print(f"  {len(PURCHASE_AND_PAYMENT_TOOLS)} purchase and payment tools are blocked")
    print()

    try:
        if not args.skip_tools_list:
            body = client.list_tools()
            result = body.get("result", {})
            tools = result.get("tools", [])
            print(f"tools/list: {len(tools)} tools advertised")
            print(f"  ttlMs {result.get('ttlMs')}, cacheScope {result.get('cacheScope')}")
            unknown = {t["name"] for t in tools} - ALL_KNOWN_SERVER_TOOLS
            if unknown:
                print(f"  NEW tools not in our map, review before use: {sorted(unknown)}")
            else:
                print(f"  all {len(tools)} tools accounted for in our map")
            print()

        print(f"search_catalog: {args.query!r}, limit {args.limit}")
        found = client.search_catalog(args.query, limit=args.limit)
    except ShopMcpError as exc:
        print(f"FAILED: {exc}")
        print("  Shop MCP is unavailable. The app falls back to Storefront discovery")
        print("  and labels it on screen. Set SHOP_MCP=0 to skip it entirely.")
        return 1

    content = found.structured_content
    products = content.get("products", [])
    pagination = content.get("pagination", {})

    print(f"  ttlMs {found.ttl_ms}, cacheScope {found.cache_scope}")
    print(f"  {len(products)} products, {pagination.get('total_count')} total matches")
    print()

    if args.raw:
        print(json.dumps(found.raw, indent=2)[:20000])
        return 0

    for product in products:
        variants = product.get("variants", [])
        variant = variants[0] if variants else {}
        seller = variant.get("seller", {})
        dims = [opt for opt in variant.get("options", []) if opt.get("name") == "Size"]
        print(f"  {product.get('title', '')[:78]}")
        print(f"    catalog id   {product.get('id')}")
        print(f"    product id   {variant.get('seller_product_id')}")
        print(f"    variant id   {variant.get('id')}")
        print(f"    price        {minor_units(variant.get('price'))}")
        print(f"    available    {variant.get('availability', {}).get('available')}")
        print(f"    seller       {seller.get('name')} ({seller.get('domain')})")
        print(f"    size option  {dims[0]['label'] if dims else 'none, dimensions not structured'}")
        print(f"    shoppable    False (discovery surface, can never reach a cart)")
        print()

    print("Reminder: these belong to other merchants. The purchasable path is the")
    print("configured development store through the Storefront API.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
