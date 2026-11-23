"""The seam between lane C and lane A.

Lane C never imports lane A's implementation directly. It asks this registry
for whatever is currently installed, which is a labelled stub until Person A
ships the real thing.

Swapping in a teammate's code is one call:

    from backend.lane_a import set_lane_a
    from ai.karishma_adapter.provider import RealLaneA

    set_lane_a(RealLaneA())

Startup does not make that call directly. It calls
`install_lane_a_from_settings(settings)`, which decides from MOCK_AI and the
configured key and never raises, so a missing credential downgrades to the
labelled stub instead of failing the boot.

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


def install_lane_a_from_settings(settings: Any) -> str:
    """Decide which lane A to install, and say why in one line.

    Real lane A needs two things: MOCK_AI off, and a Gemini key configured.
    Anything less keeps the labelled stub, because a provider that cannot
    reach its model is worse than a stub that admits what it is.

    This never raises and never half-installs. set_lane_a verifies the
    provider against contracts/interfaces.py before replacing anything, so a
    partly finished adapter leaves the stub in place rather than failing some
    calls mid-demo. The returned string is printed at startup and contains no
    credential, only whether one was present.
    """
    if getattr(settings, "mock_ai", True):
        reset_lane_a()
        return "stub (MOCK_AI is on)"

    if not getattr(settings, "api_key", None):
        reset_lane_a()
        return "stub (MOCK_AI is off but no GOOGLE_API_KEY or GEMINI_API_KEY is set)"

    try:
        from ai.karishma_adapter.provider import RealLaneA
    except Exception as exc:  # pragma: no cover - import guard
        reset_lane_a()
        return f"stub (lane A adapter would not import: {type(exc).__name__})"

    try:
        set_lane_a(RealLaneA())
    except Exception as exc:
        reset_lane_a()
        return f"stub (lane A adapter refused to install: {type(exc).__name__})"

    real = sum(1 for value in lane_a_delegation().values() if value.startswith("real"))
    return f"{lane_a_name()}, {real} of {len(LANE_A_FUNCTIONS)} functions real"


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
