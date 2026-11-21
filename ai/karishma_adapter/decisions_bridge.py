"""Route Karishma's Decision 1 calls through our plain-HTTP client.

Why this exists. Her `ai/karishma/gemini.py` calls
`genai.Client(http_options={"api_version": "v1beta"}).decisions.create(...)`.
That attribute does not exist in `google-genai` 2.33.0, which is the newest
release on PyPI; the builder guide's `>=3.2.0` was never published. So every
Decisions gate raised `AttributeError`, her gate code caught it and returned
a *degraded* result, and the photo gate silently stopped running.

We already solved this for the checkout audit in `backend/decisions.py` by
calling `v1beta/decisions` over plain HTTP. This routes her gates through the
same client and returns her exact `(answers, meta)` shape, so none of her
gate logic changes: she still reads `a["is_room"]["noul"]`,
`a["usable"]["score"]` and `a[k]["confidence"]`.

Her input parts are passed through untouched. She builds them as
`{"type": "image", "data": <base64>, "mime_type": ...}`, and inventing a
different shape would be guessing at an API we can read the real behaviour
of instead.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from ai.model_ids import MODEL_IDS


class DecisionsBridgeError(RuntimeError):
    """The call could not be made or came back unusable.

    Raised so her `_degraded` path still fires: a gate that cannot run must
    degrade loudly, not quietly return a passing answer.
    """


def _api_key() -> Optional[str]:
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def decide(
    parts: list[dict[str, Any]] | str,
    questions: dict[str, dict[str, Any]],
    *,
    model: str = MODEL_IDS["decisions"],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Her `gemini.decide` contract, served by our client.

    Returns `({question_id: answer_dict}, meta)`. Every answer dict is passed
    through exactly as the API returned it, so `noul`, `score`, `choice`,
    `probabilities` and `confidence` all reach her code unchanged.
    """
    from backend.decisions import DecisionsClient, DecisionsUnavailable

    key = _api_key()
    if not key:
        raise DecisionsBridgeError("no Gemini API key configured")

    inputs: list[dict[str, Any]]
    if isinstance(parts, str):
        inputs = [{"type": "text", "text": parts}]
    else:
        inputs = list(parts)

    started = time.time()
    try:
        body = DecisionsClient(key, model=model).create(inputs, questions)
    except DecisionsUnavailable as exc:
        raise DecisionsBridgeError(str(exc)) from exc

    answers = body.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise DecisionsBridgeError(
            f"the Decisions API returned no answers (keys: {sorted(body)})"
        )

    missing = [name for name in questions if name not in answers]
    if missing:
        raise DecisionsBridgeError(f"missing answers for: {', '.join(sorted(missing))}")

    meta = {
        "model": model,
        "latency_ms": int((time.time() - started) * 1000),
        "source": "live",
        "via": "ai.karishma_adapter.decisions_bridge",
    }
    if body.get("usage"):
        meta["usage"] = body["usage"]
    return answers, meta
