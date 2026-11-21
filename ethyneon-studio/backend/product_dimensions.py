from dataclasses import dataclass
import re


NUMBER = r"\d{1,5}(?:\.\d+)?"
UNIT = r'["″”]|(?:inches|inch|in\.?|cm|mm|m)(?![a-z])'
AXIS = r"width|height|depth|length|[whdl]"
MEASURE = (
    rf"(?P<lower>{NUMBER})\s*(?P<unit>{UNIT})?"
    rf"(?:\s*[-–—]\s*(?P<upper>{NUMBER})\s*(?P<upper_unit>{UNIT})?)?"
)
AXIS_FIRST = re.compile(rf"\s*(?P<axis>{AXIS})\b\s*:?\s*{MEASURE}", re.I)
AXIS_LAST = re.compile(rf"\s*{MEASURE}\s*(?P<axis>{AXIS})\b", re.I)
SEPARATOR = re.compile(r"\s*(?:[×x,]|\bby\b)\s*", re.I)
DESCRIPTION_SIZE = re.compile(
    r"\b(?:(?:overall|assembled|product|chair|item)\s+(?:size|dimensions?)|dimensions?)\s*:\s*",
    re.I,
)
UNIT_IN_METRES = {
    '"': 0.0254, "″": 0.0254, "”": 0.0254, "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
    "mm": 0.001, "cm": 0.01, "m": 1.0,
}


@dataclass(frozen=True)
class ListingDimensions:
    dimensions_m: dict[str, float] | None = None
    source: str = "unknown"
    height_range_m: tuple[float, float] | None = None


def _in_metres(measure: re.Match, common_unit: str | None = None) -> tuple[float, float] | None:
    unit = measure["unit"] or measure["upper_unit"] or common_unit
    if not unit:
        return None
    upper_unit = measure["upper_unit"] or unit
    lower = round(float(measure["lower"]) * UNIT_IN_METRES[unit.lower().rstrip(".")], 6)
    upper = round(float(measure["upper"] or measure["lower"]) * UNIT_IN_METRES[upper_unit.lower().rstrip(".")], 6)
    return (lower, upper) if 0 < lower <= upper else None


def _group(text: str) -> dict[str, tuple[float, float]]:
    measures = []
    position = 0
    for index in range(3):
        measure = AXIS_FIRST.match(text, position) or AXIS_LAST.match(text, position)
        if not measure:
            return {}
        measures.append(measure)
        position = measure.end()
        if index < 2:
            separator = SEPARATOR.match(text, position)
            if not separator:
                return {}
            position = separator.end()

    trailing = re.match(rf"\s*(?P<unit>{UNIT})", text[position:], re.I)
    shared_unit = trailing["unit"] if trailing else measures[-1]["upper_unit"] or measures[-1]["unit"]
    values = {}
    for measure in measures:
        value = _in_metres(measure, shared_unit)
        if value is not None:
            values[measure["axis"][0].lower()] = value
    return values


def _overall(values: dict[str, tuple[float, float]], source: str) -> ListingDimensions:
    if set(values) == {"w", "h", "d"}:
        width, height, depth = (values[axis] for axis in "whd")
    elif set(values) == {"l", "w", "h"}:
        width, height, depth = (values[axis] for axis in "lhw")
    else:
        return ListingDimensions()
    if width[0] != width[1] or depth[0] != depth[1]:
        return ListingDimensions()
    return ListingDimensions(
        {"width": width[0], "height": height[0], "depth": depth[0]},
        source,
        height if height[0] != height[1] else None,
    )


def listing_dimensions(options: list[dict[str, str]], description: str) -> ListingDimensions:
    selected = {}
    for option in options:
        name = option.get("name", "").casefold()
        label = option.get("label", "")
        if name in {"size", "dimensions"}:
            result = _overall(_group(label), "shopify_selected_size")
            if result.dimensions_m:
                return result
        elif name in {"width", "height", "depth", "length"}:
            measure = re.fullmatch(MEASURE, label.strip(), re.I)
            if measure and (value := _in_metres(measure)):
                selected[name[0]] = value

    result = _overall(selected, "shopify_selected_size")
    if result.dimensions_m:
        return result

    for heading in DESCRIPTION_SIZE.finditer(description):
        values = _group(description[heading.end():])
        if any(axis in values and values[axis] != value for axis, value in selected.items()):
            continue
        result = _overall(values, "shopify_listing_description")
        if result.dimensions_m:
            return result
    return ListingDimensions()
