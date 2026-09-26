"""Owner-scoped preferences and content-free task activity; no model-selected policy."""

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from navox.ai.routing import PolicyRules, UserPreferences, preference_scope_key
from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.db.ai_registry import AIRoutingPolicy, AITaskRun
from navox.db.models import User

router = APIRouter(prefix="/ai", tags=["ai"])


class Preference(UserPreferences):
    expected_revision: int = Field(ge=0)


@router.get("/settings")
async def get_preferences(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> dict[str, object]:
    row = await database.get(
        AIRoutingPolicy, (account.workspace.id, preference_scope_key(account.user.id))
    )
    operator = PolicyRules.model_validate(settings.ai_provider_policy)
    rules = [operator]
    for scope in ("workspace", str(account.user.id)):
        rule = await database.get(AIRoutingPolicy, (account.workspace.id, scope))
        if rule is not None:
            rules.append(PolicyRules.model_validate_json(rule.policy))
    policy = (
        UserPreferences.model_validate_json(row.policy)
        if row
        else UserPreferences(
            preferred_provider=next(
                (r.preferred_provider for r in reversed(rules) if r.preferred_provider), None
            ),
            allow_fallback=all(r.allow_fallback for r in rules),
        )
    )
    return {
        "configured": settings.ai_provider == "automatic",
        "revision": row.revision if row else 0,
        "preferred_provider": policy.preferred_provider,
        "allow_fallback": policy.allow_fallback,
    }


@router.patch("/settings")
async def set_preferences(
    payload: Preference,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    await database.scalar(select(User).where(User.id == account.user.id).with_for_update())
    scope_key = preference_scope_key(account.user.id)
    row = await database.get(
        AIRoutingPolicy, (account.workspace.id, scope_key), populate_existing=True
    )
    if (row.revision if row else 0) != payload.expected_revision:
        raise HTTPException(409, "Preferences changed; reload before saving")
    policy = UserPreferences(
        preferred_provider=payload.preferred_provider, allow_fallback=payload.allow_fallback
    )
    if row is None:
        database.add(
            AIRoutingPolicy(
                workspace_id=account.workspace.id,
                scope_key=scope_key,
                revision=1,
                policy=policy.model_dump_json(),
            )
        )
    else:
        changed = await database.scalar(
            update(AIRoutingPolicy)
            .where(
                AIRoutingPolicy.workspace_id == account.workspace.id,
                AIRoutingPolicy.scope_key == scope_key,
                AIRoutingPolicy.revision == payload.expected_revision,
            )
            .values(revision=payload.expected_revision + 1, policy=policy.model_dump_json())
            .returning(AIRoutingPolicy.revision)
        )
        if changed is None:
            raise HTTPException(409, "Preferences changed; reload before saving")
    try:
        await database.commit()
    except IntegrityError:
        await database.rollback()
        raise HTTPException(409, "Preferences changed; reload before saving") from None
    return await get_preferences(account, database, settings)


@router.get("/activity")
async def activity(
    account: CurrentAccountDependency, database: DatabaseSession
) -> dict[str, object]:
    rows = (
        await database.scalars(
            select(AITaskRun)
            .where(
                AITaskRun.workspace_id == account.workspace.id,
                AITaskRun.user_id == account.user.id,
            )
            .order_by(AITaskRun.created_at.desc(), AITaskRun.id.desc())
            .limit(100)
        )
    ).all()

    def cost(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    return {
        "runs": [
            {
                "id": str(r.id),
                "trace_id": str(r.trace_id),
                "profile": r.profile,
                "provider": r.provider,
                "model": r.model,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "usage": r.usage,
                "estimated_cost": cost(r.estimated_cost),
                "cost_currency": "USD",
                "fallback_count": r.fallback_count,
                "error_code": r.error_code,
                "shadow": r.shadow,
                "prompt": r.prompt,
                "schema": r.schema,
            }
            for r in rows
        ]
    }
