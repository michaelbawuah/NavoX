"""News schemas must be usable in strict mode without changing local validation."""

import json
from copy import deepcopy

from jsonschema import Draft202012Validator

from navox.ai.provider_schema import openai_output_schema
from navox.news.ai_contracts import EXTRACTION, news_artifacts


def test_news_optional_claim_type_becomes_required_on_wire_only():
    _, schemas = news_artifacts()
    canonical = json.loads(next(s.document.text for s in schemas if s.reference == EXTRACTION))
    original = deepcopy(canonical)
    wire = openai_output_schema(canonical)
    assert canonical == original
    original_span = canonical["$defs"]["ClaimSpan"]
    wire_span = wire["$defs"]["ClaimSpan"]
    assert "claim_type" not in original_span["required"]
    assert "claim_type" in wire_span["required"]
    assert "default" not in wire_span["properties"]["claim_type"]
    assert wire_span["additionalProperties"] is False
    payload = {
        "claims": [
            {
                "item_id": "00000000-0000-0000-0000-000000000001",
                "item_revision": 1,
                "field": "headline",
                "start": 0,
                "end": 12,
                "claim_type": "EVENT",
            }
        ]
    }
    Draft202012Validator(wire).validate(payload)
    Draft202012Validator(canonical).validate(payload)
    payload["claims"][0]["start"] = -1
    assert not Draft202012Validator(wire).is_valid(payload)
    assert not Draft202012Validator(canonical).is_valid(payload)


def test_normalization_visits_schema_positions_not_user_property_names():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "default": {"type": "string", "enum": ["default", "required"]},
            "required": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"type": "integer", "minimum": 2, "default": 2}},
                },
            },
        },
    }
    wire = openai_output_schema(schema)
    assert wire["required"] == ["default", "required", "entries"]
    assert wire["properties"]["default"]["enum"] == ["default", "required"]
    assert wire["properties"]["required"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert wire["properties"]["entries"]["items"]["required"] == ["value"]
    assert wire["properties"]["entries"]["items"]["properties"]["value"]["minimum"] == 2
