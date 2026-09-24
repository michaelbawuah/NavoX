"""Deployment-reviewed MCP server policies, independent of request handlers."""

from __future__ import annotations

from pydantic import ValidationError

from navox.connectors.builtin.mcp import MCPServerPolicy
from navox.core.settings import Settings


def approved_mcp_policies(settings: Settings) -> tuple[MCPServerPolicy, ...]:
    try:
        result = tuple(MCPServerPolicy.model_validate(item) for item in settings.mcp_servers)
    except ValidationError:
        raise ValueError("Invalid operator-approved MCP server policy") from None
    if len({item.server_id for item in result}) != len(result):
        raise ValueError("Duplicate operator-approved MCP server policy")
    if len({item.provider for item in result}) != len(result):
        raise ValueError("Duplicate MCP provider identity")
    if any(item.provider in {"google", "canvas", "import"} for item in result):
        raise ValueError("MCP provider identity is reserved")
    return result
