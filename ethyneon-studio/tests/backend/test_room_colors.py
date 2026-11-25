"""Changing the room's wall and floor colours, with no model involved.

Three things are worth pinning here.

**The colour is saved, and saved honestly.** It goes through the same guarded
edit route as every other change, so it lands in geometricconfig.json and
survives a reload, and its recorded source is `chosen_by_user`. The photo
pipeline has `sampled_from_photo` for a colour it actually measured off a
shopper's wall, and a swatch click is not that.

**The server refuses anything that is not a colour.** The route takes a hex
string and a target from whoever posts one, so "red", "#12345" and "ceiling"
are all refused at the boundary rather than written to the file.

**The parser is run, not grepped.** `parseColorCommand` lives in its own
module with no DOM and no three.js, so node can execute it. A test that only
checked the source for the string "olive" would pass against a parser that
never worked.
"""

import asyncio
import json
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.errors import ConnectionError
from backend.scene import PresetRoom, SceneStudio
from backend.scene_edit import USER_CHOSEN_COLOR, WALL_IDS, SceneEdit

STUDIO_ROOT = Path(__file__).resolve().parents[2]
STATIC = STUDIO_ROOT / "backend" / "static"
OLIVE = "#6f7a3f"
WALNUT = "#6b4a2f"

DEFAULT_WALL = "#e9e5dd"
DEFAULT_FLOOR = "#b48c63"


def run(coroutine):
    return asyncio.run(coroutine)


def action(suffix: str) -> str:
    return f"{suffix * 8}-0000-0000-0000-000000000001"[:36]


class ColorRouteTests(unittest.TestCase):
    """The scene edit route, with no connections at all: a colour needs none."""

    def scenes(self) -> SceneStudio:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        scenes = SceneStudio(None, None, path=folder / "geometricconfig.json",
                             cache_dir=folder / "cache")
        scenes.write_preset_room(PresetRoom(width_m=3.6, depth_m=3.0))
        return scenes

    def set_color(self, scenes, target, color, suffix="a"):
        return run(scenes.edit(SceneEdit(
            base_etag=scenes.read()[0], action_id=action(suffix),
            op="set_room_color", target=target, color=color,
        )))

    # -- it works ------------------------------------------------------

    def test_setting_the_walls_colours_all_four(self):
        body = self.set_color(self.scenes(), "walls", OLIVE)
        walls = body["scene"]["walls"]
        self.assertEqual([wall["id"] for wall in walls], list(WALL_IDS))
        self.assertEqual({wall["color"] for wall in walls}, {OLIVE})

    def test_setting_the_floor_leaves_the_walls_alone(self):
        scenes = self.scenes()
        body = self.set_color(scenes, "floor", WALNUT)
        self.assertEqual(body["scene"]["floor"]["color"], WALNUT)
        self.assertEqual({wall["color"] for wall in body["scene"]["walls"]}, {DEFAULT_WALL})

    def test_the_colour_is_written_to_the_file_and_survives_a_reload(self):
        scenes = self.scenes()
        self.set_color(scenes, "walls", OLIVE)
        saved = json.loads(scenes.path.read_text(encoding="utf-8"))
        self.assertEqual({wall["color"] for wall in saved["walls"]}, {OLIVE})
        scenes._last_good = None
        self.assertEqual(scenes.read()[1]["scene"]["walls"][0]["color"], OLIVE)

    def test_a_colour_edit_bumps_the_revision_like_any_other(self):
        scenes = self.scenes()
        before = scenes.read()[1]["scene"]["revision"]
        body = self.set_color(scenes, "walls", OLIVE)
        self.assertEqual(body["scene"]["revision"], before + 1)
        # last_edit is bookkeeping in the file; scene_room does not render it.
        saved = json.loads(scenes.path.read_text(encoding="utf-8"))["last_edit"]
        self.assertEqual(saved["op"], "set_room_color")
        self.assertEqual(saved["target"], "walls")
        self.assertEqual(saved["source"], "design_view")

    def test_a_mixed_case_hex_is_stored_lower_case(self):
        body = self.set_color(self.scenes(), "walls", "#6F7A3F")
        self.assertEqual(body["scene"]["walls"][0]["color"], OLIVE)

    def test_a_repeat_of_the_same_action_id_returns_the_first_result(self):
        """The idempotency cache has to cover colours too, or a double click
        on a swatch writes two revisions."""
        scenes = self.scenes()
        first = self.set_color(scenes, "walls", OLIVE)
        again = run(scenes.edit(SceneEdit(
            base_etag=first["etag"], action_id=action("a"),
            op="set_room_color", target="walls", color=WALNUT,
        )))
        self.assertEqual(again["scene"]["revision"], first["scene"]["revision"])
        self.assertEqual(again["scene"]["walls"][0]["color"], OLIVE)

    # -- the source is honest -------------------------------------------

    def test_the_saved_source_says_the_user_chose_it(self):
        body = self.set_color(self.scenes(), "walls", OLIVE)
        for wall in body["scene"]["walls"]:
            self.assertEqual(wall["color_source"], USER_CHOSEN_COLOR)
        self.assertEqual(USER_CHOSEN_COLOR, "chosen_by_user")

    def test_it_never_claims_the_colour_came_from_a_photo(self):
        scenes = self.scenes()
        self.set_color(scenes, "walls", OLIVE)
        self.set_color(scenes, "floor", WALNUT, suffix="b")
        saved = scenes.path.read_text(encoding="utf-8")
        self.assertNotIn("sampled_from_photo", saved)
        self.assertNotIn("confirmed", json.dumps(
            [json.loads(saved)["floor"], json.loads(saved)["walls"]]))

    def test_an_untouched_surface_keeps_its_estimated_source(self):
        body = self.set_color(self.scenes(), "walls", OLIVE)
        self.assertEqual(body["scene"]["floor"]["color_source"], "estimated")

    # -- undo -----------------------------------------------------------

    def test_the_reply_carries_the_colour_that_was_on_screen(self):
        """Not the colour in the file: a fresh room stores no wall colour at
        all, and an undo has to restore what the shopper was looking at."""
        scenes = self.scenes()
        self.assertNotIn("walls", json.loads(scenes.path.read_text(encoding="utf-8")))
        body = self.set_color(scenes, "walls", OLIVE)
        self.assertEqual(body["undo"], {"target": "walls", "color": DEFAULT_WALL})

    def test_undo_restores_the_previous_colour(self):
        scenes = self.scenes()
        first = self.set_color(scenes, "walls", OLIVE)
        second = run(scenes.edit(SceneEdit(
            base_etag=first["etag"], action_id=action("b"),
            op="set_room_color", target="walls", color="#2b3a55",
        )))
        self.assertEqual(second["undo"], {"target": "walls", "color": OLIVE})
        restored = run(scenes.edit(SceneEdit(
            base_etag=second["etag"], action_id=action("c"),
            op="set_room_color", target="walls", color=second["undo"]["color"],
        )))
        self.assertEqual(restored["scene"]["walls"][0]["color"], OLIVE)

    def test_undo_of_a_floor_change_names_the_floor(self):
        body = self.set_color(self.scenes(), "floor", WALNUT)
        self.assertEqual(body["undo"], {"target": "floor", "color": DEFAULT_FLOOR})

    # -- it refuses anything that is not a colour -------------------------

    def test_a_value_that_is_not_a_hex_string_is_refused(self):
        for value in ("red", "#12345", "#1234567", "6f7a3f", "#gggggg",
                      "rgb(1,2,3)", "", "#6f7a3f;", "olive"):
            with self.subTest(color=value):
                with self.assertRaises(ValidationError):
                    SceneEdit(base_etag="0" * 16, action_id=action("a"),
                              op="set_room_color", target="walls", color=value)

    def test_an_unknown_target_is_refused(self):
        for target in ("ceiling", "wall_n", "WALLS", "door", "everything", ""):
            with self.subTest(target=target):
                with self.assertRaises(ValidationError):
                    SceneEdit(base_etag="0" * 16, action_id=action("a"),
                              op="set_room_color", target=target, color=OLIVE)

    def test_a_colour_edit_with_no_target_or_no_colour_is_refused(self):
        with self.assertRaises(ValidationError):
            SceneEdit(base_etag="0" * 16, action_id=action("a"),
                      op="set_room_color", color=OLIVE)
        with self.assertRaises(ValidationError):
            SceneEdit(base_etag="0" * 16, action_id=action("a"),
                      op="set_room_color", target="walls")

    def test_a_refused_colour_writes_nothing(self):
        scenes = self.scenes()
        before = scenes.path.read_bytes()
        with self.assertRaises(ValidationError):
            SceneEdit(base_etag=scenes.read()[0], action_id=action("a"),
                      op="set_room_color", target="walls", color="red")
        self.assertEqual(scenes.path.read_bytes(), before)

    def test_a_stale_etag_is_still_refused(self):
        scenes = self.scenes()
        self.set_color(scenes, "walls", OLIVE)
        with self.assertRaises(ConnectionError) as caught:
            run(scenes.edit(SceneEdit(
                base_etag="0" * 16, action_id=action("b"),
                op="set_room_color", target="walls", color=WALNUT)))
        self.assertEqual(caught.exception.status, 409)

    # -- the product_id default cannot leak into a product edit -----------

    def test_a_product_edit_must_still_name_a_product(self):
        """product_id defaults to "room" for colour edits. A move that forgot
        to name a product would otherwise validate and act on nothing."""
        for op, extra in (("move", {"position": {"x": 1.0, "z": 1.0}}),
                          ("rotate", {"yaw": 15.0}),
                          ("remove", {})):
            with self.subTest(op=op):
                with self.assertRaises(ValidationError):
                    SceneEdit(base_etag="0" * 16, action_id=action("a"), op=op, **extra)

    def test_a_product_edit_that_names_one_is_unchanged(self):
        edit = SceneEdit(base_etag="0" * 16, action_id=action("a"),
                         op="rotate", product_id="product_1", yaw=15.0)
        self.assertEqual(edit.product_id, "product_1")
        self.assertIsNone(edit.target)
        self.assertIsNone(edit.color)

    def test_a_colour_edit_needs_no_connections_at_all(self):
        """SceneStudio here is built with shopify=None. If a colour ever
        started asking Shopify or a model anything, this would crash."""
        self.set_color(self.scenes(), "walls", OLIVE)


# ==========================================================================
# The typed command, executed rather than grepped
# ==========================================================================


NODE = shutil.which("node")

HARNESS = """
import { parseColorCommand } from "%s";
const cases = %s;
console.log(JSON.stringify(cases.map((text) => parseColorCommand(text))));
"""


class SceneFileEncodingTests(unittest.TestCase):
    """A product title with a character cp1252 cannot hold.

    Found by building the hero room: a side table called
    "Solid Walnut Side Table for Living Room - 24 x 12" with an en dash in
    it was written through write_text with no encoding, so on Windows it
    went out as mojibake and the next read died with a UnicodeDecodeError
    that surfaced as "geometricconfig.json is not a valid room". Every
    write of the scene file now names its encoding.
    """

    EN_DASH_TITLE = "Solid Walnut Side Table \u2013 24 x 12"

    def scenes(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        scenes = SceneStudio(None, None, path=folder / "geometricconfig.json",
                             cache_dir=folder / "cache")
        scenes.write_preset_room(PresetRoom(width_m=4.0, depth_m=3.2))
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        config["placed_products"] = [{
            "id": "p1", "label": self.EN_DASH_TITLE,
            "variant_id": "gid://shopify/ProductVariant/1",
            "size": {"width": 0.6, "depth": 0.3, "height": 0.65},
            "pose": {"position": {"x": 1.2, "y": 0, "z": 1.0}},
        }]
        scenes.path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        scenes._last_good = None
        return scenes

    def test_a_commit_keeps_a_non_ascii_title_readable(self):
        scenes = self.scenes()
        body = run(scenes.edit(SceneEdit(
            base_etag=scenes.read()[0], action_id=action("e"),
            op="rotate", product_id="p1", yaw=15.0)))
        self.assertEqual(body["scene"]["placed_products"][0]["label"], self.EN_DASH_TITLE)
        again = json.loads(scenes.path.read_text(encoding="utf-8"))
        self.assertEqual(again["placed_products"][0]["label"], self.EN_DASH_TITLE)

    def test_a_colour_edit_does_not_corrupt_a_non_ascii_room(self):
        scenes = self.scenes()
        run(scenes.edit(SceneEdit(
            base_etag=scenes.read()[0], action_id=action("f"),
            op="set_room_color", target="walls", color="#a8b89a")))
        scenes._last_good = None
        self.assertEqual(scenes.read()[1]["scene"]["placed_products"][0]["label"],
                         self.EN_DASH_TITLE)

    def test_every_scene_write_names_its_encoding(self):
        """read_bytes plus ensure_ascii=False only round-trips if it does."""
        source = (STUDIO_ROOT / "backend" / "scene.py").read_text(encoding="utf-8")
        for block in source.split("write_text(")[1:]:
            head = block[:200]
            if "json.dumps(config" in head:
                self.assertIn('encoding="utf-8"', head, head[:120])


@unittest.skipIf(NODE is None, "node is not installed; the parser cannot be executed")
class ColorCommandTests(unittest.TestCase):
    """scene-colors.js imports nothing and touches no DOM, so node can run it."""

    def parse(self, *commands):
        module = (STATIC / "scene-colors.js").as_uri()
        script = HARNESS % (module, json.dumps(list(commands)))
        result = subprocess.run([NODE, "--input-type=module", "-e", script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_it_reads_the_example_from_the_placeholder(self):
        self.assertEqual(self.parse("make the walls olive")[0],
                         {"target": "walls", "hex": "#6f7a3f", "name": "olive"})

    def test_it_reads_a_floor_command(self):
        self.assertEqual(self.parse("make the floor walnut")[0],
                         {"target": "floor", "hex": "#6b4a2f", "name": "walnut"})

    def test_word_order_does_not_matter(self):
        for reply in self.parse("sage walls", "walls sage", "paint the walls sage please"):
            self.assertEqual(reply, {"target": "walls", "hex": "#a8b89a", "name": "sage"})

    def test_the_longest_name_wins(self):
        """"light grey" must not be read as "grey", which is a different
        colour on a different row."""
        self.assertEqual(self.parse("floor light grey")[0]["hex"], "#c9c9c4")
        self.assertEqual(self.parse("walls warm grey")[0]["hex"], "#d6cfc4")
        # Plain "grey" is its own neutral, deliberately not the warm one.
        self.assertEqual(self.parse("walls grey")[0]["hex"], "#a6a6a3")

    def test_british_and_american_spellings_both_work(self):
        self.assertEqual(self.parse("floor light gray")[0]["hex"],
                         self.parse("floor light grey")[0]["hex"])

    def test_a_bare_hex_is_accepted(self):
        self.assertEqual(self.parse("walls #123abc")[0],
                         {"target": "walls", "hex": "#123abc", "name": "#123abc"})

    def test_an_unknown_colour_gets_a_plain_reply(self):
        for reply in self.parse("make the walls chartreuse", "walls banana"):
            self.assertEqual(reply, {"error": "I don't know that color."})

    def test_a_missing_target_says_which_it_needs(self):
        reply = self.parse("make it olive")[0]
        self.assertIn("walls or the floor", reply["error"])

    def test_empty_input_asks_for_something(self):
        self.assertIn("make the walls olive", self.parse("   ")[0]["error"])

    def test_every_swatch_name_is_understood_by_the_parser(self):
        """A swatch the command box cannot name would be a quiet gap."""
        source = (STATIC / "scene-colors.js").read_text(encoding="utf-8")
        import re
        names = re.findall(r'\["([A-Za-z ]+)", "(#[0-9a-f]{6})"\]', source)
        self.assertGreaterEqual(len(names), 12)
        walls = {name.lower(): hex_value for name, hex_value in names[:7]}
        floors = {name.lower(): hex_value for name, hex_value in names[7:]}
        replies = self.parse(*[f"walls {name}" for name in walls],
                             *[f"floor {name}" for name in floors])
        for reply in replies:
            self.assertNotIn("error", reply, reply)


# ==========================================================================
# The panel, read off the page
# ==========================================================================


REQUIRED_NAMES = ['pink', 'hot pink', 'light pink', 'rose', 'red', 'maroon', 'orange', 'peach', 'yellow', 'mustard', 'lime', 'green', 'mint', 'sage', 'forest', 'olive', 'teal', 'aqua', 'sky blue', 'blue', 'navy', 'indigo', 'purple', 'lavender', 'magenta', 'beige', 'cream', 'tan', 'brown', 'black', 'white', 'grey', 'silver', 'terracotta', 'charcoal']


class ColorNameCoverageTests(unittest.TestCase):
    """Every name in the brief, executed through the real parser.

    "pink" is why this exists: the lookup did not have it, so a shopper
    asking for a pink wall got told the colour did not exist.
    """

    def parse(self, *commands):
        module = (STATIC / "scene-colors.js").as_uri()
        script = HARNESS % (module, json.dumps(list(commands)))
        result = subprocess.run([NODE, "--input-type=module", "-e", script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_every_required_name_is_reachable_by_typing(self):
        replies = self.parse(*[f"walls {name}" for name in REQUIRED_NAMES])
        missing = [name for name, reply in zip(REQUIRED_NAMES, replies) if "error" in reply]
        self.assertEqual(missing, [], f"the parser does not know: {missing}")

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_every_required_name_also_works_on_the_floor(self):
        replies = self.parse(*[f"floor {name}" for name in REQUIRED_NAMES])
        for name, reply in zip(REQUIRED_NAMES, replies):
            with self.subTest(name=name):
                self.assertEqual(reply.get("target"), "floor", reply)

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_every_name_resolves_to_a_real_hex(self):
        replies = self.parse(*[f"walls {name}" for name in REQUIRED_NAMES])
        for name, reply in zip(REQUIRED_NAMES, replies):
            with self.subTest(name=name):
                self.assertRegex(reply.get("hex", ""), r"^#[0-9a-f]{6}$")

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_the_two_word_names_are_not_read_as_their_last_word(self):
        """"hot pink" must not become "pink", and "sky blue" must not become
        "blue". Longest name first is the whole reason this works."""
        pairs = [("hot pink", "pink"), ("light pink", "pink"),
                 ("sky blue", "blue"), ("light grey", "grey"),
                 ("warm grey", "grey"), ("dark grey", "grey")]
        replies = self.parse(*[f"walls {long}" for long, _ in pairs],
                             *[f"walls {short}" for _, short in pairs])
        half = len(pairs)
        for index, (long, short) in enumerate(pairs):
            with self.subTest(pair=(long, short)):
                self.assertNotEqual(replies[index]["hex"], replies[half + index]["hex"],
                                    f"{long} resolved the same as {short}")

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_an_unknown_name_still_gets_the_plain_reply(self):
        """Adding names must not turn the lookup into a guesser."""
        for reply in self.parse("walls chartreuse", "walls banana", "floor ultraviolet"):
            self.assertEqual(reply, {"error": "I don't know that color."})

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_a_swatch_name_resolves_to_that_swatch_exactly(self):
        """Typing "olive" has to light up the olive swatch, not land a shade
        beside it."""
        source = (STATIC / "scene-colors.js").read_text(encoding="utf-8")
        import re
        block = source.split("export const COLOR_NAMES")[0]
        swatches = dict((name.lower(), hex_value)
                        for name, hex_value in re.findall(r'\["([A-Za-z ]+)", "(#[0-9a-f]{6})"\]', block))
        names = sorted(swatches)
        replies = self.parse(*[f"walls {name}" for name in names])
        for name, reply in zip(names, replies):
            with self.subTest(name=name):
                self.assertEqual(reply["hex"], swatches[name])


@unittest.skipIf(NODE is None, "node is not installed")
class HsvRoundTripTests(unittest.TestCase):
    """The wheel's arithmetic, executed rather than assumed."""

    def evaluate(self, expression):
        module = (STATIC / "scene-colors.js").as_uri()
        script = (f'import * as c from "{module}";\n'
                  f"console.log(JSON.stringify({expression}));")
        result = subprocess.run([NODE, "--input-type=module", "-e", script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_a_hex_survives_the_round_trip(self):
        hexes = ["#a8b89a", "#6f7a3f", "#ffc0cb", "#ff69b4", "#2b3a55",
                 "#ffffff", "#000000", "#c9a16b", "#1f2220"]
        out = self.evaluate(
            "%s.map(h => c.hsvToHex(c.hexToHsv(h)))" % json.dumps(hexes))
        self.assertEqual(out, hexes)

    def test_a_bad_hex_is_rejected_rather_than_coerced(self):
        self.assertIsNone(self.evaluate('c.hexToHsv("red")'))
        self.assertIsNone(self.evaluate('c.hexToHsv("#12345")'))
        self.assertIsNone(self.evaluate('c.hexToRgb("#gggggg")'))

    def test_hue_wraps_and_values_clamp(self):
        self.assertEqual(self.evaluate("c.hsvToHex({h: 360, s: 1, v: 1})"),
                         self.evaluate("c.hsvToHex({h: 0, s: 1, v: 1})"))
        self.assertEqual(self.evaluate("c.hsvToHex({h: 0, s: 5, v: 5})"), "#ff0000")
        self.assertEqual(self.evaluate("c.hsvToHex({h: 0, s: -1, v: -1})"), "#000000")

    def test_pink_lands_where_a_person_would_expect(self):
        hsv = self.evaluate('c.hexToHsv("#ffc0cb")')
        self.assertGreater(hsv["h"], 330)
        self.assertLess(hsv["s"], 0.3)
        self.assertGreater(hsv["v"], 0.9)


class ColorWheelSourceTests(unittest.TestCase):
    """The wheel is canvas and pointer events, and saves the same way."""

    def setUp(self):
        self.wheel = (STATIC / "scene-wheel.js").read_text(encoding="utf-8")
        self.js = (STATIC / "scene.js").read_text(encoding="utf-8")
        self.markup = (STATIC / "scene.html").read_text(encoding="utf-8")
        self.room_viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")

    def test_the_wheel_imports_nothing_external(self):
        """No library, no CDN. The only import is our own colour arithmetic."""
        import re
        imports = re.findall(r'^import .*?from "([^"]+)";', self.wheel, re.M)
        self.assertEqual(imports, ["./scene-colors.js"], imports)
        self.assertNotIn("http", self.wheel)

    def test_it_is_drawn_on_a_plain_canvas(self):
        self.assertIn('<canvas id="color-wheel"', self.markup)
        self.assertIn('getContext("2d")', self.wheel)
        self.assertIn("ctx.arc(", self.wheel)
        self.assertIn("createLinearGradient", self.wheel)

    def test_it_has_a_hue_ring_and_a_shade_square(self):
        self.assertIn('return "ring"', self.wheel)
        self.assertIn('return "square"', self.wheel)

    def test_pointer_coordinates_come_from_the_canvas_rect(self):
        """The same mistake cost a working drag once already."""
        block = self.wheel.split("const local = (event) => {")[1].split("};")[0]
        self.assertIn("getBoundingClientRect()", block)
        self.assertIn("event.clientX", block)
        self.assertNotIn("devicePixelRatio", block)
        self.assertNotIn("pageX", block)

    def test_dragging_previews_and_releasing_saves(self):
        """A write per pixel of a drag would put a revision in the scene file
        for every pixel."""
        down = self.wheel.split('canvas.addEventListener("pointerdown"')[1].split("});")[0]
        self.assertIn("setPointerCapture", down)
        move = self.wheel.split('canvas.addEventListener("pointermove"')[1].split("});")[0]
        self.assertIn("this.apply(local(event))", move)
        self.assertIn("this.onPreview?.(this.hex())", self.wheel)
        release = self.wheel.split("const release = (event) => {")[1].split("};")[0]
        self.assertIn("this.onCommit?.(this.hex())", release)

    def test_the_commit_goes_through_the_guarded_edit_route(self):
        block = self.js.split("wheel = new ColorWheel(")[1].split("});")[0]
        self.assertIn("applyColor(target, hex", block)
        apply_block = self.js.split("async function applyColor(target, hex, done) {")[1].split("\n}")[0]
        self.assertIn('"/studio/scene/edit"', apply_block)
        self.assertIn('op: "set_room_color"', apply_block)
        self.assertIn("action_id: crypto.randomUUID()", apply_block)
        self.assertIn("base_etag: etag", apply_block)

    def test_the_live_preview_never_writes_anything(self):
        """onPreview touches materials only. If it ever posted, a drag would
        write a revision per frame."""
        block = self.js.split("onPreview: (hex) => {")[1].split("},")[0]
        self.assertIn("room.previewRoomColor", block)
        self.assertNotIn("post(", block)
        self.assertNotIn("fetch(", block)

    def test_the_preview_recolours_in_place_without_rebuilding(self):
        block = self.room_viewer.split("previewRoomColor(target, hex) {")[1].split("\n  }")[0]
        self.assertIn("getObjectByName", block)
        self.assertNotIn("this.show(", block)
        self.assertIn('"wall_n", "wall_e", "wall_s", "wall_w"', block)

    def test_the_undo_still_works_for_a_wheel_change(self):
        """The wheel commits through applyColor, which is what records the
        undo, so there is one undo path and not two."""
        self.assertIn("lastColorChange = body.undo ?? null;", self.js)
        block = self.js.split("onCommit: (hex) => {")[1].split("},")[0]
        self.assertIn("applyColor(", block)

    def test_the_current_colour_is_shown_as_a_chip_a_hex_and_a_name(self):
        self.assertIn('id="wheel-chip"', self.markup)
        self.assertIn('id="wheel-hex"', self.markup)
        self.assertIn('id="wheel-name"', self.markup)
        self.assertIn('id="hex-input"', self.markup)

    def test_there_is_a_walls_floor_target_switch(self):
        self.assertIn('name="wheel-target" value="walls" checked', self.markup)
        self.assertIn('name="wheel-target" value="floor"', self.markup)

    def test_the_old_controls_are_all_still_there(self):
        for fragment in ('id="wall-swatches"', 'id="floor-swatches"',
                         'type="color" id="custom-color"', 'type="color" id="custom-floor"',
                         'id="color-command"', 'id="color-undo"'):
            self.assertIn(fragment, self.markup, fragment)

    def test_the_wheel_and_the_command_stay_in_sync_both_ways(self):
        self.assertIn("wheel.setHex(current);", self.js)      # file or swatch -> wheel
        self.assertIn("wheel?.setHex(result.hex);", self.js)  # typed command -> wheel
        self.assertIn("showWheelColor(value, {typing: true});", self.js)  # hex field -> wheel

    def test_a_poll_mid_drag_cannot_repaint_under_the_thumb(self):
        self.assertIn("let wheelPreview = null;", self.js)
        self.assertIn("if (wheel && !wheelPreview) {", self.js)

    def test_a_grey_keeps_the_hue_the_shopper_chose(self):
        """Otherwise the ring snaps to red every time somebody picks white."""
        block = self.wheel.split("setHex(hex) {")[1].split("\n  }")[0]
        self.assertIn("hsv.s === 0", block)


class ColorPanelTests(unittest.TestCase):
    def setUp(self):
        self.markup = (STATIC / "scene.html").read_text(encoding="utf-8")
        self.js = (STATIC / "scene.js").read_text(encoding="utf-8")
        self.colors = (STATIC / "scene-colors.js").read_text(encoding="utf-8")
        self.css = (STATIC / "scene.css").read_text(encoding="utf-8")

    def test_the_panel_is_on_the_scene_page(self):
        self.assertIn('id="colors-title"', self.markup)
        self.assertIn("Room colors", self.markup)
        self.assertIn('id="wall-swatches"', self.markup)
        self.assertIn('id="floor-swatches"', self.markup)

    def test_both_swatch_rows_carry_the_colours_asked_for(self):
        for name in ("White", "Warm grey", "Sage", "Olive", "Navy", "Terracotta", "Charcoal"):
            self.assertIn(f'["{name}"', self.colors, name)
        for name in ("Oak", "Walnut", "Light grey", "Dark grey", "Concrete"):
            self.assertIn(f'["{name}"', self.colors, name)

    def test_there_is_a_custom_colour_input_for_each_surface(self):
        self.assertIn('type="color" id="custom-color"', self.markup)
        self.assertIn('type="color" id="custom-floor"', self.markup)

    def test_there_is_a_typed_command_box(self):
        self.assertIn('id="color-command"', self.markup)
        self.assertIn("Or type: make the walls olive", self.markup)

    def test_there_is_an_undo_control(self):
        self.assertIn('id="color-undo"', self.markup)
        self.assertIn("lastColorChange", self.js)

    def test_a_swatch_writes_through_the_guarded_edit_route(self):
        block = self.js.split("async function applyColor(target, hex, done) {")[1].split("\n}")[0]
        self.assertIn('"/studio/scene/edit"', block)
        self.assertIn('op: "set_room_color"', block)
        self.assertIn("action_id: crypto.randomUUID()", block)
        self.assertIn("base_etag: etag", block)

    def test_the_undo_uses_the_colour_the_server_reported(self):
        self.assertIn("lastColorChange = body.undo ?? null;", self.js)

    def test_the_swatches_follow_whatever_the_file_says(self):
        self.assertIn("paintSwatches();", self.js)

    def test_the_panel_says_who_chose_the_colour(self):
        self.assertIn('"chosen_by_user"', self.js)
        self.assertIn("chosen by you", self.js)

    def test_the_colour_code_asks_no_model_anything(self):
        """A lookup, not an intent call. If this ever reached Gemini, the
        panel would stop working the moment the key expired, which is the
        state the key has been in throughout."""
        start = self.js.index("// Room colors")
        block = self.js[start:]
        for forbidden in ("gemini", "/studio/command", "interact", "decide"):
            self.assertNotIn(forbidden, block.lower())

    def test_the_parser_module_pulls_in_nothing(self):
        self.assertNotIn("import ", self.colors)
        self.assertNotIn("document", self.colors)


if __name__ == "__main__":
    unittest.main()
