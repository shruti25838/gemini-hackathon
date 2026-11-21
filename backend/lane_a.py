"""The seam between lane C and lane A.

Lane C never imports lane A's implementation directly. It asks this registry
for whatever is currently installed, which is a labelled stub until Person A
ships the real thing.

Swapping in a teammate's code is one call, made once at startup:

    from backend.lane_a import set_lane_a
    from ai.agents import RealLaneA          # Person A's module

    set_lane_a(RealLaneA())

Anything with the right methods works; there is no base class to inherit.
`check_lane_a(provider)` verifies a candidate against the Protocols in
contracts/interfaces.py before it is installed, so a mismatch fails loudly at
startup instead of halfway through a demo.

See docs/LANE_C_HANDOFF.md for the per-function swap instructions.
"""

from __future__ import annotations

from typing import Any, Optional

from ai.stubs import StubLaneA

# The seven functions lane A provides, from the team integration contract.
LANE_A_FUNCTIONS: tuple[str, ...] = (
    "analyze_room",
    "validate_room_spec",
    "handle_command",
    "plan_workflow",
    "run_workflow",
    "run_checkout_gates",
    "run_recovery",
)

_provider: Any = StubLaneA()


def get_lane_a() -> Any:
    """The currently installed lane A provider."""
    return _provider


def set_lane_a(provider: Any, *, verify: bool = True) -> None:
    """Install a lane A provider, replacing the stub.

    Raises before anything is installed if the provider is missing a function,
    so a half-finished module cannot silently leave some calls on the stub.
    """
    global _provider
    if verify:
        missing = check_lane_a(provider)
        if missing:
            raise TypeError(
                f"lane A provider {type(provider).__name__} is missing: "
                f"{', '.join(missing)}"
            )
    _provider = provider


def reset_lane_a() -> None:
    """Back to the stub. Used by the tests."""
    global _provider
    _provider = StubLaneA()


def check_lane_a(provider: Any) -> list[str]:
    """Names of the required functions this provider does not supply."""
    return [
        name
        for name in LANE_A_FUNCTIONS
        if not callable(getattr(provider, name, None))
    ]


def lane_a_is_stubbed() -> bool:
    """True while the labelled stub is installed. Surfaced in /api/health."""
    return bool(getattr(_provider, "is_stub", False))


def lane_a_name() -> str:
    return str(getattr(_provider, "name", type(_provider).__name__))


def lane_a_delegation() -> dict[str, str]:
    """What each of the seven functions actually is right now.

    A provider may be partly real and partly delegated. Saying which is
    which, in /api/health, is what stops a stub being demoed as an agent.
    """
    declared = getattr(_provider, "delegation", None)
    if isinstance(declared, dict):
        return {name: str(declared.get(name, "unknown")) for name in LANE_A_FUNCTIONS}
    stubbed = lane_a_is_stubbed()
    return {
        name: "stub" if stubbed else "provider"
        for name in LANE_A_FUNCTIONS
    }
