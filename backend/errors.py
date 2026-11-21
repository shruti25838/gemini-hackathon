"""The one error shape every failed call returns: {code, message, retryable, details}."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from contracts.common import ErrorResponse


class EthyneonError(Exception):
    """Raise this anywhere in the backend. The handler renders the envelope."""

    status_code: int = 400

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
        )


class StaleRevisionError(EthyneonError):
    """409. The caller's base_revision is behind. Always carries the latest."""

    status_code = 409

    def __init__(self, latest_revision: int, kind: str = "scene") -> None:
        super().__init__(
            code="stale_base_revision",
            message=(
                f"The {kind} has moved on. Rebase on revision {latest_revision} and retry."
            ),
            retryable=True,
            status_code=409,
            details={"latest_revision": latest_revision, "kind": kind},
        )


class NotImplementedYet(EthyneonError):
    """A route that exists in the contract but lands in a later phase.

    It answers honestly rather than pretending to work, so a teammate wiring
    against the route sees the truth immediately.
    """

    status_code = 501

    def __init__(self, route: str, phase: str) -> None:
        super().__init__(
            code="not_implemented_yet",
            message=f"{route} is scaffolded but not implemented. It lands in {phase}.",
            retryable=False,
            status_code=501,
            details={"route": route, "phase": phase},
        )


async def ethyneon_error_handler(_request: Request, exc: EthyneonError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(mode="json"),
    )


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Never leak a stack trace or an internal path to a client."""
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            code="internal_error",
            message="Something went wrong on the server.",
            retryable=True,
            details={},
        ).model_dump(mode="json"),
    )
