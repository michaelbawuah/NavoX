"""Bounded multi-provider execution. Only validated reasoning leaves this boundary."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from navox.ai.context import ContextBuilder
from navox.ai.errors import AIProviderError
from navox.ai.foundation.adapter import AIProviderAdapter, ErrorCode, ProviderError, ProviderRequest
from navox.ai.foundation.contracts import (
    AIResult,
    AITask,
    FinishReason,
    LatencyClass,
    Provider,
    Usage,
    validate_result_binding,
)
from navox.ai.foundation.persistence import model_key
from navox.ai.routing import rank_eligible, reserve_cost
from navox.ai.store import GatewayStore
from navox.ai.validation import OutputRejected, compile_schema, validate_output
from navox.db.ai_registry import AITaskRun
from navox.intelligence.contracts import SourceDocument


class GatewayUnavailable(AIProviderError):
    def __init__(self) -> None:
        super().__init__(
            "No eligible provider produced a validated result", code="provider_unavailable"
        )


class GatewayRuntime:
    def __init__(self, store: GatewayStore, adapters: Mapping[Provider, AIProviderAdapter]) -> None:
        self.store, self.adapters = store, dict(adapters)

    async def execute(
        self,
        task: AITask,
        *,
        context_builder: ContextBuilder,
        documents: Mapping[UUID, SourceDocument],
        semantic_validator: Callable[[Any], None],
        user_request: str = "",
    ) -> AIResult:
        task = AITask.model_validate(task)
        result = await self._execute(
            task,
            context_builder=context_builder,
            documents=documents,
            semantic_validator=semantic_validator,
            user_request=user_request,
            shadow=False,
        )
        # Shadow results are discarded. They can neither replace the user's result
        # nor reach feature state. Unknown primary cost consumes all remaining budget.
        try:
            if result.estimated_cost is not None and result.estimated_cost < task.max_cost:
                snapshot = await self.store.snapshot(task)
                if all(r.allow_shadow for r in snapshot.rules):
                    limit = (
                        min(task.max_cost, *(r.max_cost for r in snapshot.rules))
                        - result.estimated_cost
                    )
                    if limit > 0:
                        shadow_task = AITask.model_validate(
                            {
                                **task.model_dump(),
                                "id": uuid4(),
                                "max_cost": limit,
                                "provider_policy": {
                                    **task.provider_policy.model_dump(),
                                    "allow_fallback": False,
                                    "max_fallbacks": 0,
                                },
                            }
                        )
                        try:
                            await self._execute(
                                shadow_task,
                                context_builder=context_builder,
                                documents=documents,
                                semantic_validator=semantic_validator,
                                user_request=user_request,
                                shadow=True,
                                exclude=frozenset({f"{result.provider.value}:{result.model}"}),
                            )
                        except (GatewayUnavailable, PermissionError, ValueError):
                            pass
        except (PermissionError, ValueError, LookupError):
            pass
        return result

    async def _execute(
        self,
        task: AITask,
        *,
        context_builder: ContextBuilder,
        documents: Mapping[UUID, SourceDocument],
        semantic_validator: Callable[[Any], None],
        user_request: str,
        shadow: bool,
        exclude: frozenset[str] = frozenset(),
    ) -> AIResult:
        await self.store.authorize(task)
        started = monotonic()

        def new_run(attempt: int) -> AITaskRun:
            return AITaskRun(
                id=uuid4(),
                task_id=task.id,
                workspace_id=task.workspace_id,
                user_id=task.user_id,
                trace_id=task.trace_id,
                task_type=task.task_type.value,
                profile=task.profile.value,
                prompt=f"{task.prompt.name}@{task.prompt.version}",
                schema=f"{task.output_schema.name}@{task.output_schema.version}",
                status="STARTED",
                usage={},
                fallback_count=attempt,
                shadow=shadow,
            )

        run = new_run(0)
        await self.store.trace(run)
        spent = Decimal(0)
        measured = Decimal(0)
        all_cost_known = True
        input_total: int | None = 0
        output_total: int | None = 0
        attempted = set(exclude)
        deadline = {
            LatencyClass.INTERACTIVE: 30,
            LatencyClass.BACKGROUND: 180,
            LatencyClass.BATCH: 300,
        }[task.latency_class]
        try:
            for attempt in range(4):
                snapshot = await self.store.snapshot(task, shadow=shadow)
                binding = snapshot.registry.bind_task(task)
                compile_schema(binding.output_schema.document)
                if attempt > snapshot.policy.max_fallbacks:
                    break
                context = await context_builder.build(task, documents, user_request=user_request)
                input_bound = (
                    len(context.content.text.encode())
                    + len(binding.prompt.instructions.encode())
                    + len(binding.output_schema.document.text.encode())
                    + 2048
                )
                budget = min(task.max_cost, *(r.max_cost for r in snapshot.rules)) - spent
                preferred = next(
                    (
                        r.preferred_provider
                        for r in reversed(snapshot.rules)
                        if r.preferred_provider is not None
                    ),
                    task.preferred_provider,
                )
                available = frozenset(key for key in snapshot.available if key not in attempted)
                candidates = rank_eligible(
                    task,
                    snapshot.registry,
                    policy=snapshot.policy,
                    available=available,
                    evaluations=snapshot.evaluations,
                    input_bound=input_bound,
                    remaining_budget=budget,
                    preferred=preferred,
                    weights=snapshot.weights,
                )
                selected = None
                for model, reservation in candidates:
                    adapter = self.adapters.get(model.reference.provider)
                    if adapter is None or not task.capability_requirements.issubset(
                        adapter.capabilities(model.reference.model)
                    ):
                        continue
                    if await self.store.claim(model_key(model.reference)):
                        selected = (model, reservation, adapter)
                        break
                if selected is None:
                    if run.status == "STARTED":
                        run.error_code = "no_eligible_model"
                    break
                if attempt:
                    run = new_run(attempt)
                model, reservation, adapter = selected
                key = model_key(model.reference)
                attempted.add(key)
                run.registry_revision = snapshot.registry.revision
                run.provider, run.model = model.reference.provider.value, model.reference.model
                run.reserved_cost = reservation
                await self.store.trace(run)
                request = ProviderRequest(
                    task_id=task.id,
                    model=model.reference.model,
                    instructions=binding.prompt.instructions,
                    context=context.content,
                    output_schema=binding.output_schema.document,
                    prompt_ref=task.prompt,
                    schema_ref=task.output_schema,
                    max_output_tokens=task.max_output_tokens,
                )
                attempt_started = monotonic()
                spent += reservation
                try:
                    remaining = deadline - (monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        response = await adapter.execute(request)
                    if (
                        response.model != model.reference.model
                        or response.finish_reason != FinishReason.STOP
                    ):
                        raise OutputRejected("Provider result does not match selected model")
                    run.usage = response.usage.model_dump()
                    input_total = (
                        input_total + response.usage.input_tokens
                        if input_total is not None and response.usage.input_tokens is not None
                        else None
                    )
                    output_total = (
                        output_total + response.usage.output_tokens
                        if output_total is not None and response.usage.output_tokens is not None
                        else None
                    )
                    cost = (
                        reserve_cost(
                            model, response.usage.input_tokens, response.usage.output_tokens
                        )
                        if response.usage.input_tokens is not None
                        and response.usage.output_tokens is not None
                        else None
                    )
                    run.estimated_cost = cost
                    if cost is not None:
                        measured += cost
                        spent += cost - reservation
                        if cost > reservation:
                            raise OutputRejected("Provider usage exceeded reserved budget")
                    else:
                        all_cost_known = False
                    validate_output(
                        response.output, binding.output_schema.document, semantic_validator
                    )
                    # Recheck policy and source authority after a slow call. A revoked
                    # result is not accepted even when the provider already received input.
                    current = await self.store.snapshot(task, shadow=shadow, claimed_model=key)
                    current_budget = min(task.max_cost, *(r.max_cost for r in current.rules))
                    if (
                        current.registry.revision != snapshot.registry.revision
                        or attempt > current.policy.max_fallbacks
                        or spent > current_budget
                        or not rank_eligible(
                            task,
                            current.registry,
                            policy=current.policy,
                            available=current.available & {key},
                            evaluations=current.evaluations,
                            input_bound=input_bound,
                            remaining_budget=current_budget,
                            preferred=None,
                            weights=current.weights,
                        )
                    ):
                        raise OutputRejected("Provider eligibility changed")
                    await context_builder.build(task, documents, user_request=user_request)
                except asyncio.CancelledError:
                    run.status, run.error_code = "FAILED", "cancelled"
                    await asyncio.shield(
                        self.store.health(key, ProviderError(code=ErrorCode.UNAVAILABLE))
                    )
                    raise
                except Exception as error:
                    detail = (
                        ProviderError(code=ErrorCode.INVALID_RESPONSE)
                        if isinstance(error, OutputRejected)
                        else adapter.classify_error(error)
                    )
                    run.status, run.error_code = "FAILED", detail.code.value
                    if run.estimated_cost is None:
                        all_cost_known = False
                        input_total, output_total = None, None
                    await self.store.health(key, detail)
                else:
                    await self.store.health(key, None)
                    result = AIResult(
                        task_id=task.id,
                        workspace_id=task.workspace_id,
                        user_id=task.user_id,
                        trace_id=task.trace_id,
                        provider=model.reference.provider,
                        model=response.model,
                        output=response.output,
                        finish_reason=response.finish_reason,
                        usage=Usage(input_tokens=input_total, output_tokens=output_total),
                        latency_ms=int((monotonic() - started) * 1000),
                        estimated_cost=measured if all_cost_known else None,
                        output_schema=task.output_schema,
                        prompt=task.prompt,
                        fallback_count=attempt,
                        schema_validated=True,
                        semantic_validated=True,
                        policy_validated=True,
                    )
                    validate_result_binding(task, result)
                    run.status = "COMPLETED"
                    return result
                finally:
                    run.latency_ms = int((monotonic() - attempt_started) * 1000)
                    run.finished_at = datetime.now(UTC)
                    await asyncio.shield(self.store.trace(run))
        except asyncio.CancelledError:
            run.status, run.error_code = "FAILED", "cancelled"
            raise
        except Exception:
            if run.status == "STARTED":
                run.status, run.error_code = "FAILED", "task_rejected"
        finally:
            if run.status == "STARTED":
                run.status = "FAILED"
            run.finished_at = run.finished_at or datetime.now(UTC)
            await asyncio.shield(self.store.trace(run))
        raise GatewayUnavailable() from None

    extract = execute
    classify = execute
    reason = execute
    plan = execute
    summarize = execute
    rank = execute
    embed = execute
