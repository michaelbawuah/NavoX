"""Explicit identity conflicts do not merge names or conceal either source."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from navox.db.knowledge import KnowledgeResource, KnowledgeResourcePermission
from navox.db.models import ConnectorResource
from navox.knowledge import conflicts
from navox.knowledge.indexing import backfill_workspace
from navox.knowledge.search_contracts import SearchRequest
from navox.knowledge.service import evidence_bundle, search_knowledge
from tests.knowledge_search_support import NOW, load_world
from tests.test_knowledge_search import search_engine as search_engine
from tests.test_knowledge_search import search_factory as search_factory

ICS = (
    "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:meeting-exact@example.com\n"
    "DTSTART:20260930T180000Z\nEND:VEVENT\nEND:VCALENDAR"
)


async def prepare(factory):
    world = await load_world(factory)
    async with factory() as database:
        mail = await database.get(ConnectorResource, world.message_id)
        mail.canonical = dict(mail.canonical, subject="Quarterly meeting", content=ICS)
        mail.content_hash = "a" * 64
        event = await database.get(ConnectorResource, world.event_id)
        event.canonical = dict(
            event.canonical,
            title="Quarterly meeting",
            subject="Quarterly meeting",
            starts_at="2026-09-30T19:00:00+00:00",
            ical_uid="meeting-exact@example.com",
        )
        event.source_url = "https://calendar.google.com/calendar/event?eid=fixture"
        event.content_hash = "b" * 64
        event.retrieved_at = NOW
        await database.flush()
        await backfill_workspace(
            database, workspace_id=world.workspace_id, user_id=world.user_id, now=NOW
        )
        resources = list(
            await database.scalars(
                select(KnowledgeResource.id).where(
                    KnowledgeResource.workspace_id == world.workspace_id
                )
            )
        )
        await database.commit()
    return world, resources


@pytest.mark.asyncio
async def test_conflict_surfaces_both_sources_and_current_calendar_authority(search_factory):
    world, resources = await prepare(search_factory)
    async with search_factory() as database:
        response = await search_knowledge(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            request=SearchRequest(query="quarterly meeting"),
            now=NOW,
        )
        assert len(response.conflicts) == 1
        conflict = response.conflicts[0]
        assert {value.value.hour for value in conflict.values} == {18, 19}
        assert {value.authority for value in conflict.values} == {
            "DIRECT_COMMUNICATION",
            "CALENDAR_SCHEDULE",
        }
        calendar = next(value for value in conflict.values if value.source_type == "CALENDAR_EVENT")
        assert conflict.preferred_resource_id == calendar.resource_id
        assert all(value.canonical_url for value in conflict.values)
        bundle = evidence_bundle(
            response=response,
            query="quarterly meeting",
            workspace_id=world.workspace_id,
            user_id=world.user_id,
        )
        assert bundle.conflicts == response.conflicts


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["other_uid", "recurring", "same_time", "revoked", "hash"])
async def test_conflicts_require_exact_current_unambiguous_identity(search_factory, change):
    world, resources = await prepare(search_factory)
    async with search_factory() as database:
        event = await database.get(ConnectorResource, world.event_id)
        if change == "other_uid":
            event.canonical = dict(event.canonical, ical_uid="unrelated@example.com")
        elif change == "recurring":
            event.canonical = dict(event.canonical, recurring=True)
        elif change == "same_time":
            event.canonical = dict(event.canonical, starts_at="2026-09-30T18:00:00+00:00")
        elif change == "revoked":
            await database.execute(
                delete(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id.in_(resources)
                )
            )
        else:
            event.content_hash = "c" * 64
        if change in {"other_uid", "recurring", "same_time"}:
            event.content_hash = "d" * 64
            await database.flush()
            await backfill_workspace(
                database, workspace_id=world.workspace_id, user_id=world.user_id, now=NOW
            )
        await database.commit()
        assert (
            await conflicts.detect_conflicts(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_ids=resources,
                now=NOW,
            )
            == ()
        )


@pytest.mark.asyncio
async def test_stale_calendar_is_not_preferred_and_mid_read_revocation_drops_conflict(
    search_factory, monkeypatch
):
    world, resources = await prepare(search_factory)
    async with search_factory() as database:
        result = await conflicts.detect_conflicts(
            database,
            workspace_id=world.workspace_id,
            user_id=world.user_id,
            resource_ids=resources,
            now=NOW + timedelta(hours=1),
        )
        assert result[0].preferred_resource_id is None
    original = conflicts._event_fact

    async def revoke(database, **kwargs):
        result = await original(database, **kwargs)
        if result:
            await database.execute(
                delete(KnowledgeResourcePermission).where(
                    KnowledgeResourcePermission.resource_id == result.value.resource_id
                )
            )
        return result

    monkeypatch.setattr(conflicts, "_event_fact", revoke)
    async with search_factory() as database:
        assert (
            await conflicts.detect_conflicts(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_ids=resources,
                now=NOW,
            )
            == ()
        )


def test_calendar_subset_rejects_ambiguous_dates_and_supports_unfolding():
    assert conflicts.utc_invitation(ICS) == (
        "meeting-exact@example.com",
        datetime(2026, 9, 30, 18, tzinfo=UTC),
    )
    assert (
        conflicts.utc_invitation(ICS.replace("UID:meeting-exact", "UID:meeting-\n exact"))[0]
        == "meeting-exact@example.com"
    )
    for text in (
        ICS + ICS,
        ICS.replace("T180000Z", "T180000"),
        ICS.replace("DTSTART:", "DTSTART;TZID=America/New_York:"),
        ICS.replace("END:VEVENT", "RRULE:FREQ=DAILY\nEND:VEVENT"),
        ICS.replace("20260930", "20260231"),
    ):
        assert conflicts.utc_invitation(text) is None


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        ("DTSTART;TZID=America/New_York:20260930T140000", datetime(2026, 9, 30, 18, tzinfo=UTC)),
        ("DTSTART;TZID=Asia/Kolkata:20260930T233000", datetime(2026, 9, 30, 18, tzinfo=UTC)),
        ("DTSTART;TZID=America/New_York:20261101T013000", None),
        ("DTSTART;TZID=America/New_York:20260308T023000", None),
        ("DTSTART;TZID=Imaginary/Zone:20260930T140000", None),
        ("DTSTART;TZID=../../etc/passwd:20260930T140000", None),
        ("DTSTART;TZID=America/New_York:20260930T140000Z", None),
        ("DTSTART:20260930T140000", None),
    ],
)
def test_invitation_converts_only_unambiguous_source_timezones(start, expected):
    result = conflicts.utc_invitation(ICS.replace("DTSTART:20260930T180000Z", start))
    assert result == (("meeting-exact@example.com", expected) if expected else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["identity", "recurrence"])
async def test_conflict_fence_rechecks_source_metadata_even_without_text_hash_change(
    search_factory, monkeypatch, change
):
    world, resources = await prepare(search_factory)
    original = conflicts._event_fact
    changed = False

    async def mutate(database, **kwargs):
        nonlocal changed
        result = await original(database, **kwargs)
        if result and result.value.source_type == "CALENDAR_EVENT" and not changed:
            changed = True
            event = await database.get(ConnectorResource, world.event_id)
            update = (
                {"ical_uid": "different@example.com"}
                if change == "identity"
                else {"recurring": True}
            )
            event.canonical = dict(event.canonical, **update)
            await database.flush()
        return result

    monkeypatch.setattr(conflicts, "_event_fact", mutate)
    async with search_factory() as database:
        assert (
            await conflicts.detect_conflicts(
                database,
                workspace_id=world.workspace_id,
                user_id=world.user_id,
                resource_ids=resources,
                now=NOW,
            )
            == ()
        )
    assert changed
