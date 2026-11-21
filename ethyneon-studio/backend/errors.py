from typing import Any


class ConnectionError(Exception):
    """A safe error that follows the team's REST integration contract."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.payload = {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
