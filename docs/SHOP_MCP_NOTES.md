# Shop MCP: the working recipe

The official pages at `https://mcp.shop.com/skill.md` and its linked
references describe the tools but **do not state the endpoint URL, the
transport, or the headers the server actually requires**. Everything in this
file was established by probing the live server on **2026-11-21** and is
reproduced in `backend/shop_mcp.py`, with a check script at
`scripts/check_shop_mcp.py`.

If any of this stops working, re-probe before assuming the docs are right.

## The endpoint

| Thing | Value |
| --- | --- |
| URL | `https://mcp.shop.com/` — the **root**, not `/mcp`, not `/sse` |
| Transport | Streamable HTTP, stateless |
| Protocol version | `2026-07-28` |
| Handshake | **None.** There is no `initialize` call |
| Auth | None needed for catalog reads. Anonymous works |

`/mcp`, `/sse` and `/api/mcp` all return `404` with an empty body.

## The three required headers

```
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/list | tools/call
Mcp-Name: <tool name>              # tools/call only
```

`Mcp-Method` and `Mcp-Name` are **not** part of the MCP specification and are
not documented anywhere on the Shop pages. Without them:

| Omitted | Response |
| --- | --- |
| `Mcp-Method` | `-32020 missing required Mcp-Method header` |
| `Mcp-Name` on a `tools/call` | `-32020 missing required Mcp-Name header for 'tools/call'` |
| `_meta` in params | `-32602 request _meta is missing or has malformed required fields` |

## The required `_meta` block

Because the server is stateless, every single request carries the protocol
version and client capabilities in `params._meta`, rather than agreeing them
once during an `initialize`:

```json
{
  "_meta": {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {}
  }
}
```

## A complete working call

```bash
curl -sS -X POST https://mcp.shop.com/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: search_catalog' \
  --data '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
      },
      "name": "search_catalog",
      "arguments": {"catalog": {"query": "small writing desk", "pagination": {"limit": 2}}}
    }
  }'
```

Or just run `python scripts/check_shop_mcp.py "standing desk" --limit 3`.

## A confusing error worth knowing

Sending `protocolVersion: "2026-07-28"` to an `initialize` call returns:

```
-32022 Unsupported protocol version
{"requested": "2026-07-28", "supported": ["2026-07-28"]}
```

The requested and supported versions are identical. The real cause is that
this server does not implement `initialize` at all. Do not chase the version
number; skip the handshake.

## Tools

The server advertises **22** tools. `backend/shop_mcp.py` keeps all 22 in a
map and allows only four.

**Allowed, read only:**
`search_catalog`, `lookup_catalog`, `get_product`, `get_checkout`

**Blocked, purchase or payment:**
`create_cart`, `update_cart`, `create_checkout`, `update_checkout`,
`complete_checkout`, `create_spend_permission`, `create_spend_request`,
`get_spend_request`, `update_spend_request`, `cancel_spend_request`,
`complete_spend_request`, `get_account`

`get_account` is read only but returns saved addresses and payment methods, so
it is blocked with the rest.

**Blocked, simply not needed:**
`get_cart`, `search_locations`, `lookup_locations`, `ask_business`,
`search_orders`, `get_order`

The allowlist is enforced in `assert_tool_allowed` before any request is
built, and `tests/backend/test_shop_mcp_allowlist.py` asserts every blocked
tool is refused at three separate entry points.

Note that `get_product_details` and `search_shop_catalog` **do not exist**.
The correct names are `get_product` and `search_catalog`.

## Response shape

From a real `search_catalog`. Fields that matter for `CatalogOffer`:

| Response field | Example | Maps to |
| --- | --- | --- |
| `products[].id` | `gid://shopify/p/6GpwO63YlebZieMeKo6Yik` | catalog ID, a third distinct ID |
| `variants[].id` | `gid://shopify/ProductVariant/54307265806649` | `variant_id` |
| `variants[].seller_product_id` | `gid://shopify/Product/10300986753337` | `product_id` |
| `variants[].price` | `{"amount": 7299, "currency": "USD"}` | `price`, already minor units |
| `variants[].availability.available` | `true` | `availability.available` |
| `variants[].seller` | `{id, name, url, domain}` | `store` |
| `variants[].url` | merchant product URL | `listing_url` |
| `variants[].checkout_url` | direct buy link | **never used** |
| `products[].media[].url` | CDN image | `image_url` |

There are **three** distinct IDs per result. Keep them separate: the catalog
`id` is what `get_product` takes, `seller_product_id` is the Shopify product,
and `variants[].id` is the variant.

## Dimensions: the important gap

**Shop MCP returns no structured dimension field.** There is no width, depth
or height anywhere in the response. The only size information is:

1. an option label, for example `Size: 31.5"W x 15.7"D`, and
2. free text inside `description.plain`, which is raw HTML sales copy, for
   example `Size: 31.5" x 15.7" x 28.3" (80 x 40 x 72 cm)`.

Both are parsed by a **plain-code extractor, never an LLM**, and the result is
recorded as `dimension_source: listing_text_inferred`, which caps any fit
result at tentative. A product with no parseable size gets
`dimension_source: unknown` and the fit engine answers `unknown`.

Only the development store's own metafields produce
`dimension_source: metafield`.

## Caching

`tools/list` advertises `ttlMs: 3600000` with `cacheScope: public`. That value
is used as the cache TTL rather than being hardcoded, and a reused result is
shown with a `cached` badge and its last-updated time.

Note that `tools/call` results come back with `ttlMs: null`, so the TTL from
`tools/list` is the one to carry. `SHOP_MCP_CACHE_TTL_MS_OVERRIDE` exists for
the demo if a shorter window is wanted.

Pagination: `pagination: {limit, cursor}`, limit 1 to 50, default 10. Carry
the opaque cursor forward unchanged with the same search inputs.

Rate limits: not stated anywhere in the docs, and not probed.

## Why Shop MCP results can never be bought

This build takes the deliberate position that **Shop MCP is a labelled
discovery surface only**. Its results belong to other merchants, so:

- `CatalogOffer` refuses `shoppable: true` on the `shop_mcp` surface at the
  model level, not just by convention.
- `CartReview` refuses to be built on the `shop_mcp` surface at all.
- The purchasable path is the configured development store through the
  Storefront API and its hosted **test** checkout.

**This diverges from the PRD.** PRD v3.0 page 10 says to filter Shop MCP to
the configured Shopify stores and "resolve each offer to a verified Storefront
variant before sending it to a Storefront cart". That path was considered and
explicitly rejected in favour of keeping discovery and purchase separate, so
that nothing from another merchant's listing, photo or copy is mirrored into
the dev store. The three or four hero products are seeded by hand with their
own images and dimension metafields, and marked as demo data.

If that decision is ever reversed, the two model validators named above are
where to start, and both have tests that will fail loudly.

## Product text is untrusted

Titles and descriptions come from strangers and arrive as raw HTML. They are
stripped by `backend/sanitise.py` and only ever placed inside a delimited data
block, never in the instruction position of a prompt. `CatalogOffer`
deliberately has no description field, so raw prose cannot ride along with an
offer. See `tests/backend/test_untrusted_product_text.py`.
