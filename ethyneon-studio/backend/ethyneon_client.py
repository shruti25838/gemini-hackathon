"""Client for the Ethyneon purchase server (lane C).

Studio does not decide what can be bought and does not mint checkout links.
It asks this server, which owns the things Studio has no way to know:

  which variants are on the configured development store
  whether live cart writes are permitted at all
  the shopper's approval record and its fingerprint
  the deterministic reconciliation and the Decision 1 audit

Before this existed, `checkout.py` built `https://{merchant}/cart/{variant}:1`
straight from Shop MCP's `seller.domain`. Those are real, working Shopify
cart permalinks to arbitrary third-party merchants, so a shopper could pay a
stranger's store from the demo. That code is gone; this is what replaced it.

Nothing here logs a checkout URL, and nothing writes one to a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class PurchaseServerUnavailable(RuntimeError):
    """The purchase server is not configured or did not answer.

    Raised rather than returning a link, because the only safe behaviour
    when we cannot reach the server is no purchase path at all.
    """

    def __init__(self, message: str, *, code: str = "purchase_server_unavailable") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class EthyneonClient:
    """Thin async wrapper over the four routes Studio needs."""

    base_url: str
    token: str = ""
    timeout_s: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise PurchaseServerUnavailable(
                "ETHYNEON_API_BASE is not set, so there is no purchase server to ask.",
                code="purchase_server_not_configured",
            )
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                response = await client.request(
                    method, url, headers=self._headers(), json=payload
                )
        except Exception as error:  # noqa: BLE001
            raise PurchaseServerUnavailable(
                f"The purchase server at {self.base_url} did not answer."
            ) from error

        if response.status_code >= 500:
            raise PurchaseServerUnavailable(
                f"The purchase server returned {response.status_code}."
            )

        try:
            body = response.json()
        except ValueError as error:
            raise PurchaseServerUnavailable(
                "The purchase server returned something that is not JSON."
            ) from error

        if not isinstance(body, dict):
            raise PurchaseServerUnavailable("The purchase server returned an unexpected shape.")

        # 4xx bodies are the standard {code, message, retryable, details}
        # envelope and are returned to the caller, which turns them into
        # plain words for the shopper. They are refusals, not outages.
        body.setdefault("_status_code", response.status_code)
        return body

    # -- the four routes ---------------------------------------------------

    async def create_session(self, shopper_id: str | None = None) -> dict[str, Any]:
        return await self._request("POST", "/api/session", {"shopper_id": shopper_id})

    async def cart_review(
        self,
        session_id: str,
        selections: list[dict[str, Any]],
        *,
        budget_minor: int | None = None,
        currency: str = "USD",
    ) -> dict[str, Any]:
        """Build a cart review. This is a read: it records no approval."""
        return await self._request(
            "POST",
            "/api/cart/review",
            {
                "session_id": session_id,
                "selections": selections,
                "budget_minor": budget_minor,
                "currency": currency,
            },
        )

    async def cart_confirm(
        self, session_id: str, review_id: str, base_revision: int, action_id: str
    ) -> dict[str, Any]:
        """Record the shopper's approval.

        Only ever called from the explicit link-release step, never from a
        review. An approval the shopper did not give is worse than no cart.
        """
        return await self._request(
            "POST",
            "/api/cart/confirm",
            {
                "session_id": session_id,
                "review_id": review_id,
                "base_revision": base_revision,
                "action_id": action_id,
            },
        )

    async def checkout_continue(
        self, session_id: str, review_id: str, action_id: str, *, fit_status: str = "fits"
    ) -> dict[str, Any]:
        """Reconcile, audit, and release the hosted checkout URL if both pass."""
        return await self._request(
            "POST",
            "/api/checkout/continue",
            {
                "session_id": session_id,
                "review_id": review_id,
                "action_id": action_id,
                "fit_status": fit_status,
            },
        )

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health")


def refusal_reason(body: dict[str, Any]) -> str:
    """Plain words for the shopper from our standard error envelope."""
    code = str(body.get("code") or "")
    message = str(body.get("message") or "").strip()

    friendly = {
        "no_shoppable_lines": "Not sold on the connected store, so it is view-only here.",
        "live_cart_writes_refused": "Cart writes are disabled on the purchase server.",
        "unknown_session": "The purchase server lost this session; start the review again.",
        "cart_changed": "Prices or stock changed, so the cart needs a fresh look.",
        "not_approved": "This cart has not been approved yet.",
        "stale_base_revision": "The room changed after this cart was built.",
    }
    if code in friendly:
        return friendly[code]
    return message or "The purchase server refused this cart."
