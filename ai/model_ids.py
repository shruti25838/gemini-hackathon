"""Verified Gemini model identifiers, in one place.

Source: "Build with Gemini 4: hackathon builder guide", November 2026, which
instructs teams to keep this dictionary in one shared module so nobody guesses
a retired name. Every string below is copied from that guide, not inferred.

Two things worth knowing before using these:

  The Decisions API and Managed Agents live on the v1beta API version. Model
  calls use the stable v1.

  The guide says to install google-genai>=3.2.0 for the Decisions client.
  That release does not exist on PyPI: the newest published version is 2.33.0,
  which is what is installed, and it has no client.decisions attribute. So the
  Decisions call is made as a plain v1beta HTTP request instead. See
  backend/decisions.py.
"""

from __future__ import annotations

MODEL_IDS: dict[str, str] = {
    # Gemini 4 reasoning and coding models (1M unified context, long decode)
    "frontier_deep": "gemini-4-carbon",
    "frontier_fast": "gemini-4-neon",
    "fast_workhorse": "gemini-4-hydrogen",
    "frontier_argon": "gemini-4-argon",
    "text_fast_lite": "gemini-3.5-flash-lite",
    # Calibrated multimodal routing, gating and rubric scoring (v1beta)
    "decisions": "gemini-decision-1",
    # Managed agents (v1beta)
    "antigravity_agent": "antigravity-preview-09-2026",
    "deep_research": "deep-research-preview-04-2026",
    "deep_research_max": "deep-research-max-preview-04-2026",
    # Live voice, camera, transcription, translation and speech
    "live_voice_vision": "gemini-3.8-live",
    "live_extended_thinking": "gemini-3.8-live-extended-thinking",
    "transcribe_batch": "gemini-3.5-transcribe",
    "transcribe_live": "gemini-3.5-transcribe-live",
    "live_translate": "gemini-3.5-live-translate-preview",
    "tts_studio": "gemini-3.8-flash-tts",
    "tts_fast": "gemini-3.8-flash-lite-tts",
    # Native images, video with audio, and music
    "image_default": "gemini-nano-banana-2.1",
    "image_pro": "gemini-3-pro-image",
    "image_fast": "gemini-3.1-flash-lite-image",
    "video_audio": "gemini-omni-1.2-flash",
    "music_song": "lyria-3.5",
    "music_loop": "lyria-3-clip-preview",
    # Open-weight Gemma 4 models on the Interactions API
    "gemma_dense": "gemma-4-31b-it",
    "gemma_moe": "gemma-4-26b-a4b-it",
}

# Which Ethyneon role uses which model, from PRD v3.0.
ROLE_MODELS: dict[str, str] = {
    "live_guide": MODEL_IDS["live_voice_vision"],
    "catalog_scout": MODEL_IDS["fast_workhorse"],
    "room_investigator": MODEL_IDS["frontier_fast"],
    "workflow_planner": MODEL_IDS["frontier_fast"],
    "layout_planner_argon": MODEL_IDS["frontier_argon"],
    "independent_tester_carbon": MODEL_IDS["frontier_deep"],
    "checkout_gates": MODEL_IDS["decisions"],
    "visual_preview": MODEL_IDS["image_default"],
    "ambient_audio": MODEL_IDS["music_loop"],
}

# The three Decisions API question types. "noul" is the yes/no type.
DECISION_QUESTION_TYPES = ("noul", "choice", "score")

# Thinking levels. Gemini 4 Neon and Hydrogen take four; Carbon and Argon take
# three and default to medium. Neither Neon nor Hydrogen accepts "minimal".
THINKING_LEVELS = ("low", "medium", "high", "xhigh")

API_VERSION_STABLE = "v1"
API_VERSION_BETA = "v1beta"

GENERATIVE_LANGUAGE_BASE = "https://generativelanguage.googleapis.com"

__all__ = [
    "MODEL_IDS",
    "ROLE_MODELS",
    "DECISION_QUESTION_TYPES",
    "THINKING_LEVELS",
    "API_VERSION_STABLE",
    "API_VERSION_BETA",
    "GENERATIVE_LANGUAGE_BASE",
]
