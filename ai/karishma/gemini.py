"""Thin wrappers over the Gemini Interactions and Decisions APIs.

Rules from the builder guide that this module enforces:
- never send temperature / top_p / top_k / thinking budgets to Gemini 4
- never send thinking_level="minimal" to Neon or Hydrogen
- structured output through response_format JSON schema, never parsed prose
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from functools import lru_cache
from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("ethyneon.gemini")
T = TypeVar("T", bound=BaseModel)

INLINE_LIMIT = 15 * 1024 * 1024


class GeminiError(RuntimeError):
    pass


@lru_cache
def client():
    from google import genai

    return genai.Client()


@lru_cache
def beta_client():
    from google import genai

    return genai.Client(http_options={"api_version": "v1beta"})


# ---------- content helpers ----------
def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"type": "image", "data": base64.b64encode(data).decode(), "mime_type": mime}


def audio_part(data: bytes, mime: str = "audio/webm") -> dict:
    return {"type": "audio", "data": base64.b64encode(data).decode(), "mime_type": mime}


def video_part(data: bytes, mime: str = "video/mp4", agentic: bool = True) -> dict:
    part: dict[str, Any] = {"type": "video", "mime_type": mime}
    if len(data) > INLINE_LIMIT:
        import io

        f = client().files.upload(file=io.BytesIO(data), config={"mime_type": mime})
        part["uri"] = f.uri
    else:
        part["data"] = base64.b64encode(data).decode()
    if agentic:
        part["processing"] = "agentic"
    return part


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


# ---------- JSON schema for structured output ----------
def inline_schema(model: Type[BaseModel]) -> dict:
    """Pydantic schema with $refs inlined and unsupported keys stripped."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            out = {}
            for k, v in node.items():
                if k in ("title", "default"):
                    continue
                out[k] = resolve(v)
            return out
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)


def _check_thinking(model: str, thinking: str | None):
    if thinking == "minimal":
        raise ValueError("thinking_level 'minimal' is not allowed on Gemini 4 models")


def generate_structured(
    model: str,
    parts: list[dict] | str,
    out: Type[T],
    *,
    system: str | None = None,
    thinking: str | None = "medium",
    retries: int = 1,
) -> tuple[T, dict]:
    """One Interactions call that must return JSON matching `out`.

    Returns (parsed, meta). Retries once on malformed output, then raises.
    """
    _check_thinking(model, thinking)
    if mock_ai():
        return _mock_output(out), {"model": model, "latency_ms": 0, "interaction_id": "mock", "source": "mock"}
    kwargs: dict[str, Any] = dict(
        model=model,
        input=parts,
        response_format={"type": "text", "mime_type": "application/json", "schema": inline_schema(out)},
    )
    if system:
        kwargs["system_instruction"] = system
    if thinking:
        kwargs["generation_config"] = {"thinking_level": thinking}

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            r = client().interactions.create(**kwargs)
            text = r.output_text or ""
            parsed = out.model_validate_json(text)
            meta = {
                "model": model,
                "latency_ms": int((time.time() - t0) * 1000),
                "interaction_id": r.id,
                "usage": r.usage.model_dump() if getattr(r, "usage", None) else None,
            }
            return parsed, meta
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = e
            log.warning("malformed output from %s (attempt %d): %s", model, attempt, e)
        except Exception as e:  # provider error: retry once
            last_err = e
            log.warning("provider error from %s (attempt %d): %s", model, attempt, e)
    raise GeminiError(f"{model} failed after {retries + 1} attempts: {last_err}")


# ---------- Decisions API ----------
def decide(parts: list[dict] | str, questions: dict[str, dict]) -> tuple[dict, dict]:
    """Run Decision 1 over shared input. Returns ({qid: answer_dict}, meta)."""
    from .models import DECISION

    if mock_ai():
        return _mock_decide(questions), {"model": DECISION, "latency_ms": 0, "source": "mock"}
    t0 = time.time()
    r = beta_client().decisions.create(model=DECISION, input=parts, questions=questions)
    answers = {k: (v.model_dump() if hasattr(v, "model_dump") else dict(v)) for k, v in (r.answers or {}).items()}
    meta = {"model": DECISION, "latency_ms": int((time.time() - t0) * 1000)}
    if getattr(r, "prompt_feedback", None):
        meta["prompt_feedback"] = r.prompt_feedback.model_dump()
    return answers, meta


def mock_ai() -> bool:
    return os.getenv("MOCK_AI") == "1"


_MOCKS: dict | None = None


def _mock_output(out):
    global _MOCKS
    if _MOCKS is None:
        from pathlib import Path
        _MOCKS = json.loads((Path(__file__).resolve().parent / "_vendor" / "ai_mock_outputs.json").read_text())
    if out.__name__ not in _MOCKS:
        raise GeminiError(f"MOCK_AI has no saved output for {out.__name__}")
    return out.model_validate(_MOCKS[out.__name__])


def _mock_decide(questions: dict) -> dict:
    """Saved-style answers: confident yes, no person, best score, first sensible choice."""
    ans = {}
    for k, q in questions.items():
        if q["type"] == "noul":
            p = 0.05 if "person" in k else 0.92
            ans[k] = {"type": "noul", "noul": p, "confidence": max(p, 1 - p)}
        elif q["type"] == "score":
            n = len(q["criteria"])
            ans[k] = {"type": "score", "score": float(n - 1), "confidence": 0.9, "probabilities": {}, "legend": {}}
        else:
            keys = list(q["criteria"])
            pick = next((c for c in ("ready", "product_search", "style_edit") if c in keys), keys[0])
            ans[k] = {"type": "choice", "choice": pick, "confidence": 0.9, "probabilities": {c: (0.9 if c == pick else 0.1 / max(1, len(keys) - 1)) for c in keys}}
    return ans


def configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
