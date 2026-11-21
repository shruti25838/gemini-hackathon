"""Pull product dimensions out of listing text, with plain code and no LLM.

Shop MCP returns no structured dimension field. The only size information is
an option label like `31.5"W x 15.7"D` or a sentence buried in HTML sales
copy. A model must never be asked to read it, for two reasons: the text is
untrusted, and a hallucinated measurement would silently become a fit claim.

So this is a regex extractor. It is deliberately conservative: when a string
is ambiguous it returns nothing rather than a guess, because the fit engine
answers `unknown` on missing dimensions and that is the correct outcome.

Everything it produces is labelled `listing_text_inferred`, which caps any
fit result at tentative.
"""

from __future__ import annotations

import re
from typing import Optional

from contracts.catalog_offer import ProductDimensions
from backend.sanitise import strip_html

# Metres per unit.
# Includes the plural and uppercase forms Shopify's `dimension` metafield
# type returns, for example {"value": 140.0, "unit": "CENTIMETERS"}.
UNIT_TO_METRES: dict[str, float] = {
    "mm": 0.001,
    "millimetre": 0.001,
    "millimetres": 0.001,
    "millimeter": 0.001,
    "millimeters": 0.001,
    "cm": 0.01,
    "centimetre": 0.01,
    "centimetres": 0.01,
    "centimeter": 0.01,
    "centimeters": 0.01,
    "m": 1.0,
    "metre": 1.0,
    "metres": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "in": 0.0254,
    "inch": 0.0254,
    "inches": 0.0254,
    '"': 0.0254,
    "ft": 0.3048,
    "foot": 0.3048,
    "feet": 0.3048,
    "'": 0.3048,
    "yd": 0.9144,
    "yard": 0.9144,
    "yards": 0.9144,
}

UNIT_PATTERN = r"(mm|cm|m|in|inch|inches|ft|feet|foot|\"|')"
NUMBER = r"(\d+(?:\.\d+)?)"

# "80 x 40 x 72 cm", and also '31.5" x 15.7" x 28.3"' where the unit is
# attached to each number. A unit must appear at least once; a bare
# "2 x 3 x 4" yields no units and is correctly rejected downstream.
TRIPLE = re.compile(
    rf"{NUMBER}\s*{UNIT_PATTERN}?\s*[x×]\s*"
    rf"{NUMBER}\s*{UNIT_PATTERN}?\s*[x×]\s*"
    rf"{NUMBER}\s*{UNIT_PATTERN}?",
    re.IGNORECASE,
)
# "31.5"W x 15.7"D x 28.3"H", units attached to each number
TRIPLE_LABELLED = re.compile(
    rf"{NUMBER}\s*{UNIT_PATTERN}?\s*W\s*[x×]\s*{NUMBER}\s*{UNIT_PATTERN}?\s*D"
    rf"(?:\s*[x×]\s*{NUMBER}\s*{UNIT_PATTERN}?\s*H)?",
    re.IGNORECASE,
)
# "Width: 80 cm"
SINGLE_LABELLED = re.compile(
    rf"\b(width|depth|length|height)\b\s*[:=]?\s*{NUMBER}\s*{UNIT_PATTERN}\b",
    re.IGNORECASE,
)

AXIS_ALIASES = {
    "width": "width_m",
    "depth": "depth_m",
    "length": "depth_m",  # a desk's "length" is its depth in plan view
    "height": "height_m",
}

# Sanity bounds. Furniture outside this range means the parse went wrong.
MIN_PLAUSIBLE_M = 0.01
MAX_PLAUSIBLE_M = 5.0


def _to_metres(value: str, unit: Optional[str]) -> Optional[float]:
    if unit is None:
        return None
    factor = UNIT_TO_METRES.get(unit.lower())
    if factor is None:
        return None
    metres = float(value) * factor
    if not (MIN_PLAUSIBLE_M <= metres <= MAX_PLAUSIBLE_M):
        return None
    return round(metres, 4)


def extract_dimensions(*sources: Optional[str]) -> Optional[ProductDimensions]:
    """Read width, depth and height out of any of the given strings.

    Sources are tried in order and the first that yields a footprint wins, so
    pass the most trustworthy first (an option label before sales copy).
    Returns None when nothing usable is found.
    """
    for raw in sources:
        if not raw:
            continue
        text = strip_html(raw)
        found = _extract_from(text)
        if found is not None:
            return found
    return None


def _extract_from(text: str) -> Optional[ProductDimensions]:
    # 1. "80 x 40 x 72 cm", or a unit attached to each number.
    match = TRIPLE.search(text)
    if match:
        width, w_unit, depth, d_unit, height, h_unit = match.groups()
        # A number without its own unit inherits the nearest one that was
        # given, which is how "80 x 40 x 72 cm" is written.
        shared = w_unit or d_unit or h_unit
        values = [
            _to_metres(width, w_unit or shared),
            _to_metres(depth, d_unit or shared),
            _to_metres(height, h_unit or shared),
        ]
        if all(v is not None for v in values):
            return ProductDimensions(
                width_m=values[0],
                depth_m=values[1],
                height_m=values[2],
                evidence_status="estimated",
                raw_text=match.group(0),
            )

    # 2. '31.5"W x 15.7"D', units attached per number.
    match = TRIPLE_LABELLED.search(text)
    if match:
        w_val, w_unit, d_val, d_unit, h_val, h_unit = match.groups()
        # An unlabelled number inherits the first unit that was given.
        fallback = w_unit or d_unit or h_unit
        width = _to_metres(w_val, w_unit or fallback)
        depth = _to_metres(d_val, d_unit or fallback)
        height = _to_metres(h_val, h_unit or fallback) if h_val else None
        if width is not None and depth is not None:
            return ProductDimensions(
                width_m=width,
                depth_m=depth,
                height_m=height,
                evidence_status="estimated",
                raw_text=match.group(0).strip(),
            )

    # 3. "Width: 80 cm" and friends, possibly scattered.
    axes: dict[str, float] = {}
    matched_text: list[str] = []
    for found in SINGLE_LABELLED.finditer(text):
        axis, value, unit = found.groups()
        field = AXIS_ALIASES[axis.lower()]
        metres = _to_metres(value, unit)
        if metres is not None and field not in axes:
            axes[field] = metres
            matched_text.append(found.group(0).strip())

    if axes:
        return ProductDimensions(
            width_m=axes.get("width_m"),
            depth_m=axes.get("depth_m"),
            height_m=axes.get("height_m"),
            evidence_status="estimated",
            raw_text="; ".join(matched_text),
        )

    return None


def dimensions_from_metafields(
    width: Optional[str], depth: Optional[str], height: Optional[str]
) -> Optional[ProductDimensions]:
    """Read the dev store's own metafields, which are authoritative.

    Shopify metafields of type `dimension` arrive as JSON like
    {"value": 1.2, "unit": "m"}; a plain number type arrives as a string. Both
    are handled, and anything unparseable is dropped rather than guessed.
    """
    import json

    def parse(raw: Optional[str]) -> Optional[float]:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = text
        if isinstance(parsed, dict):
            value, unit = parsed.get("value"), str(parsed.get("unit", "m"))
            if value is None:
                return None
            factor = UNIT_TO_METRES.get(unit.lower())
            return round(float(value) * factor, 4) if factor else None
        try:
            # A bare number in a metafield is metres by convention here, and
            # .env documents the namespace and keys that convention applies to.
            return round(float(parsed), 4)
        except (ValueError, TypeError):
            return None

    values = {
        "width_m": parse(width),
        "depth_m": parse(depth),
        "height_m": parse(height),
    }
    if not any(v is not None for v in values.values()):
        return None
    return ProductDimensions(
        width_m=values["width_m"],
        depth_m=values["depth_m"],
        height_m=values["height_m"],
        evidence_status="confirmed",
        raw_text=None,
    )
