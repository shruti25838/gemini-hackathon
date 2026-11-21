"""Ethyneon fit engine. Pure Python geometry, no web, database or network.

The same package runs on the server and inside the Argon and Carbon sandboxes,
so the import rules here are a hard constraint, not a style preference:

  allowed      the standard library, and contracts/ for the data shapes
  not allowed  fastapi, sqlite3, aiosqlite, httpx, requests, google.*, or any
               other network or storage dependency

tests/fit/test_no_forbidden_imports.py enforces that by reading this package's
source, so a stray import fails the build instead of the demo.

Coordinates are room-local metres throughout. The room occupies the rectangle
from (0, 0) to (width_m, depth_m).

Phase 1 implements check_fit. Phase 0 fixes the version string and the public
surface so the other lanes can import against it.
"""

from __future__ import annotations

SOLVER_VERSION = "fit/0.2.0"

# Defaults, overridable through backend config. Stated in every FitReport's
# assumptions so a result is always self-describing.
DEFAULT_CLEARANCE_MARGIN_M = 0.05
DEFAULT_MIN_WALKWAY_WIDTH_M = 0.75
DEFAULT_DOOR_SWING_STEPS = 7

__all__ = [
    "SOLVER_VERSION",
    "DEFAULT_CLEARANCE_MARGIN_M",
    "DEFAULT_MIN_WALKWAY_WIDTH_M",
    "DEFAULT_DOOR_SWING_STEPS",
]
