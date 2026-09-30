"""Refresh orchestration: stale sources refresh before the conversation answer."""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.exceptions import ActivityError, CancelledError

from navox.news import activities
from navox.news.jobs import NewsConversationWork, NewsSourceWork
from navox.workflows import news


def turn() -> NewsConversationWork:
    return NewsConversationWork(str(uuid4()), str(uuid4()), str(uuid4()))


def stale(payload: NewsConversationWork, index: int) -> NewsSourceWork:
    return NewsSourceWork(
        str(uuid4()),
        payload.workspace_id,
        payload.user_id,
        f"refresh:{payload.turn_id}:{index}",
    )


@pytest.mark.asyncio
async def test_stale_sources_refresh_before_the_answer(monkeypatch):
    payload = turn()
    sources = [stale(payload, index) for index in range(2)]
    events = []
    options = {}

    async def execute_activity(fn, received, **kwargs):
        events.append(("activity", fn, received))
        options[fn] = kwargs
        return sources if fn is activities.prepare_news_conversation_activity else None

    async def execute_child_workflow(fn, received, **kwargs):
        events.append(("child", fn, received, kwargs["id"]))
        assert received in sources

    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", execute_child_workflow)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    await news.NewsConversationRefreshWorkflow().run(payload)

    assert [event[0] for event in events] == ["activity", "child", "child", "activity"]
    assert events[0][1] is activities.prepare_news_conversation_activity
    assert events[0][2] is payload
    assert [event[2] for event in events[1:3]] == sources
    assert [event[3] for event in events[1:3]] == [
        f"news-source:{item.source_id}:{item.request_id}" for item in sources
    ]
    assert events[-1][1] is activities.news_conversation_activity
    assert events[-1][2] is payload
    answer = options[activities.news_conversation_activity]
    assert answer["start_to_close_timeout"] == timedelta(minutes=2)
    assert answer["retry_policy"].maximum_attempts == 1
    assert vars(payload) == {
        "turn_id": payload.turn_id,
        "workspace_id": payload.workspace_id,
        "user_id": payload.user_id,
    }
    assert all(
        set(vars(event[2])) == {"source_id", "workspace_id", "user_id", "request_id", "defer"}
        for event in events[1:3]
    )


@pytest.mark.asyncio
async def test_empty_refresh_list_runs_only_the_answer(monkeypatch):
    payload = turn()
    calls = []
    children = []

    async def execute_activity(fn, received, **kwargs):
        calls.append(fn)
        return []

    async def execute_child_workflow(*args, **kwargs):
        children.append(kwargs["id"])

    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", execute_child_workflow)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    await news.NewsConversationRefreshWorkflow().run(payload)

    assert calls == [
        activities.prepare_news_conversation_activity,
        activities.news_conversation_activity,
    ]
    assert children == []


@pytest.mark.asyncio
async def test_failing_ingestion_still_answers(monkeypatch, caplog):
    payload = turn()
    calls = []

    async def execute_activity(fn, received, **kwargs):
        calls.append(fn)
        return [stale(payload, 0)] if fn is activities.prepare_news_conversation_activity else None

    async def failing_child(*args, **kwargs):
        raise RuntimeError("A source is unavailable")

    logger = logging.getLogger("navox.tests.news-conversation-refresh")
    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", failing_child)
    monkeypatch.setattr(news.workflow, "logger", logger)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    with caplog.at_level(logging.WARNING, logger=logger.name):
        await news.NewsConversationRefreshWorkflow().run(payload)

    assert calls == [
        activities.prepare_news_conversation_activity,
        activities.news_conversation_activity,
    ]
    assert "deferred" in caplog.text


@pytest.mark.asyncio
async def test_unavailable_refresh_preparation_still_answers(monkeypatch, caplog):
    payload = turn()
    calls = []

    async def execute_activity(fn, received, **kwargs):
        calls.append(fn)
        if fn is activities.prepare_news_conversation_activity:
            raise ActivityError(
                "News conversation refresh unavailable",
                scheduled_event_id=1,
                started_event_id=2,
                identity="worker",
                activity_type="prepare_news_conversation_activity",
                activity_id="1",
                retry_state=None,
            )
        return None

    async def unexpected_child(*args, **kwargs):
        raise AssertionError("No refresh work should be scheduled without identifiers")

    logger = logging.getLogger("navox.tests.news-conversation-refresh")
    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", unexpected_child)
    monkeypatch.setattr(news.workflow, "logger", logger)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    with caplog.at_level(logging.WARNING, logger=logger.name):
        await news.NewsConversationRefreshWorkflow().run(payload)

    assert calls == [
        activities.prepare_news_conversation_activity,
        activities.news_conversation_activity,
    ]
    assert "deferred" in caplog.text


@pytest.mark.asyncio
async def test_pre_patch_history_keeps_the_old_path(monkeypatch):
    payload = turn()
    calls = []
    markers = []

    async def execute_activity(fn, received, **kwargs):
        calls.append(fn)

    async def unexpected_child(*args, **kwargs):
        raise AssertionError("Pre-patch histories never refresh sources")

    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", unexpected_child)
    monkeypatch.setattr(news.workflow, "patched", lambda marker: markers.append(marker) or False)

    await news.NewsConversationRefreshWorkflow().run(payload)

    assert calls == [activities.news_conversation_activity]
    assert markers == ["news-conversation-refresh-v1"]


@pytest.mark.asyncio
async def test_refresh_cancellation_propagates_before_the_answer(monkeypatch):
    payload = turn()
    calls = []

    async def execute_activity(fn, received, **kwargs):
        calls.append(fn)
        return [stale(payload, 0)]

    async def cancelled_child(*args, **kwargs):
        raise CancelledError("Refresh cancelled")

    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", cancelled_child)
    monkeypatch.setattr(news.workflow, "logger", logging.getLogger(__name__))
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    with pytest.raises(CancelledError):
        await news.NewsConversationRefreshWorkflow().run(payload)

    assert calls == [activities.prepare_news_conversation_activity]


@pytest.mark.asyncio
async def test_refresh_children_are_bounded_and_awaited(monkeypatch):
    payload = turn()
    sources = [stale(payload, index) for index in range(6)]
    active = peak = 0

    async def execute_activity(fn, received, **kwargs):
        return sources

    async def slow_child(fn, received, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1

    monkeypatch.setattr(news.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(news.workflow, "execute_child_workflow", slow_child)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)

    await news.NewsConversationRefreshWorkflow().run(payload)

    assert peak == 4 and active == 0
