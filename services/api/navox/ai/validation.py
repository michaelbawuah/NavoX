"""Local schema and application validation. Validation never confers action authority."""

import json
from collections.abc import Callable
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError
from referencing.exceptions import Unresolvable

from navox.ai.foundation.contracts import JSONDocument
from navox.intelligence.extraction import (
    EvidenceValidationError,
    extraction_validation_code,
)


class OutputRejected(ValueError):
    def __init__(self, message: str, *, validation_code: str | None = None) -> None:
        super().__init__(message)
        self.validation_code = (
            EvidenceValidationError(validation_code).code if validation_code is not None else None
        )


class OutputLimitRejected(OutputRejected):
    """A bounded output exceeded its contract; no partial text may be accepted."""


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
    except ValidationError as error:
        if error.validator == "maxLength":
            raise OutputLimitRejected("AI output exceeded its length limit") from None
        raise OutputRejected(
            "AI output failed application validation", validation_code="schema_invalid"
        ) from None
    except (ValueError, Unresolvable, RecursionError, KeyError, TypeError):
        raise OutputRejected(
            "AI output failed application validation", validation_code="schema_invalid"
        ) from None
    try:
        semantic_validator(value)
    except ValueError as error:
        raise OutputRejected(
            "AI output failed application validation",
            validation_code=extraction_validation_code(error),
        ) from None
    except (ValidationError, Unresolvable, RecursionError, KeyError, TypeError):
        raise OutputRejected(
            "AI output failed application validation", validation_code="validation_failed"
        ) from None
