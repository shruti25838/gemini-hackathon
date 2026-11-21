"""Lane A (AI and agents). Functions this lane provides, per the team integration contract.
C's server calls these; none of them writes shared state."""
from .commands import CommandResult, handle_command, plan_workflow  # noqa: F401
from .gates import checkout_audit as run_checkout_gates  # noqa: F401
from .recovery import run_recovery  # noqa: F401
from .room import DoorAnalysis, RoomAnalysis, ValidationResult, analyze_door, analyze_room, validate_room_spec  # noqa: F401
from .workflow import execute as run_workflow  # noqa: F401
