"""The README has to be true.

Before this file existed the README stated three different offline test counts
in three places, 461, 444 and 366, and the real number was none of them. It
also credited sample room photographs that were not committed, and gave a lane
A import path that did not exist. Every one of those is the kind of thing a
reader checks first and a maintainer never re-reads.

So the claims that can be checked mechanically are checked here. A number in
the README is now a thing that fails the build when it drifts, which is the
only way a number in a README stays correct.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"
SUMMARY = REPO_ROOT / "docs" / "PROJECT_SUMMARY.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collected_offline_tests() -> int:
    """Ask pytest how many tests it collects, in a subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    counts = re.findall(r"^\S+\.py: (\d+)$", result.stdout, flags=re.MULTILINE)
    assert counts, f"could not read a collection count from pytest:\n{result.stdout[-2000:]}"
    return sum(int(n) for n in counts)


# ==========================================================================
# The numbers
# ==========================================================================


def studio_claim() -> int:
    """The Studio count as the README's status line states it."""
    match = re.search(r"(\d+) in the Studio", read(README))
    assert match, "the status line should state the Studio count"
    return int(match.group(1))


def test_the_offline_test_count_is_correct() -> None:
    """Every "N offline" in the README is either this suite's or the Studio's."""
    actual = collected_offline_tests()
    claimed = [int(n) for n in re.findall(r"(\d{2,4}) offline", read(README))]

    assert claimed, "the README should state the offline test count"
    assert actual in claimed, (
        f"pytest collects {actual} offline tests; the README claims {sorted(set(claimed))}. "
        f"Update every mention, including the status line at the top."
    )
    stray = sorted({n for n in claimed if n not in (actual, studio_claim())})
    assert not stray, f"the README states {stray} offline tests somewhere, which is neither suite"


def test_the_summary_agrees_with_the_readme() -> None:
    actual = collected_offline_tests()
    assert f"{actual} pass offline" in read(SUMMARY), (
        f"docs/PROJECT_SUMMARY.md should say {actual} for the root suite"
    )


def test_the_two_suite_counts_add_up() -> None:
    """The headline total has to be the sum of its parts."""
    text = read(README)
    headline = re.search(r"\*\*(\d+) tests pass offline across the two\s+suites: (\d+) here and (\d+) in the Studio\*\*", text)

    assert headline, "the status line should state the total and both suite counts"
    total, root, studio = (int(g) for g in headline.groups())
    assert root + studio == total, f"{root} + {studio} is not {total}"
    assert root == collected_offline_tests()


def test_the_network_marked_count_is_correct() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "network", "--collect-only", "-q",
         "-p", "no:cacheprovider"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    actual = sum(int(n) for n in re.findall(r"^\S+\.py: (\d+)$", result.stdout, flags=re.MULTILINE))

    assert f"{actual} live checks" in read(README)


# ==========================================================================
# The links and the claims about files
# ==========================================================================


@pytest.mark.parametrize("document", [README, SUMMARY], ids=["README", "PROJECT_SUMMARY"])
def test_every_relative_link_resolves(document: Path) -> None:
    links = re.findall(r"\]\((?!https?:|#)([^)]+)\)", read(document))
    assert links, f"{document.name} should link to something"

    for link in links:
        # Relative to the document, not the repository root: PROJECT_SUMMARY
        # lives in docs/ and links out with ../
        target = (document.parent / link.split("#", 1)[0]).resolve()
        assert target.exists(), f"{document.name} links to {link}, which does not exist"


def test_the_lane_a_import_path_is_real() -> None:
    """The old README told people to import ai.agents, which never existed."""
    text = read(README)
    assert "from ai.karishma_adapter.provider import RealLaneA" in text
    assert "from ai.agents import" not in text

    import ai.karishma_adapter.provider as provider

    assert hasattr(provider, "RealLaneA")


def test_the_asset_table_only_credits_assets_that_are_here() -> None:
    text = read(README)
    rooms = REPO_ROOT / "ethyneon-studio" / "samples" / "rooms"

    assert "samples/rooms/" in text
    assert (rooms / "manifest.json").is_file(), "the README credits the manifest"

    # Assets that are deliberately absent must not be credited as present.
    assert "shopify-walnut-side-table" not in text
    assert "to be recorded" not in text
    assert "to be chosen" not in text


def test_the_licence_position_is_stated() -> None:
    """No LICENSE file is deliberate, so the README has to say so."""
    text = read(README)

    assert "not licensed for\nreuse" in text or "not licensed for reuse" in text
    assert not (REPO_ROOT / "LICENSE").exists(), (
        "a LICENSE file appeared; update the README section that explains its absence"
    )


def test_the_duplicate_pdfs_are_gone() -> None:
    assert not (REPO_ROOT / "Ethyneon_PRD_v3.0.pdf").exists()
    assert (REPO_ROOT / "docs" / "Ethyneon_PRD_v3.0.pdf").is_file()


# ==========================================================================
# The fit comparison, quoted in both documents
# ==========================================================================


def test_the_fit_engine_really_does_match_every_golden() -> None:
    """Both documents cite 15 of 15 as evidence. Check it rather than trust it."""
    import json

    from fit.cli import run_case

    cases = sorted((REPO_ROOT / "fixtures" / "fit_cases").glob("*.json"))
    assert len(cases) == 15, f"the documents say 15 golden cases, there are {len(cases)}"

    matched = 0
    for path in cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        if run_case(case["input"])["fit_report"]["result"] == case["expected"]["result"]:
            matched += 1

    assert matched == 15, f"fit/ matches {matched} of 15, both documents claim 15"
    assert "15 of 15" in read(SUMMARY)
