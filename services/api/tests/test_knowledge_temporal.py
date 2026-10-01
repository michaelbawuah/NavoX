"""Opt-in real Temporal/PostgreSQL checks; all external providers stay disabled."""

import asyncio
import os
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from navox.core.settings import Settings
from navox.db.knowledge import KnowledgeResource
from navox.db.models import ConnectorResource
from navox.knowledge import activities
from navox.knowledge.jobs import KnowledgeEmbeddingWork, KnowledgeResourceWork
from navox.workflows.knowledge import (
    KnowledgeEmbeddingWorkflow,
    KnowledgeEntityResolutionWorkflow,
    KnowledgePermissionRefreshWorkflow,
    KnowledgeReconciliationWorkflow,
    KnowledgeReindexWorkflow,
    KnowledgeResourceDeleteWorkflow,
    KnowledgeResourceIngestWorkflow,
    KnowledgeResourceUpdateWorkflow,
)
from tests.knowledge_search_support import load_world
from tests.test_knowledge_search import search_engine as search_engine
from tests.test_knowledge_search import search_factory as search_factory


@pytest.mark.asyncio
async def test_real_temporal_projection_replay_and_disabled_cleanup(search_factory, monkeypatch):
    target = os.environ.get("NAVOX_TEMPORAL_TEST_TARGET")
    if not target or not os.environ.get("NAVOX_CONNECTOR_TEST_DSN", "").startswith("postgresql"):
        pytest.skip("Requires explicitly configured disposable PostgreSQL and Temporal")
    world = await load_world(search_factory)
    settings = Settings(_env_file=None, app_environment="test", knowledge_enabled=True)
    monkeypatch.setattr(activities, "get_settings", lambda: settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: search_factory)
    payload = KnowledgeResourceWork(
        str(world.workspace_id), str(world.user_id), str(world.message_id), "a" * 64
    )
    client = await Client.connect(target)
    workflows = [
        KnowledgeResourceIngestWorkflow,
        KnowledgeResourceUpdateWorkflow,
        KnowledgeResourceDeleteWorkflow,
        KnowledgeEmbeddingWorkflow,
        KnowledgeEntityResolutionWorkflow,
        KnowledgePermissionRefreshWorkflow,
        KnowledgeReindexWorkflow,
        KnowledgeReconciliationWorkflow,
    ]
    queue = f"knowledge-integration-{uuid4().hex}"
    histories = []
    async with Worker(
        client,
        task_queue=queue,
        workflows=workflows,
        activities=[
            activities.knowledge_resource_activity,
            activities.knowledge_delete_activity,
            activities.knowledge_reindex_activity,
            activities.knowledge_embedding_activity,
            activities.knowledge_pending_embeddings_activity,
            activities.knowledge_source_page_activity,
        ],
    ):

        async def run(workflow, work):
            handle = await client.start_workflow(
                workflow.run,
                work,
                id=f"knowledge-check-{uuid4().hex}",
                task_queue=queue,
                execution_timeout=timedelta(seconds=30),
            )
            result = await handle.result()
            histories.append(await handle.fetch_history())
            return result

        assert await run(KnowledgeResourceIngestWorkflow, payload) == "INDEXED"
        async with search_factory() as database:
            resource = await database.scalar(
                select(KnowledgeResource).where(
                    KnowledgeResource.source_resource_id == world.message_id
                )
            )
            resource_id = resource.id
            assert resource.normalized_text and "budget" in resource.normalized_text
        for workflow in (
            KnowledgeResourceUpdateWorkflow,
            KnowledgeEntityResolutionWorkflow,
            KnowledgePermissionRefreshWorkflow,
            KnowledgeReindexWorkflow,
        ):
            assert await run(workflow, payload) == "INDEXED"
        assert (
            await run(
                KnowledgeResourceUpdateWorkflow, replace(payload, expected_content_hash="f" * 64)
            )
            == "STALE_EVENT"
        )
        assert (
            await run(
                KnowledgeEmbeddingWorkflow,
                KnowledgeEmbeddingWork(
                    str(world.workspace_id), str(world.user_id), str(resource_id), str(uuid4())
                ),
            )
            == "UNAVAILABLE"
        )
        async with search_factory() as database:
            source = await database.get(ConnectorResource, world.message_id)
            source.deleted = True
            await database.commit()
        settings.knowledge_enabled = False
        assert await run(KnowledgeResourceDeleteWorkflow, payload) == "CLEANED"
        assert await run(KnowledgeResourceUpdateWorkflow, payload) == "CLEANED"
        async with search_factory() as database:
            resource = await database.get(KnowledgeResource, resource_id)
            assert resource.normalized_text is None and resource.deleted_at is not None
        reconciliation = await client.start_workflow(
            KnowledgeReconciliationWorkflow.run,
            None,
            id=f"knowledge-reconcile-check-{uuid4().hex}",
            task_queue=queue,
            execution_timeout=timedelta(seconds=30),
        )
        for _ in range(100):
            history = await reconciliation.fetch_history()
            if any(event.HasField("timer_started_event_attributes") for event in history.events):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Reconciliation did not finish its page before the deadline")
        await reconciliation.cancel()
        try:
            await reconciliation.result()
        except Exception:
            description = await reconciliation.describe()
            assert description.status.name == "CANCELED"
        histories.append(await reconciliation.fetch_history())
    replayer = Replayer(workflows=workflows)
    for history in histories:
        await replayer.replay_workflow(history)
        assert "budget forecast" not in history.to_json()
        assert "Quarterly planning" not in history.to_json()
