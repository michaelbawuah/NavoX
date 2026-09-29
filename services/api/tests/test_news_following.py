"""Authored in-app follow fixtures; these do not measure news or model quality."""

from datetime import timedelta

import pytest
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item

from navox.db.news import NewsStoryPreference, NewsStoryVersion
from navox.news.contracts import NewsError
from navox.news.following import acknowledge_updates, following_updates, material_change
from navox.news.registry import revoke_rights
from navox.news.stories import index_item


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ({}, {}, False),
        ({}, {"a": "UNCONFIRMED"}, False),
        ({}, {"a": "ATTRIBUTED"}, False),
        ({}, {"a": "VERIFIED"}, True),
        ({"a": "VERIFIED"}, {"a": "VERIFIED"}, False),
        ({"a": "VERIFIED"}, {"a": "UNCONFIRMED"}, True),
        ({"a": "VERIFIED"}, {}, True),
        ({"a": "ATTRIBUTED"}, {"a": "CORROBORATED"}, True),
        ({"a": "VERIFIED"}, {"a": "DISPUTED"}, True),
        ({"a": "VERIFIED"}, {"a": "RETRACTED"}, True),
        ({"a": "DISPUTED"}, {"a": "CONTRADICTED"}, True),
    ],
)
def test_only_recorded_material_assessment_transitions_count(before, after, expected):
    assert material_change(before, after) is expected


def test_unknown_history_state_cannot_claim_material_verification():
    with pytest.raises(NewsError):
        material_change({}, {"a": "approved-by-model"})


async def setup(db, key="one", baseline=1):
    config, source, row = await item(db, key)
    story = await index_item(db, row, {config.key: config}, now=NOW)
    preference = NewsStoryPreference(
        cluster_id=story.id,
        workspace_id=WORKSPACE,
        user_id=USER,
        followed=True,
        saved=False,
        dismissed=False,
        last_read_version=baseline,
    )
    db.add(preference)
    await db.flush()
    return config, source, story, preference


async def version(db, story, number, states, kind="VERIFICATION_CHANGED"):
    story.version = number
    story.last_updated_at = NOW + timedelta(minutes=number)
    db.add(
        NewsStoryVersion(
            cluster_id=story.id,
            version=number,
            change_kind=kind,
            source_snapshot={},
            claim_states=states,
            generated_at=story.last_updated_at,
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_reads_are_private_side_effect_free_and_ignore_source_volume(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db)
        await version(db, story, 2, {}, "SOURCE_ADDED")
        definitions = {config.key: config}
        args = dict(workspace_id=WORKSPACE, user_id=USER, now=NOW)
        assert not (await following_updates(db, definitions, **args)).entries
        await version(db, story, 3, {"claim-a": "VERIFIED"})
        first = await following_updates(db, definitions, **args)
        second = await following_updates(db, definitions, **args)
        assert first == second and preference.last_read_version == 1
        entry = first.entries[0]
        assert [event.version for event in entry.events] == [3]
        assert entry.through_version == 3 and entry.history_complete
        assert entry.story.verification_status == "UNCONFIRMED"
        assert first.external_notifications_sent is False
        assert "claim-a" not in first.model_dump_json()
        other = await following_updates(
            db, definitions, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER, now=NOW
        )
        assert other.entries == () and other.next_story_id is None
        preference.dismissed = True
        await db.flush()
        assert (await following_updates(db, definitions, **args)).entries == ()


@pytest.mark.asyncio
async def test_acknowledgement_is_explicit_monotonic_and_cannot_skip_unseen_updates(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db)
        await version(db, story, 2, {"a": "VERIFIED"})
        await version(db, story, 3, {"a": "DISPUTED"})
        args = dict(workspace_id=WORKSPACE, user_id=USER, now=NOW)
        definitions = {config.key: config}
        result = await acknowledge_updates(db, story.id, definitions, observed_version=2, **args)
        assert result.acknowledged_version == 2 and result.current_version == 3
        page = await following_updates(db, definitions, **args)
        assert [event.version for event in page.entries[0].events] == [3]
        again = await acknowledge_updates(db, story.id, definitions, observed_version=1, **args)
        assert again.acknowledged_version == 2
        with pytest.raises(NewsError):
            await acknowledge_updates(db, story.id, definitions, observed_version=4, **args)
        assert preference.last_read_version == 2
        with pytest.raises(NewsError):
            await acknowledge_updates(
                db,
                story.id,
                definitions,
                observed_version=2,
                workspace_id=OTHER_WORKSPACE,
                user_id=OTHER_USER,
                now=NOW,
            )
        await acknowledge_updates(db, story.id, definitions, observed_version=3, **args)
        assert not (await following_updates(db, definitions, **args)).entries


@pytest.mark.asyncio
async def test_legacy_follow_and_missing_history_are_not_called_caught_up(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db, baseline=None)
        definitions = {config.key: config}
        args = dict(workspace_id=WORKSPACE, user_id=USER, now=NOW)
        page = await following_updates(db, definitions, **args)
        assert page.entries[0].baseline_required and preference.last_read_version is None
        await acknowledge_updates(db, story.id, definitions, observed_version=1, **args)
        await version(db, story, 3, {"a": "VERIFIED"})
        page = await following_updates(db, definitions, **args)
        assert not page.entries[0].history_complete
        assert page.entries[0].through_version == 1
        assert not page.entries[0].events
        with pytest.raises(NewsError):
            await acknowledge_updates(db, story.id, definitions, observed_version=2, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["suppressed", "expired", "revoked", "unfollowed"])
async def test_unavailable_followed_content_never_surfaces(ai_database, unavailable):
    await seed(ai_database)
    async with ai_database() as db:
        config, source, story, preference = await setup(db)
        await version(db, story, 2, {"a": "VERIFIED"})
        now = NOW
        if unavailable == "suppressed":
            story.suppressed = True
        elif unavailable == "expired":
            now += timedelta(days=3)
        elif unavailable == "unfollowed":
            preference.followed = False
        else:
            await revoke_rights(db, source, now=NOW)
        await db.flush()
        assert not (
            await following_updates(
                db, {config.key: config}, workspace_id=WORKSPACE, user_id=USER, now=now
            )
        ).entries


@pytest.mark.asyncio
async def test_quiet_first_page_does_not_starve_later_updates(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        a, _, quiet, _ = await setup(db, "quiet")
        b, _, busy, _ = await setup(db, "busy")
        # Use the real UUID ordering, not an assumed database insertion order.
        if busy.id < quiet.id:
            quiet, busy = busy, quiet
        await version(db, busy, 2, {"a": "VERIFIED"})
        args = dict(workspace_id=WORKSPACE, user_id=USER, now=NOW, limit=1)
        first = await following_updates(db, {a.key: a, b.key: b}, **args)
        assert first.entries == () and first.next_story_id == quiet.id
        second = await following_updates(
            db, {a.key: a, b.key: b}, after_story_id=first.next_story_id, **args
        )
        assert second.entries[0].story.id == busy.id and second.next_story_id is None


@pytest.mark.asyncio
async def test_bounded_history_never_acknowledges_unseen_tail(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, _ = await setup(db)
        for number in range(2, 53):
            await version(db, story, number, {"a": "VERIFIED" if number % 2 else "DISPUTED"})
        definitions = {config.key: config}
        args = dict(workspace_id=WORKSPACE, user_id=USER, now=NOW)
        first = (await following_updates(db, definitions, **args)).entries[0]
        assert len(first.events) == 50 and first.through_version == 51
        assert not first.history_complete and first.story.version == 52
        await acknowledge_updates(
            db, story.id, definitions, observed_version=first.through_version, **args
        )
        second = (await following_updates(db, definitions, **args)).entries[0]
        assert [event.version for event in second.events] == [52]
        assert second.history_complete


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, 26, 1000])
async def test_following_scan_is_bounded(ai_database, limit):
    async with ai_database() as db:
        with pytest.raises(ValueError):
            await following_updates(
                db, {}, workspace_id=WORKSPACE, user_id=USER, now=NOW, limit=limit
            )


@pytest.mark.asyncio
async def test_acknowledgement_cannot_jump_across_missing_history(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db)
        await version(db, story, 3, {"a": "VERIFIED"})
        with pytest.raises(NewsError, match="source_changed"):
            await acknowledge_updates(
                db,
                story.id,
                {config.key: config},
                observed_version=3,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
            )
        assert preference.last_read_version == 1


@pytest.mark.asyncio
async def test_acknowledgement_cannot_jump_beyond_a_bounded_page(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db)
        for number in range(2, 53):
            await version(db, story, number, {"a": "VERIFIED"})
        with pytest.raises(NewsError, match="source_changed"):
            await acknowledge_updates(
                db,
                story.id,
                {config.key: config},
                observed_version=52,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
            )
        assert preference.last_read_version == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unfollowed", "dismissed", "other_owner"])
async def test_projection_rechecks_current_follow_scope(ai_database, change):
    from navox.news.following import follow_update

    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db)
        if change == "unfollowed":
            preference.followed = False
        elif change == "dismissed":
            preference.dismissed = True
        else:
            preference.user_id = OTHER_USER
        with pytest.raises(NewsError, match="source_changed"):
            await follow_update(db, story, preference, {config.key: config}, now=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", [0, 5])
async def test_invalid_saved_marker_cannot_silently_acknowledge(ai_database, baseline):
    await seed(ai_database)
    async with ai_database() as db:
        config, _, story, preference = await setup(db, baseline=baseline)
        with pytest.raises(NewsError, match="source_changed"):
            await acknowledge_updates(
                db,
                story.id,
                {config.key: config},
                observed_version=1,
                workspace_id=WORKSPACE,
                user_id=USER,
                now=NOW,
            )
        assert preference.last_read_version == baseline
