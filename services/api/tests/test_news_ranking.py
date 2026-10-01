"""Observed ranking fixtures; these do not establish real-world importance quality."""

from datetime import timedelta

import pytest
from test_news_foundation import NOW, seed
from test_news_stories import item

from navox.news.ingestion import item_view
from navox.news.ranking import TrendSignals, observed_trend_signals, order_trending_items
from navox.news.stories import index_item, story_view


@pytest.mark.asyncio
async def test_trend_signals_count_current_independent_sources_and_recorded_activity(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long report with identical wording and context copied "
            "from the same observation so exact-copy grouping is deterministic."
        )
        first_config, _, first = await item(db, description=copied)
        second_config, _, second = await item(db, "two", description=copied)
        definitions = {first_config.key: first_config, second_config.key: second_config}
        story = await index_item(db, first, definitions, now=NOW)
        joined = await index_item(db, second, definitions, now=NOW)
        assert joined.id == story.id
        view = await story_view(db, story, definitions, now=NOW)
        signals = await observed_trend_signals(db, story, view.sources, now=NOW)
        assert signals.recent_changes == 2
        assert signals.recent_source_additions == 1
        assert signals.recent_reports == 2
        assert signals.independent_source_groups == 2


def test_trend_order_prefers_observed_activity_without_truth_or_importance_score():
    base = dict(
        story_id="00000000-0000-0000-0000-000000000001",
        recent_changes=1,
        recent_source_additions=0,
        recent_reports=1,
        independent_source_groups=1,
        last_updated_at=NOW,
    )
    quiet = TrendSignals.model_validate(base)
    active = TrendSignals.model_validate(
        base
        | {
            "story_id": "00000000-0000-0000-0000-000000000002",
            "recent_source_additions": 1,
        }
    )
    assert active.sort_key < quiet.sort_key
    payload = active.model_dump()
    assert "importance_score" not in payload and "verification_status" not in payload


def test_old_activity_falls_outside_trend_window():
    signals = TrendSignals(
        story_id="00000000-0000-0000-0000-000000000003",
        recent_changes=0,
        recent_source_additions=0,
        recent_reports=0,
        independent_source_groups=3,
        last_updated_at=NOW - timedelta(days=2),
    )
    assert signals.recent_changes == 0 and signals.recent_reports == 0


@pytest.mark.asyncio
async def test_order_trending_items_ranks_activity_and_never_drops_authorized_items(ai_database):
    await seed(ai_database)
    async with ai_database() as db:
        copied = (
            "A sufficiently long report with identical wording and context copied "
            "from the same observation so exact-copy grouping is deterministic."
        )
        first_config, _, first = await item(db, description=copied)
        second_config, _, second = await item(db, "two", description=copied)
        quiet_config, _, quiet = await item(db, "three", headline="Single routine report")
        loose_config, _, loose = await item(db, "four", headline="Never indexed report")
        definitions = {
            first_config.key: first_config,
            second_config.key: second_config,
            quiet_config.key: quiet_config,
            loose_config.key: loose_config,
        }
        active = await index_item(db, first, definitions, now=NOW)
        assert (await index_item(db, second, definitions, now=NOW)).id == active.id
        await index_item(db, quiet, definitions, now=NOW)
        views = tuple(
            [
                await item_view(db, row, definitions, now=NOW)
                for row in (quiet, loose, first, second)
            ]
        )
        ordered = await order_trending_items(db, views, now=NOW)
        assert [view.id for view in ordered[:2]] == sorted(
            (first.id, second.id), key=lambda value: value.hex
        )
        assert [view.id for view in ordered[2:]] == [quiet.id, loose.id]
        assert {view.id for view in ordered} == {view.id for view in views}
