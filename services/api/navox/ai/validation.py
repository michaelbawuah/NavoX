"""Local schema and application validation. Validation never confers action authority."""

import json
from collections.abc import Callable
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing.exceptions import Unresolvable

from navox.ai.foundation.contracts import JSONDocument


class OutputRejected(ValueError):
    pass


def compile_schema(document: JSONDocument) -> Draft202012Validator:
    schema = json.loads(document.text)

    def check_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "$id":
                    raise OutputRejected("Schema base URI changes are forbidden")
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise OutputRejected("External schema references are forbidden")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    try:
        check_refs(schema)
        Draft202012Validator.check_schema(schema)
    except (ValueError, SchemaError, RecursionError):
        raise OutputRejected("Invalid output schema") from None
    return Draft202012Validator(schema)


def validate_output(
    output: JSONDocument,
    schema: JSONDocument,
    semantic_validator: Callable[[Any], None],
) -> None:
    try:
        value = json.loads(output.text)
        compile_schema(schema).validate(value)
        semantic_validator(value)
    except (ValueError, ValidationError, Unresolvable, RecursionError, KeyError, TypeError):
        raise OutputRejected("AI output failed application validation") from None
