"""Owned source outages are explicit without disclosing another workspace's sources."""

import pytest

from navox.core.settings import Settings
from navox.db.models import ConnectorConnection
from navox.knowledge.search_contracts import SearchRequest
from navox.knowledge.service import search_knowledge
from tests.knowledge_search_support import NOW, load_world
from tests.test_knowledge_search import search_engine as search_engine
from tests.test_knowledge_search import search_factory as search_factory


@pytest.mark.asyncio
async def test_source_outage_labels_respect_owner_and_query_source_scope(search_factory):
    world = await load_world(search_factory)
    async with search_factory() as database:
        connection = await database.get(ConnectorConnection, world.connection_id)
        connection.health_state = "SYNC_FAILED"
        await database.commit()

        async def search(user_id, workspace_id, source_ids=()):
            return await search_knowledge(
                database,
                workspace_id=workspace_id,
                user_id=user_id,
                request=SearchRequest(query="budget", sources=source_ids),
                now=NOW,
                settings=Settings(_env_file=None, knowledge_enabled=True),
                record_recent=False,
            )

        result = await search(world.user_id, world.workspace_id)
        assert len(result.coverage.source_issues) == 1
        issue = result.coverage.source_issues[0]
        assert issue.connection_id == world.connection_id and issue.source_label == "Gmail"
        assert issue.state == "SYNC_FAILED"
        assert "SOURCE_UNAVAILABLE" in result.coverage.partial_reasons
        assert not (
            await search(world.user_id, world.workspace_id, (world.calendar_connection_id,))
        ).coverage.source_issues
        assert not (
            await search(world.other_user_id, world.other_workspace_id)
        ).coverage.source_issues
        connection.authorized_capabilities = []
        await database.commit()
        assert not (await search(world.user_id, world.workspace_id)).coverage.source_issues
