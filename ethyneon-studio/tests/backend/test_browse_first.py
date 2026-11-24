"""Browse first: preview placement, the fit bridge, the drag, and Your list.

The product is now "look across every merchant, design a room, buy on the
merchant's own page". Three things have to stay true for that to be honest,
and each gets its own section here.

**A preview item can be looked at and never bought.** It places with no
Storefront credentials at all, and checkout refuses it before a line exists.
The refusal is server side, because the only reason there is no buy button
on a preview card is that we chose not to draw one.

**The verdict comes from the deterministic engine, with its evidence
intact.** The Studio and `fit/geometry.py` walk two of the four walls in
opposite directions, so a door offset means two different places unless
something converts it. That silent disagreement is what put the vendored
lane A solver at 7 of 15 on the golden cases, so it is tested here against
the Studio's own rule rather than against a number somebody typed.

**Nothing here needs a model.** The Gemini key has been rejecting every call
since before this work started, so a flow that quietly depends on it is a
flow that does not exist. Every test in this file runs on fake transports
with no key.
"""

import asyncio
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

import httpx

from backend import fit_bridge
from backend.checkout import CheckoutRequest, CheckoutStudio
from backend.connections import (
    NOT_ON_SHOPIFY_REASON,
    PREVIEW_NOT_PURCHASABLE_REASON,
    ShopifyConnection,
)
from backend.errors import ConnectionError
from backend.scene import PresetRoom, SceneStudio, scene_room
from backend.scene_edit import SceneEdit, opening_zone, overlaps
from backend.storefront import CatalogConnection, StorefrontConnection
from backend.studio import ProductStudio, StudioCommand, bare_search

STUDIO_ROOT = Path(__file__).resolve().parents[2]
STATIC = STUDIO_ROOT / "backend" / "static"

OTHER = "gtracingchair.myshopify.com"
THEIRS = "gid://shopify/ProductVariant/77000000000002"
THEIR_PRODUCT = "gid://shopify/Product/9000000000002"


def run(coroutine):
    return asyncio.run(coroutine)


# ==========================================================================
# Fakes. No credentials, no network.
# ==========================================================================


class FakeShop:
    """Shop MCP with one gaming chair, whose size is only in its prose."""

    DESCRIPTION = 'Dimensions: 26.5" W x 27.5" D x 51" H. Fabric gaming chair.'

    def __init__(self, description=None, title="GT905 Fabric Gaming Chair"):
        self.description = self.DESCRIPTION if description is None else description
        self.title = title
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body["params"]["name"])
        product = {
            "id": THEIR_PRODUCT,
            "title": self.title,
            "description": self.description,
            "variants": [{
                "id": THEIRS,
                "seller": {"domain": OTHER},
                "price": {"amount": 42900, "currency": "USD"},
                "availability": {"available": True},
                "url": f"https://{OTHER}/products/gt905?variant=2",
                "options": [{"name": "Finish", "label": "Black"}],
                "media": [{"type": "image", "url": "https://cdn.shopify.com/gt905.jpg"}],
            }],
        }
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"structuredContent": {"products": [product]}},
        })

    def connection(self, shops=()) -> ShopifyConnection:
        return ShopifyConnection(
            httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), tuple(shops))


def dead_storefront() -> StorefrontConnection:
    """No Storefront store is configured. This is the demo machine."""
    def refuse(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the Storefront API must not be called when unconfigured")
    return StorefrontConnection(
        httpx.AsyncClient(transport=httpx.MockTransport(refuse)), "", "")


def router(shop: FakeShop | None = None, shops=()) -> CatalogConnection:
    return CatalogConnection(dead_storefront(), (shop or FakeShop()).connection(shops))


class FakeGemini:
    """Refuses every call, exactly as the real key does today."""

    def __init__(self):
        self.calls = 0

    async def interact(self, payload, **kwargs):
        self.calls += 1
        raise ConnectionError(502, "provider_authentication_failed", "Gemini rejected the credentials.")

    async def decide(self, payload):
        self.calls += 1
        raise ConnectionError(502, "provider_authentication_failed", "Gemini rejected the credentials.")


def temp_studio(test, shop=None) -> SceneStudio:
    folder = Path(test.enterContext(tempfile.TemporaryDirectory()))
    return SceneStudio(router(shop), None, path=folder / "geometricconfig.json",
                       cache_dir=folder / "cache")


# ==========================================================================
# A room you can type in
# ==========================================================================


class PresetRoomTests(unittest.TestCase):
    def test_a_room_is_written_with_no_model_and_no_network(self):
        scenes = temp_studio(self)
        body = scenes.write_preset_room(PresetRoom(width_m=3.2, depth_m=3.0, category="gaming_room"))
        dimensions = body["scene"]["dimensions"]
        self.assertEqual(dimensions["width"]["value_m"], 3.2)
        self.assertEqual(dimensions["depth"]["value_m"], 3.0)
        self.assertEqual(body["scene"]["category"], "gaming_room")
        self.assertEqual(len(body["scene"]["openings"]), 1)

    def test_typed_measurements_are_recorded_as_confirmed(self):
        """Somebody held a tape. That is better evidence than a model guess,
        and the fit engine is allowed to know it."""
        scenes = temp_studio(self)
        body = scenes.write_preset_room(PresetRoom(width_m=3.2, depth_m=3.0))
        self.assertEqual(body["scene"]["dimensions"]["width"]["status"], "confirmed")

    def test_a_door_that_does_not_fit_its_wall_is_refused(self):
        scenes = temp_studio(self)
        with self.assertRaises(ConnectionError) as caught:
            scenes.write_preset_room(PresetRoom(width_m=1.2, depth_m=3.0, door_offset_m=0.8))
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["code"], "door_off_the_wall")

    def test_an_unrecorded_hinge_is_kept_unrecorded(self):
        scenes = temp_studio(self)
        body = scenes.write_preset_room(
            PresetRoom(width_m=3.2, depth_m=3.0, door_hinge="none", door_swing="none"))
        self.assertEqual(body["scene"]["openings"][0]["hinge"], "none")

    def test_replacing_a_room_that_holds_products_needs_saying_so(self):
        scenes = temp_studio(self)
        scenes.write_preset_room(PresetRoom(width_m=3.2, depth_m=3.0))
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        config["placed_products"] = [{"id": "p1", "label": "A chair"}]
        scenes.path.write_text(json.dumps(config), encoding="utf-8")
        scenes._last_good = None

        with self.assertRaises(ConnectionError) as caught:
            scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=4.0))
        self.assertEqual(caught.exception.status, 409)

        replaced = scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=4.0, replace=True))
        self.assertEqual(replaced["scene"]["placed_products"], [])


# ==========================================================================
# Browse, and place as a preview, with nothing configured
# ==========================================================================


class PreviewPlacementTests(unittest.TestCase):
    def scenes(self, shop=None) -> SceneStudio:
        scenes = temp_studio(self, shop)
        scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=3.0))
        return scenes

    def test_browse_returns_results_with_no_storefront_and_no_gemini(self):
        scenes = self.scenes()
        body = run(scenes.browse("gaming chair"))
        self.assertFalse(body["shoppable"])
        self.assertEqual(len(body["results"]), 1)
        offer = body["results"][0]
        self.assertEqual(offer["merchant"], OTHER)
        self.assertFalse(offer["shoppable"])
        self.assertTrue(offer["listing_url"].startswith(f"https://{OTHER}/"))

    def test_browse_reads_the_size_out_of_the_listing_and_says_where_from(self):
        offer = run(self.scenes().browse("gaming chair"))["results"][0]
        self.assertEqual(offer["dimension_source"], "shopify_listing_description")
        self.assertAlmostEqual(offer["size_m"]["width"], 0.6731, places=3)

    def test_a_listing_with_no_size_says_unknown_rather_than_guessing(self):
        offer = run(self.scenes(FakeShop(description="A very comfy chair."))
                    .browse("gaming chair"))["results"][0]
        self.assertEqual(offer["dimension_source"], "unknown")
        self.assertIsNone(offer["size_m"])

    def add(self, scenes, **overrides):
        body = {
            "base_etag": scenes.read()[0],
            "action_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "op": "add", "product_id": "new",
            "new_product": {
                "variant_id": THEIRS, "title": "GT905 Fabric Gaming Chair",
                "price": {"amount": 42900, "currency": "USD"},
                "listing_url": f"https://{OTHER}/products/gt905?variant=2",
                "preview": True, "merchant": OTHER,
                "dimension_source": "shopify_listing_description",
                "size": {"width": 0.67, "depth": 0.7, "height": 1.3},
                **overrides,
            },
        }
        return run(scenes.edit(SceneEdit(**body)))

    def test_a_preview_item_places_with_no_storefront_configured(self):
        """The whole point. The demo machine has no Storefront credentials."""
        scenes = self.scenes()
        body = self.add(scenes)
        placed = body["scene"]["placed_products"]
        self.assertEqual(len(placed), 1)
        self.assertTrue(placed[0]["preview"])
        self.assertEqual(placed[0]["merchant"], OTHER)
        self.assertEqual(placed[0]["dimension_source"], "shopify_listing_description")

    def test_the_scene_file_records_the_merchant_and_the_listing(self):
        scenes = self.scenes()
        self.add(scenes)
        saved = json.loads(scenes.path.read_text(encoding="utf-8"))["placed_products"][0]
        self.assertEqual(saved["merchant"], OTHER)
        self.assertIn("/products/gt905", saved["listing_url"])
        self.assertIs(saved["preview"], True)

    def test_a_buyable_placement_still_needs_the_store(self):
        """Preview is a different question, not a way round the old one."""
        scenes = self.scenes()
        with self.assertRaises(ConnectionError) as caught:
            self.add(scenes, preview=False)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.payload["code"], "no_shoppable_store")

    def test_the_variant_is_still_checked_against_shopify(self):
        """Preview is not a free pass. A variant nobody sells is refused."""
        class Empty(FakeShop):
            def handler(self, request):
                body = json.loads(request.content)
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": body["id"],
                    "result": {"structuredContent": {"products": []}},
                })

        scenes = self.scenes(Empty())
        with self.assertRaises(ConnectionError) as caught:
            self.add(scenes)
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["message"], NOT_ON_SHOPIFY_REASON)

    def test_a_refused_placement_writes_nothing(self):
        scenes = self.scenes()
        before = scenes.path.read_bytes()
        with self.assertRaises(ConnectionError):
            self.add(scenes, preview=False)
        self.assertEqual(scenes.path.read_bytes(), before)


# ==========================================================================
# A preview item cannot be bought, and the refusal is in the server
# ==========================================================================


class PreviewNeverReachesMoneyTests(unittest.TestCase):
    def checkout(self):
        scenes = temp_studio(self)
        scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=3.0))
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        config["placed_products"] = [{
            "id": "product_1", "label": "GT905 Fabric Gaming Chair", "variant_id": THEIRS,
            "preview": True, "merchant": OTHER,
            "dimension_source": "shopify_listing_description",
            "price": {"amount": 42900, "currency": "USD"},
            "size": {"width": 0.67, "depth": 0.7, "height": 1.3},
            "pose": {"position": {"x": 2.0, "y": 0, "z": 1.5}},
        }]
        scenes.path.write_text(json.dumps(config), encoding="utf-8")
        scenes._last_good = None

        self.asked = []
        gemini = FakeGemini()
        studio = CheckoutStudio(scenes.shopify, gemini, scenes, ethyneon=None)

        async def never(*args, **kwargs):
            self.asked.append(args)
            raise AssertionError("the purchase server must never be asked about a preview item")

        studio._purchase = never
        self.gemini = gemini
        return studio, scenes

    def test_a_cart_holding_a_preview_item_is_refused_outright(self):
        studio, scenes = self.checkout()
        with self.assertRaises(ConnectionError) as caught:
            run(studio.review(CheckoutRequest(base_etag=scenes.read()[0], confirm=True)))
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["code"], "preview_not_purchasable")

    def test_the_refusal_names_the_items_to_remove(self):
        studio, scenes = self.checkout()
        with self.assertRaises(ConnectionError) as caught:
            run(studio.review(CheckoutRequest(base_etag=scenes.read()[0])))
        message = caught.exception.payload["message"]
        self.assertIn(PREVIEW_NOT_PURCHASABLE_REASON, message)
        self.assertIn("GT905 Fabric Gaming Chair", message)
        self.assertEqual(caught.exception.payload["details"]["preview_items"],
                         ["GT905 Fabric Gaming Chair"])

    def test_nothing_is_priced_audited_or_sent_anywhere(self):
        """Refused before a line exists, so no Shopify read, no Decision 1
        call, and no request to the purchase server."""
        studio, scenes = self.checkout()
        with self.assertRaises(ConnectionError):
            run(studio.review(CheckoutRequest(base_etag=scenes.read()[0], confirm=True)))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.gemini.calls, 0)


# ==========================================================================
# The fit bridge: the frames agree, and the evidence survives
# ==========================================================================


class WallFrameTests(unittest.TestCase):
    """The Studio and fit/geometry.py disagree on two of the four walls.

    Rather than assert a formula, each wall is checked against the Studio's
    own `opening_zone`: put the product exactly in the swept area the Studio
    draws, and the engine must agree it does not fit. Put it on the opposite
    side of the room and the engine must be happy. If the conversion is
    dropped, wall_s and wall_w fail in both directions.
    """

    WIDTH, DEPTH = 4.0, 3.0

    def scene_with_door(self, wall, products):
        return scene_room({
            "revision": 1,
            "dimensions": {"width": {"value_m": self.WIDTH, "status": "confirmed"},
                           "depth": {"value_m": self.DEPTH, "status": "confirmed"},
                           "height": {"value_m": 2.5, "status": "confirmed"}},
            "openings": [{"id": "door_main", "type": "door", "wall": wall,
                          "offset_m": 0.1, "width_m": 0.9, "height_m": 2.03,
                          "hinge": "left", "swing": "inward"}],
            "existing_items": [],
            "placed_products": products,
        })

    def product_at(self, x, z):
        return {
            "id": "p1", "label": "Chair", "variant_id": THEIRS, "preview": True,
            "merchant": OTHER, "dimension_source": "shopify_listing_description",
            "price": {"amount": 42900, "currency": "USD"},
            "size": {"width": 0.5, "depth": 0.5, "height": 1.0},
            "pose": {"position": {"x": x, "y": 0, "z": z}, "rotation": {"yaw": 0, "pitch": 0, "roll": 0}},
        }

    def zone_centre(self, wall):
        empty = self.scene_with_door(wall, [])
        zone = opening_zone(empty["openings"][0], self.WIDTH, self.DEPTH)
        return zone["x"], zone["z"]

    def test_the_offset_conversion_only_touches_the_mirrored_walls(self):
        self.assertEqual(fit_bridge.fit_offset("wall_n", 0.1, 0.9, 4.0), 0.1)
        self.assertEqual(fit_bridge.fit_offset("wall_e", 0.1, 0.9, 3.0), 0.1)
        self.assertAlmostEqual(fit_bridge.fit_offset("wall_s", 0.1, 0.9, 4.0), 3.0)
        self.assertAlmostEqual(fit_bridge.fit_offset("wall_w", 0.1, 0.9, 3.0), 2.0)

    def test_the_engine_refuses_the_spot_the_studio_calls_the_door_swing(self):
        for wall in ("wall_n", "wall_e", "wall_s", "wall_w"):
            with self.subTest(wall=wall):
                x, z = self.zone_centre(wall)
                scene = self.scene_with_door(wall, [self.product_at(x, z)])
                verdict = fit_bridge.check_placement(scene, "p1")
                self.assertEqual(verdict["result"], "does_not_fit",
                                 f"{wall}: {verdict['cause']}")
                self.assertEqual(verdict["failing_check"], "door_swing")

    def test_the_engine_is_happy_well_away_from_that_spot(self):
        for wall in ("wall_n", "wall_e", "wall_s", "wall_w"):
            with self.subTest(wall=wall):
                x, z = self.zone_centre(wall)
                # Reflect through the centre of the room: as far from the door
                # as the room allows, on the same diagonal.
                away = (self.WIDTH - x, self.DEPTH - z)
                scene = self.scene_with_door(wall, [self.product_at(*away)])
                verdict = fit_bridge.check_placement(scene, "p1")
                self.assertIn(verdict["result"], {"fits", "tight"}, verdict["cause"])

    def test_the_hinge_side_is_not_flipped(self):
        """The two conventions name the same physical end once the offset is
        mirrored. If this ever stops being true the swing lands on the wrong
        side of the door and nothing else here would catch it."""
        source = (STUDIO_ROOT / "backend" / "fit_bridge.py").read_text(encoding="utf-8")
        self.assertIn("Hinge sides do NOT flip", source)
        self.assertEqual(fit_bridge.HINGE_SIDE, {"left": "left", "right": "right"})


class EvidenceSurvivesTests(unittest.TestCase):
    def scene(self, dimension_source, size=None):
        return scene_room({
            "revision": 1,
            "dimensions": {"width": {"value_m": 4.0, "status": "confirmed"},
                           "depth": {"value_m": 3.0, "status": "confirmed"},
                           "height": {"value_m": 2.5, "status": "confirmed"}},
            "openings": [], "existing_items": [],
            "placed_products": [{
                "id": "p1", "label": "GT905 Fabric Gaming Chair", "variant_id": THEIRS,
                "preview": True, "merchant": OTHER, "dimension_source": dimension_source,
                "price": {"amount": 42900, "currency": "USD"},
                "size": size or {"width": 0.67, "depth": 0.7, "height": 1.3},
                "pose": {"position": {"x": 2.0, "y": 0, "z": 1.5},
                         "rotation": {"yaw": 0, "pitch": 0, "roll": 0}},
            }],
        })

    def test_a_listing_text_size_can_never_return_a_firm_fit(self):
        for source in ("shopify_listing_description", "shopify_selected_size"):
            with self.subTest(source=source):
                verdict = fit_bridge.check_placement(self.scene(source), "p1")
                self.assertTrue(verdict["tentative"])
                self.assertEqual(verdict["dimension_source"], "listing_text_inferred")

    def test_a_tentative_verdict_carries_a_sentence_in_words(self):
        verdict = fit_bridge.check_placement(self.scene("shopify_listing_description"), "p1")
        self.assertIn("read from the listing text", verdict["tentative_note"])
        self.assertIn("not measured", verdict["tentative_note"])

    def test_an_unknown_size_gives_unknown_and_names_the_missing_datum(self):
        verdict = fit_bridge.check_placement(self.scene("unknown"), "p1")
        self.assertEqual(verdict["result"], "unknown")
        self.assertTrue(verdict["unknown_reasons"])
        self.assertIn("no documented footprint", " ".join(verdict["unknown_reasons"]))
        self.assertIn(THEIRS, " ".join(verdict["unknown_reasons"]))

    def test_an_unknown_size_never_comes_back_as_fits(self):
        self.assertNotEqual(fit_bridge.check_placement(self.scene("unknown"), "p1")["result"], "fits")

    def test_the_sentence_names_the_product_not_its_internal_id(self):
        verdict = fit_bridge.check_placement(self.scene("shopify_listing_description"), "p1")
        self.assertIn("GT905 Fabric Gaming Chair", verdict["cause"])
        self.assertNotIn("p1 ", verdict["cause"])

    def test_a_missing_hinge_is_reported_rather_than_assumed(self):
        scene = scene_room({
            "revision": 1,
            "dimensions": {"width": {"value_m": 4.0}, "depth": {"value_m": 3.0}, "height": {"value_m": 2.5}},
            "openings": [{"id": "door_main", "type": "door", "wall": "wall_s", "offset_m": 0.1,
                          "width_m": 0.9, "height_m": 2.03, "hinge": "none", "swing": "none"}],
            "existing_items": [],
            "placed_products": [{
                "id": "p1", "label": "Chair", "variant_id": THEIRS, "preview": True,
                "merchant": OTHER, "dimension_source": "shopify_listing_description",
                "price": {"amount": 42900, "currency": "USD"},
                "size": {"width": 0.5, "depth": 0.5, "height": 1.0},
                "pose": {"position": {"x": 0.5, "y": 0, "z": 2.5},
                         "rotation": {"yaw": 0, "pitch": 0, "roll": 0}},
            }],
        })
        verdict = fit_bridge.check_placement(scene, "p1")
        self.assertEqual(verdict["result"], "unknown")
        self.assertTrue(any("hinge" in reason or "swing" in reason
                            for reason in verdict["unknown_reasons"]), verdict["unknown_reasons"])


class FitEngineLocationTests(unittest.TestCase):
    def test_the_repo_root_is_found_and_checked(self):
        root = fit_bridge.repo_root()
        self.assertTrue((root / "fit" / "engine.py").is_file())
        self.assertTrue((root / "contracts" / "__init__.py").is_file())

    def test_a_missing_engine_fails_with_somewhere_to_go(self):
        from unittest import mock
        with mock.patch.object(fit_bridge, "__file__", str(Path(tempfile.gettempdir()) / "a" / "b" / "c.py")):
            with self.assertRaises(fit_bridge.FitEngineUnavailable) as caught:
                fit_bridge.repo_root()
        message = str(caught.exception)
        self.assertIn("fit/engine.py", message)
        self.assertIn("contracts/__init__.py", message)

    def test_the_path_insert_happens_in_exactly_one_place(self):
        hits = []
        for path in (STUDIO_ROOT / "backend").rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "sys.path.insert" in line:
                    hits.append(f"{path.name}:{number}")
        self.assertEqual(hits, ["fit_bridge.py:" + str(next(
            number for number, line in enumerate(
                (STUDIO_ROOT / "backend" / "fit_bridge.py").read_text(encoding="utf-8").splitlines(), 1)
            if "sys.path.insert" in line))], hits)


class VerdictsOnReadTests(unittest.TestCase):
    def test_a_scene_read_carries_one_verdict_per_product(self):
        scenes = temp_studio(self)
        scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=3.0))
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        config["placed_products"] = [{
            "id": "p1", "label": "Chair", "variant_id": THEIRS, "preview": True,
            "merchant": OTHER, "dimension_source": "shopify_listing_description",
            "price": {"amount": 42900, "currency": "USD"},
            "size": {"width": 0.5, "depth": 0.5, "height": 1.0},
            "pose": {"position": {"x": 2.0, "y": 0, "z": 1.5}},
        }]
        scenes.path.write_text(json.dumps(config), encoding="utf-8")
        scenes._last_good = None

        body = scenes.read()[1]
        self.assertIn("p1", body["fit"])
        self.assertTrue(body["fit"]["p1"]["tentative"])

    def test_a_room_with_nothing_in_it_has_no_verdicts_rather_than_empty_ones(self):
        scenes = temp_studio(self)
        body = scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=3.0))
        self.assertEqual(body["fit"], {})


# ==========================================================================
# Drag, read off the page source
# ==========================================================================


class DragSourceTests(unittest.TestCase):
    def setUp(self):
        self.viewer = (STATIC / "scene-viewer.js").read_text(encoding="utf-8")
        self.scene_js = (STATIC / "scene.js").read_text(encoding="utf-8")

    def test_a_click_is_three_centimetres_not_one(self):
        self.assertIn("const CLICK_THRESHOLD_M = 0.03;", self.viewer)
        self.assertIn("distanceTo(start) > CLICK_THRESHOLD_M", self.viewer)

    def test_a_movement_below_the_threshold_puts_the_product_back(self):
        """Otherwise a shaky select silently moves it a centimetre."""
        self.assertIn("group.position.copy(start);", self.viewer)

    def test_the_product_is_clamped_inside_the_room_and_snapped(self):
        self.assertIn("const SNAP_M = 0.05;", self.viewer)
        self.assertIn("clampToRoom(point, item)", self.viewer)
        self.assertIn("Math.round(value / SNAP_M) * SNAP_M", self.viewer)

    def test_the_colour_changes_on_every_pointer_move(self):
        move = self.viewer.split('addEventListener("pointermove"')[1].split("});")[0]
        self.assertIn("this.liveConflict", move)
        self.assertIn("this.paintDrag", move)

    def test_red_and_green_are_both_defined(self):
        self.assertIn("const BLOCKED = 0xff3b3b;", self.viewer)
        self.assertIn("const CLEAR = 0x3ecf8e;", self.viewer)

    def test_the_live_rule_mirrors_the_server_rule(self):
        """If these drift apart, the colour under the pointer disagrees with
        the verdict on release, which is worse than no colour at all."""
        self.assertIn("const PASSAGE_CLEARANCE_M = 0.6;", self.viewer)
        for wall in ("wall_n", "wall_s", "wall_w", "wall_e"):
            self.assertIn(f"{wall}: {{", self.viewer)
        self.assertIn('["outward", "sliding"].includes(opening.swing)', self.viewer)

    def test_the_server_still_decides(self):
        self.assertIn("/studio/scene/edit", self.scene_js)
        self.assertIn("the server's guardrails", self.scene_js)

    def test_the_painting_does_not_rebuild_the_room(self):
        """Rebuilding drops the mesh the pointer is holding."""
        paint = self.viewer.split("paintDrag(id, blocked) {")[1].split("\n  }")[0]
        self.assertNotIn("showScene", paint)


class ServerAndClientAgreeTests(unittest.TestCase):
    """The duplicated door zone is the one place two languages hold the same
    rule, so the numbers are compared rather than trusted."""

    def test_the_client_constants_match_scene_edit(self):
        from backend import scene_edit
        viewer = (STATIC / "scene-viewer.js").read_text(encoding="utf-8")
        self.assertIn(f"const PASSAGE_CLEARANCE_M = {scene_edit.PASSAGE_CLEARANCE_M};", viewer)

    def test_the_server_zone_is_a_square_of_the_door_width(self):
        """What the client reimplements. If the server moves to a real arc,
        the client has to move with it."""
        zone = opening_zone(
            {"id": "door_main", "type": "door", "wall": "wall_n", "offset_m": 0.1,
             "width_m": 0.9, "swing": "inward"}, 4.0, 3.0)
        self.assertAlmostEqual(zone["hw"] * 2, 0.9)
        self.assertAlmostEqual(zone["hd"] * 2, 0.9)


# ==========================================================================
# Your list, and the stand-in labels
# ==========================================================================


class YourListTests(unittest.TestCase):
    def setUp(self):
        self.markup = (STATIC / "scene.html").read_text(encoding="utf-8")
        self.scene_js = (STATIC / "scene.js").read_text(encoding="utf-8")

    def test_the_attribution_is_on_the_list_panel(self):
        after = self.markup.split('id="your-list-title"')[1]
        self.assertIn("Browse results come from other merchants, are for viewing only, "
                      "and cannot be bought here.", after)

    def test_the_panel_says_where_buying_happens(self):
        self.assertIn("Purchases happen on each merchant's own site", self.markup)
        self.assertIn("this list is not a cart", self.markup)

    def test_the_total_is_not_called_a_subtotal(self):
        self.assertIn("Listed prices total", self.markup)
        self.assertNotIn(">Subtotal<", self.markup)

    def test_each_row_links_to_the_merchant_in_a_new_tab(self):
        self.assertIn('buy.target = "_blank"', self.scene_js)
        self.assertIn('buy.rel = "noopener noreferrer"', self.scene_js)
        self.assertIn("Buy at ${item.merchant", self.scene_js)

    def test_a_preview_row_says_who_sells_it(self):
        self.assertIn("Preview only, sold by ${item.merchant", self.scene_js)

    def test_the_merchant_link_is_validated_before_it_is_rendered(self):
        self.assertIn("const url = safeUrl(item.listing_url);", self.scene_js)

    def test_merchant_photos_are_capped_at_card_size(self):
        """Their photography, shown as a thumbnail and nothing larger."""
        self.assertIn('url.searchParams.set("width", "220")', self.scene_js)
        self.assertIn('safeUrl(value, "cdn.shopify.com")', self.scene_js)
        css = (STATIC / "scene.css").read_text(encoding="utf-8")
        self.assertIn("max-height: 110px", css)


class StandInLabelTests(unittest.TestCase):
    def setUp(self):
        self.viewer = (STATIC / "scene-viewer.js").read_text(encoding="utf-8")
        self.scene_js = (STATIC / "scene.js").read_text(encoding="utf-8")

    def test_a_box_is_never_called_a_3d_model_of_the_product(self):
        self.assertIn("Stand-in box at the listing size", self.viewer)
        self.assertIn("Stand-in box at listing size", self.scene_js)
        self.assertIn("Not a 3D model of this product", self.viewer)

    def test_the_tag_in_the_room_carries_the_word(self):
        self.assertIn('note.textContent = "stand-in";', self.viewer)

    def test_a_preview_item_is_labelled_in_the_room_too(self):
        self.assertIn('note.textContent = "preview";', self.viewer)

    def test_a_screen_reader_is_told_the_same_thing(self):
        self.assertIn("shown as a stand-in box at the listing size", self.viewer)
        self.assertIn("preview only, sold by", self.viewer)

    def test_the_verdict_is_shown_in_words_not_only_a_colour(self):
        self.assertIn("const FIT_WORDS", self.scene_js)
        self.assertIn('does_not_fit: "Does not fit"', self.scene_js)
        self.assertIn("Tentative: size read from the listing text, not measured", self.scene_js)

    def test_an_absent_verdict_shows_no_badge_at_all(self):
        """An unchecked placement must not look like a checked one."""
        block = self.scene_js.split("function showVerdict(productId) {")[1].split("\n}")[0]
        self.assertIn("line.hidden = true;", block)


# ==========================================================================
# Browsing does not need a model
# ==========================================================================


class BareSearchTests(unittest.TestCase):
    def studio(self, shop=None):
        return ProductStudio(router(shop), FakeGemini(), models=None)

    def test_a_product_name_searches_without_calling_gemini(self):
        gemini = FakeGemini()
        studio = ProductStudio(router(), gemini, models=None)
        result = run(studio.run(StudioCommand(command="gaming chair", mode="browse")))
        self.assertEqual(result["action"], "show_results")
        self.assertEqual(gemini.calls, 0, "browse search must not need the model")
        self.assertTrue(result["results"])

    def test_our_store_mode_still_goes_through_the_model(self):
        gemini = FakeGemini()
        studio = ProductStudio(router(), gemini, models=None)
        with self.assertRaises(ConnectionError):
            run(studio.run(StudioCommand(command="gaming chair", mode="store")))
        self.assertEqual(gemini.calls, 1)

    def test_an_edit_command_is_still_the_models_job(self):
        for command in ("rotate it", "make it green", "add a headrest", "help"):
            with self.subTest(command=command):
                self.assertIsNone(bare_search(command))

    def test_a_product_name_is_passed_through_verbatim(self):
        """Including its typos. Correcting them was the model's job and the
        model is not in this path; a wrong search is better than no search."""
        self.assertEqual(bare_search("gaing chair").value, "gaing chair")

    def test_blank_input_is_not_a_search(self):
        self.assertIsNone(bare_search("   "))


if __name__ == "__main__":
    unittest.main()
