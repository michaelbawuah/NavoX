"""Authenticated subscription registry and explicit cancellation commands."""

import asyncio
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from navox.api.auth import CurrentAccountDependency, DatabaseSession, SettingsDependency
from navox.api.connector_management import require_origin
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.subscription_cancellation import (
    available_cancellation_profiles,
    enable_subscription_cancellation,
    inspect_cancellation_binding,
    resolve_subscription_cancellation,
)
from navox.db.models import CancellationAttempt
from navox.subscriptions import service
from navox.subscriptions.cancellation import (
    abort_cancellation,
    confirm_cancellation,
    get_cancellation,
    prepare_cancellation,
    target_for_obligation,
)
from navox.subscriptions.cancellation_contracts import (
    CancellationAttemptView,
    ConfirmCancellationRequest,
    PrepareCancellationRequest,
)
from navox.subscriptions.dispatcher import (
    dispatch_cancellation,
    dispatch_subscription_reconciliation,
)
from navox.subscriptions.metrics import PreventedRenewals, prevented_renewals
from navox.subscriptions.schemas import (
    EvidenceRead,
    PriceHistoryRead,
    SubscriptionCreate,
    SubscriptionKeep,
    SubscriptionPatch,
    SubscriptionRead,
    SubscriptionReview,
    SubscriptionSummary,
)

router = APIRouter(tags=["subscriptions"])


class CancellationGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str = Field(min_length=1, max_length=64)
    request_id: UUID
    confirmed: bool = Field(strict=True)

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class CancellationBinding(CancellationGrant):
    connection_id: UUID
    external_resource_id: str = Field(min_length=1, max_length=512)


class SubscriptionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["SUMMARY", "UPCOMING", "TRIALS", "SEARCH"]
    text: str = Field(default="", max_length=200)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    days: int = Field(default=30, ge=1, le=366)


class SubscriptionQueryResult(BaseModel):
    intent: str
    summary: SubscriptionSummary | None = None
    subscriptions: list[SubscriptionRead] = Field(default_factory=list)


def provider_failure(error: ConnectorRuntimeError) -> HTTPException:
    code = 403 if error.code == "PERMISSION_DENIED" else 409
    # Provider error messages and response bodies never become API diagnostics.
    return HTTPException(code, f"Cancellation provider unavailable: {error.code}")


async def schedule_reconciliation(settings: SettingsDependency) -> None:
    try:
        await asyncio.wait_for(dispatch_subscription_reconciliation(settings=settings), timeout=5)
    except Exception:
        # Registry changes and outbox facts are durable; the worker scans pending work.
        return


async def schedule_cancellation(attempt: CancellationAttempt, settings: SettingsDependency) -> None:
    try:
        await asyncio.wait_for(
            dispatch_cancellation(
                attempt_id=attempt.id,
                obligation_id=attempt.obligation_id,
                user_id=attempt.user_id,
                workspace_id=attempt.workspace_id,
                settings=settings,
            ),
            timeout=5,
        )
    except Exception:
        # Exact approval remains persisted for reconciliation; this does not execute inline.
        return


@router.get("/subscriptions", response_model=list[SubscriptionRead])
async def subscriptions(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    status: Annotated[str | None, Query(max_length=32)] = None,
) -> list[SubscriptionRead]:
    rows = await service.list_subscriptions(
        database, workspace_id=account.workspace.id, user_id=account.user.id, status=status
    )
    return [service.to_subscription_read(row) for row in rows]


@router.post("/subscriptions", response_model=SubscriptionRead, status_code=201)
async def create_subscription(
    request: Request,
    command: SubscriptionCreate,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionRead:
    require_origin(request, settings.web_origin)
    row = await service.create_subscription(
        database, workspace_id=account.workspace.id, user_id=account.user.id, payload=command
    )
    await schedule_reconciliation(settings)
    return service.to_subscription_read(row)


@router.get("/subscriptions/summary", response_model=SubscriptionSummary)
async def summary(
    account: CurrentAccountDependency, database: DatabaseSession
) -> SubscriptionSummary:
    return await service.subscription_summary(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )


@router.get("/subscriptions/upcoming", response_model=list[SubscriptionRead])
async def upcoming(
    account: CurrentAccountDependency,
    database: DatabaseSession,
    days: Annotated[int, Query(ge=1, le=366)] = 30,
) -> list[SubscriptionRead]:
    rows = await service.list_upcoming(
        database, workspace_id=account.workspace.id, user_id=account.user.id, days=days
    )
    return [service.to_subscription_read(row) for row in rows]


@router.get("/subscriptions/prevented-renewals", response_model=PreventedRenewals)
async def prevented(
    account: CurrentAccountDependency, database: DatabaseSession
) -> PreventedRenewals:
    return await prevented_renewals(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )


@router.get("/subscriptions/trials", response_model=list[SubscriptionRead])
async def trials(
    account: CurrentAccountDependency, database: DatabaseSession
) -> list[SubscriptionRead]:
    rows = await service.list_trials(
        database, workspace_id=account.workspace.id, user_id=account.user.id
    )
    return [service.to_subscription_read(row) for row in rows]


@router.get("/subscriptions/cancellation-profiles")
async def cancellation_profiles(
    account: CurrentAccountDependency, database: DatabaseSession, settings: SettingsDependency
) -> list[dict[str, object]]:
    try:
        return await available_cancellation_profiles(
            database, settings, workspace_id=account.workspace.id, user_id=account.user.id
        )
    except (ValueError, ConnectorRuntimeError):
        raise HTTPException(503, "Reviewed cancellation profiles are unavailable") from None


@router.post("/subscriptions/query", response_model=SubscriptionQueryResult)
async def structured_query(
    request: Request,
    command: SubscriptionQuery,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionQueryResult:
    require_origin(request, settings.web_origin)
    if command.intent == "SUMMARY":
        result = await service.subscription_summary(
            database, workspace_id=account.workspace.id, user_id=account.user.id
        )
        if command.currency is not None:
            result.currency_totals = [
                total for total in result.currency_totals if total.currency == command.currency
            ]
        return SubscriptionQueryResult(intent=command.intent, summary=result)
    if command.intent == "UPCOMING":
        rows = await service.list_upcoming(
            database, workspace_id=account.workspace.id, user_id=account.user.id, days=command.days
        )
    elif command.intent == "TRIALS":
        rows = await service.list_trials(
            database, workspace_id=account.workspace.id, user_id=account.user.id
        )
    else:
        rows = await service.list_subscriptions(
            database, workspace_id=account.workspace.id, user_id=account.user.id
        )
    query = command.text.casefold().strip()
    return SubscriptionQueryResult(
        intent=command.intent,
        subscriptions=[
            service.to_subscription_read(row)
            for row in rows
            if (command.currency is None or row.billing_currency == command.currency)
            and (not query or query in f"{row.name} {row.plan_name or ''}".casefold())
        ],
    )


@router.get("/subscriptions/{obligation_id}", response_model=SubscriptionRead)
async def subscription_detail(
    obligation_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> SubscriptionRead:
    row = await service.get_subscription(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )
    return service.to_subscription_read(row)


@router.patch("/subscriptions/{obligation_id}", response_model=SubscriptionRead)
async def patch_subscription(
    obligation_id: UUID,
    request: Request,
    command: SubscriptionPatch,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionRead:
    require_origin(request, settings.web_origin)
    row = await service.update_subscription(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
        payload=command,
    )
    await schedule_reconciliation(settings)
    return service.to_subscription_read(row)


@router.get("/subscriptions/{obligation_id}/evidence", response_model=list[EvidenceRead])
async def subscription_evidence(
    obligation_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> list[EvidenceRead]:
    return await service.list_evidence(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )


@router.get("/subscriptions/{obligation_id}/history", response_model=list[PriceHistoryRead])
async def subscription_history(
    obligation_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> list[PriceHistoryRead]:
    return await service.list_history(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )


@router.post("/subscriptions/{obligation_id}/keep", response_model=SubscriptionRead)
async def keep_subscription(
    obligation_id: UUID,
    request: Request,
    command: SubscriptionKeep,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionRead:
    require_origin(request, settings.web_origin)
    row = await service.keep_subscription(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
        payload=command,
    )
    return service.to_subscription_read(row)


@router.post("/subscriptions/{obligation_id}/review", response_model=SubscriptionRead)
async def review_subscription(
    obligation_id: UUID,
    request: Request,
    command: SubscriptionReview,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionRead:
    require_origin(request, settings.web_origin)
    row = await service.review_subscription(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
        payload=command,
    )
    await schedule_reconciliation(settings)
    return service.to_subscription_read(row)


@router.post("/connectors/generic-rest-api/connections/{connection_id}/cancellation-grant")
async def grant_cancellation(
    connection_id: UUID,
    request: Request,
    command: CancellationGrant,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> dict[str, object]:
    require_origin(request, settings.web_origin)
    try:
        result = await enable_subscription_cancellation(
            database,
            settings,
            connection_id=connection_id,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            profile_id=command.profile_id,
            confirmed=command.confirmed,
            request_id=command.request_id,
        )
        await database.commit()
        return result
    except ConnectorRuntimeError as error:
        raise provider_failure(error) from None
    except ValueError:
        raise HTTPException(409, "Reviewed cancellation configuration is unavailable") from None


@router.post("/subscriptions/{obligation_id}/cancellation-binding", response_model=SubscriptionRead)
async def bind_cancellation(
    obligation_id: UUID,
    request: Request,
    command: CancellationBinding,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> SubscriptionRead:
    require_origin(request, settings.web_origin)
    target = await target_for_obligation(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )
    try:
        preview = await inspect_cancellation_binding(
            database,
            settings,
            target=target.model_copy(update={"external_resource_id": command.external_resource_id}),
            profile_id=command.profile_id,
            connection_id=command.connection_id,
        )
        if preview.target.merchant_domain is None or preview.target.connection_id is None:
            raise HTTPException(409, "Verified subscription target is unavailable")
        row = await service.apply_cancellation_binding(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            obligation_id=obligation_id,
            connection_id=preview.target.connection_id,
            external_resource_id=command.external_resource_id,
            verified_merchant_domain=preview.target.merchant_domain,
            request_id=command.request_id,
        )
        return service.to_subscription_read(row)
    except ConnectorRuntimeError as error:
        raise provider_failure(error) from None
    except ValueError:
        raise HTTPException(409, "Verified cancellation binding is unavailable") from None


@router.post("/subscriptions/{obligation_id}/cancel", response_model=CancellationAttemptView)
async def cancel_subscription(
    obligation_id: UUID,
    request: Request,
    command: PrepareCancellationRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CancellationAttemptView:
    require_origin(request, settings.web_origin)
    target = await target_for_obligation(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )
    try:
        capability = await resolve_subscription_cancellation(database, settings, target)
        attempt = await prepare_cancellation(
            database,
            workspace_id=account.workspace.id,
            user_id=account.user.id,
            obligation_id=obligation_id,
            request_id=command.request_id,
            capability=capability,
        )
    except ConnectorRuntimeError as error:
        raise provider_failure(error) from None
    await schedule_cancellation(attempt, settings)
    return CancellationAttemptView.model_validate(attempt)


@router.get(
    "/subscriptions/{obligation_id}/cancellation", response_model=CancellationAttemptView | None
)
async def latest_cancellation(
    obligation_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> CancellationAttemptView | None:
    await service.get_subscription(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        obligation_id=obligation_id,
    )
    attempt = await database.scalar(
        select(CancellationAttempt)
        .where(
            CancellationAttempt.obligation_id == obligation_id,
            CancellationAttempt.workspace_id == account.workspace.id,
            CancellationAttempt.user_id == account.user.id,
        )
        .order_by(CancellationAttempt.requested_at.desc(), CancellationAttempt.id)
        .limit(1)
    )
    return CancellationAttemptView.model_validate(attempt) if attempt is not None else None


@router.get("/cancellations/{attempt_id}", response_model=CancellationAttemptView)
async def cancellation_detail(
    attempt_id: UUID, account: CurrentAccountDependency, database: DatabaseSession
) -> CancellationAttemptView:
    attempt = await get_cancellation(
        database, workspace_id=account.workspace.id, user_id=account.user.id, attempt_id=attempt_id
    )
    return CancellationAttemptView.model_validate(attempt)


@router.post("/cancellations/{attempt_id}/confirm", response_model=CancellationAttemptView)
async def confirm_subscription_cancellation(
    attempt_id: UUID,
    request: Request,
    command: ConfirmCancellationRequest,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CancellationAttemptView:
    require_origin(request, settings.web_origin)
    attempt = await confirm_cancellation(
        database,
        workspace_id=account.workspace.id,
        user_id=account.user.id,
        attempt_id=attempt_id,
        request_id=command.request_id,
        preview_hash=command.preview_hash,
    )
    await schedule_cancellation(attempt, settings)
    return CancellationAttemptView.model_validate(attempt)


@router.post("/cancellations/{attempt_id}/abort", response_model=CancellationAttemptView)
async def abort_subscription_cancellation(
    attempt_id: UUID,
    request: Request,
    account: CurrentAccountDependency,
    database: DatabaseSession,
    settings: SettingsDependency,
) -> CancellationAttemptView:
    require_origin(request, settings.web_origin)
    attempt = await abort_cancellation(
        database, workspace_id=account.workspace.id, user_id=account.user.id, attempt_id=attempt_id
    )
    return CancellationAttemptView.model_validate(attempt)
