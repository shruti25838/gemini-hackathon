"""What the Studio's browser code is allowed to talk to.

The Studio pages are plain HTML, CSS and JavaScript served from this backend.
Nothing type-checks them and no bundler inspects their imports, so the one
property worth pinning in a test is where they send requests.

Two rules:

  every network target is either same-origin, meaning a route this backend or
  the Ethyneon server serves, or one of the named third-party libraries below

  the camera and demo-video controls on the room page read only from
  /studio/samples/rooms, the local CC0 sample route

The live camera and demo video controls came from feature/mitran. They are
additive: 231 lines across three files with nothing removed. The demo video
mode replays a CC0 sample set as if it were a camera feed, which is what makes
a screenshot of the scan flow possible without anyone's real room.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "backend" / "static"

# Third-party origins the pages may load a library from. Recorded in the
# README's asset table. Anything else is a finding.
ALLOWED_ORIGINS = {
    "cdn.jsdelivr.net",  # three.js and the Agora RTC SDK
    "mcp.shop.com",      # named in copy, not fetched from the browser
    "store.com",         # the Shop MCP host, in a comment
}

ABSOLUTE_URL = re.compile(r"https?://([a-zA-Z0-9.-]+)")
FETCH_TARGET = re.compile(r"""fetch\(\s*[`'"]([^`'"]+)""")


def static_files(*suffixes: str) -> list[Path]:
    return sorted(p for p in STATIC.iterdir() if p.suffix in suffixes)


class StaticFrontEndTests(unittest.TestCase):
    def test_the_pages_exist(self) -> None:
        for name in ("room.html", "scene.html", "studio.html", "voice.html", "room-chat.html"):
            with self.subTest(page=name):
                self.assertTrue((STATIC / name).is_file())

    def test_every_absolute_url_points_at_an_allowed_origin(self) -> None:
        for path in static_files(".html", ".js", ".css"):
            text = path.read_text(encoding="utf-8")
            for host in ABSOLUTE_URL.findall(text):
                with self.subTest(file=path.name, host=host):
                    self.assertIn(
                        host,
                        ALLOWED_ORIGINS,
                        f"{path.name} reaches {host}, which is not in the asset table",
                    )

    def test_every_fetch_is_same_origin(self) -> None:
        """A browser fetch must be a path on this backend, never an absolute URL."""
        for path in static_files(".js", ".html"):
            text = path.read_text(encoding="utf-8")
            for target in FETCH_TARGET.findall(text):
                with self.subTest(file=path.name, target=target):
                    self.assertFalse(
                        target.startswith(("http://", "https://", "//")),
                        f"{path.name} fetches an absolute URL",
                    )
                    self.assertTrue(
                        target.startswith(("/", "$", "{")),
                        f"{path.name} fetches {target!r}, which is not a rooted path",
                    )


class RoomPageCaptureControlTests(unittest.TestCase):
    """The live camera and demo video controls, taken from feature/mitran."""

    def setUp(self) -> None:
        self.markup = (STATIC / "room.html").read_text(encoding="utf-8")
        self.script = (STATIC / "room.js").read_text(encoding="utf-8")

    def test_the_controls_are_on_the_page(self) -> None:
        for element_id in (
            "live-toggle",
            "live-video",
            "live-stop",
            "demo-room",
            "demo-toggle",
        ):
            with self.subTest(id=element_id):
                self.assertIn(f'id="{element_id}"', self.markup)
                self.assertIn(element_id, self.script)

    def test_the_demo_video_reads_only_the_local_sample_route(self) -> None:
        self.assertIn("/studio/samples/rooms", self.script)
        for target in FETCH_TARGET.findall(self.script):
            if "samples" in target:
                with self.subTest(target=target):
                    self.assertTrue(target.startswith("/studio/samples/rooms"))

    def test_the_demo_video_degrades_instead_of_breaking(self) -> None:
        """With no samples committed, the control disables rather than throwing."""
        self.assertRegex(
            self.script,
            r"catch\(\(\)\s*=>\s*\{\s*byId\(\"demo-toggle\"\)\.disabled\s*=\s*true;",
        )
