"""The CC0 sample room sets, and the route that serves them.

These photo sets are the only room imagery in the repository. They exist so a
demo never needs anyone's real room, and so the photo gate can be shown working
on inputs whose provenance is recorded rather than asserted.

Two things are tested here. The route behaves, including refusing a path that
tries to climb out of the samples folder. And every set still carries the
provenance the manifest promises, because an asset table in a README is only
true while the manifest backing it is intact.

The photo sets and the generator that built them are Mitran's work, taken from
feature/mitran. The panoramas they are cut from are CC0 from Poly Haven.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "rooms"
MANIFEST = SAMPLES / "manifest.json"

# Matches the browser the Studio pages are opened from.
LOCAL = {"base_url": "http://127.0.0.1:8000", "client": ("127.0.0.1", 50898)}


def local_client() -> TestClient:
    return TestClient(create_app(Settings(connection_token="test-studio-key")), **LOCAL)


class SampleRoomAssetTests(unittest.TestCase):
    def test_the_manifest_exists_and_covers_every_committed_set(self) -> None:
        self.assertTrue(MANIFEST.is_file(), "samples/rooms/manifest.json is missing")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertTrue(manifest, "the manifest is empty")

        on_disk = {path.name for path in SAMPLES.iterdir() if path.is_dir()}
        described = {entry["folder"] for entry in manifest}
        self.assertEqual(
            on_disk,
            described,
            "every committed folder must be described, and nothing described may be missing",
        )

    def test_every_set_records_its_source_and_licence(self) -> None:
        """The README's asset table is only true while this holds."""
        for entry in json.loads(MANIFEST.read_text(encoding="utf-8")):
            with self.subTest(folder=entry["folder"]):
                self.assertEqual(entry["license"], "CC0 1.0")
                self.assertTrue(entry["source"].startswith("https://polyhaven.com/"))
                self.assertTrue(entry["plain_room"], "sets must be plain, empty rooms")
                self.assertGreaterEqual(entry["photos"], 2)

    def test_no_set_contains_anything_but_numbered_jpegs(self) -> None:
        """Nothing stray, and in particular no personal photograph."""
        for folder in (path for path in SAMPLES.iterdir() if path.is_dir()):
            names = sorted(child.name for child in folder.iterdir())
            with self.subTest(folder=folder.name):
                self.assertEqual(
                    names,
                    [f"photo-{index}.jpg" for index in range(1, len(names) + 1)],
                )

    def test_the_generated_product_models_are_not_committed(self) -> None:
        """samples/models and samples/catalog stay out: no licence covers them.

        The question is what git tracks, not what is on this disk. Whoever
        generated those assets keeps them locally, and .gitignore is what has
        to hold. So this asks git, and skips where git cannot answer.
        """
        try:
            tracked = subprocess.run(
                ["git", "ls-files", "--", "samples"],
                cwd=SAMPLES.parents[1],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.split()
        except (OSError, subprocess.CalledProcessError):  # pragma: no cover
            self.skipTest("git is not available here")

        self.assertTrue(tracked, "the sample rooms should be tracked")
        for path in tracked:
            with self.subTest(path=path):
                self.assertTrue(
                    path.startswith("samples/rooms/"),
                    f"{path} is tracked but only samples/rooms/ may be",
                )


class SampleRoomRouteTests(unittest.TestCase):
    def test_the_listing_names_each_set_and_its_photos(self) -> None:
        with local_client() as browser:
            reply = browser.get("/studio/samples/rooms")
            self.assertEqual(reply.status_code, 200, reply.text)
            sets = reply.json()

        self.assertTrue(sets)
        for entry in sets:
            with self.subTest(folder=entry["folder"]):
                self.assertTrue(entry["photos"])
                self.assertTrue(all(name.startswith("photo-") for name in entry["photos"]))

    def test_a_photo_is_served_as_a_jpeg(self) -> None:
        with local_client() as browser:
            first = browser.get("/studio/samples/rooms").json()[0]
            reply = browser.get(f"/studio/samples/rooms/{first['folder']}/{first['photos'][0]}")

        self.assertEqual(reply.status_code, 200)
        self.assertEqual(reply.headers["content-type"], "image/jpeg")
        self.assertTrue(reply.content.startswith(b"\xff\xd8"), "not a JPEG")

    def test_a_missing_photo_is_a_clean_404(self) -> None:
        with local_client() as browser:
            reply = browser.get("/studio/samples/rooms/kitchen-5-photos/photo-9.jpg")
        self.assertEqual(reply.status_code, 404)

    def test_the_route_cannot_be_walked_out_of_the_samples_folder(self) -> None:
        attempts = [
            "/studio/samples/rooms/../../backend/config.py",
            "/studio/samples/rooms/kitchen-5-photos/../../../transfer.md",
            "/studio/samples/rooms/Kitchen-5-Photos/photo-1.jpg",
            "/studio/samples/rooms/kitchen-5-photos/manifest.json",
        ]
        with local_client() as browser:
            for attempt in attempts:
                with self.subTest(path=attempt):
                    self.assertNotEqual(browser.get(attempt).status_code, 200)

    def test_the_samples_route_is_local_only(self) -> None:
        app = create_app(Settings(connection_token="test-studio-key"))
        with TestClient(app) as remote:
            self.assertEqual(remote.get("/studio/samples/rooms").status_code, 403)
