"""Related-story reads from shared admitted claim digests inside one owner scope."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, definition

from navox.api import news, news_stories
from navox.db.models import User, Workspace, WorkspaceMembership
from navox.db.news import NewsClaim, NewsStory
from navox.news.contracts import NewsItemInput
from navox.news.evidence import ClaimSpan, admit_claim
from navox.news.ingestion import store_item
from navox.news.registry import activate_source, current_rights
from navox.news.stories import index_item


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    monkeypatch.setattr(news, "datetime", Clock)
    monkeypatch.setattr(news_stories, "datetime", Clock)


@pytest.fixture(autouse=True)
def feed_enabled(subscription_env):
    subscription_env.settings.news_feed_enabled = True


def source_definition(key: str):
    return definition(summary_generation_allowed=True).model_copy(
        update={
            "key": f"source-{key}",
            "name": f"Source {key}",
            "domain": f"{key}.example.com",
            "endpoint": f"https://{key}.example.com/feed",
            "article_domains": (f"{key}.example.com",),
            "independence_group": f"origin-{key}",
        }
    )


async def add_story(env, key, *, headline, description, published_at=None, owner=None):
    """Store one owned source item as its own story and return the fixture parts."""
    config = source_definition(key)
    env.settings.news_source_catalog = [
        *env.settings.news_source_catalog,
        config.model_dump(mode="json"),
    ]
    workspace_id, user_id = owner or (env.workspace_id, env.user_id)
    source = await activate_source(
        env.database, config, workspace_id=workspace_id, user_id=user_id, now=NOW
    )
    item = await store_item(
        env.database,
        source,
        await current_rights(env.database, source),
        config,
        NewsItemInput(
            external_id=key,
            headline=headline,
            description=description,
            canonical_url=f"https://{key}.example.com/{key}",
            published_at=published_at or NOW,
            categories=("science",),
        ),
        now=NOW,
    )
    story = await index_item(env.database, item, {config.key: config}, now=NOW)
    await env.database.commit()
    return SimpleNamespace(config=config, source=source, item=item, story=story)


async def add_claim(env, entry, definitions, *, field="headline", start=0, end=None):
    text = entry.item.headline if field == "headline" else (entry.item.description or "")
    span = ClaimSpan(
        item_id=entry.item.id,
        item_revision=entry.item.revision,
        field=field,
        start=start,
        end=len(text) if end is None else end,
    )
    claim = await admit_claim(env.database, entry.story, span, definitions, now=NOW)
    await env.database.commit()
    return claim


async def related(env, story_id):
    return await env.client.get(f"/api/v1/news/stories/{story_id}/related")


@pytest.mark.asyncio
async def test_related_stories_share_only_current_claim_digests(subscription_env):
    env = subscription_env
    first = await add_story(
        env,
        "one",
        headline="An observation was recorded",
        description="The instrument measured a change.",
    )
    second = await add_story(
        env,
        "two",
        headline="An observation was recorded",
        description="A separate report about the same observation.",
    )
    unrelated = await add_story(
        env,
        "three",
        headline="An unrelated headline",
        description="Something else entirely.",
    )
    definitions = {entry.config.key: entry.config for entry in (first, second, unrelated)}
    for entry in (first, second, unrelated):
        await add_claim(env, entry, definitions)

    response = await related(env, first.story.id)
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body] == [str(second.story.id)]
    assert body[0]["headline"] == "An observation was recorded"
    assert body[0]["sources"][0]["source_name"] == "Source two"
    assert "importance_score" not in body[0]
    assert "trend_score" not in body[0]

    reverse = await related(env, second.story.id)
    assert [row["id"] for row in reverse.json()] == [str(first.story.id)]
    assert (await related(env, unrelated.story.id)).json() == []


@pytest.mark.asyncio
async def test_related_stories_are_empty_without_a_shared_claim(subscription_env):
    env = subscription_env
    left = await add_story(
        env, "left", headline="Left headline text", description="Left side tail."
    )
    right = await add_story(
        env, "right", headline="Right headline text", description="Right side tail."
    )
    definitions = {entry.config.key: entry.config for entry in (left, right)}
    for entry in (left, right):
        await add_claim(env, entry, definitions)

    response = await related(env, left.story.id)
    assert response.status_code == 200
    assert response.json() == []
    assert (await related(env, right.story.id)).json() == []


@pytest.mark.asyncio
async def test_related_stories_exclude_suppressed_and_unavailable_stories(subscription_env):
    env = subscription_env
    requested = await add_story(
        env, "req", headline="Shared headline text", description="Requested tail."
    )
    visible = await add_story(
        env, "vis", headline="Shared headline text", description="Visible tail."
    )
    hidden = await add_story(
        env, "hid", headline="Shared headline text", description="Suppressed tail."
    )
    definitions = {entry.config.key: entry.config for entry in (requested, visible, hidden)}
    for entry in (requested, visible, hidden):
        await add_claim(env, entry, definitions)
    hidden.story.suppressed = True
    await env.database.commit()

    response = await related(env, requested.story.id)
    assert [row["id"] for row in response.json()] == [str(visible.story.id)]

    requested.story.suppressed = True
    await env.database.commit()
    assert (await related(env, requested.story.id)).status_code == 404
    assert (await related(env, uuid4())).status_code == 404
    env.settings.news_feed_enabled = False
    assert (await related(env, visible.story.id)).status_code == 503


@pytest.mark.asyncio
async def test_related_stories_never_cross_owner_or_workspace_boundaries(subscription_env):
    env = subscription_env
    env.database.add_all(
        [
            User(id=OTHER_USER, email="related-other@example.com"),
            Workspace(id=OTHER_WORKSPACE, name="Other news"),
        ]
    )
    await env.database.flush()
    env.database.add(WorkspaceMembership(workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER))
    await env.database.commit()

    mine = await add_story(env, "mine", headline="Shared headline text", description="My own tail.")
    theirs = await add_story(
        env,
        "theirs",
        headline="Shared headline text",
        description="Another owner tail.",
        owner=(OTHER_WORKSPACE, OTHER_USER),
    )
    definitions = {entry.config.key: entry.config for entry in (mine, theirs)}
    for entry in (mine, theirs):
        await add_claim(env, entry, definitions)

    assert (await related(env, mine.story.id)).json() == []
    assert (
        await env.database.scalar(select(NewsStory).where(NewsStory.id == theirs.story.id))
    ) is not None


@pytest.mark.asyncio
async def test_related_stories_skip_candidates_with_stale_claims_or_lost_rights(subscription_env):
    env = subscription_env
    requested = await add_story(
        env, "requested", headline="Shared headline text", description="Requested tail."
    )
    kept = await add_story(env, "kept", headline="Shared headline text", description="Kept tail.")
    stale = await add_story(
        env, "stale", headline="Shared headline text", description="Stale tail."
    )
    revoked = await add_story(
        env, "revoked", headline="Shared headline text", description="Revoked tail."
    )
    definitions = {entry.config.key: entry.config for entry in (requested, kept, stale, revoked)}
    for entry in (requested, kept, stale, revoked):
        await add_claim(env, entry, definitions)

    # A claim whose origin snapshot moved on is no longer a current claim.
    stale_claim = await env.database.scalar(
        select(NewsClaim).where(NewsClaim.cluster_id == stale.story.id)
    )
    assert stale_claim is not None
    stale_claim.origin_revision += 1
    await env.database.commit()

    # A source whose summary rights were withdrawn can no longer support a current claim.
    narrowed = revoked.config.model_copy(
        update={
            "rights": revoked.config.rights.model_copy(update={"summary_generation_allowed": False})
        }
    )
    await activate_source(
        env.database, narrowed, workspace_id=env.workspace_id, user_id=env.user_id, now=NOW
    )
    await env.database.commit()

    body = (await related(env, requested.story.id)).json()
    assert [row["id"] for row in body] == [str(kept.story.id)]
    assert stale_claim.origin_revision != stale.item.revision
    assert str(stale.story.id) not in [row["id"] for row in body]
    assert str(revoked.story.id) not in [row["id"] for row in body]


@pytest.mark.asyncio
async def test_related_stories_cap_at_five_and_order_deterministically(subscription_env):
    env = subscription_env
    requested = await add_story(
        env,
        "requested",
        headline="Shared headline text",
        description="First shared fact. Second shared fact. Only here.",
    )
    definitions = {requested.config.key: requested.config}
    await add_claim(env, requested, definitions)
    await add_claim(env, requested, definitions, field="description", start=0, end=18)
    await add_claim(env, requested, definitions, field="description", start=19, end=38)

    # key: headline, description, digests shared with the requested story, published_at
    specs = [
        ("two", "Shared headline text", "First shared fact. Two tail.", 2, 6),
        ("four", "Four headline", "First shared fact. Four tail.", 1, 4),
        ("one", "One headline", "Second shared fact. One tail.", 1, 1),
        ("two-a", "Two a headline", "First shared fact. Two a tail.", 1, 2),
        ("two-b", "Two b headline", "First shared fact. Two b tail.", 1, 2),
        ("three", "Three headline", "First shared fact. Three tail.", 1, 3),
    ]
    candidates = {}
    for key, headline, description, shared, hours_ago in specs:
        candidates[key] = await add_story(
            env,
            key,
            headline=headline,
            description=description,
            published_at=NOW - timedelta(hours=hours_ago),
        )
        definitions[candidates[key].config.key] = candidates[key].config
        if shared == 2:
            await add_claim(env, candidates[key], definitions)
        first_fact = "First shared fact." in description
        await add_claim(
            env,
            candidates[key],
            definitions,
            field="description",
            start=0,
            end=18 if first_fact else 19,
        )

    ids = [row["id"] for row in (await related(env, requested.story.id)).json()]
    tied = sorted(
        (candidates[key].story.id for key, _, _, _, hours in specs if hours == 2),
        key=lambda value: value.hex,
    )
    assert ids == [
        str(candidates["two"].story.id),
        str(candidates["one"].story.id),
        *(str(value) for value in tied),
        str(candidates["three"].story.id),
    ]
    assert str(candidates["four"].story.id) not in ids
    assert ids == [row["id"] for row in (await related(env, requested.story.id)).json()]
