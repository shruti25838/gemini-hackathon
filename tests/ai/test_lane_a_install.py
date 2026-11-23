"""Which lane A gets installed at startup, and why.

The point of this file is the negative cases. A demo fails honestly when the
stub is on and says so; it fails dishonestly when something calls itself an
agent and is not. So the rule is: real lane A needs MOCK_AI off and a Gemini
key present, and every path short of that keeps the labelled stub.

None of these tests reach the network. Installing RealLaneA only constructs
the adapter; her modules are imported lazily inside analyze_room, and no call
is made here.
"""

from __future__ import annotations

from typing import Optional

import pytest

from backend.config import Settings
from backend.lane_a import (
    LANE_A_FUNCTIONS,
    get_lane_a,
    install_lane_a_from_settings,
    lane_a_delegation,
    lane_a_is_stubbed,
    reset_lane_a,
)

pytestmark = pytest.mark.ai

FAKE_KEY = "not-a-real-key-" + "0" * 8


@pytest.fixture(autouse=True)
def _restore_the_stub():
    """Every test starts and ends on the stub, whatever it installed."""
    reset_lane_a()
    yield
    reset_lane_a()


def settings_for(*, mock_ai: bool, key: Optional[str]) -> Settings:
    return Settings(MOCK_AI=mock_ai, GOOGLE_API_KEY=key, GEMINI_API_KEY=None)


# ==========================================================================
# The stub stays on, in every case short of both conditions
# ==========================================================================


def test_mock_ai_on_keeps_the_stub_even_with_a_key() -> None:
    reason = install_lane_a_from_settings(settings_for(mock_ai=True, key=FAKE_KEY))

    assert lane_a_is_stubbed()
    assert "stub" in reason
    assert "MOCK_AI" in reason


def test_a_missing_key_keeps_the_stub_even_with_mock_ai_off() -> None:
    reason = install_lane_a_from_settings(settings_for(mock_ai=False, key=None))

    assert lane_a_is_stubbed()
    assert "stub" in reason
    assert "GOOGLE_API_KEY" in reason


def test_an_empty_key_counts_as_missing() -> None:
    """A blanked credential in the environment is not a credential."""
    reason = install_lane_a_from_settings(settings_for(mock_ai=False, key=""))

    assert lane_a_is_stubbed()
    assert "stub" in reason


def test_the_stub_reports_every_function_as_stub() -> None:
    install_lane_a_from_settings(settings_for(mock_ai=True, key=FAKE_KEY))

    assert lane_a_delegation() == {name: "stub" for name in LANE_A_FUNCTIONS}


# ==========================================================================
# With both conditions met, the real adapter goes in
# ==========================================================================


def test_mock_ai_off_with_a_key_installs_the_real_adapter() -> None:
    reason = install_lane_a_from_settings(settings_for(mock_ai=False, key=FAKE_KEY))

    assert not lane_a_is_stubbed()
    assert type(get_lane_a()).__name__ == "RealLaneA"
    assert "karishma" in reason


def test_the_real_adapter_declares_what_is_real_and_what_is_delegated() -> None:
    """A partly real provider has to say which parts, or a stub gets demoed."""
    install_lane_a_from_settings(settings_for(mock_ai=False, key=FAKE_KEY))
    delegation = lane_a_delegation()

    assert set(delegation) == set(LANE_A_FUNCTIONS)
    assert delegation["analyze_room"].startswith("real")
    assert all(
        delegation[name].startswith("delegated")
        for name in LANE_A_FUNCTIONS
        if name != "analyze_room"
    )
    assert "unknown" not in delegation.values()


def test_installing_is_reversible() -> None:
    install_lane_a_from_settings(settings_for(mock_ai=False, key=FAKE_KEY))
    assert not lane_a_is_stubbed()

    install_lane_a_from_settings(settings_for(mock_ai=True, key=FAKE_KEY))
    assert lane_a_is_stubbed()


# ==========================================================================
# Startup never breaks, and never prints a credential
# ==========================================================================


def test_a_broken_provider_leaves_the_stub_rather_than_raising(monkeypatch) -> None:
    class Incomplete:
        is_stub = False
        name = "incomplete"

        def analyze_room(self, image, session_id):  # pragma: no cover
            raise AssertionError("never called")

    import ai.karishma_adapter.provider as provider_module

    monkeypatch.setattr(provider_module, "RealLaneA", Incomplete)
    reason = install_lane_a_from_settings(settings_for(mock_ai=False, key=FAKE_KEY))

    assert lane_a_is_stubbed(), "a provider missing functions must not be installed"
    assert "refused to install" in reason


def test_the_reason_never_contains_the_key() -> None:
    for mock_ai in (True, False):
        reason = install_lane_a_from_settings(settings_for(mock_ai=mock_ai, key=FAKE_KEY))
        assert FAKE_KEY not in reason
        assert "not-a-real-key" not in reason
