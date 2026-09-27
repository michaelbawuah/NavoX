from uuid import uuid4

import pytest

from navox.ai.foundation.contracts import Provider, ProviderGrant, Sensitivity
from navox.ai.manage import publish_policy
from navox.ai.routing import PolicyRules, UserPreferences, preference_scope_key
from navox.db.ai_registry import AIRoutingPolicy, AITaskRun
from navox.db.models import User, Workspace, WorkspaceMembership


@pytest.mark.asyncio
async def test_preferences_preserve_grants_and_reject_stale_or_privilege_changes(subscription_env):
    env = subscription_env
    rules = PolicyRules(
        grants=(
            ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PUBLIC})),
        )
    )
    env.settings.ai_provider_policy = rules.model_dump(mode="json")
    initial = await env.client.get("/api/v1/ai/settings")
    assert initial.status_code == 200 and initial.json()["revision"] == 0
    choice = {"expected_revision": 0, "preferred_provider": "xai", "allow_fallback": True}
    response = await env.client.patch("/api/v1/ai/settings", json=choice)
    assert response.status_code == 200 and response.json()["revision"] == 1
    row = await env.database.get(
        AIRoutingPolicy, (env.workspace_id, preference_scope_key(env.user_id))
    )
    stored = UserPreferences.model_validate_json(row.policy)
    assert stored.preferred_provider == Provider.XAI and "grants" not in stored.model_dump()
    assert await env.database.get(AIRoutingPolicy, (env.workspace_id, str(env.user_id))) is None
    assert (await env.client.patch("/api/v1/ai/settings", json=choice)).status_code == 409
    assert (
        await env.client.patch(
            "/api/v1/ai/settings",
            json={
                **choice,
                "expected_revision": 1,
                "grants": [{"provider": "xai", "sensitivities": ["SENSITIVE"]}],
            },
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_preferences_cannot_overwrite_operator_user_fallback_ceiling(subscription_env):
    from test_ai_gateway_foundation import make_policy, make_registry, make_task

    from navox.ai.foundation.persistence import RegistryStore
    from navox.ai.store import GatewayStore

    env = subscription_env
    grants = (
        ProviderGrant(provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PUBLIC})),
    )
    operator = PolicyRules(grants=grants, allow_fallback=True, max_fallbacks=3)
    user_limit = PolicyRules(grants=grants, allow_fallback=True, max_fallbacks=1)
    env.settings.ai_provider_policy = operator.model_dump(mode="json")
    async with env.factory() as db:
        await RegistryStore(db).publish(make_registry(), expected_revision=0)
        await publish_policy(
            db,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            expected_revision=0,
            policy=user_limit,
        )
        await db.commit()
    task = make_task(
        workspace_id=env.workspace_id,
        user_id=env.user_id,
        sensitivity=Sensitivity.PUBLIC,
        provider_policy=make_policy(
            workspace_id=env.workspace_id,
            user_id=env.user_id,
            grants=grants,
            allow_fallback=True,
            max_fallbacks=3,
        ),
    )
    for revision, allow, expected_limit in ((0, True, 1), (1, False, 0), (2, True, 1)):
        result = await env.client.patch(
            "/api/v1/ai/settings",
            json={"expected_revision": revision, "allow_fallback": allow},
        )
        assert result.status_code == 200
        snapshot = await GatewayStore(env.factory, operator).snapshot(task)
        assert snapshot.policy.max_fallbacks == expected_limit
        async with env.factory() as db:
            row = await db.get(AIRoutingPolicy, (env.workspace_id, str(env.user_id)))
            assert row.revision == 1 and PolicyRules.model_validate_json(row.policy) == user_limit


@pytest.mark.asyncio
async def test_activity_scopes_trace_rows_and_keeps_unknown_cost_unknown(subscription_env):
    env = subscription_env
    other_user, other_workspace = uuid4(), uuid4()
    async with env.factory() as db:
        db.add_all(
            [
                User(id=other_user, email="other@example.com"),
                Workspace(id=other_workspace, name="Other"),
            ]
        )
        await db.flush()
        db.add(WorkspaceMembership(workspace_id=other_workspace, user_id=other_user))
        for user, workspace in ((env.user_id, env.workspace_id), (other_user, other_workspace)):
            db.add(
                AITaskRun(
                    task_id=uuid4(),
                    workspace_id=workspace,
                    user_id=user,
                    trace_id=uuid4(),
                    task_type="extract",
                    profile="EXTRACTION_FAST",
                    prompt="fixture@v1",
                    schema="fixture@v1",
                    status="FAILED",
                    error_code="unavailable",
                )
            )
        await db.commit()
    result = await env.client.get("/api/v1/ai/activity")
    assert result.status_code == 200
    rows = result.json()["runs"]
    assert len(rows) == 1 and rows[0]["estimated_cost"] is None
    assert rows[0]["cost_currency"] == "USD" and rows[0]["error_code"] == "unavailable"
    assert "context" not in rows[0] and "output" not in rows[0]


@pytest.mark.asyncio
async def test_operator_policy_updates_require_current_revision_and_real_scope(subscription_env):
    from navox.ai.foundation.persistence import RegistryConflict

    env = subscription_env
    async with env.factory() as db:
        await publish_policy(
            db,
            workspace_id=env.workspace_id,
            user_id=None,
            expected_revision=0,
            policy=PolicyRules(),
        )
        await db.commit()
        with pytest.raises(RegistryConflict):
            await publish_policy(
                db,
                workspace_id=env.workspace_id,
                user_id=None,
                expected_revision=0,
                policy=PolicyRules(allow_fallback=True),
            )
        await db.rollback()
        with pytest.raises(RegistryConflict):
            await publish_policy(
                db,
                workspace_id=env.workspace_id,
                user_id=uuid4(),
                expected_revision=0,
                policy=PolicyRules(),
            )


@pytest.mark.asyncio
async def test_provider_choices_follow_active_catalog_and_intersected_sensitivities(
    subscription_env,
):
    from test_ai_evaluation import model_for

    from navox.ai.catalog import catalog_template
    from navox.ai.foundation.persistence import RegistryStore

    env = subscription_env
    models = tuple(model_for(p).model_copy(update={"enabled": p != Provider.XAI}) for p in Provider)
    operator = PolicyRules(
        grants=tuple(
            ProviderGrant(
                provider=p, sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL})
            )
            for p in Provider
        )
    )
    env.settings.ai_provider_policy = operator.model_dump(mode="json")
    async with env.factory() as db:
        await RegistryStore(db).publish(
            catalog_template().model_copy(update={"models": models}), expected_revision=0
        )
        await publish_policy(
            db,
            workspace_id=env.workspace_id,
            user_id=None,
            expected_revision=0,
            policy=PolicyRules(
                grants=(
                    ProviderGrant(
                        provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PUBLIC})
                    ),
                    ProviderGrant(
                        provider=Provider.GEMINI, sensitivities=frozenset({Sensitivity.PERSONAL})
                    ),
                    ProviderGrant(
                        provider=Provider.XAI, sensitivities=frozenset({Sensitivity.PUBLIC})
                    ),
                )
            ),
        )
        await db.commit()
    response = await env.client.get("/api/v1/ai/settings")
    assert response.status_code == 200
    assert response.json()["available_providers"] == ["openai"]
