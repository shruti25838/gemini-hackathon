"""Owner: C (provisional copy in A's full build)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Dims(BaseModel):
    w: float
    d: float
    h: float


class CatalogOffer(BaseModel):
    product_id: str
    variant_id: str
    title: str
    variant_title: str = ""
    category: str
    price_minor: int
    currency: str
    available: bool
    image_url: Optional[str] = None
    url: Optional[str] = None
    dims_m: Optional[Dims] = None
    colors: list[str] = []
    source: Literal["live", "cached", "fixture", "mock"] = "live"
    fetched_at: Optional[str] = None
