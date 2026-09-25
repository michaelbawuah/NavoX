from uuid import UUID

import pytest

from navox.connectors.builtin.imports import ImportConnector
from navox.connectors.contracts import SyncRequest


@pytest.mark.asyncio
async def test_ics_csv_and_json_imports_use_the_same_canonical_contract() -> None:
    connection_id = UUID("11111111-1111-4111-8111-111111111111")
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    cases = [
        (
            "ics",
            """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-1
SUMMARY:Physics review
DESCRIPTION:Review standing waves
DTSTART:20260925T180000Z
DTEND:20260925T190000Z
END:VEVENT
END:VCALENDAR""",
            "import.calendar_event",
            "Physics review",
        ),
        (
            "csv",
            "id,title,due_at,description\n7,Submit report,2026-09-30T23:59:00Z,Upload final PDF\n",
            "import.csv_row",
            "Submit report",
        ),
        (
            "json",
            '[{"id":"9","title":"Confirm travel","occurred_at":"2026-09-24T12:00:00Z"}]',
            "import.json_item",
            "Confirm travel",
        ),
    ]

    for format_name, content, resource_type, subject in cases:
        connector = ImportConnector(
            {
                "format": format_name,
                "content": content,
                "source_name": "fixture",
            },
            None,
        )
        page = await connector.sync(
            SyncRequest(
                connection_id=connection_id,
                workspace_id=workspace_id,
                capabilities=frozenset(
                    {
                        "imports.calendar.read",
                        "imports.tabular.read",
                        "imports.json.read",
                    }
                ),
            )
        )
        assert len(page.resources) == 1
        resource = page.resources[0]
        assert resource.resource_type == resource_type
        assert resource.canonical["subject"] == subject
        assert resource.workspace_id == workspace_id
        assert resource.connector_connection_id == connection_id


@pytest.mark.asyncio
async def test_import_replay_has_stable_resource_identity() -> None:
    connector = ImportConnector(
        {
            "format": "json",
            "content": '[{"id":"stable","title":"Stable item"}]',
        },
        None,
    )
    request = SyncRequest(
        connection_id=UUID("11111111-1111-4111-8111-111111111111"),
        workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
        capabilities=frozenset({"imports.json.read"}),
    )

    first = await connector.sync(request)
    second = await connector.sync(request)

    assert first.resources[0].resource_id == second.resources[0].resource_id


@pytest.mark.parametrize(
    "format_name,content",
    [
        ("json", "not-json"),
        ("csv", ""),
        ("xml", "<items/>"),
    ],
)
def test_import_rejects_invalid_inputs(format_name: str, content: str) -> None:
    if format_name == "xml":
        with pytest.raises(ValueError):
            ImportConnector({"format": format_name, "content": content}, None)
        return
    connector = ImportConnector({"format": format_name, "content": content}, None)
    if format_name == "json":
        with pytest.raises(ValueError):
            connector._resources(UUID(int=1), UUID(int=2))
    else:
        with pytest.raises(ValueError):
            connector._resources(UUID(int=1), UUID(int=2))
