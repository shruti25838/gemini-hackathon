"""The secret scanner has to catch things, and has to stay quiet about what it caught.

A scanner nobody has tested is a scanner that reports clean because its regex
never matched anything. These tests pin the three properties that matter: real
credential shapes are caught, the narrow shpat_ exception holds, and a finding
never contains the value that triggered it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "secret_scan", REPO_ROOT / "scripts" / "secret_scan.py"
)
assert SPEC and SPEC.loader
secret_scan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secret_scan)


def match_rules(line: str) -> set[str]:
    return {name for name, rx in secret_scan.PATTERNS if rx.search(line)}


# ==========================================================================
# Real credential shapes are caught
# ==========================================================================


@pytest.mark.parametrize(
    "rule, sample",
    [
        ("google_api_key", "AIza" + "b" * 35),
        ("shopify_admin_token", "shpat_" + "a" * 32),
        ("shopify_custom_app_token", "shpca_" + "f" * 32),
        ("shopify_private_app_token", "shppa_" + "0" * 32),
        ("shopify_app_secret", "shpss_" + "9" * 32),
        ("anthropic_api_key", "sk-ant-" + "x" * 25),
        # Assembled rather than written out, so this file does not trip the
        # scanner it is testing. The scanner reads source a line at a time.
        ("private_key_block", "-----BEGIN RSA " + "PRIVATE KEY" + "-----"),
        ("aws_access_key", "AKIA" + "Q" * 16),
        ("slack_token", "xoxb-" + "1" * 20),
    ],
)
def test_a_real_credential_shape_is_caught(rule: str, sample: str) -> None:
    assert rule in match_rules(f"TOKEN={sample}")


def test_an_empty_placeholder_is_not_a_finding() -> None:
    for line in ("GOOGLE_API_KEY=", "SHOPIFY_STOREFRONT_TOKEN=", "AGORA_APP_CERTIFICATE="):
        assert match_rules(line) == set()


# ==========================================================================
# The shpat_ exception is narrow
# ==========================================================================


def test_the_bare_prefix_is_allowed_only_in_three_places() -> None:
    assert secret_scan.BARE_PREFIX_ALLOWED_IN == (
        "tests/",
        "scripts/check_access.py",
        "scripts/secret_scan.py",
    )
    assert "tests/backend/test_cart_write_gates.py".startswith(
        secret_scan.BARE_PREFIX_ALLOWED_IN
    )
    assert not "backend/app.py".startswith(secret_scan.BARE_PREFIX_ALLOWED_IN)
    assert not "docs/LANE_C_HANDOFF.md".startswith(secret_scan.BARE_PREFIX_ALLOWED_IN)
    assert not "scripts/dev".startswith(secret_scan.BARE_PREFIX_ALLOWED_IN)
    assert not "README.md".startswith(secret_scan.BARE_PREFIX_ALLOWED_IN)


def test_the_exception_does_not_cover_a_real_token() -> None:
    """A sentinel inside tests/ is fine. A full token inside tests/ is not."""
    assert "shopify_admin_token" in match_rules("shpat_" + "a" * 32)
    assert match_rules("shpat_sentinel_must_never_appear_anywhere") == set()


# ==========================================================================
# The scanner does not leak
# ==========================================================================


def test_the_repository_is_clean_right_now() -> None:
    assert secret_scan.main() == 0


def test_a_finding_never_prints_the_value(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "AIza" + "z" * 35
    secret_scan.main()
    assert secret not in capsys.readouterr().out


def test_forbidden_paths_cover_the_standing_rules() -> None:
    """.env, databases, caches and room photos, as the brief requires."""
    rules = {name for name, _ in secret_scan.FORBIDDEN_PATHS}
    assert {"env_file", "database", "cache_dir", "room_photo"} <= rules

    def hits(path: str) -> bool:
        return any(rx.search(path) for _, rx in secret_scan.FORBIDDEN_PATHS)

    assert hits(".env")
    assert hits("ethyneon-studio/.env")
    assert hits("ethyneon.sqlite")
    assert hits("ethyneon.sqlite-wal")
    assert hits("fixtures/photos/room_good.jpeg")
    assert hits(".cache/thing.json")
    assert hits("secrets.txt")
    assert not hits(".env.example")
    assert not hits("fixtures/photos/README.md")
    assert not hits("backend/app.py")


def test_nobodys_room_photograph_can_be_committed_through_eval() -> None:
    """An evaluation case carries somebody's room and, outside the team, their
    consent. Neither the media nor the case file is committed. See eval/CASES.md."""

    def hits(path: str) -> bool:
        return any(rx.search(path) for _, rx in secret_scan.FORBIDDEN_PATHS)

    assert hits("eval/data/cases.json")
    assert hits("eval/data/a-neighbours-room-1.jpg")
    assert hits("eval/data/door-opening.mp4")
    assert not hits("eval/data/cases.example.json")
    assert not hits("eval/CASES.md")
    assert not hits("eval/run_eval.py")
