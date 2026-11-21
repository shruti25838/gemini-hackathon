"""The fit engine returns each of fits, tight, does_not_fit and unknown.

Rooms are built here rather than loaded from fixtures so each test states the
exact geometry it depends on. The room is 4.0 m wide by 4.0 m deep with a
0.9 m door on wall_s hinged left, opening inward.
"""

from __future__ import annotations

import pytest

from contracts.catalog_offer import (
    Availability,
    CatalogOffer,
    Freshness,
    ProductDimensions,
    StoreRef,
)
from contracts.common import ColorSpec, Measurement, Money, Point2D, Polygon2D, Pose, Rect2D
from contracts.room_spec import (
    ExistingItem,
    Floor,
    FreeZone,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    Wall,
)
from contracts.scene_revision import ObjectPlacement, SceneRevision
from fit.engine import ProposedPlacement, check_fit

pytestmark = pytest.mark.fit

ROOM_W = 4.0
ROOM_D = 4.0

GREY = ColorSpec(hex="#cccccc", evidence_status="confirmed", source="generated")


def measurement(value: float, status: str = "confirmed") -> Measurement:
    return Measurement(value_m=value, evidence_status=status, source="live")


def make_room(
    *,
    openings: list[Opening] | None = None,
    existing: list[ExistingItem] | None = None,
    free_zones: list[FreeZone] | None = None,
    width_status: str = "confirmed",
    depth_status: str = "confirmed",
) -> RoomSpec:
    return RoomSpec(
        room_revision=1,
        session_id="sess_test",
        dimensions_m=RoomDimensions(
            width_m=measurement(ROOM_W, width_status),
            depth_m=measurement(ROOM_D, depth_status),
            height_m=measurement(2.5),
        ),
        walls=[
            Wall(wall_id="wall_n", length_m=ROOM_W, color=GREY),
            Wall(wall_id="wall_e", length_m=ROOM_D, color=GREY),
            Wall(wall_id="wall_s", length_m=ROOM_W, color=GREY),
            Wall(wall_id="wall_w", length_m=ROOM_D, color=GREY),
        ],
        floor=Floor(color=GREY),
        openings=openings or [],
        existing_items=existing or [],
        free_zones=free_zones or [],
        evidence_status="confirmed",
    )


def inward_door(
    hinge_side: str = "left", offset_m: float = 0.2, width_m: float = 0.9
) -> Opening:
    return Opening(
        opening_id="door_main",
        wall_id="wall_s",
        type="door",
        offset_m=offset_m,
        width_m=width_m,
        height_m=2.03,
        hinge=HingeSpec(
            hinge_side=hinge_side,
            swing_direction="inward",
            swing_angle_max_deg=90.0,
            evidence_status="confirmed",
            motion_evidence_ref="clip_door_001",
        ),
        evidence_status="confirmed",
    )


def empty_scene(placements: list[ObjectPlacement] | None = None) -> SceneRevision:
    return SceneRevision(
        scene_revision=1,
        session_id="sess_test",
        room_revision=1,
        placements=placements or [],
    )


def placed(
    object_id: str, x: float, y: float, w: float, d: float, locked: bool = False
) -> ObjectPlacement:
    return ObjectPlacement(
        object_id=object_id,
        label=object_id,
        pose=Pose(x_m=x, y_m=y),
        footprint=Rect2D(x_m=x, y_m=y, width_m=w, depth_m=d),
        locked=locked,
    )


def proposal(object_id: str, x: float, y: float, w: float, d: float, rot: float = 0.0):
    return ProposedPlacement(
        object_id=object_id,
        pose=Pose(x_m=x, y_m=y, rotation_deg=rot),
        footprint=Rect2D(x_m=x, y_m=y, width_m=w, depth_m=d, rotation_deg=rot),
    )


# ==========================================================================
# fits
# ==========================================================================


def test_fits_in_an_empty_measured_room() -> None:
    report = check_fit(make_room(), empty_scene(), proposal("desk", 2.0, 0.5, 1.2, 0.6))
    assert report.result == "fits"
    assert report.tentative is False
    assert report.rejected_placement is None
    assert report.unknown_reasons == []
    assert report.solver_version.startswith("fit/")


def test_fits_clear_of_an_inward_door() -> None:
    """Against the far wall, well out of the swing."""
    room = make_room(openings=[inward_door()])
    report = check_fit(room, empty_scene(), proposal("desk", 2.5, 0.5, 1.2, 0.6))
    assert report.result == "fits"
    swing = [c for c in report.checks_run if c.name == "door_swing"]
    assert swing and swing[0].result == "pass"


def test_a_report_always_names_its_assumptions() -> None:
    report = check_fit(make_room(), empty_scene(), proposal("desk", 2.0, 0.5, 1.2, 0.6))
    joined = " ".join(report.assumptions)
    assert "clearance margin" in joined
    assert "door swing tested" in joined


# ==========================================================================
# tight
# ==========================================================================


def test_tight_when_clearance_is_below_the_margin_but_nothing_overlaps() -> None:
    # The shelf spans y 0.80 to 1.20. The desk spans y 1.22 to 1.82.
    # That leaves a 0.02 m gap, under the 0.05 m margin.
    scene = empty_scene([placed("shelf", 2.0, 1.0, 1.0, 0.4)])
    report = check_fit(
        make_room(), scene, proposal("desk", 2.0, 1.52, 1.2, 0.6), clearance_margin_m=0.05
    )
    assert report.result == "tight"
    assert report.minimum_clearance_m is not None
    assert report.minimum_clearance_m < 0.05


def test_tight_near_a_wall() -> None:
    report = check_fit(
        make_room(), empty_scene(), proposal("desk", 2.0, 0.31, 1.2, 0.6),
        clearance_margin_m=0.05,
    )
    bounds = [c for c in report.checks_run if c.name == "room_bounds"][0]
    assert bounds.result == "tight"
    assert report.result == "tight"


# ==========================================================================
# does_not_fit
# ==========================================================================


def test_does_not_fit_when_it_overlaps_a_locked_item() -> None:
    scene = empty_scene([placed("monitor", 2.0, 1.0, 0.6, 0.25, locked=True)])
    report = check_fit(make_room(), scene, proposal("desk", 2.0, 1.0, 1.2, 0.6))
    assert report.result == "does_not_fit"
    assert report.rejected_placement is not None
    assert report.rejected_placement.failing_check == "locked_object_overlap"
    assert "monitor" in report.rejected_placement.cause


def test_does_not_fit_when_it_sticks_out_of_the_room() -> None:
    report = check_fit(make_room(), empty_scene(), proposal("desk", 3.8, 2.0, 1.2, 0.6))
    assert report.result == "does_not_fit"
    assert report.rejected_placement.failing_check == "room_bounds"
    assert "outside" in report.rejected_placement.cause


def test_does_not_fit_on_a_door_swing_overlap() -> None:
    """The case the PRD demo turns on: a desk dragged into the door's arc."""
    room = make_room(openings=[inward_door(hinge_side="left", offset_m=0.2, width_m=0.9)])
    # The door hinges at (3.8, 4.0) on wall_s and sweeps into the room.
    report = check_fit(room, empty_scene(), proposal("desk", 3.4, 3.4, 1.0, 0.6))
    assert report.result == "does_not_fit"
    assert report.rejected_placement is not None
    assert report.rejected_placement.failing_check == "door_swing"
    swing = [c for c in report.checks_run if c.name == "door_swing"][0]
    assert swing.blocking_ids == ["door_main"]
    assert "swept area" in swing.detail


def test_a_door_swing_failure_names_the_angle_it_first_collides_at() -> None:
    room = make_room(openings=[inward_door()])
    report = check_fit(room, empty_scene(), proposal("desk", 3.4, 3.4, 1.0, 0.6))
    swing = [c for c in report.checks_run if c.name == "door_swing"][0]
    assert "degrees" in swing.detail


def test_does_not_fit_when_it_blocks_the_walking_route() -> None:
    route = FreeZone(
        zone_id="route_door_to_desk",
        label="Door to desk",
        area=Polygon2D(
            points=[
                Point2D(x_m=0.2, y_m=3.9),
                Point2D(x_m=1.1, y_m=3.9),
                Point2D(x_m=1.1, y_m=0.8),
                Point2D(x_m=0.2, y_m=0.8),
            ]
        ),
        purpose="walking_route",
    )
    report = check_fit(
        make_room(free_zones=[route]), empty_scene(), proposal("desk", 0.7, 2.0, 1.2, 0.6)
    )
    assert report.result == "does_not_fit"
    assert report.rejected_placement.failing_check == "walking_route"


def test_does_not_fit_intruding_on_a_keep_clear_zone() -> None:
    zone = FreeZone(
        zone_id="keep_clear_radiator",
        label="Radiator",
        area=Polygon2D(
            points=[
                Point2D(x_m=3.0, y_m=0.0),
                Point2D(x_m=4.0, y_m=0.0),
                Point2D(x_m=4.0, y_m=0.5),
                Point2D(x_m=3.0, y_m=0.5),
            ]
        ),
        purpose="keep_clear",
    )
    report = check_fit(
        make_room(free_zones=[zone]), empty_scene(), proposal("desk", 3.4, 0.4, 1.0, 0.6)
    )
    assert report.result == "does_not_fit"


# ==========================================================================
# unknown
# ==========================================================================


def test_unknown_when_the_door_has_no_hinge() -> None:
    door = Opening(
        opening_id="door_main",
        wall_id="wall_s",
        type="door",
        offset_m=0.2,
        width_m=0.9,
        height_m=2.03,
        hinge=None,
    )
    report = check_fit(make_room(openings=[door]), empty_scene(), proposal("desk", 2.0, 0.5, 1.2, 0.6))
    assert report.result == "unknown"
    assert any("hinge" in reason for reason in report.unknown_reasons)


def test_unknown_when_the_swing_direction_is_missing() -> None:
    door = Opening(
        opening_id="door_main",
        wall_id="wall_s",
        type="door",
        offset_m=0.2,
        width_m=0.9,
        height_m=2.03,
        hinge=HingeSpec(hinge_side="left", swing_direction="unknown"),
    )
    report = check_fit(make_room(openings=[door]), empty_scene(), proposal("desk", 2.0, 0.5, 1.2, 0.6))
    assert report.result == "unknown"
    assert any("swing direction" in reason for reason in report.unknown_reasons)


def test_unknown_when_the_product_has_no_dimensions() -> None:
    """A variant with no documented size can be sold but never fit-checked."""
    offer = CatalogOffer(
        offer_id="offer_nodims",
        store=StoreRef(store_domain="dev.myshopify.com", api_surface="storefront_api"),
        product_id="gid://shopify/Product/1",
        variant_id="gid://shopify/ProductVariant/1",
        title="Side table, no dimensions",
        price=Money(amount_minor=9900, currency="USD"),
        availability=Availability(available=True),
        listing_url="https://dev.myshopify.com/products/x",
        dimensions=None,
        dimension_source="unknown",
        shoppable=True,
    )
    placement = ProposedPlacement(
        object_id="table", pose=Pose(x_m=2.0, y_m=2.0), footprint=None, offer_ref="offer_nodims"
    )
    report = check_fit(make_room(), empty_scene(), placement, offers=[offer])
    assert report.result == "unknown"
    assert any("no documented footprint" in reason for reason in report.unknown_reasons)
    assert report.minimum_clearance_m is None


def test_unknown_result_must_always_say_why() -> None:
    door = Opening(
        opening_id="d", wall_id="wall_s", type="door", offset_m=0.2, width_m=0.9, height_m=2.0
    )
    report = check_fit(make_room(openings=[door]), empty_scene(), proposal("desk", 2.0, 0.5, 1.0, 0.5))
    assert report.result == "unknown"
    assert report.unknown_reasons


def test_a_sliding_door_sweeps_nothing() -> None:
    door = Opening(
        opening_id="door_main",
        wall_id="wall_s",
        type="door",
        offset_m=0.2,
        width_m=0.9,
        height_m=2.03,
        hinge=HingeSpec(hinge_side="left", swing_direction="sliding", evidence_status="confirmed"),
    )
    report = check_fit(make_room(openings=[door]), empty_scene(), proposal("desk", 2.0, 3.5, 1.2, 0.6))
    assert report.result == "fits"


# ==========================================================================
# tentative
# ==========================================================================


def test_never_a_confident_fit_on_estimated_room_scale() -> None:
    """The rule: a result built on estimated dimensions is labelled tentative."""
    room = make_room(depth_status="estimated")
    report = check_fit(room, empty_scene(), proposal("desk", 2.0, 0.5, 1.2, 0.6))
    assert report.result == "fits"
    assert report.tentative is True
    assert any("estimated" in a for a in report.assumptions)


def test_dimensions_inferred_from_listing_text_are_tentative() -> None:
    offer = CatalogOffer(
        offer_id="offer_mcp",
        store=StoreRef(store_domain="other.myshopify.com", api_surface="shop_mcp"),
        product_id="gid://shopify/Product/2",
        variant_id="gid://shopify/ProductVariant/2",
        title="Folding desk",
        price=Money(amount_minor=7299, currency="USD"),
        availability=Availability(available=True),
        listing_url="https://example.com/p",
        dimensions=ProductDimensions(
            width_m=0.8, depth_m=0.4, height_m=0.72, evidence_status="estimated",
            raw_text="80 x 40 x 72 cm",
        ),
        dimension_source="listing_text_inferred",
        shoppable=False,
    )
    placement = ProposedPlacement(
        object_id="desk", pose=Pose(x_m=2.0, y_m=0.5), footprint=None, offer_ref="offer_mcp"
    )
    report = check_fit(make_room(), empty_scene(), placement, offers=[offer])
    assert report.result == "fits"
    assert report.tentative is True
    assert any("listing text" in a for a in report.assumptions)


def test_metafield_dimensions_in_a_measured_room_are_not_tentative() -> None:
    offer = CatalogOffer(
        offer_id="offer_dev",
        store=StoreRef(store_domain="dev.myshopify.com", api_surface="storefront_api"),
        product_id="gid://shopify/Product/3",
        variant_id="gid://shopify/ProductVariant/3",
        title="Walnut desk",
        price=Money(amount_minor=42900, currency="USD"),
        availability=Availability(available=True),
        listing_url="https://dev.myshopify.com/products/walnut",
        dimensions=ProductDimensions(
            width_m=1.2, depth_m=0.6, height_m=0.74, evidence_status="confirmed"
        ),
        dimension_source="metafield",
        shoppable=True,
    )
    placement = ProposedPlacement(
        object_id="desk", pose=Pose(x_m=2.0, y_m=0.5), footprint=None, offer_ref="offer_dev"
    )
    report = check_fit(make_room(), empty_scene(), placement, offers=[offer])
    assert report.result == "fits"
    assert report.tentative is False


# ==========================================================================
# Determinism, which the sandboxes depend on
# ==========================================================================


def test_the_same_inputs_give_byte_identical_reports() -> None:
    """Argon proposes and Carbon reproduces. If this is not stable, the whole
    independent-test story collapses."""
    room, scene = make_room(openings=[inward_door()]), empty_scene()
    first = check_fit(room, scene, proposal("desk", 3.4, 3.4, 1.0, 0.6))
    second = check_fit(room, scene, proposal("desk", 3.4, 3.4, 1.0, 0.6))
    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})
    assert first.report_id == second.report_id


def test_replacing_an_object_ignores_the_item_being_replaced() -> None:
    """A swap must not collide with the thing it is swapping out."""
    scene = empty_scene([placed("old_desk", 2.0, 0.5, 1.2, 0.6)])
    placement = ProposedPlacement(
        object_id="new_desk",
        pose=Pose(x_m=2.0, y_m=0.5),
        footprint=Rect2D(x_m=2.0, y_m=0.5, width_m=1.4, depth_m=0.7),
        replaces_object_id="old_desk",
    )
    report = check_fit(make_room(), scene, placement)
    assert report.result == "fits"
