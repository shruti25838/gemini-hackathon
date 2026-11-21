"""Build the valid sample fixtures in fixtures/ from the contract models.

  python scripts/make_fixtures.py

Every file written here is constructed through its Pydantic model, so a
committed fixture cannot drift out of the contract without the contract test
noticing. Timestamps and IDs are fixed, not generated, so re-running this
produces byte-identical output and the diff stays readable.

The deliberately invalid samples live in fixtures/failures/ and are hand
written raw JSON, because an invalid fixture cannot be built from the model
that rejects it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from contracts.activity_event import ActivityEvent, RevisionRefs  # noqa: E402
from contracts.cart_review import (  # noqa: E402
    ApprovalRecord,
    CartLine,
    CartRef,
    CartReview,
)
from contracts.catalog_offer import (  # noqa: E402
    Availability,
    CatalogOffer,
    Freshness,
    ProductDimensions,
    SelectedOption,
    StoreRef,
)
from contracts.checkout_audit import (  # noqa: E402
    CheckoutAudit,
    DecisionAnswer,
    DeterministicCheck,
)
from contracts.common import (  # noqa: E402
    ColorSpec,
    Measurement,
    Money,
    Point2D,
    Polygon2D,
    Pose,
    Rect2D,
)
from contracts.design_brief import (  # noqa: E402
    DesignBrief,
    HardConstraint,
    Preferences,
    RequiredItem,
)
from contracts.fit_report import FitCheck, FitReport, RejectedPlacement  # noqa: E402
from contracts.interfaces import (  # noqa: E402
    BudgetLine,
    Command,
    CommandResult,
    GateAnswer,
    GateAnswers,
    RoomAnalysis,
)
from contracts.placement_review import (  # noqa: E402
    AlternativeTested,
    PlacementReview,
    StyleAssessment,
)
from contracts.recovery_run import PendingInput, RecoveryRun  # noqa: E402
from contracts.room_snapshot import (  # noqa: E402
    MeasuredAnchor,
    MediaRef,
    ObservedObject,
    RoomSnapshot,
)
from contracts.room_spec import (  # noqa: E402
    ExistingItem,
    Floor,
    FreeZone,
    HingeSpec,
    Opening,
    RoomDimensions,
    RoomSpec,
    ScaleReference,
    Wall,
)
from contracts.scene_revision import (  # noqa: E402
    LightState,
    MaterialState,
    ObjectPlacement,
    SceneAction,
    ScenePatch,
    SceneRevision,
    VariantSelection,
)
from contracts.workflow_plan import (  # noqa: E402
    PlanConstraints,
    WorkflowPlan,
    WorkflowStep,
)

FIXTURES = REPO_ROOT / "fixtures"

T0 = datetime(2026, 11, 21, 12, 0, 0, tzinfo=timezone.utc)
SESSION = "sess_01HQ8ETHYNEON0001"
SHOPPER = "shopper_01HQ8ETHYNEON0001"
DEV_STORE = "ethyneon-dev.myshopify.com"

# The room: 3.2 m wide (confirmed by the user), 4.0 m deep (still estimated).
ROOM_W = 3.2
ROOM_D = 4.0
ROOM_H = 2.5

DEV = StoreRef(
    store_domain=DEV_STORE,
    store_name="Ethyneon Dev Store",
    store_id="gid://shopify/Shop/90000000001",
    api_surface="storefront_api",
)

SHOP_MCP = StoreRef(
    store_domain="mygarvee.myshopify.com",
    store_name="GARVEE",
    store_id="gid://shopify/Shop/72466989369",
    api_surface="shop_mcp",
)


def write(name: str, model) -> None:
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def write_list(name: str, models: list) -> None:
    path = FIXTURES / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [m.model_dump(mode="json") for m in models]
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)} ({len(models)} items)")


# --------------------------------------------------------------------------
# B's contracts
# --------------------------------------------------------------------------


def room_snapshot() -> RoomSnapshot:
    return RoomSnapshot(
        snapshot_id="snap_0001",
        session_id=SESSION,
        media=[
            MediaRef(
                media_id="media_room_good",
                kind="photo",
                uri="session://sess_01HQ8ETHYNEON0001/media/room_good.jpg",
                width_px=1536,
                height_px=2048,
                captured_at=T0,
                metadata_stripped=True,
                sha256="a" * 64,
            )
        ],
        source_device="iPhone 15, browser capture",
        captured_at=T0,
        received_at=T0 + timedelta(seconds=2),
        observed_objects=[
            ObservedObject(
                object_id="obj_existing_monitor",
                label="27 inch monitor",
                confidence=0.91,
                bbox=Rect2D(x_m=1.6, y_m=0.45, width_m=0.62, depth_m=0.22),
                keep=True,
            ),
            ObservedObject(
                object_id="obj_existing_chair",
                label="desk chair",
                confidence=0.84,
                keep=True,
            ),
        ],
        measured_anchors=[
            MeasuredAnchor(
                anchor_id="anchor_desk_wall",
                description="Width of the desk wall, confirmed by the shopper",
                length_m=ROOM_W,
                evidence_status="confirmed",
                confirmed_by_user=True,
            )
        ],
        evidence_status="sampled_from_photo",
        source="live",
        consent_given=True,
    )


def scene_revision() -> SceneRevision:
    return SceneRevision(
        scene_revision=7,
        parent_revision=6,
        session_id=SESSION,
        room_revision=3,
        placements=[
            ObjectPlacement(
                object_id="obj_existing_monitor",
                label="Shopper's own 27 inch monitor",
                pose=Pose(x_m=1.60, y_m=0.40, rotation_deg=0.0),
                footprint=Rect2D(x_m=1.60, y_m=0.40, width_m=0.62, depth_m=0.22),
                height_m=0.46,
                locked=True,
                variant=None,
                asset_id="asset_monitor_generic",
                fit_result="fits",
            ),
            ObjectPlacement(
                object_id="obj_walnut_desk",
                label="Walnut writing desk",
                pose=Pose(x_m=1.60, y_m=0.45, rotation_deg=0.0),
                footprint=Rect2D(x_m=1.60, y_m=0.45, width_m=1.20, depth_m=0.60),
                height_m=0.74,
                locked=False,
                variant=VariantSelection(
                    product_id="gid://shopify/Product/9000000000001",
                    variant_id="gid://shopify/ProductVariant/49000000000001",
                    store_domain=DEV_STORE,
                    offer_ref="offer_dev_desk_walnut",
                ),
                asset_id="asset_desk_rect",
                asset_is_representative=True,
                fit_result="fits",
                fit_report_ref="fit_0007",
            ),
        ],
        material_state=MaterialState(
            wall_colors={
                "wall_n": ColorSpec(
                    hex="#6b7a4f", evidence_status="confirmed", source="generated"
                )
            },
            floor_color=ColorSpec(
                hex="#9c7b53", evidence_status="sampled_from_photo", source="inferred"
            ),
        ),
        light_state=LightState(preset="evening", intensity=0.8, color_temp_k=3200),
        selected_variants=[
            VariantSelection(
                product_id="gid://shopify/Product/9000000000001",
                variant_id="gid://shopify/ProductVariant/49000000000001",
                store_domain=DEV_STORE,
                offer_ref="offer_dev_desk_walnut",
            )
        ],
        fit_status="fits",
        action_id="3f1b2c84-0d7e-4a19-9c31-7d2f5a6b8e40",
        source="generated",
        created_at=T0 + timedelta(minutes=6),
    )


def activity_event() -> ActivityEvent:
    return ActivityEvent(
        event_id="evt_0042",
        session_id=SESSION,
        ts=T0 + timedelta(minutes=6, seconds=12),
        source="commerce",
        type="variant_verified",
        step_id="shop",
        message="Walnut desk variant verified on the development store.",
        revision_refs=RevisionRefs(room_revision=3, scene_revision=7),
        payload={
            "variant_id": "gid://shopify/ProductVariant/49000000000001",
            "store_domain": DEV_STORE,
        },
        source_label="live",
        safe_to_display=True,
    )


# --------------------------------------------------------------------------
# A's contracts
# --------------------------------------------------------------------------


def room_spec() -> RoomSpec:
    return RoomSpec(
        room_revision=3,
        session_id=SESSION,
        dimensions_m=RoomDimensions(
            width_m=Measurement(
                value_m=ROOM_W, evidence_status="confirmed", source="live", confidence=1.0
            ),
            depth_m=Measurement(
                value_m=ROOM_D, evidence_status="estimated", source="inferred", confidence=0.62
            ),
            height_m=Measurement(
                value_m=ROOM_H, evidence_status="estimated", source="inferred", confidence=0.55
            ),
        ),
        scale_reference=ScaleReference(
            anchor_id="anchor_desk_wall",
            description="Desk wall width, confirmed by the shopper",
            length_m=ROOM_W,
            confirmed=True,
            evidence_status="confirmed",
            measures="width",
        ),
        walls=[
            Wall(
                wall_id="wall_n",
                length_m=ROOM_W,
                color=ColorSpec(
                    hex="#e8e4dc", evidence_status="sampled_from_photo", source="inferred"
                ),
                label="Desk wall",
            ),
            Wall(
                wall_id="wall_e",
                length_m=ROOM_D,
                color=ColorSpec(
                    hex="#e8e4dc", evidence_status="sampled_from_photo", source="inferred"
                ),
            ),
            Wall(
                wall_id="wall_s",
                length_m=ROOM_W,
                color=ColorSpec(
                    hex="#e8e4dc", evidence_status="sampled_from_photo", source="inferred"
                ),
                label="Door wall",
            ),
            Wall(
                wall_id="wall_w",
                length_m=ROOM_D,
                color=ColorSpec(
                    hex="#e8e4dc", evidence_status="sampled_from_photo", source="inferred"
                ),
            ),
        ],
        floor=Floor(
            floor_id="floor",
            color=ColorSpec(
                hex="#9c7b53", evidence_status="sampled_from_photo", source="inferred"
            ),
            material="oak laminate",
        ),
        openings=[
            Opening(
                opening_id="door_main",
                wall_id="wall_s",
                type="door",
                offset_m=0.30,
                width_m=0.82,
                height_m=2.03,
                hinge=HingeSpec(
                    hinge_side="left",
                    swing_direction="inward",
                    swing_angle_max_deg=90.0,
                    evidence_status="confirmed",
                    motion_evidence_ref="clip_door_open_001",
                ),
                evidence_status="confirmed",
            ),
            Opening(
                opening_id="window_north",
                wall_id="wall_n",
                type="window",
                offset_m=1.90,
                width_m=1.10,
                height_m=1.20,
                sill_m=0.90,
                evidence_status="estimated",
            ),
        ],
        existing_items=[
            ExistingItem(
                item_id="obj_existing_monitor",
                label="27 inch monitor",
                footprint=Rect2D(x_m=1.60, y_m=0.40, width_m=0.62, depth_m=0.22),
                height_m=0.46,
                locked=True,
                keep=True,
                evidence_status="confirmed",
            )
        ],
        free_zones=[
            FreeZone(
                zone_id="route_door_to_desk",
                label="Walking route from the door to the desk",
                area=Polygon2D(
                    points=[
                        Point2D(x_m=0.30, y_m=3.95),
                        Point2D(x_m=1.12, y_m=3.95),
                        Point2D(x_m=1.12, y_m=0.80),
                        Point2D(x_m=0.30, y_m=0.80),
                    ]
                ),
                purpose="walking_route",
            )
        ],
        evidence_status="estimated",
        source="generated",
        snapshot_ref="snap_0001",
        created_at=T0 + timedelta(minutes=2),
    )


def design_brief() -> DesignBrief:
    return DesignBrief(
        brief_id="brief_0001",
        session_id=SESSION,
        brief_revision=2,
        room_use="dual_monitor_gaming_corner",
        locked_object_ids=["obj_existing_monitor"],
        merchandise_budget=Money(amount_minor=120000, currency="USD"),
        required_items=[
            RequiredItem(
                item_id="req_desk", label="Desk", quantity=1, category="desks"
            ),
            RequiredItem(
                item_id="req_monitor",
                label="Second monitor",
                quantity=1,
                category="monitors",
                notes="Must match the 27 inch monitor the shopper already owns.",
            ),
        ],
        preferences=Preferences(
            finish=["walnut"],
            lighting=["warm"],
            style=["minimal"],
            colors=["olive"],
            free_text="Keep the room usable as a guest room.",
        ),
        hard_constraints=[
            HardConstraint(
                constraint_id="hc_door_clear",
                kind="keep_door_clear",
                description="The door must open fully without hitting furniture.",
                evidence_status="confirmed",
            ),
            HardConstraint(
                constraint_id="hc_walkway",
                kind="min_walkway_width",
                value=0.75,
                unit="m",
                description="A 0.75 m route from the door to the desk must stay clear.",
                evidence_status="confirmed",
            ),
        ],
        created_at=T0 + timedelta(minutes=3),
    )


def workflow_plan() -> WorkflowPlan:
    return WorkflowPlan(
        plan_id="plan_0001",
        session_id=SESSION,
        goal="dual_monitor_gaming_corner",
        constraints=PlanConstraints(
            merchandise_budget=Money(amount_minor=120000, currency="USD"),
            locked_objects=["obj_existing_monitor"],
            keep_door_clear=True,
            min_walkway_width_m=0.75,
        ),
        steps=[
            WorkflowStep(id="room", op="inspect_room_motion", after=[]),
            WorkflowStep(id="shop", op="discover_shopify_variants", after=[]),
            WorkflowStep(id="scale", op="confirm_measurement_if_needed", after=["room"]),
            WorkflowStep(id="plan", op="search_layouts", after=["shop", "scale"]),
            WorkflowStep(id="test", op="test_layout_motion", after=["plan"]),
            WorkflowStep(id="preview", op="prepare_scene_preview", after=["test"]),
        ],
        room_revision=3,
        scene_revision=7,
        state="validated",
        created_at=T0 + timedelta(minutes=4),
    )


def placement_review() -> PlacementReview:
    return PlacementReview(
        review_id="prev_0001",
        session_id=SESSION,
        base_scene_revision=7,
        selected_object_id="obj_walnut_desk",
        requested_pose=Pose(x_m=0.80, y_m=3.30, rotation_deg=0.0),
        style_assessment=StyleAssessment(
            summary=(
                "Under the window suits the minimal, warm look the shopper asked for, "
                "but it sits in the door's path."
            ),
            matches_preferences=True,
            confidence=0.78,
            notes=["Walnut against the olive wall reads warm in evening light."],
        ),
        affected_paths=["route_door_to_desk"],
        affected_openings=["door_main"],
        fit_evidence=fit_report_does_not_fit(),
        alternatives_tested=[
            AlternativeTested(
                pose=Pose(x_m=1.60, y_m=0.45, rotation_deg=0.0),
                fit_report_ref="fit_0007",
                fit_result="fits",
                rationale="Against the desk wall, clear of the door swing and the route.",
            )
        ],
        accepted_pose=Pose(x_m=1.60, y_m=0.45, rotation_deg=0.0),
        created_at=T0 + timedelta(minutes=5),
    )


def recovery_run() -> RecoveryRun:
    return RecoveryRun(
        run_id="rec_0001",
        session_id=SESSION,
        failed_step_id="scale",
        error_class="invalid_room_geometry",
        triggering_revision=3,
        interaction_id="interaction_argon_0001",
        allowed_actions=["request_measurement", "request_rescan", "pause"],
        chosen_action="request_measurement",
        attempt_count=1,
        max_attempts=3,
        last_verified_state={"room_revision": 2, "scene_revision": 6},
        outcome="awaiting_input",
        pending_input=PendingInput(
            interaction_id="interaction_argon_0001",
            prompt="How wide is the wall behind the desk, in metres?",
            expects="measurement",
            frame_ref="media_room_good",
        ),
        agent_available=True,
        created_at=T0 + timedelta(minutes=4, seconds=30),
    )


# --------------------------------------------------------------------------
# C's contracts
# --------------------------------------------------------------------------


def catalog_offer() -> CatalogOffer:
    return CatalogOffer(
        offer_id="offer_dev_desk_walnut",
        store=DEV,
        product_id="gid://shopify/Product/9000000000001",
        variant_id="gid://shopify/ProductVariant/49000000000001",
        title="Ethyneon Demo Walnut Writing Desk, 120 cm",
        selected_options=[
            SelectedOption(name="Finish", value="Walnut"),
            SelectedOption(name="Width", value="120 cm"),
        ],
        price=Money(amount_minor=42900, currency="USD"),
        compare_at_price=Money(amount_minor=49900, currency="USD"),
        availability=Availability(available=True, quantity=12, checked_at=T0),
        listing_url=f"https://{DEV_STORE}/products/demo-walnut-writing-desk",
        image_url=f"https://{DEV_STORE}/cdn/shop/files/demo-walnut-desk.jpg",
        dimensions=ProductDimensions(
            width_m=1.20, depth_m=0.60, height_m=0.74, evidence_status="confirmed"
        ),
        dimension_source="metafield",
        freshness=Freshness(fetched_at=T0, ttl_ms=300000, source_label="live"),
        shoppable=True,
    )


def catalog_offers() -> list[CatalogOffer]:
    """18 offers: the dev store's shoppable demo products plus Shop MCP
    discovery results, including the missing-field cases the tests need."""
    offers: list[CatalogOffer] = [catalog_offer()]

    dev_products = [
        ("desk_oak", "Ethyneon Demo Oak Desk, 140 cm", 38900, 1.40, 0.70, 0.74, True, 8),
        ("desk_compact", "Ethyneon Demo Compact Desk, 100 cm", 24900, 1.00, 0.50, 0.73, True, 20),
        ("monitor_27", "Ethyneon Demo 27 inch Monitor", 31900, 0.62, 0.22, 0.46, True, 5),
        ("monitor_arm", "Ethyneon Demo Dual Monitor Arm", 11900, 0.30, 0.25, 0.52, True, 31),
        ("chair_mesh", "Ethyneon Demo Mesh Task Chair", 27900, 0.66, 0.66, 1.18, True, 9),
        ("lamp_desk", "Ethyneon Demo Warm Desk Lamp", 6900, 0.18, 0.18, 0.44, True, 44),
        ("rug_olive", "Ethyneon Demo Olive Rug, 160 x 230", 15900, 2.30, 1.60, 0.01, True, 6),
        ("shelf_wall", "Ethyneon Demo Wall Shelf, 80 cm", 8900, 0.80, 0.24, 0.20, True, 17),
    ]
    for idx, (slug, title, price, w, d, h, available, qty) in enumerate(dev_products, start=2):
        offers.append(
            CatalogOffer(
                offer_id=f"offer_dev_{slug}",
                store=DEV,
                product_id=f"gid://shopify/Product/900000000000{idx}",
                variant_id=f"gid://shopify/ProductVariant/4900000000000{idx}",
                title=title,
                selected_options=[SelectedOption(name="Finish", value="Default")],
                price=Money(amount_minor=price, currency="USD"),
                availability=Availability(available=available, quantity=qty, checked_at=T0),
                listing_url=f"https://{DEV_STORE}/products/{slug.replace('_', '-')}",
                image_url=f"https://{DEV_STORE}/cdn/shop/files/{slug}.jpg",
                dimensions=ProductDimensions(
                    width_m=w, depth_m=d, height_m=h, evidence_status="confirmed"
                ),
                dimension_source="metafield",
                freshness=Freshness(fetched_at=T0, ttl_ms=300000, source_label="live"),
                shoppable=True,
            )
        )

    # Missing-field case: a dev store variant with no documented dimensions.
    # It is shoppable but any fit check against it must return unknown.
    offers.append(
        CatalogOffer(
            offer_id="offer_dev_sidetable_nodims",
            store=DEV,
            product_id="gid://shopify/Product/9000000000010",
            variant_id="gid://shopify/ProductVariant/49000000000010",
            title="Ethyneon Demo Side Table, dimensions not documented",
            price=Money(amount_minor=9900, currency="USD"),
            availability=Availability(available=True, quantity=3, checked_at=T0),
            listing_url=f"https://{DEV_STORE}/products/demo-side-table",
            dimensions=None,
            dimension_source="unknown",
            freshness=Freshness(fetched_at=T0, ttl_ms=300000, source_label="live"),
            shoppable=True,
        )
    )

    # Missing-field case: out of stock. Must never reach a cart.
    offers.append(
        CatalogOffer(
            offer_id="offer_dev_desk_walnut_oos",
            store=DEV,
            product_id="gid://shopify/Product/9000000000011",
            variant_id="gid://shopify/ProductVariant/49000000000011",
            title="Ethyneon Demo Walnut Desk, 160 cm, out of stock",
            price=Money(amount_minor=52900, currency="USD"),
            availability=Availability(available=False, quantity=0, checked_at=T0),
            listing_url=f"https://{DEV_STORE}/products/demo-walnut-desk-160",
            dimensions=ProductDimensions(
                width_m=1.60, depth_m=0.70, height_m=0.74, evidence_status="confirmed"
            ),
            dimension_source="metafield",
            freshness=Freshness(fetched_at=T0, ttl_ms=300000, source_label="live"),
            shoppable=True,
        )
    )

    # Shop MCP discovery results. Never shoppable. Dimensions, where present,
    # are parsed from the option label or the listing text by plain code.
    mcp_products = [
        (
            "garvee_folding_desk",
            "gid://shopify/p/6GpwO63YlebZieMeKo6Yik",
            "gid://shopify/Product/10300986753337",
            "gid://shopify/ProductVariant/54307265806649",
            "Garvee Folding Computer Desk for Small Spaces, Natural Wood",
            7299,
            (0.80, 0.40, 0.72),
            'Size: 31.5" x 15.7" x 28.3" (80 x 40 x 72 cm)',
            True,
        ),
        (
            "mcp_desk_corner",
            "gid://shopify/p/7AbcD00000000000000001",
            "gid://shopify/Product/10300986753338",
            "gid://shopify/ProductVariant/54307265806650",
            "Corner Computer Desk with Shelf",
            9999,
            (1.20, 0.75, 0.75),
            "Overall: 120 x 75 x 75 cm",
            True,
        ),
        (
            "mcp_desk_nodims",
            "gid://shopify/p/7AbcD00000000000000002",
            "gid://shopify/Product/10300986753339",
            "gid://shopify/ProductVariant/54307265806651",
            "Minimalist Writing Table, no size given",
            12500,
            None,
            None,
            True,
        ),
        (
            "mcp_lamp_partial",
            "gid://shopify/p/7AbcD00000000000000003",
            "gid://shopify/Product/10300986753340",
            "gid://shopify/ProductVariant/54307265806652",
            "Brass Desk Lamp, height only",
            4500,
            (None, None, 0.42),
            "Height: 42 cm",
            True,
        ),
        (
            "mcp_chair_oos",
            "gid://shopify/p/7AbcD00000000000000004",
            "gid://shopify/Product/10300986753341",
            "gid://shopify/ProductVariant/54307265806653",
            "Ergonomic Mesh Chair, sold out",
            18900,
            (0.64, 0.64, 1.15),
            "64 x 64 x 115 cm",
            False,
        ),
        (
            "mcp_shelf",
            "gid://shopify/p/7AbcD00000000000000005",
            "gid://shopify/Product/10300986753342",
            "gid://shopify/ProductVariant/54307265806654",
            "Floating Wall Shelf, walnut",
            3200,
            (0.90, 0.20, 0.04),
            "90 x 20 x 4 cm",
            True,
        ),
    ]
    for slug, _catalog_id, product_id, variant_id, title, price, dims, raw, available in mcp_products:
        dimensions = None
        dim_source = "unknown"
        if dims is not None:
            w, d, h = dims
            dimensions = ProductDimensions(
                width_m=w,
                depth_m=d,
                height_m=h,
                evidence_status="estimated",
                raw_text=raw,
            )
            dim_source = "listing_text_inferred"
        offers.append(
            CatalogOffer(
                offer_id=f"offer_mcp_{slug}",
                store=SHOP_MCP,
                product_id=product_id,
                variant_id=variant_id,
                title=title,
                price=Money(amount_minor=price, currency="USD"),
                availability=Availability(available=available, checked_at=T0),
                listing_url=f"https://www.garvee.com/products/{slug.replace('_', '-')}",
                dimensions=dimensions,
                dimension_source=dim_source,
                # Shop MCP advertises ttlMs 3600000 with cacheScope public.
                freshness=Freshness(fetched_at=T0, ttl_ms=3600000, source_label="live"),
                shoppable=False,
            )
        )

    return offers


def fit_report_fits() -> FitReport:
    return FitReport(
        report_id="fit_0007",
        room_revision=3,
        scene_revision=7,
        solver_version="fit/0.1.0",
        assumptions=[
            "clearance margin 0.05 m",
            "door swing tested at 15 degree steps from 0 to 90",
            "room depth is estimated, so this result is tentative",
        ],
        checks_run=[
            FitCheck(
                name="room_bounds",
                result="pass",
                detail="Footprint lies inside the room.",
                measured_clearance_m=0.40,
            ),
            FitCheck(
                name="object_overlap",
                result="pass",
                detail="No overlap with the locked monitor.",
                measured_clearance_m=0.18,
            ),
            FitCheck(
                name="door_swing",
                result="pass",
                detail="Clear of the swept area of door_main.",
                measured_clearance_m=1.42,
            ),
            FitCheck(
                name="walking_route",
                result="pass",
                detail="The 0.75 m route from the door to the desk stays clear.",
                measured_clearance_m=0.82,
            ),
        ],
        minimum_clearance_m=0.18,
        rejected_placement=None,
        evidence=["clip_door_open_001", "offer_dev_desk_walnut"],
        result="fits",
        tentative=True,
        created_at=T0 + timedelta(minutes=5, seconds=30),
    )


def fit_report_does_not_fit() -> FitReport:
    return FitReport(
        report_id="fit_0008",
        room_revision=3,
        scene_revision=7,
        solver_version="fit/0.1.0",
        assumptions=[
            "clearance margin 0.05 m",
            "door swing tested at 15 degree steps from 0 to 90",
        ],
        checks_run=[
            FitCheck(name="room_bounds", result="pass", detail="Inside the room."),
            FitCheck(
                name="door_swing",
                result="fail",
                detail="The desk overlaps the swept area of door_main at 55 degrees.",
                measured_clearance_m=0.0,
                blocking_ids=["door_main"],
            ),
            FitCheck(
                name="walking_route",
                result="tight",
                detail="The route narrows to 0.41 m, below the 0.75 m requirement.",
                measured_clearance_m=0.41,
                blocking_ids=["route_door_to_desk"],
            ),
        ],
        minimum_clearance_m=0.0,
        rejected_placement=RejectedPlacement(
            object_id="obj_walnut_desk",
            pose=Pose(x_m=0.80, y_m=3.30, rotation_deg=0.0),
            footprint=Rect2D(x_m=0.80, y_m=3.30, width_m=1.20, depth_m=0.60),
            cause="Overlaps the door swing of door_main and narrows the walking route.",
            failing_check="door_swing",
        ),
        evidence=["clip_door_open_001"],
        result="does_not_fit",
        tentative=True,
        created_at=T0 + timedelta(minutes=5, seconds=10),
    )


def cart_review() -> CartReview:
    lines = [
        CartLine(
            line_id="line_desk",
            product_id="gid://shopify/Product/9000000000001",
            variant_id="gid://shopify/ProductVariant/49000000000001",
            title="Ethyneon Demo Walnut Writing Desk, 120 cm",
            quantity=1,
            unit_price=Money(amount_minor=42900, currency="USD"),
            line_subtotal=Money(amount_minor=42900, currency="USD"),
            offer_ref="offer_dev_desk_walnut",
            offer_fetched_at=T0,
        ),
        CartLine(
            line_id="line_monitor",
            product_id="gid://shopify/Product/9000000000004",
            variant_id="gid://shopify/ProductVariant/49000000000004",
            title="Ethyneon Demo 27 inch Monitor",
            quantity=1,
            unit_price=Money(amount_minor=31900, currency="USD"),
            line_subtotal=Money(amount_minor=31900, currency="USD"),
            offer_ref="offer_dev_monitor_27",
            offer_fetched_at=T0,
        ),
    ]
    return CartReview(
        review_id="cart_0001",
        session_id=SESSION,
        shopper_id=SHOPPER,
        store=DEV,
        lines=lines,
        merchandise_subtotal=Money(amount_minor=74800, currency="USD"),
        known_charges=[],
        budget=Money(amount_minor=120000, currency="USD"),
        within_budget=True,
        brief_revision=2,
        scene_revision=7,
        approval=ApprovalRecord(
            approval_id="appr_0001",
            shopper_id=SHOPPER,
            approved_at=T0 + timedelta(minutes=7),
            expires_at=T0 + timedelta(minutes=22),
            fingerprint="sha256:9f2c1ab8d4e35760c1f0a2b3d4e5f60718293a4b5c6d7e8f9012a3b4c5d6e7f8",
            base_scene_revision=7,
            base_brief_revision=2,
            action_id="8c7d6e5f-4a3b-2c1d-0e9f-8a7b6c5d4e3f",
        ),
        cart_reference=CartRef(
            cart_id="gid://shopify/Cart/c1-abc123def456",
            store_domain=DEV_STORE,
            checkout_url=None,
            fingerprint="sha256:1122334455667788990011223344556677889900112233445566778899001122",
            written_at=T0 + timedelta(minutes=7, seconds=5),
            write_outcome="confirmed",
        ),
        status="approved",
        payment_state="cart_ready",
        source="live",
        created_at=T0 + timedelta(minutes=6, seconds=50),
    )


def checkout_audit() -> CheckoutAudit:
    return CheckoutAudit(
        audit_id="audit_0001",
        session_id=SESSION,
        workflow_run_id="plan_0001",
        scene_revision=7,
        brief_revision=2,
        approval_fingerprint=(
            "sha256:9f2c1ab8d4e35760c1f0a2b3d4e5f60718293a4b5c6d7e8f9012a3b4c5d6e7f8"
        ),
        cart_fingerprint=(
            "sha256:1122334455667788990011223344556677889900112233445566778899001122"
        ),
        deterministic_results=[
            DeterministicCheck(
                name="store_matches", passed=True, detail="Cart and approval are on the dev store."
            ),
            DeterministicCheck(
                name="variant_ids_match", passed=True, detail="Both lines match exactly."
            ),
            DeterministicCheck(name="quantities_match", passed=True, detail="1 and 1."),
            DeterministicCheck(name="currency_matches", passed=True, detail="USD throughout."),
            DeterministicCheck(
                name="stock_available", passed=True, detail="Both variants in stock on re-read."
            ),
            DeterministicCheck(
                name="amounts_match",
                passed=True,
                detail="Subtotal 74800 USD matches the approved amount.",
                expected="74800",
                observed="74800",
            ),
            DeterministicCheck(
                name="within_budget", passed=True, detail="74800 of 120000 USD."
            ),
            DeterministicCheck(
                name="fit_status_acceptable", passed=True, detail="Scene fit status is fits."
            ),
            DeterministicCheck(
                name="approval_valid", passed=True, detail="Approval has not expired."
            ),
            DeterministicCheck(
                name="revisions_current", passed=True, detail="Scene revision 7 is still current."
            ),
        ],
        decision_answers=[
            DecisionAnswer(
                question="purchase_scope_matches",
                answer_type="noul",
                answer="yes",
                confidence=0.94,
                threshold=0.80,
                passed_threshold=True,
                source="mock",
            ),
            DecisionAnswer(
                question="required_constraints_resolved",
                answer_type="noul",
                answer="yes",
                confidence=0.91,
                threshold=0.80,
                passed_threshold=True,
                source="mock",
            ),
            DecisionAnswer(
                question="next_action",
                answer_type="choice",
                answer="ready",
                confidence=0.92,
                threshold=0.80,
                passed_threshold=True,
                source="mock",
            ),
        ],
        next_action="ready",
        outcome="ready",
        expires_at=T0 + timedelta(minutes=17),
        evidence_status="confirmed",
        created_at=T0 + timedelta(minutes=7, seconds=20),
    )


# --------------------------------------------------------------------------
# Lane A fixtures that are not contracts, served when MOCK_AI is on
# --------------------------------------------------------------------------


def room_analysis() -> RoomAnalysis:
    return RoomAnalysis(
        analysis_id="analysis_0001",
        session_id=SESSION,
        gates=GateAnswers(
            gate_set="room_photo",
            answers=[
                GateAnswer(
                    gate="room_photo",
                    question="is_room_photo",
                    answer="yes",
                    confidence=0.97,
                    threshold=0.80,
                    passed=True,
                    source="mock",
                ),
                GateAnswer(
                    gate="room_photo",
                    question="person_visible",
                    answer="no",
                    confidence=0.95,
                    threshold=0.80,
                    passed=True,
                    source="mock",
                ),
                GateAnswer(
                    gate="room_photo",
                    question="usable_for_layout",
                    answer="yes",
                    confidence=0.88,
                    threshold=0.80,
                    passed=True,
                    source="mock",
                ),
            ],
            all_passed=True,
            created_at=T0 + timedelta(minutes=1),
        ),
        room_spec_draft=room_spec(),
        confidence=0.88,
        evidence_refs=["media_room_good"],
        source="mock",
    )


def command_result() -> CommandResult:
    return CommandResult(
        status="accepted",
        patch=ScenePatch(
            actions=[
                SceneAction(
                    type="set_wall_color",
                    object_id=None,
                    payload={"wall_id": "wall_n", "hex": "#6b7a4f"},
                )
            ],
            base_revision=7,
            action_id="5e4d3c2b-1a09-4f8e-7d6c-5b4a39281706",
            origin="user",
        ),
        plan_id=None,
        input_request=None,
        message="Painting the desk wall olive.",
    )


def gate_answers() -> GateAnswers:
    return GateAnswers(
        gate_set="checkout_audit",
        answers=[
            GateAnswer(
                gate="checkout_audit",
                question="purchase_scope_matches",
                answer="yes",
                confidence=0.94,
                threshold=0.80,
                passed=True,
                source="mock",
            ),
            GateAnswer(
                gate="checkout_audit",
                question="required_constraints_resolved",
                answer="yes",
                confidence=0.91,
                threshold=0.80,
                passed=True,
                source="mock",
            ),
            GateAnswer(
                gate="checkout_audit",
                question="next_action",
                answer="ready",
                confidence=0.92,
                threshold=0.80,
                passed=True,
                source="mock",
            ),
        ],
        all_passed=True,
        created_at=T0 + timedelta(minutes=7, seconds=15),
    )


def command() -> Command:
    return Command(
        command_id="cmd_0001",
        session_id=SESSION,
        text="Make that wall olive, keep my monitor, and show me the walnut desk.",
        transcript_source="voice",
        selected_object_id="obj_existing_monitor",
        frame_refs=["media_room_good"],
        room_revision=3,
        scene_revision=7,
    )


def budget_lines() -> list[BudgetLine]:
    return [
        BudgetLine(
            variant_id="gid://shopify/ProductVariant/49000000000001",
            quantity=1,
            unit_price=Money(amount_minor=42900, currency="USD"),
        ),
        BudgetLine(
            variant_id="gid://shopify/ProductVariant/49000000000004",
            quantity=1,
            unit_price=Money(amount_minor=31900, currency="USD"),
        ),
    ]


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    # One file per contract.
    write("room_snapshot.sample.json", room_snapshot())
    write("room_spec.sample.json", room_spec())
    write("design_brief.sample.json", design_brief())
    write("workflow_plan.sample.json", workflow_plan())
    write("placement_review.sample.json", placement_review())
    write("scene_revision.sample.json", scene_revision())
    write("catalog_offer.sample.json", catalog_offer())
    write("fit_report.sample.json", fit_report_fits())
    write("cart_review.sample.json", cart_review())
    write("checkout_audit.sample.json", checkout_audit())
    write("recovery_run.sample.json", recovery_run())
    write("activity_event.sample.json", activity_event())

    # Mock-mode fixtures named in the integration doc.
    write_list("catalog_offers.sample.json", catalog_offers())
    write("room_analysis.sample.json", room_analysis())
    write("command_result.sample.json", command_result())
    write("gate_answers.sample.json", gate_answers())
    write("command.sample.json", command())
    write("fit_report_does_not_fit.sample.json", fit_report_does_not_fit())
    write_list("budget_lines.sample.json", budget_lines())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
