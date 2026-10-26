"""Explicitly qualified background learning; no ad-hoc provider or native fallback."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.ai.configured import build_runtime
from navox.ai.context import ContextDenied, MinimizedContext, reject_credentials
from navox.ai.features import configured_secrets
from navox.ai.foundation.contracts import AITask, JSONDocument, ProviderPolicy, Sensitivity
from navox.ai.routing import rank_eligible
from navox.ai.runtime import GatewayRuntime
from navox.ai.store import GatewayStore
from navox.ai.validation import compile_schema
from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.db.conversation_memory import MemoryLearningJob
from navox.db.models import User
from navox.intelligence.contracts import SourceDocument
from navox.memory import history
from navox.memory.learning import (
    CLAIM_LIFETIME,
    apply_learning,
    current_source,
    validate_output,
)
from navox.memory.learning_contracts import (
    MAX_SOURCE_CHARACTERS,
    MEMORY_LEARNING,
    LearningOutput,
    learning_task,
)
from navox.memory.service import MemoryError, lock_owner

MAX_BACKGROUND_JOBS_PER_DAY = 10


def primary_only(runtime: GatewayRuntime) -> GatewayRuntime:
    return GatewayRuntime(
        GatewayStore(
            runtime.store.factory,
            runtime.store.operator_policy.model_copy(
                update={
                    "allow_shadow": False,
                    "allow_fallback": False,
                    "max_fallbacks": 0,
                }
            ),
        ),
        runtime.adapters,
    )


def task_for(
    runtime: GatewayRuntime, user_id: UUID, workspace_id: UUID, task_id: UUID | None = None
) -> AITask:
    ceiling = runtime.store.operator_policy
    return learning_task(
        user_id,
        workspace_id,
        task_id=task_id,
        max_cost=ceiling.max_cost,
        policy=ProviderPolicy(
            user_id=user_id,
            workspace_id=workspace_id,
            revision=1,
            grants=ceiling.grants,
            allow_fallback=False,
            max_fallbacks=0,
        ),
    )


async def background_configured(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    workspace_id: UUID,
) -> bool:
    if not settings.memory_semantic_learning_enabled or settings.ai_provider != "automatic":
        return False
    try:
        async with asyncio.timeout(5):
            runtime = primary_only(await build_runtime(settings, factory))
            task = task_for(runtime, user_id, workspace_id)
            snapshot = await runtime.store.snapshot(task)
            binding = snapshot.registry.bind_task(task)
            compile_schema(binding.output_schema.document)
            bound = (
                MAX_SOURCE_CHARACTERS * 6
                + 2048
                + len(binding.prompt.instructions.encode())
                + len(binding.output_schema.document.text.encode())
            )
            candidates = rank_eligible(
                task,
                snapshot.registry,
                policy=snapshot.policy,
                available=snapshot.available,
                evaluations=snapshot.evaluations,
                input_bound=bound,
                remaining_budget=task.max_cost,
                preferred=task.preferred_provider,
                weights=snapshot.weights,
            )
            return any(model.reference.provider in runtime.adapters for model, _ in candidates)
    except Exception:
        return False  # Read-only, content-free status; no qualification or probes are granted.


async def native_configured(
    settings: Settings,
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
) -> bool:
    if not (
        settings.student_vox_enabled
        and settings.student_vox_media_enabled
        and settings.student_vox_worker_qualified
        and settings.student_vox_livekit_cloud_qualified
    ):
        return False
    try:
        from navox.vox.inference_config import load_inference_config, route_credential
        from navox.vox.usage import budget_status, get_policy

        config = load_inference_config(settings)
        if config is None:
            return False
        route = config.realtime if config.mode == "realtime" else config.llm
        route_credential(route, settings)
        # Browser memory authority already holds the owner row in this
        # transaction. A second session would deadlock on budget_status's owner
        # lock; use the same transaction for the entire private status read.
        _, policy = await get_policy(database)
        budget = await budget_status(database, user_id, workspace_id)
        return not budget.blocked and any(
            (price.provider, price.model) == (route.provider, route.model)
            and price.input_text is not None
            and price.output_text is not None
            for price in policy.model_pricing
        )
    except Exception:
        return False


class LearningContext:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        job_id: UUID,
        claim_id: UUID,
    ) -> None:
        self.factory, self.settings, self.job_id, self.claim_id = (
            factory,
            settings,
            job_id,
            claim_id,
        )
        self.turns: list[dict[str, Any]] = []

    async def build(
        self, task: AITask, documents: Mapping[UUID, SourceDocument], *, user_request: str = ""
    ) -> MinimizedContext:
        if (
            documents
            or user_request
            or task.prompt != MEMORY_LEARNING
            or task.output_schema != MEMORY_LEARNING
        ):
            raise ContextDenied("Learning context does not accept external source bodies")
        async with self.factory() as database:
            await lock_owner(database, task.user_id)
            job = await database.get(MemoryLearningJob, self.job_id, populate_existing=True)
            if (
                job is None
                or job.status != "processing"
                or job.claim_id != self.claim_id
                or job.task_id != task.id
                or (job.user_id, job.workspace_id) != (task.user_id, task.workspace_id)
            ):
                raise ContextDenied("Memory learning source changed")
            try:
                source, _, self.turns = await current_source(
                    database, self.settings, job, now=await database_now(database)
                )
            except MemoryError:
                raise ContextDenied("Memory learning source changed") from None
            user = await database.get(User, task.user_id)
            payload = {
                "schema_version": "conversation-memory-learning-input.v1",
                "source_kind": source.source_kind,
                "timezone": user.timezone if user else "UTC",
                "completed_student_turns": self.turns,
                "coverage": "last_eight_whole_student_messages_at_most_500_characters_each",
            }
        content = json.dumps(payload, ensure_ascii=False)
        reject_credentials(content, configured_secrets(self.settings))
        return MinimizedContext(
            user_id=task.user_id,
            workspace_id=task.workspace_id,
            source_ids=(),
            sensitivity=Sensitivity.PERSONAL,
            content=JSONDocument(text=content),
        )


async def process_one(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    runtime: GatewayRuntime | None = None,
) -> bool:
    """Claim once and never automatically repeat an uncertain provider attempt."""
    if not settings.memory_semantic_learning_enabled or settings.ai_provider != "automatic":
        return False
    async with factory() as database:
        now = await database_now(database)
        candidates = list(
            await database.execute(
                select(
                    MemoryLearningJob.id,
                    MemoryLearningJob.user_id,
                    MemoryLearningJob.workspace_id,
                )
                .where(MemoryLearningJob.status.in_(("queued", "processing")))
                .order_by(MemoryLearningJob.created_at, MemoryLearningJob.id)
                .limit(20)
            )
        )
    for identifier, user_id, workspace_id in candidates:
        if not await background_configured(settings, factory, user_id, workspace_id):
            continue
        claim = uuid4()
        async with factory() as database:
            await lock_owner(database, user_id)
            job = await database.get(MemoryLearningJob, identifier, populate_existing=True)
            if job is None:
                continue
            now = await database_now(database)
            if job.status == "processing":
                if (
                    job.claimed_at is not None
                    and now - history.utc(job.claimed_at) > CLAIM_LIFETIME
                ):
                    job.status, job.updated_at = "failed", now
                    await database.commit()
                continue  # A lost/uncertain attempt never authorizes a new provider submission.
            if job.status != "queued":
                continue
            used = await database.scalar(
                select(func.count())
                .select_from(MemoryLearningJob)
                .where(
                    MemoryLearningJob.user_id == user_id,
                    MemoryLearningJob.workspace_id == workspace_id,
                    MemoryLearningJob.claimed_at > now - timedelta(days=1),
                    MemoryLearningJob.execution_kind == "background",
                )
            )
            if used is None or used >= MAX_BACKGROUND_JOBS_PER_DAY:
                continue
            try:
                await current_source(database, settings, job, now=now)
            except MemoryError:
                job.status, job.updated_at = "superseded", now
                await database.commit()
                continue
            job.status, job.claim_id, job.claimed_at, job.updated_at = "processing", claim, now, now
            job.execution_kind = "background"
            task_id = job.task_id
            await database.commit()
        try:
            selected_runtime = primary_only(runtime or await build_runtime(settings, factory))
            task = task_for(selected_runtime, user_id, workspace_id, task_id)
            context = LearningContext(factory, settings, identifier, claim)

            def semantic(value: Any, captured: LearningContext = context) -> None:
                output = LearningOutput.model_validate(value)
                validate_output(output, captured.turns)

            result = await selected_runtime.execute(
                task, context_builder=context, documents={}, semantic_validator=semantic
            )
            output = LearningOutput.model_validate_json(result.output.text)
            async with factory() as database:
                await lock_owner(database, user_id)
                job = await database.get(MemoryLearningJob, identifier, populate_existing=True)
                if job is None:
                    return True  # Erasure won; no recreated row or derivative.
                await apply_learning(
                    database,
                    settings,
                    job,
                    output,
                    claim_id=claim,
                    now=await database_now(database),
                )
                await database.commit()
        except Exception:
            async with factory() as database:
                await lock_owner(database, user_id)
                job = await database.get(MemoryLearningJob, identifier, populate_existing=True)
                if job is not None and job.status == "processing" and job.claim_id == claim:
                    job.status, job.updated_at = "failed", await database_now(database)
                    await database.commit()
            # No private provider diagnostics or transcripts enter logs/task metadata.
        return True
    return False
