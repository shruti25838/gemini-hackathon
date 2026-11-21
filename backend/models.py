"""Single source of model IDs (from the hackathon builder guide)."""
import os

NEON = os.getenv("MODEL_NEON", "gemini-4-neon")          # RoomSpec, door video, workflow planner
HYDROGEN = os.getenv("MODEL_HYDROGEN", "gemini-4-hydrogen")  # voice commands, fast turns
CARBON = os.getenv("MODEL_CARBON", "gemini-4-carbon")      # independent tester (Managed Agent)
ARGON = os.getenv("MODEL_ARGON", "gemini-4-argon")         # layout proposer + repair (Managed Agent)
DECISION = os.getenv("MODEL_DECISION", "gemini-decision-1")  # gates and audits
ANTIGRAVITY_AGENT = os.getenv("ANTIGRAVITY_AGENT", "antigravity-preview-09-2026")
