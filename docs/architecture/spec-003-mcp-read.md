# Reviewed MCP read connector

MCP uses operator-reviewed `MCP_SERVERS` settings. Each entry fixes a public HTTPS
origin/path, server identifier, provider identifier, optional token secret name,
allowed resource URI prefixes, and exact read tool names with immutable arguments.
Changing any reviewed field changes the connector key. Existing connections then
fail closed until their owner reviews a new connection. A server's discovered
descriptions, schemas, or self-declared annotations never grant capabilities.

Authenticated owners can list safe setup metadata at
`GET /api/v1/connectors/mcp/servers`. The explicit
`POST /api/v1/connectors/mcp/connect` body is
`{server_id, capabilities, token?, confirmed: true, request_id}`. Tokens are
encrypted by the existing owner-bound Secret Broker; URLs, arbitrary tools,
and static arguments cannot be supplied by the user. Manual read sync uses
`POST /api/v1/connections/{id}/sync` with `source: "resources"`.

The existing ConnectorRuntime intersects manifest, provider, owner-consented,
and workspace-policy capabilities before calling this adapter. The adapter
enumerates only authorized resource or tool categories, accepts reviewed
resource prefixes, and invokes only exact reviewed read tools with fixed
arguments. It maps bounded text into canonical resources and enters the shared
SPEC-002 ingestion and Today pipeline. Direct fetches, writes, file resources,
arbitrary server code, binary content, redirects, cross-origin requests, and
runtime-supplied tool arguments are rejected. Server content remains untrusted
source data and is never interpreted as a NavoX instruction.

The wire implementation pins MCP **2026-07-28** over Streamable HTTP JSON
responses. It includes the protocol, method, name, and request metadata headers
specified by MCP. Request-scoped SSE responses and earlier protocol versions are
not supported and fail closed. Deployment with a real server still needs live
interoperability and provider-side read semantics review; local fixture tests
cannot prove a purported read tool has no side effects.
