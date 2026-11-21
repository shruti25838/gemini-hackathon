"""Karishma's contract models, vendored for her code's internal use only.

These are NOT our contracts and must never leak across the adapter boundary.
`ai/karishma_adapter/contracts_bridge.py` translates between these and the
real ones in `contracts/`.

Vendoring them was unavoidable: her `ai/` imports `contracts.schemas` on
almost every module, and our `contracts/` has different field names, so
pointing her code at ours would break it everywhere rather than adapt it.
Keeping them private here is what stops her dialect becoming the project's.
"""
from .schemas import *  # noqa: F401,F403
from . import schemas  # noqa: F401
