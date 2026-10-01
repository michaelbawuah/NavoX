"""Provider grammar compatibility only; the canonical schema still validates locally."""

from copy import deepcopy
from typing import Any


def openai_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Require every declared property for OpenAI's strict output grammar.

    Requiring an optional property narrows the wire grammar; it does not relax
    the canonical schema validated locally. Nullable properties retain their
    explicit null branch. Defaults are annotations, never generated values.
    https://developers.openai.com/api/docs/guides/structured-outputs
    """
    result = deepcopy(schema)

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
        for key in ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas"):
            entries = node.get(key)
            if isinstance(entries, dict):
                for child in entries.values():
                    visit(child)
        for key in (
            "items",
            "contains",
            "not",
            "if",
            "then",
            "else",
            "additionalProperties",
            "propertyNames",
        ):
            visit(node.get(key))
        for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
            entries = node.get(key)
            if isinstance(entries, list):
                for child in entries:
                    visit(child)

    visit(result)
    return result


def claude_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Move unsupported bounds into descriptions without mutating the catalog.

    Anthropic's JSON-output grammar rejects these constraints. As in its SDK,
    describe them on the wire and enforce the original schema after generation.
    Visit schema positions only: property names and enum/const values are data.
    https://platform.claude.com/docs/en/build-with-claude/structured-outputs
    """
    result = deepcopy(schema)
    unsupported = {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "maxItems",
        "uniqueItems",
        "minContains",
        "maxContains",
    }

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        constraints = []
        for key in tuple(node):
            if key in unsupported or (key == "minItems" and node[key] not in (0, 1)):
                constraints.append(f"{key}={node.pop(key)}")
        if constraints:
            text = "NavoX validates these constraints: " + ", ".join(constraints) + "."
            node["description"] = " ".join(filter(None, (node.get("description"), text)))
        for key in ("properties", "$defs", "definitions", "patternProperties", "dependentSchemas"):
            entries = node.get(key)
            if isinstance(entries, dict):
                for child in entries.values():
                    visit(child)
        for key in (
            "items",
            "contains",
            "not",
            "if",
            "then",
            "else",
            "additionalProperties",
            "propertyNames",
            "unevaluatedItems",
            "unevaluatedProperties",
        ):
            visit(node.get(key))
        for key in ("anyOf", "allOf", "oneOf", "prefixItems"):
            entries = node.get(key)
            if isinstance(entries, list):
                for child in entries:
                    visit(child)

    visit(result)
    return result
