"""Application entry point for grounded operational suggestions, never execution."""

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import select

from navox.ai.domains import Domain, DomainOutput, render_domain, validate_domain
from navox.ai.foundation.contracts import Contract, ProviderPolicy
from navox.ai.operational_context import OperationalContext, operational_task
from navox.ai.runtime import GatewayRuntime
from navox.ai.sessions import append_turn, authorize
from navox.core.settings import Settings
from navox.db.communications import AssistantSession


class OperationalRequest(Contract):
    item_ids: tuple[UUID, ...] = Field(min_length=1, max_length=12)
    instructions: str = Field(min_length=1, max_length=2000)
    target_id: UUID | None = None
    session_id: UUID | None = None


class OperationalResponse(Contract):
    task_id: UUID
    trace_id: UUID
    context_digest: str
    domain: Domain
    proposal: DomainOutput
    details: list[str]
    session_id: UUID | None = None
    turn_sequence: int | None = None
    actions_executed: Literal[False] = False


async def advise(
    runtime: GatewayRuntime,
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
    domain: Domain,
    request: OperationalRequest,
) -> OperationalResponse:
    factory = runtime.store.factory
    if request.session_id is not None:
        async with factory() as db:
            await authorize(db, user_id, workspace_id)
            session = await db.scalar(
                select(AssistantSession).where(
                    AssistantSession.id == request.session_id,
                    AssistantSession.workspace_id == workspace_id,
                    AssistantSession.user_id == user_id,
                    AssistantSession.status == "active",
                )
            )
            if session is None or domain != Domain.ASSISTANT:
                raise ValueError("Assistant session is unavailable")
    context = OperationalContext(
        factory,
        settings,
        workspace_id=workspace_id,
        user_id=user_id,
        domain=domain,
        item_ids=request.item_ids,
        instructions=request.instructions,
        target_id=request.target_id,
    )
    snapshot = await context.prepare()
    ceiling = runtime.store.operator_policy
    task = operational_task(
        domain,
        workspace_id=workspace_id,
        user_id=user_id,
        sensitivity=snapshot.sensitivity,
        max_cost=ceiling.max_cost,
        policy=ProviderPolicy(
            workspace_id=workspace_id,
            user_id=user_id,
            revision=1,
            grants=ceiling.grants,
            allow_fallback=ceiling.allow_fallback,
            max_fallbacks=ceiling.max_fallbacks if ceiling.allow_fallback else 0,
        ),
    )

    def semantic(value: Any) -> None:
        validate_domain(domain, value, snapshot.context)

    result = await runtime.execute(
        task, context_builder=context, documents={}, semantic_validator=semantic
    )
    proposal = validate_domain(domain, json.loads(result.output.text), snapshot.context)
    sequence = None
    if request.session_id is not None:
        # Opaque references identify this turn; they carry neither content nor
        # authority. Each new turn reloads the selected NavoX-owned state.
        prefix = f"navox:{workspace_id}:{user_id}:"
        async with factory() as db:
            turn = await append_turn(
                db,
                session_id=request.session_id,
                workspace_id=workspace_id,
                user_id=user_id,
                result=result,
                user_input_reference=prefix + str(uuid4()),
                context_snapshot_reference=prefix + str(uuid4()),
                output_reference=prefix + str(uuid4()),
            )
            sequence = turn.sequence
            await db.commit()
    return OperationalResponse(
        task_id=result.task_id,
        trace_id=result.trace_id,
        context_digest=snapshot.fingerprint,
        domain=domain,
        proposal=proposal,
        details=render_domain(proposal, snapshot.context),
        session_id=request.session_id,
        turn_sequence=sequence,
    )
