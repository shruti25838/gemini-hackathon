"""Settings load correctly, including from a .env copied straight from the example.

The README and scripts/dev both say "cp .env.example .env". That file lists
every variable with no value, so every typed setting arrives as an empty
string. If that is not handled, the server cannot start on a fresh clone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.backend


@pytest.fixture(autouse=True)
def _isolate_from_the_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about what a .env file produces.

    A process environment variable outranks an env_file, which is correct, but
    it means a caller that exports MOCK_COMMERCE (scripts/e2e_mock.sh does)
    would otherwise change the outcome of a test about file handling.
    """
    for name in (
        "MOCK_AI",
        "MOCK_COMMERCE",
        "MOCK_ROOM_INPUT",
        "MOCK_AGORA",
        "SHOP_MCP",
        "ETHYNEON_PORT",
        "APPROVAL_TTL_SECONDS",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_STOREFRONT_TOKEN",
        "FIT_MIN_WALKWAY_WIDTH_M",
    ):
        monkeypatch.delenv(name, raising=False)


def test_a_env_copied_from_the_example_still_starts(tmp_path: Path) -> None:
    """The exact thing the README tells a teammate to do."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )

    settings = Settings(_env_file=str(env_file))

    assert settings.port == 8000
    assert settings.approval_ttl_seconds == 900
    assert settings.fit_clearance_margin_m == 0.05
    assert settings.shop_mcp_timeout_seconds == 20.0
    assert settings.shop_mcp_cache_ttl_ms_override is None


def test_blank_values_fall_back_to_defaults(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ETHYNEON_PORT=\nAPPROVAL_TTL_SECONDS=\nSHOPIFY_STOREFRONT_TOKEN=\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=str(env_file))
    assert settings.port == 8000
    assert settings.approval_ttl_seconds == 900
    assert settings.shopify_storefront_token is None


def test_real_values_are_still_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ETHYNEON_PORT=9001\nMOCK_COMMERCE=0\nFIT_MIN_WALKWAY_WIDTH_M=0.9\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=str(env_file))
    assert settings.port == 9001
    assert settings.mock_commerce is False
    assert settings.fit_min_walkway_width_m == 0.9


def test_everything_is_mocked_by_default(tmp_path: Path) -> None:
    """A fresh clone with no configuration runs fully offline."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    settings = Settings(_env_file=str(env_file))
    assert settings.mock_ai is True
    assert settings.mock_commerce is True
    assert settings.mock_room_input is True
    assert settings.mock_agora is True
    assert set(settings.active_mocks()) == {
        "MOCK_AI",
        "MOCK_COMMERCE",
        "MOCK_ROOM_INPUT",
        "MOCK_AGORA",
    }


def test_commerce_is_not_live_without_both_a_domain_and_a_token(tmp_path: Path) -> None:
    """Turning MOCK_COMMERCE off is not enough. Live needs real credentials,
    so a half-configured store can never be mistaken for a working one."""
    env_file = tmp_path / ".env"

    env_file.write_text("MOCK_COMMERCE=0\n", encoding="utf-8")
    assert Settings(_env_file=str(env_file)).commerce_is_live is False

    env_file.write_text(
        "MOCK_COMMERCE=0\nSHOPIFY_STORE_DOMAIN=example.myshopify.com\n", encoding="utf-8"
    )
    assert Settings(_env_file=str(env_file)).commerce_is_live is False

    env_file.write_text(
        "MOCK_COMMERCE=1\nSHOPIFY_STORE_DOMAIN=example.myshopify.com\n"
        "SHOPIFY_STOREFRONT_TOKEN=placeholder\n",
        encoding="utf-8",
    )
    assert Settings(_env_file=str(env_file)).commerce_is_live is False


def test_env_example_lists_every_setting_the_code_reads() -> None:
    """A setting the code reads but .env.example does not list is invisible to
    a teammate configuring the project."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    listed = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    for name, field in Settings.model_fields.items():
        alias = field.alias or name.upper()
        assert alias in listed, f"{alias} is read by the code but missing from .env.example"


def test_env_example_contains_no_values() -> None:
    """It is committed, so it must never carry a real credential."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        # Only the mock flags and feature switches carry a value, and those
        # are not secrets.
        if value.strip():
            assert key.startswith(("MOCK_", "SHOP_MCP", "ENABLE_", "LIVE_")), (
                f"{key} has a value in .env.example; it must be empty"
            )
            assert value.strip() in {"0", "1"}, f"{key} should be a 0/1 flag"
