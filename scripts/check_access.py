"""Prove access to Shopify, Gemini, Managed Agents and the Decisions API.

  python scripts/check_access.py
  python scripts/check_access.py --only shopify
  python scripts/check_access.py --only gemini

This is gate 1 from the team integration doc: "each person shows a screenshot
or log of their own proof, and all keys sit in .env only".

It never prints, logs or echoes a secret value. Credentials are reported as
present or missing and nothing else. Read the source before trusting that
claim: every print in this file is a literal or a non-secret API response
field.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")

# The six model names PRD v3.0 specifies. None is verified; that is the point
# of this check. They are never used as a fallback.
PRD_MODEL_NAMES = [
    "gemini-4-neon",
    "gemini-4-hydrogen",
    "gemini-4-argon",
    "gemini-4-carbon",
    "gemini-decision-1",
    "gemini-3.8-live",
]

# Shopify's own sample data, which must never appear in the shoppable catalog.
SHOPIFY_SAMPLE_MARKERS = (
    "snowboard",
    "ski wax",
    "skiwax",
    "gift card",
    "selling plans ski",
)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[tuple[str, str, str]] = []


def record(item: str, status: str, detail: str) -> None:
    results.append((item, status, detail))
    print(f"  [{status}] {item}: {detail}")


def first_env(*names: str) -> tuple[Optional[str], Optional[str]]:
    """Return (value, which_name_it_came_from) without revealing the value."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return None, None


# ==========================================================================
# 1. Shopify Storefront API
# ==========================================================================

PRODUCTS_QUERY = """
query EthyneonAccessCheck {
  shop { name primaryDomain { url } }
  products(first: 5) {
    edges {
      node {
        id
        title
        handle
        productType
        width: metafield(namespace: "custom", key: "width") { value type }
        depth: metafield(namespace: "custom", key: "depth") { value type }
        height: metafield(namespace: "custom", key: "height") { value type }
        variants(first: 5) {
          edges {
            node {
              id
              title
              availableForSale
              quantityAvailable
              price { amount currencyCode }
            }
          }
        }
      }
    }
  }
}
"""


def check_shopify() -> None:
    print("\n1. Shopify Storefront API")

    domain = os.environ.get("SHOPIFY_STORE_DOMAIN", "").strip()
    version = os.environ.get("SHOPIFY_API_VERSION", "").strip()

    if not domain:
        record("Shopify store domain", FAIL, "SHOPIFY_STORE_DOMAIN is not set")
        return
    if not version:
        record("Shopify API version", FAIL, "SHOPIFY_API_VERSION is not set")
        return

    endpoint = f"https://{domain}/api/{version}/graphql.json"
    print(f"  endpoint: {endpoint}")

    token, token_var = first_env(
        "SHOPIFY_STOREFRONT_PRIVATE_TOKEN", "SHOPIFY_STOREFRONT_TOKEN"
    )

    import httpx

    # Even with no token, this proves the domain and API version resolve.
    if token is None:
        try:
            probe = httpx.post(
                endpoint,
                json={"query": "{ shop { name } }"},
                headers={"Content-Type": "application/json"},
                timeout=20.0,
            )
        except Exception as exc:  # noqa: BLE001
            record("Shopify reachability", FAIL, f"cannot reach the store: {exc!r}")
            return

        if probe.status_code in (401, 403):
            record(
                "Shopify store and API version",
                PASS,
                f"endpoint resolves and demands auth (HTTP {probe.status_code}), "
                f"so the domain and version {version} are valid",
            )
        elif probe.status_code == 404:
            record(
                "Shopify store and API version",
                FAIL,
                f"HTTP 404: API version {version} is probably not a real version",
            )
        else:
            record(
                "Shopify store and API version",
                FAIL,
                f"unexpected HTTP {probe.status_code} on an unauthenticated probe",
            )

        record(
            "Shopify Storefront token",
            FAIL,
            "neither SHOPIFY_STOREFRONT_PRIVATE_TOKEN nor SHOPIFY_STOREFRONT_TOKEN "
            "is set in .env, so the products query cannot run",
        )
        return

    record("Shopify Storefront token", PASS, f"present in {token_var}")

    # The private token header. A public token uses X-Shopify-Storefront-
    # Access-Token instead; both are tried so the check reports which works.
    header_candidates = [
        ("Shopify-Storefront-Private-Token", token),
        ("X-Shopify-Storefront-Access-Token", token),
    ]

    data: Optional[dict[str, Any]] = None
    used_header: Optional[str] = None
    last_status: Optional[int] = None
    last_body: str = ""

    for header_name, header_value in header_candidates:
        try:
            response = httpx.post(
                endpoint,
                json={"query": PRODUCTS_QUERY},
                headers={
                    "Content-Type": "application/json",
                    header_name: header_value,
                },
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            record("Shopify products query", FAIL, f"request failed: {exc!r}")
            return

        last_status = response.status_code
        last_body = response.text[:300]
        if response.status_code == 200:
            body = response.json()
            if "errors" in body and not body.get("data"):
                continue
            data = body.get("data")
            used_header = header_name
            break

    if data is None:
        record(
            "Shopify products query",
            FAIL,
            f"HTTP {last_status} with both header styles. Response begins: {last_body}",
        )
        record(
            "Token type",
            FAIL,
            "not confirmed as a Storefront token. A 200 with data would confirm it; "
            "an Admin token (shpat_ prefix) will never work on this endpoint",
        )
        return

    record(
        "Shopify products query",
        PASS,
        f"HTTP 200 with data, using the {used_header} header",
    )
    record(
        "Token type",
        PASS,
        "confirmed Storefront: the Storefront GraphQL endpoint returned data",
    )

    shop_name = (data.get("shop") or {}).get("name", "unknown")
    edges = ((data.get("products") or {}).get("edges")) or []
    record("Shop", PASS, f"{shop_name}, {len(edges)} product(s) in the first page")

    samples: list[str] = []
    seeded: list[str] = []
    with_dimensions: list[str] = []

    print()
    for edge in edges:
        node = edge["node"]
        title = node.get("title", "")
        variants = ((node.get("variants") or {}).get("edges")) or []
        dims = {
            axis: (node.get(axis) or {}).get("value") if node.get(axis) else None
            for axis in ("width", "depth", "height")
        }
        has_dims = all(dims[axis] is not None for axis in ("width", "depth", "height"))
        is_sample = any(marker in title.lower() for marker in SHOPIFY_SAMPLE_MARKERS)

        (samples if is_sample else seeded).append(title)
        if has_dims:
            with_dimensions.append(title)

        flag = "sample, excluded" if is_sample else "shoppable"
        print(f"    {title[:56]:58} {len(variants)} variant(s)  [{flag}]")
        print(
            f"      metafields custom.width/depth/height: "
            f"{dims['width']} / {dims['depth']} / {dims['height']}"
        )

    print()
    if seeded:
        record("Seeded furniture", PASS, f"{len(seeded)} non-sample product(s): {seeded}")
    else:
        record(
            "Seeded furniture",
            FAIL,
            "every product in the first page is Shopify sample data; "
            "no furniture to sell yet",
        )

    if samples:
        record(
            "Shopify sample products",
            PASS,
            f"{len(samples)} found and will be excluded from the shoppable catalog: {samples}",
        )
    else:
        record("Shopify sample products", PASS, "none in the first page")

    if with_dimensions:
        record(
            "Dimension metafields",
            PASS,
            f"{len(with_dimensions)} product(s) expose all three of "
            f"custom.width, custom.depth, custom.height",
        )
    else:
        record(
            "Dimension metafields",
            FAIL,
            "no product returned all three dimension metafields. Note that a "
            "metafield must be exposed to the Storefront API to be readable here",
        )


# ==========================================================================
# 2. Gemini
# ==========================================================================


def check_gemini() -> None:
    print("\n2. Gemini")

    key, key_var = first_env("GOOGLE_API_KEY", "GEMINI_API_KEY")
    if key is None:
        record(
            "Gemini API key",
            FAIL,
            "neither GOOGLE_API_KEY nor GEMINI_API_KEY is set in .env",
        )
        record("Gemini call", SKIP, "no key")
        record("Model availability", SKIP, "no key")
        return

    record("Gemini API key", PASS, f"present in {key_var}")

    try:
        from google import genai
    except ImportError as exc:
        record("google-genai SDK", FAIL, f"not importable: {exc!r}")
        return

    record("google-genai SDK", PASS, f"version {genai.__version__}")

    try:
        client = genai.Client(api_key=key)
    except Exception as exc:  # noqa: BLE001
        record("Gemini client", FAIL, f"could not construct: {exc!r}")
        return

    # List what the key can actually see. This is the authoritative answer and
    # avoids guessing a model name.
    available: list[str] = []
    try:
        for model in client.models.list():
            name = getattr(model, "name", "") or ""
            available.append(name.removeprefix("models/"))
    except Exception as exc:  # noqa: BLE001
        record("Model listing", FAIL, f"models.list failed: {exc!r}")
        available = []

    if available:
        record("Model listing", PASS, f"{len(available)} model(s) visible to this key")
        print("\n    models this key can see:")
        for name in sorted(available):
            print(f"      {name}")

    print("\n    PRD model names, checked against that list:")
    for wanted in PRD_MODEL_NAMES:
        if wanted in available:
            record(f"model {wanted}", PASS, "available")
        else:
            near = [a for a in available if a.startswith(wanted.rsplit("-", 1)[0])]
            hint = f" Nearest visible: {near[:3]}" if near else ""
            record(f"model {wanted}", FAIL, f"not available to this key.{hint}")

    # One tiny real call, using a model the key actually has.
    if not available:
        record("Gemini call", SKIP, "no model list to choose from")
        return

    preferred = [m for m in available if "flash" in m and "lite" not in m] or available
    chosen = sorted(preferred)[0]
    try:
        response = client.models.generate_content(
            model=chosen,
            contents="Reply with the single word: ok",
        )
        text = (getattr(response, "text", "") or "").strip()[:40]
        record("Gemini call", PASS, f"{chosen} replied: {text!r}")
    except Exception as exc:  # noqa: BLE001
        record("Gemini call", FAIL, f"{chosen} failed: {type(exc).__name__}: {exc}")


# ==========================================================================
# 3. Managed Agents and the Decisions API
# ==========================================================================


def check_agents_and_decisions() -> None:
    print("\n3. Managed Agents and the Decisions API")

    # Offline facts first. These need no credentials and are worth recording.
    try:
        from google.genai import interactions

        has_antigravity = hasattr(interactions, "AntigravityAgentConfig")
        record(
            "Managed Agents in the SDK",
            PASS if has_antigravity else FAIL,
            "google.genai.interactions exposes AntigravityAgentConfig"
            if has_antigravity
            else "no Antigravity agent config found in the SDK",
        )
    except ImportError as exc:
        record("Managed Agents in the SDK", FAIL, f"interactions not importable: {exc!r}")

    try:
        import google.adk as adk
        from google.adk.agents import _managed_agent  # noqa: F401

        record(
            "ADK ManagedAgent",
            PASS,
            f"google.adk {adk.__version__} provides agents._managed_agent",
        )
    except ImportError as exc:
        record("ADK ManagedAgent", FAIL, f"not available: {exc!r}")

    # The Decisions API. The PRD describes a yes/no answer type called "noul".
    from google.genai import types as genai_types

    decision_types = [n for n in dir(genai_types) if "ecision" in n or "oul" in n.lower()]
    if decision_types:
        record("Decisions API types", PASS, f"found: {decision_types}")
    else:
        record(
            "Decisions API types",
            FAIL,
            "google-genai exposes no Decisions types and no 'noul' answer type. "
            "The PRD's gemini-decision-1 and 'noul' are unverified",
        )

    key, _ = first_env("GOOGLE_API_KEY", "GEMINI_API_KEY")
    if key is None:
        record("Managed Agents account access", SKIP, "no API key to test with")
        record("Decisions API account access", SKIP, "no API key to test with")
        return

    try:
        from google import genai

        client = genai.Client(api_key=key)
        listed = list(client.agents.list())
        record(
            "Managed Agents account access",
            PASS,
            f"client.agents.list() succeeded, {len(listed)} agent(s)",
        )
    except Exception as exc:  # noqa: BLE001
        record(
            "Managed Agents account access",
            FAIL,
            f"{type(exc).__name__}: {str(exc)[:200]}",
        )


# ==========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["shopify", "gemini", "agents"], default=None)
    args = parser.parse_args()

    print("Ethyneon access check")
    print("No secret value is ever printed by this script.")

    if args.only in (None, "shopify"):
        check_shopify()
    if args.only in (None, "gemini"):
        check_gemini()
    if args.only in (None, "agents"):
        check_agents_and_decisions()

    print("\n" + "=" * 70)
    print(f"{'ITEM':44} RESULT")
    print("-" * 70)
    for item, status, _detail in results:
        print(f"{item[:44]:44} {status}")

    failed = sum(1 for _i, s, _d in results if s == FAIL)
    skipped = sum(1 for _i, s, _d in results if s == SKIP)
    passed = sum(1 for _i, s, _d in results if s == PASS)
    print("-" * 70)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
