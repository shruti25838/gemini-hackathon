"""How the room is drawn, and the line between drawing it and storing it.

The first hero shot of this room was not postable. Its windows were flat
grey panels, a black void showed through the open ceiling, and every
surface was lit so evenly that the furniture read as cut paper. The fixes
are three: a painted sky behind each window, a ceiling drawn only from
below, and a lighting rig where one warm key does the shaping.

All three are dressing. They exist in the viewer and nowhere else, and that
is the thing worth pinning. A sky that quietly became a scene object would
be a window on to a place that does not exist, saved as though a shopper
had put it there, and a stored ceiling colour would be a measurement nobody
took. So: the scene file never gains a key for any of it, the server has no
word for any of it, and a colour edit cannot name the ceiling.

The second thing worth pinning is that Plan and Overview still work. They
are the two views people measure in, and the ceiling could easily have
hidden the room from above. It does not, and not because a view remembers
to switch it off: the plane is single sided with its normal pointing down,
so from above there is nothing there to draw. The sky is covered the same
way, by being smaller than the wall it stands behind. Neither needs a view
to know about it, which is the only version of this that cannot be got
wrong later.

room-viewer.js imports three.js, so node cannot execute it here and these
are source-level tests. What they cannot prove, the captures in
docs/screenshots do: 24b-hero-eye-level.png is the eye-level view and
25-hero-plan-labelled.png is the plan of the same room.
"""

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.scene import PresetRoom, PresetWindow, SceneStudio
from backend.scene_edit import SceneEdit

STUDIO_ROOT = Path(__file__).resolve().parents[2]
STATIC = STUDIO_ROOT / "backend" / "static"

# Words for things that are drawn and never stored. A scene file or a
# server module that knows any of them has crossed the line.
DRESSING = ("ceiling", "sky", "backdrop", "daylight", "emissive", "fov", "camera")


def run(coroutine):
    return asyncio.run(coroutine)


def every_key(value, found=None):
    """Every key anywhere in a nested structure."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            every_key(item, found)
    elif isinstance(value, list):
        for item in value:
            every_key(item, found)
    return found


class DressingStaysInTheViewerTests(unittest.TestCase):
    """Drawn, not stored, and with no route from one to the other."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")

    def scenes(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        scenes = SceneStudio(None, None, path=folder / "geometricconfig.json",
                             cache_dir=folder / "cache")
        scenes.write_preset_room(PresetRoom(
            width_m=4.0, depth_m=3.2, height_m=2.6,
            windows=(PresetWindow(wall="wall_n", offset_m=0.9, width_m=1.6, sill_m=0.85),),
        ))
        return scenes

    def test_a_room_with_a_window_stores_no_sky_and_no_ceiling(self):
        scenes = self.scenes()
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        keys = {key.lower() for key in every_key(config)}
        for word in DRESSING:
            self.assertFalse([key for key in keys if word in key],
                             f"{word} reached the scene file: {sorted(keys)}")

    def test_a_colour_edit_does_not_add_one_either(self):
        scenes = self.scenes()
        run(scenes.edit(SceneEdit(
            base_etag=scenes.read()[0],
            action_id="aaaaaaaa-0000-0000-0000-000000000001",
            op="set_room_color", target="walls", color="#a8b89a")))
        config = json.loads(scenes.path.read_text(encoding="utf-8"))
        keys = {key.lower() for key in every_key(config)}
        for word in DRESSING:
            self.assertFalse([key for key in keys if word in key], word)

    def test_the_server_has_no_word_for_any_of_it(self):
        """Nothing server side can write what it cannot name."""
        for name in ("scene.py", "scene_edit.py", "room.py", "room_lighting.py"):
            source = (STUDIO_ROOT / "backend" / name).read_text(encoding="utf-8")
            # Comments may discuss it. Code may not quote it as a key.
            code = "\n".join(line.split("#")[0] for line in source.splitlines())
            for word in ("ceiling", "backdrop", "daylight"):
                self.assertIsNone(
                    re.search(r"[\"']" + word + r"[\"']", code, re.I),
                    f"{name} names {word} as data")

    def test_a_colour_edit_cannot_name_the_ceiling(self):
        """The only painted surfaces are the ones the shopper can see
        measured: four walls and a floor. A ceiling colour would be an
        invention, so the boundary refuses the word outright."""
        with self.assertRaises(ValidationError):
            SceneEdit(base_etag="0" * 16,
                      action_id="aaaaaaaa-0000-0000-0000-000000000002",
                      op="set_room_color", target="ceiling", color="#ffffff")

    def test_the_viewer_posts_nothing_it_draws(self):
        """room-viewer.js renders. It has never had a way to write."""
        self.assertNotIn("fetch(", self.viewer)
        self.assertNotIn("/studio/scene", self.viewer)
        self.assertNotIn("XMLHttpRequest", self.viewer)

    def test_the_sky_is_painted_and_not_a_photograph(self):
        """Every other image in this app belongs to a merchant. This one is
        a canvas gradient, so it belongs to nobody and shows nowhere."""
        self.assertIn("createLinearGradient", self.viewer)
        self.assertNotIn("TextureLoader", self.viewer)
        self.assertNotIn("new Image(", self.viewer)
        self.assertIsNone(re.search(r"\.(jpe?g|png|webp|hdr|exr)\b", self.viewer, re.I))


class CeilingTests(unittest.TestCase):
    """One plane, drawn from below, invisible from above by its own geometry."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        head = self.viewer.split('ceiling.name = "ceiling"')[0]
        self.block = head.rsplit("const ceiling =", 1)[1]

    def test_it_is_single_sided(self):
        self.assertIn("side: THREE.FrontSide", self.block)
        self.assertNotIn("DoubleSide", self.block)

    def test_its_normal_points_down(self):
        """A plane's normal is +z; rotating +90 degrees about x turns that
        to -y. Facing down and single sided is what makes it disappear from
        every camera above the room, Plan and Overview included."""
        self.assertIn("ceiling.rotation.x = Math.PI / 2;", self.viewer)

    def test_it_casts_nothing(self):
        """A ceiling that cast shadows would put the room in the dark from
        above, which is exactly what Plan must not be."""
        self.assertIn("ceiling.castShadow = false;", self.viewer)

    def test_no_view_switches_it_on_or_off(self):
        """If visibility were a flag, some future view would forget it."""
        views = self.viewer.split("  view(name, candidate")[1]
        self.assertNotIn("ceiling", views)
        for method in ("setLabels(visible)", "setItems(visible)"):
            body = self.viewer.split(method)[1].split("\n  }")[0]
            self.assertNotIn("ceiling", body)


class SkyTests(unittest.TestCase):
    """Behind the glass, and nowhere else."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        body = self.viewer.split("  daylight(wall, frame, openings, height) {")[1]
        self.daylight = body.split("\n  ghostItem(")[0]

    def constant(self, name):
        return float(re.search("const " + name + r" = ([0-9.]+);", self.viewer).group(1))

    def test_it_stops_short_of_the_wall_top(self):
        """The leak that cost the first attempt: seen from above, a sky as
        tall as the wall shows over the roofline."""
        self.assertGreater(self.constant("SKY_HEADROOM_M"), 0.0)
        self.assertIn("height - SKY_HEADROOM_M", self.daylight)

    def test_it_sits_barely_outside_the_wall(self):
        self.assertLess(self.constant("SKY_SET_BACK_M"), 0.1)
        self.assertIn("WALL_THICKNESS + SKY_SET_BACK_M", self.daylight)

    def test_it_faces_into_the_room_and_only_into_the_room(self):
        """Single sided with its normal turned inward, so from outside the
        room there is nothing to draw even where the wall does not cover."""
        self.assertIn("side: THREE.FrontSide", self.daylight)
        self.assertIn("sky.lookAt(sky.position.clone().addScaledVector(frame.out, -1));",
                      self.daylight)

    def test_it_runs_the_whole_length_of_its_wall(self):
        self.assertIn("new THREE.PlaneGeometry(frame.length + extend * 2, top - bottom)",
                      self.daylight)

    def test_one_sky_per_wall_and_not_one_per_window(self):
        """Two coplanar skies on one wall would fight for the same pixels."""
        before, after = self.daylight.split("for (const opening of windows) {")
        self.assertIn("group.add(sky);", before)
        self.assertNotIn("group.add(sky);", after)


class LightOnTheFloorTests(unittest.TestCase):
    """A warm patch inside each window, which blocks nothing."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        self.scene_viewer = (STATIC / "scene-viewer.js").read_text(encoding="utf-8")
        tail = self.viewer.split("const patch = new THREE.Mesh(")[1]
        self.patch = tail.split("group.add(patch);")[0]

    def test_it_is_drawn_over_the_floor_without_owning_it(self):
        self.assertIn("depthWrite: false", self.patch)
        self.assertIn("blending: THREE.AdditiveBlending", self.patch)

    def test_nothing_can_be_dragged_into_it(self):
        """The grab volumes live on their own layer. The patch is on none,
        so the raycaster never sees it and a product never collides with a
        picture of light."""
        self.assertNotIn("layers", self.patch)
        self.assertNotIn("hitVolume", self.patch)

    def test_a_window_is_still_not_an_obstacle(self):
        """Unchanged, and re-checked here because this change put something
        new on the floor in front of every window."""
        zones = self.scene_viewer.split("export function openingZones(scene) {")[1]
        zones = zones.split("\n}")[0]
        self.assertIn('if (opening.type === "window") continue;', zones)


class PlanAndOverviewTests(unittest.TestCase):
    """The two views people work in, framed exactly as before."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        self.view = self.viewer.split("  view(name, candidate")[1]

    def test_plan_keeps_its_camera(self):
        self.assertIn(
            "this.camera.position.set(width / 2, span * 1.75 + height, depth / 2 + 0.001);",
            self.view)

    def test_overview_keeps_its_camera(self):
        self.assertIn(
            "this.camera.position.set(width * 1.25, height + span * 0.95, depth + span * 0.95);",
            self.view)

    def test_only_eye_level_widens_the_lens(self):
        """60 to 70 degrees suits a room photograph and ruins a plan."""
        self.assertIn("let fov = PLAN_FOV;", self.view)
        branch = self.view.split('} else if (name === "eye") {')[1].split("} else {")[0]
        self.assertIn("fov = chosen.fov;", branch)
        # Two writes to the local, and no third: the default, and the eye
        # branch. `this.camera.fov` is excluded by the lookbehind, because
        # that is the one place the chosen value is applied.
        self.assertEqual(len(re.findall(r"(?<![.\w])fov = ", self.view)), 2)

    def test_the_lens_change_is_applied_once_and_declared(self):
        self.assertIn("this.camera.fov = fov;", self.view)
        self.assertEqual(len(re.findall(r"this\.camera\.fov = ", self.view)), 1)
        self.assertIn("this.camera.updateProjectionMatrix();", self.view)


class EyeLevelTests(unittest.TestCase):
    """Three vantages, and a way to compare them that reaches nothing else."""

    PATTERN = r"\{fov: ([\d.]+), eye: \[([^\]]+)\], look: \[([^\]]+)\]\}"

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")
        self.js = (STATIC / "scene.js").read_text(encoding="utf-8")
        block = self.viewer.split("const EYE_VIEWS = [")[1].split("];")[0]
        self.views = [
            {"fov": float(match.group(1)),
             "eye": [float(value) for value in match.group(2).split(",")],
             "look": [float(value) for value in match.group(3).split(",")]}
            for match in re.finditer(self.PATTERN, block)
        ]

    def test_there_are_three_of_them(self):
        self.assertEqual(len(self.views), 3, self.views)

    def test_every_lens_is_a_room_photograph(self):
        for view in self.views:
            self.assertGreaterEqual(view["fov"], 60, view)
            self.assertLessEqual(view["fov"], 70, view)

    def test_every_one_stands_at_eye_height_inside_the_room(self):
        """x and z are fractions of the room so a vantage survives a room of
        another size; y is metres, because eye level does not scale."""
        for view in self.views:
            self.assertAlmostEqual(view["eye"][1], 1.6, delta=0.1)
            for axis in (0, 2):
                self.assertGreater(view["eye"][axis], 0.0, view)
                self.assertLess(view["eye"][axis], 1.0, view)
                self.assertGreater(view["look"][axis], 0.0, view)
                self.assertLess(view["look"][axis], 1.0, view)

    def test_a_short_room_still_fits_a_person_in_it(self):
        self.assertIn("Math.min(chosen.eye[1], height - 0.25)", self.viewer)

    def test_the_eye_parameter_reaches_the_camera_and_nothing_else(self):
        self.assertIn('new URLSearchParams(location.search).get("eye")', self.js)
        self.assertEqual(len(re.findall("EYE_CANDIDATE", self.js)), 2,
                         "read once, used once")
        self.assertIn("room.view(button.dataset.view, EYE_CANDIDATE)", self.js)

    def test_an_absent_or_nonsense_value_gives_the_first_vantage(self):
        self.assertIn('Number(new URLSearchParams(location.search).get("eye")) || 0', self.js)
        self.assertIn("EYE_VIEWS[candidate] ?? EYE_VIEWS[0]", self.viewer)


class LightingTests(unittest.TestCase):
    """One key that shapes, one fill that does not."""

    def setUp(self):
        self.viewer = (STATIC / "room-viewer.js").read_text(encoding="utf-8")

    def test_the_fill_casts_no_shadow_of_its_own(self):
        """Two shadow casters in a room with one window is a lie about where
        the light comes from."""
        block = self.viewer.split("this.fill = new THREE.DirectionalLight")[1]
        block = block.split("this.scene.add(")[0]
        self.assertNotIn("castShadow", block)

    def test_the_fill_is_weaker_than_the_key(self):
        key = re.search(r"this\.sun = new THREE\.DirectionalLight\(0x[0-9a-f]+, ([\d.]+)\)",
                        self.viewer)
        fill = re.search(r"this\.fill = new THREE\.DirectionalLight\(0x[0-9a-f]+, ([\d.]+)\)",
                         self.viewer)
        self.assertLess(float(fill.group(1)), float(key.group(1)) / 2)

    def test_the_key_comes_from_outside_the_window(self):
        self.assertIn("addScaledVector(frames[window.wall].out, 2.6)", self.viewer)

    def test_the_glass_is_clear_enough_to_show_what_is_behind_it(self):
        """At the old 0.55 the pane was a film over whatever lay beyond, and
        with nothing beyond it that film was the grey slab to be fixed."""
        glass = self.viewer.split('if (opening.type === "window") {')[1]
        glass = glass.split("glass.position")[0]
        self.assertLess(float(re.search(r"opacity: ([\d.]+)", glass).group(1)), 0.3)


if __name__ == "__main__":
    unittest.main()
