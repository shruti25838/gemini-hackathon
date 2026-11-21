"""Lane A: AI and agents.

Owns analyze_room, validate_room_spec, handle_command, plan_workflow,
run_workflow, run_checkout_gates and run_recovery, plus the Argon and Carbon
Managed Agents, the Decision 1 gates and the ADK Live guide.

Nothing here writes shared state. Every function returns a proposal
(PlacementReview, WorkflowPlan, RecoveryRun, a scene patch) and lane C's
commit_revision is the only writer.

The Protocol stubs these implement are in contracts/interfaces.py.
Lands in Phase 2.
"""
