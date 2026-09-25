# Reviewed REST connector setup

The Generic REST adapter is registered only from deployment operator configuration.
Set `GENERIC_REST_CONNECTORS` to a JSON array of `{ "id", "config" }` entries before
starting the API and worker with the same value. For example:

```json
[
  {
    "id": "course-app",
    "config": {
      "display_name": "Course App",
      "provider": "course_app",
      "base_url": "https://api.example.edu",
      "auth": "bearer",
      "endpoints": [
        {
          "name": "assignments",
          "path": "/v1/assignments",
          "capability": "academic.assignments.read",
          "resource_type": "academic.assignment",
          "items_field": "results",
          "id_field": "id",
          "subject_field": "title",
          "content_field": "description",
          "occurred_at_field": "updated_at"
        }
      ]
    }
  }
]
```

The operator reviews the domain, read endpoint, schema mapping, capability, and
authentication strategy. The configuration contains no bearer token. The base URL
must be an HTTPS origin; requests reject redirects, non-public DNS answers, and
oversized responses. Do not point an approved configuration at a service whose
response may disclose the bearer token.

An authenticated owner lists approved choices through
`GET /api/v1/connectors/generic-rest-api/configurations`. The response includes
only name, authentication type, and available read capabilities. Explicit
selection is required with `POST /api/v1/connectors/generic-rest-api/connect`:

```json
{
  "configuration_id": "course-app",
  "capabilities": ["academic.assignments.read"],
  "token": "<user-provided-token>",
  "confirmed": true,
  "request_id": "<uuid>"
}
```

For `auth: "none"`, omit `token`. The bearer token goes into the encrypted,
owner-bound Secret Broker, never the connection configuration, audits, catalogue,
workflow payload, or model context. `POST /api/v1/connections/{id}/sync` with
`{"source":"resources","request_id":"<uuid>"}` schedules a read. Bearer
credential replacement uses
`POST /api/v1/connectors/generic-rest-api/connections/{id}/credential` with
`{"token":"<replacement>","confirmed":true,"request_id":"<uuid>"}`. Replacement
does not expand capabilities or resume a paused connection.

Each approved configuration has an immutable registry key derived from its ID
and canonical configuration digest. A changed or removed operator approval
cannot reuse an old connection for network access; an owner must reconnect to
the newly approved version. No user-facing API accepts a URL or endpoint schema.
The current generic REST path is read-only and does not support fetch-by-id or
provider writes. Live provider compatibility still needs an authorized test
against the selected service.
