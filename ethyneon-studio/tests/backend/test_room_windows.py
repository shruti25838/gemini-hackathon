"""Windows on the typed room, and the fact that one never blocks anything.

The scene file and the 3D viewer have always handled an opening of type
"window": `scene_room()` reads its sill and feeds `lighting.windows`, and
`room-viewer.js` cuts the wall above and below the sill and draws a glass
pane. The only missing piece was a way to type one in, so this covers the
form and the validation and nothing else.

The last class is the one worth reading. A window is not an obstacle, at
any sill height, and that is true in two independent places: the Studio's
own guardrail skips it, and `fit/analysis.py` skips every opening that is
not a door. A sofa under a window has to fit.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend import fit_bridge
from backend.errors import ConnectionError
from backend.scene import MAX_WINDOWS, PresetRoom, PresetWindow, SceneStudio, scene_room
from backend.scene_edit import opening_zone

STUDIO_ROOT = Path(__file__).resolve().parents[2]
STATIC = STUDIO_ROOT / "backend" / "static"

VARIANT = "gid://shopify/ProductVariant/77000000000002"


def window(**overrides):
    return {"wall": "wall_n", "offset_m": 0.8, "width_m": 1.4,
            "height_m": 1.3, "sill_m": 0.9, **overrides}


class WindowRouteTests(unittest.TestCase):
    def scenes(self) -> SceneStudio:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        return SceneStudio(None, None, path=folder / "geometricconfig.json",
                           cache_dir=folder / "cache")

    def write(self, scenes, **overrides):
        return scenes.write_preset_room(PresetRoom(
            width_m=4.0, depth_m=3.2, height_m=2.6,
            door_wall="wall_s", door_offset_m=0.3, **overrides))

    # -- it works ------------------------------------------------------

    def test_a_window_is_written_as_an_opening_of_type_window(self):
        body = self.write(self.scenes(), windows=[window()])
        openings = body["scene"]["openings"]
        self.assertEqual([o["type"] for o in openings], ["door", "window"])
        pane = openings[1]
        self.assertEqual(pane["id"], "window_1")
        self.assertEqual(pane["wall"], "wall_n")
        self.assertEqual(pane["sill_m"], 0.9)
        self.assertEqual(pane["height_m"], 1.3)

    def test_two_windows_on_different_walls_both_land(self):
        body = self.write(self.scenes(), windows=[
            window(wall="wall_n"), window(wall="wall_e", offset_m=0.9, width_m=1.2)])
        walls = [o["wall"] for o in body["scene"]["openings"] if o["type"] == "window"]
        self.assertEqual(walls, ["wall_n", "wall_e"])

    def test_a_window_reaches_the_lighting_model(self):
        """room_lighting counts window area, so a room with glass is brighter
        and the painting-spot scoring has something to work with."""
        body = self.write(self.scenes(), windows=[window(), window(wall="wall_e", width_m=1.2)])
        lighting = body["scene"]["lighting"]
        self.assertEqual(len(lighting["windows"]), 2)
        self.assertAlmostEqual(lighting["window_area_m2"], 1.4 * 1.3 + 1.2 * 1.3, places=3)

    def test_a_room_with_no_windows_is_unchanged(self):
        body = self.write(self.scenes())
        self.assertEqual([o["type"] for o in body["scene"]["openings"]], ["door"])
        self.assertEqual(body["scene"]["lighting"]["windows"], [])

    def test_the_file_records_them_and_they_survive_a_reload(self):
        scenes = self.scenes()
        self.write(scenes, windows=[window()])
        saved = json.loads(scenes.path.read_text(encoding="utf-8"))["openings"]
        self.assertEqual(saved[1]["type"], "window")
        scenes._last_good = None
        self.assertEqual(len(scenes.read()[1]["scene"]["openings"]), 2)

    # -- it refuses a window that cannot exist ---------------------------

    def test_a_window_off_the_end_of_its_wall_is_refused(self):
        with self.assertRaises(ConnectionError) as caught:
            self.write(self.scenes(), windows=[window(wall="wall_e", offset_m=2.6, width_m=1.4)])
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.payload["code"], "window_off_the_wall")
        self.assertIn("3.20 m long", caught.exception.payload["message"])

    def test_a_window_wider_than_its_wall_is_refused(self):
        with self.assertRaises(ConnectionError) as caught:
            self.write(self.scenes(), windows=[window(wall="wall_e", offset_m=0.0, width_m=4.0)])
        self.assertEqual(caught.exception.payload["code"], "window_off_the_wall")

    def test_a_window_that_reaches_past_the_ceiling_is_refused(self):
        with self.assertRaises(ConnectionError) as caught:
            self.write(self.scenes(), windows=[window(sill_m=2.0, height_m=1.2)])
        self.assertEqual(caught.exception.payload["code"], "window_above_the_ceiling")
        self.assertIn("2.60 m ceiling", caught.exception.payload["message"])

    def test_a_window_standing_in_the_doorway_is_refused(self):
        """The door is on wall_s from 0.30 to 1.20 m."""
        with self.assertRaises(ConnectionError) as caught:
            self.write(self.scenes(), windows=[window(wall="wall_s", offset_m=0.5, width_m=1.0)])
        self.assertEqual(caught.exception.payload["code"], "window_overlaps_an_opening")
        self.assertIn("the door", caught.exception.payload["message"])

    def test_two_windows_overlapping_each_other_are_refused(self):
        with self.assertRaises(ConnectionError) as caught:
            self.write(self.scenes(), windows=[
                window(wall="wall_n", offset_m=0.5, width_m=1.5),
                window(wall="wall_n", offset_m=1.0, width_m=1.5)])
        self.assertEqual(caught.exception.payload["code"], "window_overlaps_an_opening")

    def test_a_window_on_the_door_wall_but_clear_of_it_is_allowed(self):
        body = self.write(self.scenes(), windows=[window(wall="wall_s", offset_m=2.0, width_m=1.2)])
        self.assertEqual(len(body["scene"]["openings"]), 2)

    def test_more_than_four_windows_is_refused_by_the_model(self):
        with self.assertRaises(ValidationError):
            PresetRoom(width_m=4.0, depth_m=3.2, windows=[window()] * (MAX_WINDOWS + 1))

    def test_bad_numbers_are_refused_by_the_model(self):
        for bad in ({"width_m": 0.1}, {"width_m": 9.0}, {"height_m": 0.0},
                    {"sill_m": -0.5}, {"offset_m": -1.0}, {"wall": "ceiling"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    PresetWindow(**window(**bad))

    def test_a_refused_window_writes_no_room_at_all(self):
        scenes = self.scenes()
        with self.assertRaises(ConnectionError):
            self.write(scenes, windows=[window(sill_m=2.4)])
        self.assertFalse(scenes.path.exists())


# ==========================================================================
# A window is never an obstacle
# ==========================================================================


class WindowsBlockNothingTests(unittest.TestCase):
    """Two independent checks have to agree on this, or a sofa under a
    window would be refused for standing in thin air."""

    def room(self, placed_x, placed_z):
        return scene_room({
            "revision": 1,
            "dimensions": {"width": {"value_m": 4.0, "status": "confirmed"},
                           "depth": {"value_m": 3.2, "status": "confirmed"},
                           "height": {"value_m": 2.6, "status": "confirmed"}},
            "openings": [
                {"id": "door_main", "type": "door", "wall": "wall_s", "offset_m": 0.3,
                 "width_m": 0.9, "height_m": 2.03, "hinge": "left", "swing": "inward"},
                {"id": "window_1", "type": "window", "wall": "wall_n", "offset_m": 0.8,
                 "width_m": 1.4, "height_m": 1.3, "sill_m": 0.9},
            ],
            "existing_items": [],
            "placed_products": [{
                "id": "p1", "label": "Armchair", "variant_id": VARIANT, "preview": True,
                "merchant": "someone.myshopify.com",
                "dimension_source": "shopify_listing_description",
                "price": {"amount": 29900, "currency": "USD"},
                "size": {"width": 0.75, "depth": 0.8, "height": 0.85},
                "pose": {"position": {"x": placed_x, "y": 0, "z": placed_z},
                         "rotation": {"yaw": 0, "pitch": 0, "roll": 0}},
            }],
        })

    def test_the_studio_guardrail_has_no_zone_for_a_window(self):
        pane = {"id": "window_1", "type": "window", "wall": "wall_n",
                "offset_m": 0.8, "width_m": 1.4, "sill_m": 0.9}
        self.assertIsNone(opening_zone(pane, 4.0, 3.2))

    def test_the_fit_engine_skips_every_opening_that_is_not_a_door(self):
        source = (STUDIO_ROOT.parent / "fit" / "analysis.py").read_text(encoding="utf-8")
        self.assertIn('if opening.type != "door":', source)
        self.assertEqual(source.count("room_spec.openings"), 1,
                         "openings are read in one place; a second would need checking too")

    def test_an_armchair_directly_under_the_window_fits(self):
        """Hard against wall_n, centred on the glass."""
        verdict = fit_bridge.check_placement(self.room(1.5, 0.45), "p1")
        self.assertIn(verdict["result"], {"fits", "tight"}, verdict["cause"])
        self.assertNotIn("window", verdict["cause"].lower())

    def test_the_window_is_not_named_as_a_failing_check(self):
        verdict = fit_bridge.check_placement(self.room(1.5, 0.45), "p1")
        self.assertNotEqual(verdict["result"], "does_not_fit")
        self.assertEqual(verdict["unknown_reasons"], [])

    def test_the_door_still_blocks_what_it_should(self):
        """The control. If this passed too, the test above would prove
        nothing about windows."""
        verdict = fit_bridge.check_placement(self.room(0.75, 2.75), "p1")
        self.assertEqual(verdict["result"], "does_not_fit")
        self.assertEqual(verdict["failing_check"], "door_swing")


# ==========================================================================
# The form
# ==========================================================================


class WindowFormTests(unittest.TestCase):
    def setUp(self):
        self.markup = (STATIC / "scene.html").read_text(encoding="utf-8")
        self.js = (STATIC / "scene.js").read_text(encoding="utf-8")

    def test_the_form_has_a_repeatable_window_row(self):
        self.assertIn('id="window-row-template"', self.markup)
        self.assertIn('id="window-rows"', self.markup)
        self.assertIn('id="add-window"', self.markup)

    def test_a_row_carries_every_field_the_server_wants(self):
        for field in ("wall", "offset_m", "width_m", "height_m", "sill_m"):
            self.assertIn(f'data-window="{field}"', self.markup, field)

    def test_a_row_can_be_removed(self):
        self.assertIn('data-window="remove"', self.markup)
        self.assertIn("row.remove()", self.js)

    def test_the_form_stops_at_four(self):
        self.assertIn("const MAX_WINDOWS = 4;", self.js)
        self.assertIn("rows.children.length >= MAX_WINDOWS", self.js)

    def test_the_client_cap_matches_the_server(self):
        self.assertEqual(MAX_WINDOWS, 4)

    def test_the_windows_are_sent_with_the_room(self):
        self.assertIn("windows: collectWindows(),", self.js)
        block = self.js.split("function collectWindows() {")[1].split("\n}")[0]
        for field in ("wall", "offset_m", "width_m", "height_m", "sill_m"):
            self.assertIn(f'field("{field}")', block)

    def test_a_second_window_defaults_to_a_different_wall(self):
        """Two clicks should give two windows that can both be saved, rather
        than an overlap the server then refuses."""
        self.assertIn("walls[rows.children.length % walls.length]", self.js)

    def test_the_form_says_a_window_blocks_nothing(self):
        self.assertIn("A window is never an obstacle", self.markup)

    def test_the_viewer_was_not_touched_for_this(self):
        """room-viewer.js already drew windows. If this feature had needed a
        change there, the claim that it is form-only would be wrong."""
        viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        self.assertIn('if (opening.type === "window")', viewer)
        self.assertIn("MeshStandardMaterial", viewer)


if __name__ == "__main__":
    unittest.main()
