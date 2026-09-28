"""Canonical contracts map to provider syntax. Proposals confer no execution rights."""

import json
from dataclasses import dataclass
from typing import Any

from navox.agent.contracts import ActionContract, get_action_contract
from navox.ai.foundation.contracts import JSONDocument, Provider
from navox.ai.validation import OutputRejected, compile_schema, validate_output


@dataclass(frozen=True)
class CanonicalTool:
    contract: ActionContract
    parameters: JSONDocument

    @property
    def wire_name(self) -> str:
        return self.contract.name.replace(".", "__")


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    parameters: JSONDocument
    risk_level: str
    required_permissions: tuple[str, ...]


def provider_tool(provider: Provider, tool: CanonicalTool) -> dict[str, Any]:
    if get_action_contract(tool.contract.name) != tool.contract:
        raise OutputRejected("Action contract is not registered")
    compile_schema(tool.parameters)
    base = {
        "name": tool.wire_name,
        "description": f"Propose {tool.contract.name}; NavoX approval rules apply.",
    }
    schema = json.loads(tool.parameters.text)
    if provider == Provider.ANTHROPIC:
        return {**base, "input_schema": schema, "strict": True}
    if provider == Provider.GEMINI:
        return {"functionDeclarations": [{**base, "parametersJsonSchema": schema}]}
    return {**base, "type": "function", "parameters": schema, "strict": True}


def normalize_proposal(
    name: str, arguments: str, tools: tuple[CanonicalTool, ...]
) -> ProposedAction:
    tool = next((tool for tool in tools if tool.wire_name == name), None)
    if tool is None or get_action_contract(tool.contract.name) != tool.contract:
        raise OutputRejected("Model proposed an unavailable action")
    try:
        parsed = JSONDocument(text=arguments)
    except ValueError:
        raise OutputRejected("Invalid proposed action arguments") from None

    def object_only(value: Any) -> None:
        if not isinstance(value, dict):
            raise OutputRejected("Tool arguments must be an object")

    validate_output(parsed, tool.parameters, object_only)
    return ProposedAction(
        action_type=tool.contract.name,
        parameters=parsed,
        risk_level=tool.contract.risk_level,
        required_permissions=tool.contract.required_permissions,
    )
