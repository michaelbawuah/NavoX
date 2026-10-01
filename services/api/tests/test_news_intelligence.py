"""Synthetic pipeline tests; no provider qualification or live source claims."""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_news_conversations import SelectingRuntime
from test_news_foundation import NOW, OTHER_USER, OTHER_WORKSPACE, USER, WORKSPACE, seed
from test_news_stories import item, review, span

from navox.ai.foundation.contracts import JSONDocument
from navox.ai.runtime import GatewayUnavailable
from navox.core.settings import Settings
from navox.db.models import User
from navox.db.news import NewsClaim, NewsIntelligenceRun, NewsStory, NewsStoryVersion
from navox.news import activities, ai_context, intelligence
from navox.news.ai_contracts import EXTRACTION, SYNTHESIS
from navox.news.contracts import NewsError
from navox.news.evidence import admit_claim, refresh_verification, review_evidence
from navox.news.intelligence import begin_run, run_story_intelligence
from navox.news.jobs import NewsSourceWork, NewsWorkResult
from navox.news.registry import current_rights, revoke_rights
from navox.news.stories import index_item
from navox.news.synthesis import summary_view
from navox.workflows import news


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    for module in (ai_context, intelligence, activities):
        monkeypatch.setattr(module, "datetime", Clock)


class PipelineRuntime(SelectingRuntime):
    def __init__(self):
        super().__init__()
        self.after_return = None
        self.bad_output = False
        self.fail_synthesis = False

    async def execute(self, task, *, context_builder, documents, semantic_validator):
        context = await context_builder.build(task, documents)
        self.calls.append((task, context))
        snapshot = json.loads(context.content.text)["news_context"]
        first = snapshot["items"][0]
        if task.prompt == EXTRACTION:
            output = {
                "claims": [
                    {
                        "item_id": first["id"],
                        "item_revision": first["revision"],
                        "field": "headline",
                        "start": 0,
                        "end": len(first["headline"]),
                    }
                ]
            }
            if self.bad_output:
                output["verification_status"] = "VERIFIED"
        else:
            assert task.prompt == SYNTHESIS
            if self.fail_synthesis:
                raise ValueError("PRIVATE PROVIDER ERROR MUST NOT BE PERSISTED")
            claims = snapshot["claims"]
            uncertain = any(
                c["status"] not in {"VERIFIED", "CORROBORATED", "ATTRIBUTED"} for c in claims
            )
            output = {
                "headline_item_id": first["id"],
                "sections": [
                    {
                        "heading": "what_is_unclear" if uncertain else "what_happened",
                        "claim_ids": [c["id"] for c in claims],
                    }
                ]
                if claims
                else [],
            }
        semantic_validator(output)
        await context_builder.build(task, documents)
        if self.after_return:
            await self.after_return(task)
        from types import SimpleNamespace

        return SimpleNamespace(output=JSONDocument(text=json.dumps(output)), trace_id=uuid4())


async def setup(factory, monkeypatch, *, verified=False):
    await seed(factory)
    async with factory() as db:
        config, source, record = await item(db)
        story = await index_item(db, record, {config.key: config}, now=NOW)
        if verified:
            claim = await admit_claim(db, story, span(record), {config.key: config}, now=NOW)
            await review_evidence(
                db, claim, span(record), review("PRIMARY_RECORD"), {config.key: config}, now=NOW
            )
            await refresh_verification(db, story, {config.key: config}, now=NOW)
        await db.commit()
    settings = Settings(
        _env_file=None,
        news_feed_enabled=True,
        news_intelligence_enabled=True,
        news_chat_enabled=False,
        news_source_catalog=[config.model_dump(mode="json")],
    )
    runtime = PipelineRuntime()

    async def build(*args):
        return runtime

    monkeypatch.setattr(intelligence, "build_runtime", build)
    return settings, runtime, config, source, record, story


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [False, True])
async def test_durable_pipeline_preserves_evidence_status_and_never_duplicates_spend(
    ai_database, monkeypatch, verified
):
    settings, runtime, config, source, record, story = await setup(
        ai_database, monkeypatch, verified=verified
    )
    result = await run_story_intelligence(
        ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
    )
    assert result == "READY"
    assert [t.prompt for t, _ in runtime.calls] == [EXTRACTION, SYNTHESIS]
    assert all(
        t.profile == "NEWS_SYNTHESIS" and t.latency_class == "BACKGROUND" and t.max_cost <= 0.025
        for t, _ in runtime.calls
    )
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    assert len(runtime.calls) == 2
    async with ai_database() as db:
        saved = await db.scalar(select(NewsIntelligenceRun))
        assert saved.status == "READY" and len(saved.trace_ids) == 2
        assert record.headline not in json.dumps(saved.selection)
        assert await db.scalar(select(func.count()).select_from(NewsIntelligenceRun)) == 1
        summary = await summary_view(
            db, ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert summary.status == "READY" and not summary.actions_executed
        fact = summary.sections[0].facts[0]
        assert fact.source_url == record.canonical_url and fact.text == record.headline
        assert fact.status == ("VERIFIED" if verified else "UNCONFIRMED")
        assert summary.sections[0].heading == ("what_happened" if verified else "what_is_unclear")
        history = list(
            await db.scalars(
                select(NewsStoryVersion.change_kind).order_by(NewsStoryVersion.version)
            )
        )
        assert history[-1] == "SYNTHESIS_PUBLISHED"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", [EXTRACTION, SYNTHESIS])
@pytest.mark.parametrize("change", ["revoke", "pause", "version"])
async def test_change_after_provider_response_blocks_publication(
    ai_database, monkeypatch, phase, change
):
    settings, runtime, _, source, record, story = await setup(ai_database, monkeypatch)

    async def mutate(task):
        if task.prompt != phase:
            return
        async with ai_database() as db:
            if change == "revoke":
                from navox.news.registry import owned_source

                current = await owned_source(
                    db, source.id, workspace_id=WORKSPACE, user_id=USER, lock=True
                )
                await revoke_rights(db, current, now=NOW)
            elif change == "pause":
                owner = await db.get(User, USER)
                owner.agent_paused = True
            else:
                current_story = await db.get(NewsStory, story.id)
                current_story.version += 1
            await db.commit()

    runtime.after_return = mutate
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "UNAVAILABLE"
    )
    async with ai_database() as db:
        rows = list(await db.scalars(select(NewsIntelligenceRun)))
        assert not any(row.status == "READY" or row.selection for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid", "synthesis", "unqualified"])
async def test_failed_run_has_fixed_error_and_no_blind_retry(ai_database, monkeypatch, failure):
    settings, runtime, _, _, record, story = await setup(ai_database, monkeypatch)
    runtime.bad_output = failure == "invalid"
    runtime.fail_synthesis = failure == "synthesis"
    if failure == "unqualified":

        async def unavailable(*args):
            raise GatewayUnavailable()

        monkeypatch.setattr(intelligence, "build_runtime", unavailable)
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "UNAVAILABLE"
    )
    calls = len(runtime.calls)
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    assert len(runtime.calls) == calls
    async with ai_database() as db:
        run = await db.scalar(select(NewsIntelligenceRun))
        assert run.failure_code == "ai_unavailable" and run.selection is None
        assert "PRIVATE" not in str(run.__dict__)


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["news_feed_enabled", "news_intelligence_enabled"])
async def test_disabled_switch_blocks_provider_and_ledger(ai_database, monkeypatch, switch):
    settings, runtime, _, _, _, story = await setup(ai_database, monkeypatch)
    setattr(settings, switch, False)
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    assert not runtime.calls
    async with ai_database() as db:
        assert await db.scalar(select(func.count()).select_from(NewsIntelligenceRun)) == 0


@pytest.mark.asyncio
async def test_owner_scope_and_read_time_expiry(ai_database, monkeypatch):
    settings, runtime, _, _, _, story = await setup(ai_database, monkeypatch)
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=OTHER_WORKSPACE, user_id=OTHER_USER
        )
        == "UNAVAILABLE"
    )
    assert not runtime.calls
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "READY"
    )
    async with ai_database() as db:
        with pytest.raises(NewsError):
            await summary_view(
                db,
                ai_database,
                settings,
                story.id,
                workspace_id=OTHER_WORKSPACE,
                user_id=OTHER_USER,
                now=NOW,
            )
        expired = await summary_view(
            db,
            ai_database,
            settings,
            story.id,
            workspace_id=WORKSPACE,
            user_id=USER,
            now=NOW + timedelta(minutes=31),
        )
        assert expired.status == "SOURCES_CHANGED" and not expired.sections


@pytest.mark.asyncio
async def test_summary_rechecks_claim_review_changes_not_only_story_version(
    ai_database, monkeypatch
):
    settings, _, config, _, record, story = await setup(ai_database, monkeypatch, verified=True)
    await run_story_intelligence(
        ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
    )
    async with ai_database() as db:
        claim = await db.scalar(select(NewsClaim))
        await review_evidence(
            db,
            claim,
            span(record),
            review("PRIMARY_RECORD", "CONTRADICTS"),
            {config.key: config},
            now=NOW,
        )
        await db.commit()
    async with ai_database() as db:
        result = await summary_view(
            db, ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER, now=NOW
        )
        assert result.status == "SOURCES_CHANGED" and result.sections == ()


@pytest.mark.asyncio
async def test_per_owner_budget_ledger_includes_incomplete_runs(ai_database, monkeypatch):
    settings, runtime, config, _, _, first_story = await setup(ai_database, monkeypatch)
    assert await begin_run(
        ai_database, settings, first_story.id, workspace_id=WORKSPACE, user_id=USER
    )
    for n in range(1, 5):
        async with ai_database() as db:
            cfg, _, record = await item(db, f"budget-{n}")
            settings.news_source_catalog.append(cfg.model_dump(mode="json"))
            story = await index_item(db, record, {cfg.key: cfg}, now=NOW)
            await db.commit()
        if n < 4:
            assert await begin_run(
                ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
            )
        else:
            with pytest.raises(NewsError, match="rate_limited"):
                await begin_run(
                    ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
                )
    assert not runtime.calls


@pytest.mark.asyncio
async def test_workflow_preserves_ingestion_and_uses_one_paid_activity_attempt(monkeypatch):
    calls = []

    async def execute(fn, payload, **kwargs):
        calls.append((fn, kwargs))
        if fn == activities.ingest_news_source_activity:
            # A payload that deferred exact indexing to the clustering activity.
            return NewsWorkResult("COMPLETED", 1, 0, True)
        if fn == activities.news_clustering_activity:
            return NewsWorkResult("COMPLETED", 1)
        return 1

    monkeypatch.setattr(news.workflow, "execute_activity", execute)
    monkeypatch.setattr(news.workflow, "patched", lambda _: True)
    result = await news.NewsSourceIngestionWorkflow().run(
        NewsSourceWork(str(uuid4()), str(WORKSPACE), str(USER), str(uuid4()))
    )
    # Bounded semantic clustering runs between ingestion and the single paid
    # intelligence attempt when the payload deferred indexing, and each paid
    # phase keeps one attempt.
    assert result.stored_count == 1
    assert [call[0] for call in calls[1:]] == [
        activities.news_clustering_activity,
        activities.news_intelligence_activity,
    ]
    assert calls[1][1]["retry_policy"].maximum_attempts == 1
    assert calls[2][1]["retry_policy"].maximum_attempts == 1


@pytest.mark.asyncio
async def test_cleanup_closes_abandoned_run_and_removes_expired_selection(ai_database, monkeypatch):
    settings, runtime, _, _, _, story = await setup(ai_database, monkeypatch)
    run_id, _ = await begin_run(
        ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
    )
    async with ai_database() as db:
        row = await db.get(NewsIntelligenceRun, run_id)
        row.created_at = NOW - timedelta(minutes=11)
        row.expires_at = NOW - timedelta(seconds=1)
        row.selection = {"headline_item_id": str(uuid4()), "sections": []}
        row.claim_snapshot = {"old-id": "old-digest"}
        await db.commit()
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    await activities.news_sources_activity(None)
    async with ai_database() as db:
        row = await db.get(NewsIntelligenceRun, run_id)
        assert row.status == "UNAVAILABLE" and row.selection is None and not row.claim_snapshot
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    assert not runtime.calls


@pytest.mark.asyncio
async def test_scheduler_skips_processed_versions_before_its_page_limit(ai_database, monkeypatch):
    settings, _, config, source, record, story = await setup(ai_database, monkeypatch)
    await run_story_intelligence(
        ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
    )
    from navox.news.contracts import NewsItemInput
    from navox.news.ingestion import store_item

    async with ai_database() as db:
        rights = await current_rights(db, source)
        candidate = NewsItemInput(
            external_id="second-story",
            headline="Another separate observation",
            canonical_url="https://one.example.com/second",
            published_at=NOW,
        )
        row = await store_item(db, source, rights, config, candidate, now=NOW)
        next_story = await index_item(db, row, {config.key: config}, now=NOW)
        await db.commit()
    calls = []

    async def collect(factory, config, identifier, **scope):
        calls.append(identifier)
        return "READY"

    monkeypatch.setattr(intelligence, "run_story_intelligence", collect)
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: ai_database)
    payload = NewsSourceWork(str(source.id), str(WORKSPACE), str(USER), str(uuid4()))
    assert await activities.news_intelligence_activity(payload) == 1
    assert calls == [next_story.id]


@pytest.mark.asyncio
async def test_concurrent_workers_do_not_repeat_provider_work(ai_database, monkeypatch):
    import asyncio

    settings, runtime, _, _, _, story = await setup(ai_database, monkeypatch)
    outcomes = await asyncio.gather(
        *[
            run_story_intelligence(
                ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
            )
            for _ in range(2)
        ]
    )
    assert outcomes.count("READY") == 1
    assert set(outcomes) <= {"READY", "SKIPPED", "UNAVAILABLE"}
    assert len(runtime.calls) == 2
    async with ai_database() as db:
        assert await db.scalar(select(func.count()).select_from(NewsIntelligenceRun)) == 1


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed_or_retried(ai_database, monkeypatch):
    import asyncio

    settings, runtime, _, _, _, story = await setup(ai_database, monkeypatch)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(runtime, "execute", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
    assert (
        await run_story_intelligence(
            ai_database, settings, story.id, workspace_id=WORKSPACE, user_id=USER
        )
        == "SKIPPED"
    )
    async with ai_database() as db:
        run = await db.scalar(select(NewsIntelligenceRun))
        assert run.status == "PROCESSING" and run.selection is None
