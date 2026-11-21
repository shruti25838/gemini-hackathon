from typing import Any

from pydantic import BaseModel


def gemini_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Reduce a Pydantic schema to the simple grammar Gemini serves.

    Gemini rejects deeply constrained response grammars, so the provider schema keeps only
    types, enums, properties, items, and required fields. Pydantic enforces bounds locally.
    Keep Gemini-facing models free of optional unions: `anyOf` has no simple equivalent.
    """
    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})

    def shape(node: dict[str, Any]) -> dict[str, Any]:
        if "$ref" in node:
            node = definitions[node["$ref"].rsplit("/", 1)[-1]]
        result = {key: node[key] for key in ("type", "enum", "required") if key in node}
        if "properties" in node:
            result["properties"] = {name: shape(value) for name, value in node["properties"].items()}
        if "items" in node:
            result["items"] = shape(node["items"])
        return result

    return shape(schema)
