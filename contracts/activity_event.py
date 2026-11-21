"""Owner: B (provisional copy in A's full build). Made by everyone."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ActivityEvent(BaseModel):
    seq: int
    ts: float
    step: str
    status: Literal["started", "ok", "warn", "error", "needs_input", "info"]
    detail: str = ""
    data: dict = {}
