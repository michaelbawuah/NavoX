"""Claude wire schema regressions. Authored HTTP responses, never live evidence."""

import json
from copy import deepcopy

import httpx
import pytest
from pydantic import SecretStr
from test_ai_operational_domains import authored_output
from test_ai_providers import MODEL, definition, request, response_body

from navox.ai.domain_corpus import CORPORA
from navox.ai.domains import Domain, domain_schema, validate_domain
from navox.ai.foundation.contracts import JSONDocument, Provider
from navox.ai.provider_schema import claude_output_schema
from navox.ai.providers import ClaudeAdapter
from navox.ai.validation import OutputRejected, compile_schema, validate_output


@pytest.mark.parametrize("domain", tuple(Domain))
@pytest.mark.asyncio
async def test_operational_schema_reaches_claude_without_rejected_array_bounds(domain):
    schema = domain_schema(domain)
    fixture = CORPORA[domain][0]
    output = authored_output(domain, fixture)
    calls = []

    async def send(wire):
        payload = json.loads(wire.content)
        transmitted = payload["output_config"]["format"]["schema"]
        assert '"maxItems":' not in json.dumps(transmitted)
        assert "maxItems=" in json.dumps(transmitted)
        assert transmitted["additionalProperties"] is False
        assert (
            "tools" not in payload and payload["messages"][0]["content"] == request().context.text
        )
        compile_schema(JSONDocument(text=json.dumps(transmitted))).validate(output)
        calls.append(wire)
        return httpx.Response(200, json=response_body(Provider.ANTHROPIC, json.dumps(output)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(send)) as client:
        adapter = ClaudeAdapter(
            api_key=SecretStr("fixture"), models=(definition(Provider.ANTHROPIC),), client=client
        )
        result = await adapter.execute(request().model_copy(update={"output_schema": schema}))
    validate_output(
        result.output, schema, lambda value: validate_domain(domain, value, fixture.context)
    )
    assert len(calls) == 1 and result.model == MODEL
    assert schema == domain_schema(domain)  # Canonical catalog schema was not edited.


def test_conversion_visits_schema_nodes_without_rewriting_property_names_or_literal_values():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "maxItems": {"type": "array", "maxItems": 2, "items": {"$ref": "#/$defs/entry"}},
            "description": {"const": {"maxItems": 25}},
        },
        "$defs": {
            "entry": {
                "anyOf": [
                    {"type": "string", "minLength": 2, "maxLength": 7, "description": "Identifier"},
                    {"type": "number", "minimum": 0, "maximum": 10},
                ]
            }
        },
        "required": ["maxItems", "description"],
    }
    original = deepcopy(schema)
    result = claude_output_schema(schema)
    assert schema == original
    assert result["properties"]["description"] == {"const": {"maxItems": 25}}
    assert result["required"] == ["maxItems", "description"]
    assert result["properties"]["maxItems"]["items"] == {"$ref": "#/$defs/entry"}
    assert result["$defs"]["entry"]["anyOf"][0]["description"].startswith("Identifier ")


@pytest.mark.parametrize("minimum", [0, 1, 2])
def test_supported_minimum_array_sizes_remain_grammar_constraints(minimum):
    schema = {"type": "array", "items": {"type": "string"}, "minItems": minimum}
    result = claude_output_schema(schema)
    assert result.get("minItems") == (minimum if minimum in (0, 1) else None)
    if minimum == 2:
        assert "minItems=2" in result["description"]


@pytest.mark.parametrize(
    "schema,value",
    [
        ({"type": "array", "maxItems": 1, "items": {"type": "integer"}}, [1, 2]),
        ({"type": "array", "minItems": 2, "items": {"type": "integer"}}, [1]),
        ({"type": "string", "maxLength": 3}, "long"),
        ({"type": "integer", "maximum": 5}, 6),
    ],
)
def test_grammar_compatibility_does_not_weaken_local_output_validation(schema, value):
    # Grammar-compatible does not mean accepted by NavoX.
    compile_schema(JSONDocument(text=json.dumps(claude_output_schema(schema)))).validate(value)
    with pytest.raises(OutputRejected):
        validate_output(
            JSONDocument(text=json.dumps(value)),
            JSONDocument(text=json.dumps(schema)),
            lambda _: None,
        )
