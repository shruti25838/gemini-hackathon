"""The Shop MCP allowlist. No purchase or payment tool can be reached.

These run offline. The guard is checked before any request is built, so no
network is needed to prove it holds.
"""

from __future__ import annotations

import pytest

from backend.shop_mcp import (
    ALL_KNOWN_SERVER_TOOLS,
    ALLOWED_TOOLS,
    OTHER_SERVER_TOOLS,
    PURCHASE_AND_PAYMENT_TOOLS,
    ShopMcpClient,
    ShopMcpToolNotAllowed,
    assert_tool_allowed,
    build_headers,
    build_payload,
)

pytestmark = [pytest.mark.backend, pytest.mark.commerce]


def test_allowlist_is_exactly_the_four_read_only_tools() -> None:
    assert ALLOWED_TOOLS == {
        "search_catalog",
        "lookup_catalog",
        "get_product",
        "get_checkout",
    }


def test_the_tool_map_covers_every_tool_the_server_advertises() -> None:
    """The live server advertised 22 tools. If that changes, the map must be
    reviewed before anything new is called."""
    assert len(ALL_KNOWN_SERVER_TOOLS) == 22
    assert not (ALLOWED_TOOLS & PURCHASE_AND_PAYMENT_TOOLS)
    assert not (ALLOWED_TOOLS & OTHER_SERVER_TOOLS)


@pytest.mark.parametrize("tool", sorted(PURCHASE_AND_PAYMENT_TOOLS))
def test_every_purchase_and_payment_tool_is_rejected(tool: str) -> None:
    """The requirement, stated directly: no tool that completes a purchase or
    uses saved payment details can be called."""
    with pytest.raises(ShopMcpToolNotAllowed):
        assert_tool_allowed(tool)


@pytest.mark.parametrize("tool", sorted(PURCHASE_AND_PAYMENT_TOOLS))
def test_purchase_tools_are_rejected_by_the_client_entry_point(tool: str) -> None:
    """call_tool is the only way in, and it refuses before building a request."""
    client = ShopMcpClient()
    with pytest.raises(ShopMcpToolNotAllowed):
        client.call_tool(tool, {})


@pytest.mark.parametrize("tool", sorted(PURCHASE_AND_PAYMENT_TOOLS | OTHER_SERVER_TOOLS))
def test_blocked_tools_cannot_even_produce_headers_or_a_payload(tool: str) -> None:
    """Defence in depth: the request builders refuse too, so a future caller
    that bypasses call_tool still cannot construct the call."""
    with pytest.raises(ShopMcpToolNotAllowed):
        build_headers("tools/call", tool, "2026-07-28")
    with pytest.raises(ShopMcpToolNotAllowed):
        build_payload("tools/call", "2026-07-28", tool=tool, arguments={})


def test_unknown_tool_names_are_rejected() -> None:
    for tool in ("", "search_shop_catalog", "get_product_details", "drop_tables"):
        with pytest.raises(ShopMcpToolNotAllowed):
            assert_tool_allowed(tool)


@pytest.mark.parametrize("tool", sorted(ALLOWED_TOOLS))
def test_allowed_tools_pass_the_guard(tool: str) -> None:
    assert_tool_allowed(tool)
    headers = build_headers("tools/call", tool, "2026-07-28")
    assert headers["Mcp-Name"] == tool


def test_the_error_message_says_why_a_purchase_tool_is_refused() -> None:
    with pytest.raises(ShopMcpToolNotAllowed) as excinfo:
        assert_tool_allowed("complete_checkout")
    message = str(excinfo.value)
    assert "payment" in message or "checkout" in message
    assert "search_catalog" in message, "the message should name what is allowed"


# --------------------------------------------------------------------------
# The undocumented protocol details, pinned so a regression is obvious
# --------------------------------------------------------------------------


def test_headers_carry_the_three_required_fields() -> None:
    headers = build_headers("tools/call", "search_catalog", "2026-07-28")
    assert headers["MCP-Protocol-Version"] == "2026-07-28"
    assert headers["Mcp-Method"] == "tools/call"
    assert headers["Mcp-Name"] == "search_catalog"


def test_tools_list_needs_no_tool_name() -> None:
    headers = build_headers("tools/list", None, "2026-07-28")
    assert "Mcp-Name" not in headers
    assert headers["Mcp-Method"] == "tools/list"


def test_tools_call_without_a_tool_name_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        build_headers("tools/call", None, "2026-07-28")


def test_every_payload_carries_the_meta_block() -> None:
    """Without _meta the server answers -32602."""
    for payload in (
        build_payload("tools/list", "2026-07-28"),
        build_payload("tools/call", "2026-07-28", tool="search_catalog", arguments={}),
    ):
        meta = payload["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
        assert "io.modelcontextprotocol/clientCapabilities" in meta


def test_search_catalog_payload_shape() -> None:
    client = ShopMcpClient()
    payload = build_payload(
        "tools/call",
        client.protocol_version,
        tool="search_catalog",
        arguments={"catalog": {"query": "desk", "pagination": {"limit": 5}}},
    )
    assert payload["method"] == "tools/call"
    assert payload["params"]["name"] == "search_catalog"
    assert payload["params"]["arguments"]["catalog"]["pagination"]["limit"] == 5
