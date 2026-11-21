"""check_fit: the one place a fit result is decided.

Pure geometry. No web, database or network imports, because the same code
runs on the server and inside the Argon and Carbon sandboxes and must produce
an identical FitReport from identical inputs.

Three rules the tests pin down:

  unknown wins. If a dimension, hinge or swing direction is missing, the
  result is unknown and the reasons say exactly what was missing. The engine
  never fills a gap with an assumption.

  estimated scale never yields a confident fits. If the room dimension the
  check depends on is still estimated, or the product dimensions were read
  out of listing text, the report is marked tentative.

  does_not_fit always carries the rejected placement and its cause, so the
  replay and the bounded repair have something concrete to work with.

The work happens in fit/analysis.py, which also keeps the overlap polygons,
the door swept areas, the corridor profile and a plain-English cause. This
function returns only the FitReport, because that is the shape the contract
fixes. Call `analyze_fit` directly when you want the rest.
"""

from __future__ import annotations

from typing import Optional, Sequence

from contracts.catalog_offer import CatalogOffer
from contracts.fit_report import FitReport
from contracts.room_spec import RoomSpec
from contracts.scene_revision import SceneRevision
from fit import (
    DEFAULT_CLEARANCE_MARGIN_M,
    DEFAULT_DOOR_SWING_STEPS,
    DEFAULT_MIN_WALKWAY_WIDTH_M,
    SOLVER_VERSION,
)
from fit.analysis import FitAnalysis, ProposedPlacement, analyze_fit

__all__ = ["check_fit", "analyze_fit", "ProposedPlacement", "FitAnalysis", "SOLVER_VERSION"]


def check_fit(
    room_spec: RoomSpec,
    scene: SceneRevision,
    placement: ProposedPlacement,
    offers: Optional[Sequence[CatalogOffer]] = None,
    *,
    clearance_margin_m: float = DEFAULT_CLEARANCE_MARGIN_M,
    min_walkway_width_m: float = DEFAULT_MIN_WALKWAY_WIDTH_M,
    door_swing_steps: int = DEFAULT_DOOR_SWING_STEPS,
) -> FitReport:
    """Decide whether a placement fits, and say exactly why."""
    return analyze_fit(
        room_spec,
        scene,
        placement,
        offers,
        clearance_margin_m=clearance_margin_m,
        min_walkway_width_m=min_walkway_width_m,
        door_swing_steps=door_swing_steps,
    ).report
