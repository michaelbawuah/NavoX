"""Exact reviewed personal events, with durable uncertain outcomes and read-only recovery.

No background job resubmits an event. Provider I/O never holds native owner locks.
Only an explicit approval can submit; only an explicit Undo can conditionally delete.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import InvalidToken
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.import_storage import cipher
from navox.connectors.sync_state import utc
from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.models import OAuthAuthorizationAttempt
from navox.db.student_calendars import StudentCalendarEvent, StudentCalendarWriteGrant
from navox.providers.google_oauth import GoogleAccessTokenError
from navox.providers.microsoft_mail import MicrosoftMailError
from navox.student import service as schedule
from navox.student_availability import service as availability
from navox.student_availability.engine import MEETING_BUFFER, _uncertain_day_span
from navox.student_availability.schemas import AvailabilitySnapshot
from navox.student_calendars import apple, displacement, microsoft, service
from navox.student_calendars.apple_provider import AppleCalendarWriteProvider
from navox.student_calendars.microsoft_provider import MicrosoftCalendarWriteProvider
from navox.student_calendars.provider import (
    CalendarWriteChanged,
    CalendarWriteRejected,
    GoogleCalendarWriteError,
    GoogleCalendarWriteProvider,
)
from navox.student_calendars.schemas import (
    MAX_REVISION,
    MICROSOFT_SCHEDULING_SCOPES,
    SCHEDULING_CONSENT_VERSION,
    SCHEDULING_SCOPES,
    CalendarCommand,
    CalendarEventApproval,
    CalendarEventCommand,
    CalendarEventPreview,
    CalendarEventStatus,
    CalendarEventView,
    CalendarGrantView,
    CalendarSchedulingStart,
    CalendarSchedulingView,
    CalendarStudyMove,
    CalendarTargetsRequest,
    CalendarTargetsView,
    CalendarTargetView,
    CalendarWriteProviderView,
)
from navox.student_calendars.write_protocol import (
    AppleWriteCredential,
    AppleWriteProvider,
    BearerWriteProvider,
    BoundAppleWriter,
    BoundBearerWriter,
    BoundWriteProvider,
    WriteCredential,
)

DRAFT_TTL = timedelta(minutes=10)
MAX_EVENTS = 500
MAX_EVENT_CLAIMS = 500
CLAIMED_STATUSES = ("executing", "uncertain", "created", "changed", "undoing")
MESSAGES: dict[str, str] = {
    "pending": "Review this exact event before adding it. No event has been submitted.",
    "executing": "The request may have reached Google. Check status before doing anything else.",
    "uncertain": "The outcome is not confirmed. Check status; do not submit another event.",
    "created": "Google confirmed this exact event. Undo removes only this unchanged event.",
    "changed": "The Google event changed. NavoX will not remove it; review it in Google Calendar.",
    "undoing": "Undo may have reached Google. Check status before making another change.",
    "undone": "Google confirmed this event is absent. No other event was removed.",
    "failed": "Google rejected this request. No automatic retry will be made.",
}


@dataclass(frozen=True)
class WriteAuthority:
    scope: service.CalendarScope
    grant_id: UUID
    grant_revision: int
    connection_id: UUID
    session_id: UUID
    provider: str = "google"


def _admission(settings: Settings, provider: str | None = None) -> None:
    if not settings.student_calendar_availability_enabled or not getattr(
        settings, "student_calendar_writes_enabled", False
    ):
        raise schedule.StudentError(404, "Calendar scheduling is not enabled")
    if provider == "microsoft":
        # Graph's event endpoint has no proven exact-version DELETE contract.
        # A broad OAuth scope, model request, or flag cannot invent that proof.
        raise schedule.StudentError(
            409, "Microsoft event creation and exact Undo are not available here"
        )
    if provider == "icloud" and not settings.student_apple_calendar_writes_enabled:
        raise schedule.StudentError(404, "Apple calendar scheduling is not enabled")


def _kind(value: str | None) -> str:
    if value is None:
        return "google"
    if not isinstance(value, str) or value not in {"google", "microsoft", "icloud"}:
        raise schedule.StudentError(503, "The calendar provider could not be verified")
    return value


def _api_provider(value: str | None) -> Any:
    return "outlook" if _kind(value) == "microsoft" else _kind(value)


def _label(value: str | None) -> str:
    return {"google": "Google", "microsoft": "Microsoft", "icloud": "Apple"}[_kind(value)]


def _scopes(provider: str) -> tuple[str, ...]:
    return MICROSOFT_SCHEDULING_SCOPES if provider == "microsoft" else SCHEDULING_SCOPES


def _undo_supported(provider: str) -> bool:
    return provider != "microsoft"


def _exact_version(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r'"[\x21\x23-\x7e]+"', value) is not None


def _capabilities(settings: Settings) -> list[CalendarWriteProviderView]:
    enabled = (
        settings.student_calendar_availability_enabled and settings.student_calendar_writes_enabled
    )
    google_configured = bool(
        settings.google_oauth_client_id.strip()
        and settings.google_oauth_client_secret
        and settings.google_oauth_client_secret.get_secret_value().strip()
    )
    try:
        CredentialVault(settings)
        vault = True
    except CredentialVaultError:
        vault = False
    google_state = (
        "available"
        if enabled and google_configured and vault
        else "setup_pending"
        if enabled
        else "unavailable"
    )
    apple_enabled = enabled and settings.student_apple_calendar_writes_enabled
    apple_state = (
        "available"
        if apple_enabled and vault
        else "setup_pending"
        if apple_enabled
        else "unavailable"
    )
    return [
        CalendarWriteProviderView(
            id="google",
            availability=cast(Any, google_state),
            message="Review one personal Google event and exact unchanged-event Undo."
            if google_state == "available"
            else "Google calendar setup is pending."
            if google_state == "setup_pending"
            else "New Google calendar actions are disabled here.",
        ),
        CalendarWriteProviderView(
            id="outlook",
            availability="unavailable",
            message="Microsoft event creation and exact Undo are not available here yet.",
        ),
        CalendarWriteProviderView(
            id="icloud",
            availability=cast(Any, apple_state),
            message="Separately approve Apple event writes using your current busy-time connection."
            if apple_state == "available"
            else "Apple calendar setup is pending."
            if apple_state == "setup_pending"
            else "New Apple calendar actions are disabled here.",
        ),
    ]


def _transport_draft(provider: str, draft: CalendarEventPreview) -> None:
    if provider == "icloud" and (
        draft.start_at.microsecond
        or draft.end_at.microsecond
        or any(
            ord(char) < 32 or 127 <= ord(char) <= 159
            for value in (draft.title, draft.location)
            for char in value
        )
    ):
        raise schedule.StudentError(
            422, "Use plain event fields and whole-second Apple event times"
        )


async def _has_other_sources(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> bool:
    if await apple._grants(database, user_id, workspace_id):
        return True
    for grant in await service.grants(database, user_id, workspace_id):
        connection = await service.owned_connection(
            database, user_id, workspace_id, grant.connection_id
        )
        if connection is not None and connection.provider != "google":
            return True
    return False


def _writer(
    authority: WriteAuthority,
    credential: WriteCredential,
    settings: Settings,
    timezone: str,
    provider: BearerWriteProvider | AppleWriteProvider | None,
) -> BoundWriteProvider:
    if authority.provider == "icloud":
        if not isinstance(credential, AppleWriteCredential):
            raise schedule.StudentError(503, "Apple calendar authorization could not be verified")
        return BoundAppleWriter(
            cast(AppleWriteProvider, provider)
            if provider is not None
            else AppleCalendarWriteProvider(
                enabled=settings.student_apple_calendar_writes_enabled, timezone=timezone
            ),
            credential,
        )
    if not isinstance(credential, str):
        raise schedule.StudentError(503, "Calendar authorization could not be verified")
    implementation: BearerWriteProvider
    if provider is not None:
        implementation = cast(BearerWriteProvider, provider)
    elif authority.provider == "microsoft":
        implementation = MicrosoftCalendarWriteProvider(timezone=timezone)
    else:
        implementation = GoogleCalendarWriteProvider()
    return BoundBearerWriter(implementation, credential)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _seal(row: StudentCalendarEvent, payload: dict[str, Any], settings: Settings) -> str:
    envelope = {
        "v": 1,
        "purpose": "calendar-event.v1",
        "id": str(row.id),
        "user": str(row.user_id),
        "workspace": str(row.workspace_id),
        "generation": row.generation,
        "digest": row.digest,
        "provider": _kind(row.provider),
        "payload": payload,
    }
    try:
        return (
            cipher(settings).encrypt(json.dumps(envelope, separators=(",", ":")).encode()).decode()
        )
    except ConnectorRuntimeError:
        raise schedule.StudentError(503, "Private event storage is not configured") from None


def _open(row: StudentCalendarEvent, settings: Settings) -> dict[str, Any]:
    try:
        envelope = json.loads(cipher(settings).decrypt(row.encrypted_payload.encode()))
        if (
            envelope["v"],
            envelope["purpose"],
            envelope["id"],
            envelope["user"],
            envelope["workspace"],
            envelope["generation"],
            envelope["digest"],
        ) != (
            1,
            "calendar-event.v1",
            str(row.id),
            str(row.user_id),
            str(row.workspace_id),
            row.generation,
            row.digest,
        ) or not isinstance(envelope["payload"], dict):
            raise ValueError
        if _kind(envelope.get("provider")) != _kind(row.provider) or _kind(
            envelope["payload"].get("provider")
        ) != _kind(row.provider):
            raise ValueError
        return cast(dict[str, Any], envelope["payload"])
    except (ConnectorRuntimeError, InvalidToken, ValueError, KeyError, TypeError):
        raise schedule.StudentError(503, "This private event receipt could not be opened") from None


async def scheduling_view(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, session_hash: str, settings: Settings
) -> CalendarSchedulingView:
    current = await service.boundary(
        database, user_id, workspace_id, session_hash, lock=False, allow_paused=True
    )
    items = []
    for entry in await service.grants(database, user_id, workspace_id, scheduling=True):
        grant = cast(StudentCalendarWriteGrant, entry)
        provider = _kind(grant.provider)
        connection = await service.owned_connection(
            database, user_id, workspace_id, grant.connection_id
        )
        status = "revoked" if grant.status == "revoked" else "unavailable"
        if grant.status == "pending":
            attempt = (
                await database.get(OAuthAuthorizationAttempt, grant.oauth_attempt_id)
                if grant.oauth_attempt_id
                else None
            )
            if (
                attempt is not None
                and attempt.used_at is None
                and utc(attempt.expires_at) > current.now
            ):
                status = "pending"
        elif (
            grant.status == "active"
            and service.ready(connection)
            and connection is not None
            and connection.provider == provider
            and (provider == "icloud" or set(_scopes(provider)).issubset(connection.granted_scopes))
            and grant.generation == current.generation
            and grant.consent_version == SCHEDULING_CONSENT_VERSION
        ):
            status = "connected"
        items.append(
            CalendarGrantView(
                id=grant.id,
                connection_id=connection.id if connection else None,
                account_label=connection.external_email if connection else None,
                status=cast(Any, status),
                revision=grant.revision,
                provider=_api_provider(provider),
            )
        )
    return CalendarSchedulingView(
        generation=current.generation,
        revision=current.revision,
        grants=items,
        providers=_capabilities(settings)
        if (
            settings.student_apple_calendar_writes_enabled
            or settings.student_microsoft_calendar_writes_enabled
            or any(item.provider != "google" for item in items)
            or await _has_other_sources(database, user_id, workspace_id)
        )
        else None,
    )


async def disconnect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    grant_id: UUID,
    payload: CalendarCommand,
    settings: Settings,
) -> CalendarSchedulingView:
    current = await service.boundary(
        database, user_id, workspace_id, session_hash, allow_paused=True
    )
    grant = await database.scalar(
        select(StudentCalendarWriteGrant)
        .where(
            StudentCalendarWriteGrant.id == grant_id,
            StudentCalendarWriteGrant.user_id == user_id,
            StudentCalendarWriteGrant.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if grant is None:
        raise schedule.StudentError(404, "Scheduling permission not found")
    if (
        current.state is None
        or grant.owner_scope_id != current.state.id
        or payload.generation != current.generation
    ):
        raise schedule.StudentError(409, "Calendar permissions changed. Refresh and try again")
    if grant.status != "revoked":
        service.check_command(current, payload, admitting=False)
        await service.retire(database, grant, current.now)
        service.advance(current.state)
        schedule.audit(
            database, user_id, workspace_id, "student.calendar.scheduling.disconnected", grant.id
        )
        await database.commit()
    return await scheduling_view(database, user_id, workspace_id, session_hash, settings)


async def start_apple(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: CalendarSchedulingStart,
    settings: Settings,
) -> CalendarSchedulingView:
    """New native write consent for the exact current Apple busy credential."""
    _admission(settings, "icloud")
    if payload.consent_version != SCHEDULING_CONSENT_VERSION:
        raise schedule.StudentError(422, "Confirm the separate calendar write purpose")
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    service.check_command(current, payload)
    state = await service.ensure_state(database, current, user_id, workspace_id)
    connection = await apple._connection(database, user_id, workspace_id, payload.connection_id)
    busy = next(
        (
            grant
            for grant in await apple._grants(database, user_id, workspace_id)
            if grant.connection_id == payload.connection_id
            and grant.owner_scope_id == state.id
            and grant.generation == current.generation
            and grant.consent_version == apple.CONSENT_VERSION
        ),
        None,
    )
    if connection is None or not apple._ready(connection) or busy is None:
        raise schedule.StudentError(409, "Connect current Apple busy-time access before scheduling")
    scope = await service.scope_snapshot(database, user_id, workspace_id, session_hash)
    snapshot = next(
        (
            grant
            for grant in scope.grants
            if grant.provider == "icloud"
            and grant.id == busy.id
            and grant.connection_id == connection.id
        ),
        None,
    )
    if snapshot is None:
        raise schedule.StudentError(409, "Apple busy-time permission changed. Review again")
    # Opening the owner-bound envelope proves current credential/account linkage;
    # the credential is neither copied into the grant nor sent to another API.
    await apple.credentials(database, scope, snapshot, settings)
    existing = await service.grants(database, user_id, workspace_id, scheduling=True)
    if len(existing) >= service.MAX_GRANTS or any(
        grant.request_id == payload.request_id for grant in existing
    ):
        raise schedule.StudentError(409, "This scheduling consent needs a new review")
    for grant in existing:
        if grant.connection_id == connection.id and grant.status != "revoked":
            await service.retire(database, grant, current.now)
    grant = StudentCalendarWriteGrant(
        owner_scope_id=state.id,
        user_id=user_id,
        workspace_id=workspace_id,
        connection_id=connection.id,
        oauth_attempt_id=None,
        provider="icloud",
        auth_session_id=current.session.id,
        request_id=payload.request_id,
        generation=current.generation,
        revision=1,
        status="active",
        consent_version=SCHEDULING_CONSENT_VERSION,
        accepted_at=current.now,
    )
    database.add(grant)
    service.advance(state)
    await database.flush()
    schedule.audit(
        database, user_id, workspace_id, "student.calendar.scheduling.consented", grant.id
    )
    await database.commit()
    return await scheduling_view(database, user_id, workspace_id, session_hash, settings)


async def _authority(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    grant_id: UUID,
) -> WriteAuthority:
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    grant = await database.scalar(
        select(StudentCalendarWriteGrant)
        .where(
            StudentCalendarWriteGrant.id == grant_id,
            StudentCalendarWriteGrant.user_id == user_id,
            StudentCalendarWriteGrant.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        grant is None
        or current.state is None
        or grant.owner_scope_id != current.state.id
        or grant.status != "active"
        or grant.generation != current.generation
        or grant.consent_version != SCHEDULING_CONSENT_VERSION
        or grant.connection_id is None
    ):
        raise schedule.StudentError(
            409, "Scheduling permission is unavailable. Review the connection"
        )
    connection = await service.owned_connection(
        database, user_id, workspace_id, grant.connection_id
    )
    if (
        not service.ready(connection)
        or connection is None
        or connection.provider != _kind(grant.provider)
        or (
            connection.provider != "icloud"
            and not set(_scopes(connection.provider)).issubset(connection.granted_scopes)
        )
    ):
        raise schedule.StudentError(409, "Calendar scheduling permission is unavailable")
    scope = await service.scope_snapshot(database, user_id, workspace_id, session_hash)
    if not any(
        item.connection_id == connection.id
        and item.status == "active"
        and item.provider == connection.provider
        for item in scope.grants
    ):
        raise schedule.StudentError(409, "Busy-time permission is required before scheduling")
    return WriteAuthority(
        scope, grant.id, grant.revision, connection.id, current.session.id, connection.provider
    )


async def _check(
    factory: async_sessionmaker[AsyncSession],
    authority: WriteAuthority,
    session_hash: str,
) -> None:
    async with factory() as database:
        observed = await _authority(
            database,
            authority.scope.user_id,
            authority.scope.workspace_id,
            session_hash,
            authority.grant_id,
        )
        if observed != authority:
            raise schedule.StudentError(
                409, "Calendar permissions or sign-in changed. Review again"
            )
        await database.commit()


async def _token(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    authority: WriteAuthority,
    session_hash: str,
) -> WriteCredential:
    grant = next(
        item
        for item in authority.scope.grants
        if item.connection_id == authority.connection_id and item.status == "active"
    )
    try:
        if authority.provider == "icloud":
            async with factory() as database:
                await service.check_scope(database, authority.scope, session_hash)
                account, password = await apple.credentials(
                    database, authority.scope, grant, settings
                )
                await database.commit()
            token: WriteCredential = AppleWriteCredential(account, password)
        elif authority.provider == "microsoft":
            token = await microsoft.access(factory, settings, authority.scope, grant, session_hash)
        else:
            token = await availability._token(
                factory, settings, authority.scope, grant, session_hash
            )
        await _check(factory, authority, session_hash)
        return token
    except (GoogleAccessTokenError, MicrosoftMailError):
        raise schedule.StudentError(
            503, "Calendar scheduling is unavailable. Check the connection"
        ) from None


async def targets(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: CalendarTargetsRequest,
    *,
    provider: BearerWriteProvider | AppleWriteProvider | None = None,
) -> CalendarTargetsView:
    _admission(settings)
    async with factory() as database:
        current = await service.boundary(database, user_id, workspace_id, session_hash)
        service.check_command(current, payload)
        authority = await _authority(
            database, user_id, workspace_id, session_hash, payload.grant_id
        )
        timezone = (await schedule.owner(database, user_id, workspace_id, lock=False)).timezone
        await database.commit()
    token = await _token(factory, settings, authority, session_hash)
    writer = _writer(authority, token, settings, timezone, provider)

    async def check() -> None:
        _admission(settings)
        await _check(factory, authority, session_hash)

    try:
        items = await writer.owned_calendars(check=check)
    except GoogleCalendarWriteError:
        raise schedule.StudentError(503, "Calendars could not be checked. Try again") from None
    await check()
    return CalendarTargetsView(
        generation=authority.scope.generation,
        revision=authority.scope.revision,
        grant_id=authority.grant_id,
        provider=_api_provider(authority.provider),
        targets=[
            CalendarTargetView(id=item.id, label=item.label, timezone=item.timezone)
            for item in items
        ],
    )


def _view(
    row: StudentCalendarEvent, payload: dict[str, Any], current: service.Boundary
) -> CalendarEventView:
    draft = CalendarEventPreview.model_validate(payload["draft"])
    study = payload.get("study_displacement")
    study_fields: dict[str, Any] = {}
    study_message = ""
    if study is not None:
        if (
            not isinstance(study, dict)
            or study.get("v") != 1
            or type(study.get("applied")) is not bool
        ):
            raise schedule.StudentError(503, "This private study review could not be opened")
        try:
            moves = [
                displacement.StudyMove.model_validate(value) for value in study["review"]["moves"]
            ]
        except (ValueError, TypeError, KeyError):
            raise schedule.StudentError(
                503, "This private study review could not be opened"
            ) from None
        study_fields = {
            "study_changes_applied": study["applied"],
            "study_details_redacted": study.get("redacted") is True,
            "study_moves": [
                CalendarStudyMove(
                    block_id=move.before.id,
                    task_id=move.before.task_id,
                    title=move.before.title,
                    minutes=move.before.minutes,
                    before_start_at=move.before.start_at,
                    before_end_at=move.before.end_at,
                    after_start_at=move.after.start_at,
                    after_end_at=move.after.end_at,
                )
                for move in moves
            ],
        }
        if study["applied"]:
            study_message = (
                " The reviewed study moves were saved and were not rolled back. "
                "Undo removes the calendar event only; saved study moves remain."
            )
        if study.get("redacted") is True:
            study_message += " Forgotten study-change details are no longer retained or displayed."
    return CalendarEventView(
        generation=current.generation,
        calendar_revision=current.revision,
        id=row.id,
        revision=row.revision,
        digest=row.digest,
        status=cast(CalendarEventStatus, row.status),
        calendar_label=payload["calendar_label"],
        title=draft.title,
        location=draft.location,
        start_at=draft.start_at,
        end_at=draft.end_at,
        timezone=draft.timezone,
        expires_at=row.expires_at,
        changes_applied=(
            True
            if row.status == "created"
            else False
            if row.status in {"pending", "failed", "undone"}
            else None
        ),
        can_undo=(
            row.status == "created"
            and _undo_supported(_kind(row.provider))
            and _exact_version(payload.get("etag"))
            and payload.get("creation_etag") == payload.get("etag")
            and row.generation == current.generation
        ),
        message=(
            (
                "This request predates your reset; only its status can be checked here. "
                if row.generation != current.generation
                else ""
            )
            + (
                "Exact unchanged-event Undo is not available for this provider. "
                "Review the event there."
                if row.status == "created" and not _undo_supported(_kind(row.provider))
                else "Google confirmed the event, but its original creation version "
                "was not confirmed. "
                "Review it in Google Calendar to make changes."
                if row.status == "created" and payload.get("creation_etag") != payload.get("etag")
                else (
                    "This study review was explicitly forgotten and cannot be approved. "
                    "Nothing was submitted."
                )
                if study is not None and study.get("redacted") is True and not study["applied"]
                else MESSAGES[row.status]
            )
            + study_message
        ).replace("Google", _label(row.provider)),
        provider=_api_provider(row.provider),
        **study_fields,
    )


async def list_events(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    settings: Settings,
    *,
    cursor: UUID | None = None,
) -> dict[str, Any]:
    current = await service.boundary(
        database, user_id, workspace_id, session_hash, lock=False, allow_paused=True
    )
    rows = list(
        await database.scalars(
            select(StudentCalendarEvent)
            .where(
                StudentCalendarEvent.user_id == user_id,
                StudentCalendarEvent.workspace_id == workspace_id,
                or_(
                    StudentCalendarEvent.generation == current.generation,
                    and_(
                        StudentCalendarEvent.generation < current.generation,
                        StudentCalendarEvent.status.in_(CLAIMED_STATUSES),
                    ),
                ),
            )
            .order_by(
                case(
                    (StudentCalendarEvent.status.in_(["executing", "uncertain", "undoing"]), 0),
                    else_=1,
                ),
                StudentCalendarEvent.created_at.desc(),
                StudentCalendarEvent.id,
            )
            .limit(MAX_EVENTS + MAX_EVENT_CLAIMS + 1)
        )
    )
    if len(rows) >= MAX_EVENTS + MAX_EVENT_CLAIMS + 1:
        raise schedule.StudentError(409, "Calendar activity needs review before another page")
    offset = 0
    if cursor is not None:
        position = next((i for i, row in enumerate(rows) if row.id == cursor), None)
        if position is None:
            raise schedule.StudentError(409, "Calendar activity changed. Reload from the beginning")
        offset = position + 1
    page = rows[offset : offset + 20]
    return {
        "generation": current.generation,
        "calendar_revision": current.revision,
        "events": [_view(row, _open(row, settings), current) for row in page],
        "next_cursor": str(page[-1].id) if page and offset + 20 < len(rows) else None,
    }


async def event_view(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    event_id: UUID,
    settings: Settings,
) -> CalendarEventView:
    """Recover one known receipt without provider I/O or stale command authority."""
    current = await service.boundary(
        database, user_id, workspace_id, session_hash, lock=False, allow_paused=True
    )
    row = await database.scalar(
        select(StudentCalendarEvent)
        .where(
            StudentCalendarEvent.id == event_id,
            StudentCalendarEvent.user_id == user_id,
            StudentCalendarEvent.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.owner_scope_id != (current.state.id if current.state else None)
        or row.generation > current.generation
    ):
        raise schedule.StudentError(404, "Calendar event receipt not found")
    return _view(row, _open(row, settings), current)


async def _free(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    draft: CalendarEventPreview,
) -> AvailabilitySnapshot:
    snapshot = await availability.preview(
        factory, settings, user_id, workspace_id, session_hash, draft.availability
    )
    if draft.start_at - MEETING_BUFFER < snapshot.checked_at or not displacement.fits(
        snapshot, draft
    ):
        raise schedule.StudentError(
            409, "That time is no longer confirmed free. Check availability again"
        )
    if draft.allow_study_displacement or not snapshot.input.study_blocks:
        return snapshot
    # A maximal provisional opening can contain study elsewhere. Independently
    # certify a zero-move slice; a client option or ID list is never authority.
    async with factory() as database:
        current = await service.boundary(database, user_id, workspace_id, session_hash)
        await _fresh_internal(
            database, current, user_id, workspace_id, draft, snapshot, settings=settings
        )
        context = await displacement.load_context(
            database,
            user_id,
            workspace_id,
            snapshot,
            planning_enabled=settings.student_planning_enabled,
        )
        if (
            displacement.certify(context, snapshot, draft.start_at, draft.end_at, current.now)
            is not None
        ):
            raise schedule.StudentError(
                409, "That time requires an explicit review of affected study work"
            )
        await database.commit()
    return snapshot


async def _fresh_internal(
    database: AsyncSession,
    current: service.Boundary,
    user_id: UUID,
    workspace_id: UUID,
    draft: CalendarEventPreview,
    fresh: AvailabilitySnapshot,
    *,
    settings: Settings,
) -> None:
    if current.now >= fresh.expires_at:
        raise schedule.StudentError(409, "The availability check expired. Review the time again")
    internal = await availability._internal(
        database, user_id, workspace_id, draft.availability, settings=settings
    )
    if internal.digest != fresh.revision.student_revision:
        raise schedule.StudentError(
            409, "Your schedule or routine changed. Check availability again"
        )


async def _check_claims(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    draft: CalendarEventPreview,
    settings: Settings,
    *,
    excluding: UUID | None = None,
) -> None:
    rows = list(
        await database.scalars(
            select(StudentCalendarEvent)
            .where(
                StudentCalendarEvent.user_id == user_id,
                StudentCalendarEvent.workspace_id == workspace_id,
                StudentCalendarEvent.status.in_(CLAIMED_STATUSES),
            )
            .order_by(StudentCalendarEvent.id)
            .limit(MAX_EVENT_CLAIMS + 1)
        )
    )
    if len(rows) > MAX_EVENT_CLAIMS:
        raise schedule.StudentError(
            409, "Too many saved calendar event claims to check safely. Review earlier events first"
        )
    for row in rows:
        if row.id == excluding:
            continue
        previous = CalendarEventPreview.model_validate(_open(row, settings)["draft"])
        first, last = previous.start_at - MEETING_BUFFER, previous.end_at + MEETING_BUFFER
        if draft.availability.travel_assumption == "unknown":
            # Mirror the existing availability engine's bounded unknown-travel
            # day span; a lagging provider cannot turn that uncertainty into zero.
            zone = ZoneInfo(draft.timezone)
            first_day = (first - timedelta(hours=4)).astimezone(zone).date()
            last_day = (
                (last + timedelta(hours=4) - timedelta(microseconds=1)).astimezone(zone).date()
            )
            first, last = _uncertain_day_span(first_day, zone, days=(last_day - first_day).days + 1)
        if first < draft.end_at + MEETING_BUFFER and last > draft.start_at - MEETING_BUFFER:
            if row.status in {"created", "changed"}:
                raise schedule.StudentError(
                    409,
                    "A saved calendar event still claims this time. Check its exact status first",
                )
            raise schedule.StudentError(
                409, "An earlier calendar request for this time is unconfirmed. Check its status"
            )


async def preview(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    draft: CalendarEventPreview,
    *,
    provider: BearerWriteProvider | AppleWriteProvider | None = None,
) -> CalendarEventView:
    _admission(settings)
    # Require valid encrypted storage before contacting any provider.
    try:
        cipher(settings)
    except ConnectorRuntimeError:
        raise schedule.StudentError(503, "Private event storage is not configured") from None
    async with factory() as database:
        current = await service.boundary(database, user_id, workspace_id, session_hash)
        service.check_command(current, draft)
        authority = await _authority(database, user_id, workspace_id, session_hash, draft.grant_id)
        _admission(settings, authority.provider)
        _transport_draft(authority.provider, draft)
        previous = (
            await database.scalar(
                select(StudentCalendarEvent).where(
                    StudentCalendarEvent.owner_scope_id == current.state.id,
                    StudentCalendarEvent.request_id == draft.request_id,
                )
            )
            if current.state
            else None
        )
        if previous is not None:
            payload = _open(previous, settings)
            if payload["draft"] != draft.model_dump(mode="json"):
                raise schedule.StudentError(
                    409, "This review request was already used for a different event"
                )
            return _view(previous, payload, current)
        await _check_claims(database, user_id, workspace_id, draft, settings)
        await database.commit()
    _admission(settings, authority.provider)
    token = await _token(factory, settings, authority, session_hash)

    async def check() -> None:
        _admission(settings, authority.provider)
        await _check(factory, authority, session_hash)

    writer = _writer(authority, token, settings, draft.timezone, provider)
    try:
        target = await writer.target(calendar_id=draft.calendar_id, check=check)
    except GoogleCalendarWriteError:
        raise schedule.StudentError(
            409, "The selected writable calendar could not be confirmed"
        ) from None
    fresh = await _free(factory, settings, user_id, workspace_id, session_hash, draft)
    async with factory() as database:
        current = await service.boundary(database, user_id, workspace_id, session_hash)
        service.check_command(current, draft)
        await _fresh_internal(
            database, current, user_id, workspace_id, draft, fresh, settings=settings
        )
        if (
            await _authority(database, user_id, workspace_id, session_hash, draft.grant_id)
            != authority
        ):
            raise schedule.StudentError(409, "Calendar permissions changed. Review again")
        # Two identical preview requests can finish their read-only provider
        # work concurrently. Recheck the durable replay fence under the owner
        # lock instead of relying on a unique-constraint error at publication.
        previous = (
            await database.scalar(
                select(StudentCalendarEvent).where(
                    StudentCalendarEvent.owner_scope_id == current.state.id,
                    StudentCalendarEvent.request_id == draft.request_id,
                )
            )
            if current.state
            else None
        )
        if previous is not None:
            previous_payload = _open(previous, settings)
            if previous_payload["draft"] != draft.model_dump(mode="json"):
                raise schedule.StudentError(409, "This review request was used for another event")
            return _view(previous, previous_payload, current)
        await _check_claims(database, user_id, workspace_id, draft, settings)
        count = await database.scalar(
            select(func.count())
            .select_from(StudentCalendarEvent)
            .where(
                StudentCalendarEvent.user_id == user_id,
                StudentCalendarEvent.workspace_id == workspace_id,
                StudentCalendarEvent.generation == current.generation,
            )
        )
        if count is None or count >= MAX_EVENTS:
            raise schedule.StudentError(
                409, "Review earlier calendar activity before adding another event"
            )
        assert current.state is not None
        row = StudentCalendarEvent(
            id=uuid4(),
            owner_scope_id=current.state.id,
            user_id=user_id,
            workspace_id=workspace_id,
            grant_id=authority.grant_id,
            auth_session_id=current.session.id,
            request_id=draft.request_id,
            generation=current.generation,
            grant_revision=authority.grant_revision,
            revision=1,
            status="pending",
            provider=authority.provider,
            created_at=current.now,
            expires_at=current.now + DRAFT_TTL,
            encrypted_payload="",
            digest="",
        )
        payload = {
            "draft": draft.model_dump(mode="json"),
            "calendar_label": target.label,
            "connection_id": str(authority.connection_id),
            "etag": None,
            "creation_etag": None,
            "provenance": "Added by NavoX",
        }
        if authority.provider != "google":
            payload["provider"] = authority.provider
        if draft.allow_study_displacement:
            review = await displacement.prepare(
                database, user_id, workspace_id, draft, fresh, current.now, settings=settings
            )
            if review is not None:
                payload["study_displacement"] = {
                    "v": 1,
                    "review": review.model_dump(mode="json"),
                    "applied": False,
                    "applied_revision": None,
                }
                await displacement.index_review(
                    database, user_id, workspace_id, row.id, review, current.now
                )
        row.digest = _digest({"id": str(row.id), "generation": row.generation, "payload": payload})
        row.encrypted_payload = _seal(row, payload, settings)
        database.add(row)
        await database.flush()
        result = _view(row, payload, current)
        schedule.audit(database, user_id, workspace_id, "student.calendar.event.reviewed", row.id)
        await database.commit()
        return result


async def _record(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    event_id: UUID,
    command: CalendarEventCommand,
    settings: Settings,
    *,
    allow_earlier: bool = False,
) -> tuple[service.Boundary, StudentCalendarEvent, dict[str, Any]]:
    current = await service.boundary(database, user_id, workspace_id, session_hash)
    service.check_command(current, command, admitting=False)
    row = await database.scalar(
        select(StudentCalendarEvent)
        .where(
            StudentCalendarEvent.id == event_id,
            StudentCalendarEvent.user_id == user_id,
            StudentCalendarEvent.workspace_id == workspace_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise schedule.StudentError(404, "Calendar event review not found")
    generation_valid = row.generation == current.generation or (
        allow_earlier
        and row.generation < current.generation
        and row.status in {"executing", "uncertain", "undoing", "created", "changed", "undone"}
    )
    if not generation_valid or row.owner_scope_id != (current.state.id if current.state else None):
        raise schedule.StudentError(409, "This calendar review belongs to an earlier reset")
    if (
        command.event_revision != row.revision
        or command.digest != row.digest
        or row.revision >= MAX_REVISION
    ):
        raise schedule.StudentError(409, "This event review changed. Refresh before continuing")
    return current, row, _open(row, settings)


async def _recover_authority(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: dict[str, Any],
) -> WriteAuthority:
    for grant in await service.grants(database, user_id, workspace_id, scheduling=True):
        if (
            grant.status == "active"
            and str(grant.connection_id) == payload["connection_id"]
            and _kind(cast(StudentCalendarWriteGrant, grant).provider)
            == _kind(payload.get("provider"))
        ):
            return await _authority(database, user_id, workspace_id, session_hash, grant.id)
    raise schedule.StudentError(
        409, "Reconnect scheduling access to this same calendar account before checking the event"
    )


def _body(
    row: StudentCalendarEvent, draft: CalendarEventPreview, *, description: str = ""
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row.id.hex,
        "summary": draft.title,
        "start": {"dateTime": draft.start_at.isoformat(), "timeZone": draft.timezone},
        "end": {"dateTime": draft.end_at.isoformat(), "timeZone": draft.timezone},
        "visibility": "private",
        "transparency": "opaque",
        "extendedProperties": {"private": {"navoxAction": row.id.hex, "navoxDigest": row.digest}},
        "reminders": {"useDefault": False},
    }
    if draft.location:
        body["location"] = draft.location
    if description:
        body["description"] = description
    return body


def _matches(
    row: StudentCalendarEvent,
    draft: CalendarEventPreview,
    event: dict[str, Any],
    *,
    description: str = "",
) -> bool:
    try:
        allowed = {
            "kind",
            "etag",
            "id",
            "status",
            "htmlLink",
            "created",
            "updated",
            "summary",
            "location",
            "creator",
            "organizer",
            "start",
            "end",
            "iCalUID",
            "sequence",
            "reminders",
            "eventType",
            "extendedProperties",
            "transparency",
            "visibility",
            "description",
            "guestsCanInviteOthers",
            "guestsCanSeeOtherGuests",
            "guestsCanModify",
            "anyoneCanAddSelf",
            "privateCopy",
            "locked",
        }
        start, end = event["start"]["dateTime"], event["end"]["dateTime"]
        return bool(
            set(event).issubset(allowed)
            and event["id"] == row.id.hex
            and event.get("status") == "confirmed"
            and event.get("summary") == draft.title
            and event.get("location", "") == draft.location
            and datetime.fromisoformat(start.replace("Z", "+00:00")) == draft.start_at
            and datetime.fromisoformat(end.replace("Z", "+00:00")) == draft.end_at
            and event["start"].get("timeZone") == draft.timezone
            and event["end"].get("timeZone") == draft.timezone
            and event.get("description", "") == description
            and event.get("eventType", "default") == "default"
            and event.get("reminders", {}).get("useDefault") is False
            and not event.get("reminders", {}).get("overrides")
            and event.get("guestsCanInviteOthers", True) is True
            and event.get("guestsCanSeeOtherGuests", True) is True
            and event.get("guestsCanModify", False) is False
            and event.get("anyoneCanAddSelf", False) is False
            and event.get("privateCopy", False) is False
            and event.get("locked", False) is False
            and event.get("extendedProperties", {}).get("private", {}).get("navoxAction")
            == row.id.hex
            and event.get("extendedProperties", {}).get("private", {}).get("navoxDigest")
            == row.digest
            and event.get("transparency", "opaque") == "opaque"
            and event.get("visibility") == "private"
            and not event.get("attendees")
            and not event.get("recurrence")
            and not event.get("conferenceData")
            and isinstance(event.get("etag"), str)
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


async def approve(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    event_id: UUID,
    command: CalendarEventApproval,
    *,
    provider: BearerWriteProvider | AppleWriteProvider | None = None,
) -> CalendarEventView:
    _admission(settings)
    async with factory() as database:
        current, row, payload = await _record(
            database, user_id, workspace_id, session_hash, event_id, command, settings
        )
        service.check_command(current, command)
        if row.status != "pending":
            raise schedule.StudentError(409, "This event was already submitted. Check its status")
        if payload.get("study_displacement", {}).get("redacted") is True:
            raise schedule.StudentError(409, "This study review was forgotten. Prepare a new event")
        if utc(row.expires_at) <= current.now or row.auth_session_id != current.session.id:
            raise schedule.StudentError(
                409, "This approval expired or sign-in changed. Review a new event"
            )
        assert row.grant_id is not None
        authority = await _authority(database, user_id, workspace_id, session_hash, row.grant_id)
        _admission(settings, authority.provider)
        if authority.grant_revision != row.grant_revision:
            raise schedule.StudentError(409, "Scheduling permission changed. Review a new event")
        draft = CalendarEventPreview.model_validate(payload["draft"])
        _transport_draft(authority.provider, draft)
        await _check_claims(database, user_id, workspace_id, draft, settings, excluding=row.id)
        await database.commit()
    # Fresh complete all-calendar/internal availability is mandatory immediately
    # before the one submitted side effect; there is no override of unknowns.
    fresh = await _free(factory, settings, user_id, workspace_id, session_hash, draft)
    _admission(settings, authority.provider)
    token = await _token(factory, settings, authority, session_hash)

    async def check() -> None:
        _admission(settings, authority.provider)
        await _check(factory, authority, session_hash)
        async with factory() as database:
            current = await service.boundary(database, user_id, workspace_id, session_hash)
            await _fresh_internal(
                database, current, user_id, workspace_id, draft, fresh, settings=settings
            )
            await database.commit()

    writer = _writer(authority, token, settings, draft.timezone, provider)
    try:
        target = await writer.target(calendar_id=draft.calendar_id, check=check)
    except GoogleCalendarWriteError:
        raise schedule.StudentError(409, "The selected calendar is no longer writable") from None
    if target.label != payload["calendar_label"]:
        raise schedule.StudentError(409, "The selected calendar changed. Review a new event")
    async with factory() as database:
        current, row, payload = await _record(
            database, user_id, workspace_id, session_hash, event_id, command, settings
        )
        service.check_command(current, command)
        if (
            row.status != "pending"
            or utc(row.expires_at) <= current.now
            or row.auth_session_id != current.session.id
        ):
            raise schedule.StudentError(409, "The event approval is no longer available")
        if (
            await _authority(database, user_id, workspace_id, session_hash, authority.grant_id)
            != authority
        ):
            raise schedule.StudentError(409, "Calendar permissions changed. Review again")
        await _fresh_internal(
            database, current, user_id, workspace_id, draft, fresh, settings=settings
        )
        await _check_claims(database, user_id, workspace_id, draft, settings, excluding=row.id)
        study = payload.get("study_displacement")
        review = None
        if study is not None:
            if study.get("redacted") is True or study.get("applied") is not False:
                raise schedule.StudentError(409, "The study review is no longer available")
            try:
                review = displacement.StudyReview.model_validate(study["review"])
            except (ValueError, TypeError, KeyError):
                raise schedule.StudentError(
                    503, "This private study review could not be opened"
                ) from None
            await displacement.validate(
                database,
                user_id,
                workspace_id,
                draft,
                fresh,
                review,
                current.now,
                settings=settings,
            )
        elif draft.allow_study_displacement:
            if (
                await displacement.prepare(
                    database, user_id, workspace_id, draft, fresh, current.now, settings=settings
                )
                is not None
            ):
                raise schedule.StudentError(
                    409, "This event now affects study work. Review all changes again"
                )
        # Any own-app side effect admitted since another request's fresh read
        # invalidates that request, even after this event becomes confirmed.
        # Recapture our own authority at the new native revision so our bounded
        # provider call remains valid while stale concurrent requests fail.
        assert current.state is not None
        service.advance(current.state)
        await database.flush()
        authority = await _authority(
            database, user_id, workspace_id, session_hash, authority.grant_id
        )
        row.status, row.started_at = "executing", current.now
        row.revision += 1
        if review is not None:
            planning = await displacement.apply(
                database, user_id, workspace_id, review, row.id, current.now, settings=settings
            )
            assert study is not None
            study["applied"], study["applied_revision"] = True, planning.revision
            row.encrypted_payload = _seal(row, payload, settings)
            await database.flush()
            from navox.student_planning import service as planning_service

            planning.accepted_source_hash = await planning_service.stable_source_hash(
                database, user_id, workspace_id, planning
            )
        # Admission creates a native interval claim even without study moves.
        # Recapture only that authorized native transition; the provider facts
        # and their original expiry remain the ones checked before admission.
        await database.flush()
        internal = await availability._internal(
            database, user_id, workspace_id, draft.availability, settings=settings
        )
        fresh = fresh.model_copy(
            update={
                "revision": fresh.revision.model_copy(
                    update={
                        "student_revision": internal.digest,
                        "planning_revision": internal.planning_revision,
                    }
                )
            }
        )
        latest = await service.boundary(database, user_id, workspace_id, session_hash)
        await _fresh_internal(
            database, latest, user_id, workspace_id, draft, fresh, settings=settings
        )
        body = _body(row, draft, description=payload.get("provenance", ""))
        schedule.audit(database, user_id, workspace_id, "student.calendar.event.submitted", row.id)
        await database.commit()
    # Once this receipt is durable no code path returns the action to pending.
    rejected = False
    try:
        receipt = await writer.insert(calendar_id=draft.calendar_id, event=body, check=check)
        inserted = receipt.event
        if not _matches(row, draft, inserted, description=payload.get("provenance", "")):
            return await _finish(
                factory,
                settings,
                authority,
                session_hash,
                event_id,
                "changed",
                None,
                expected_revision=command.event_revision + 1,
            )
        if receipt.creation_etag is not None:
            await _pin_creation(
                factory, settings, authority, session_hash, event_id, receipt.creation_etag
            )
    except CalendarWriteRejected as error:
        rejected = error.status_code != 409  # 409 needs exact-ID reconciliation.
    except GoogleCalendarWriteError:
        pass
    if rejected:
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            "failed",
            None,
            expected_revision=command.event_revision + 1,
        )
    return await _observe(factory, settings, authority, session_hash, event_id, token, writer)


async def _pin_creation(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    authority: WriteAuthority,
    session_hash: str,
    event_id: UUID,
    etag: str,
) -> None:
    async with factory() as database:
        if (
            await _authority(
                database,
                authority.scope.user_id,
                authority.scope.workspace_id,
                session_hash,
                authority.grant_id,
            )
            != authority
        ):
            raise schedule.StudentError(409, "Permissions changed. Check the held event status")
        row = await database.get(StudentCalendarEvent, event_id)
        if (
            row is None
            or row.generation != authority.scope.generation
            or row.status not in {"executing", "uncertain", "created", "changed"}
        ):
            return
        payload = _open(row, settings)
        if payload.get("creation_etag") not in {None, etag} or payload.get("etag") not in {
            None,
            etag,
        }:
            row.status = "changed"
        payload["creation_etag"] = etag
        if payload.get("etag") is None:
            payload["etag"] = etag
        row.encrypted_payload = _seal(row, payload, settings)
        row.revision += 1
        await database.commit()


async def _finish(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    authority: WriteAuthority,
    session_hash: str,
    event_id: UUID,
    status: str,
    etag: str | None,
    *,
    expected_revision: int,
) -> CalendarEventView:
    async with factory() as database:
        observed = await _authority(
            database,
            authority.scope.user_id,
            authority.scope.workspace_id,
            session_hash,
            authority.grant_id,
        )
        if observed != authority:
            raise schedule.StudentError(
                409, "Permissions changed. The event outcome remains held for review"
            )
        current = await service.boundary(
            database, authority.scope.user_id, authority.scope.workspace_id, session_hash
        )
        row = await database.scalar(
            select(StudentCalendarEvent)
            .where(
                StudentCalendarEvent.id == event_id,
                StudentCalendarEvent.user_id == authority.scope.user_id,
                StudentCalendarEvent.workspace_id == authority.scope.workspace_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.generation > current.generation
            or row.owner_scope_id != (current.state.id if current.state else None)
        ):
            raise schedule.StudentError(409, "This event receipt changed. Check activity")
        payload = _open(row, settings)
        if row.revision != expected_revision:
            return _view(row, payload, current)
        if row.status not in {"executing", "uncertain", "created", "changed", "undoing", "undone"}:
            return _view(row, payload, current)
        # A concurrent observer can only confirm an outcome; it cannot turn a
        # confirmed deletion back into a created event or downgrade a success.
        if (
            row.status == "undone"
            or (row.status == "changed" and status != "undone")
            or (status == "uncertain" and row.status in {"created", "changed"})
        ):
            return _view(row, payload, current)
        row.status = status
        row.revision += 1
        if etag is not None and payload.get("etag") is None:
            payload["etag"] = etag
        if status in {"created", "undone"}:
            row.verified_at = current.now
        row.encrypted_payload = _seal(row, payload, settings)
        result = _view(row, payload, current)
        schedule.audit(
            database,
            authority.scope.user_id,
            authority.scope.workspace_id,
            "student.calendar.event.checked",
            row.id,
        )
        await database.commit()
        return result


async def _observe(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    authority: WriteAuthority,
    session_hash: str,
    event_id: UUID,
    token: WriteCredential,
    provider: BoundWriteProvider,
) -> CalendarEventView:
    async with factory() as database:
        current = await service.boundary(
            database, authority.scope.user_id, authority.scope.workspace_id, session_hash
        )
        row = await database.get(StudentCalendarEvent, event_id)
        if (
            row is None
            or row.user_id != authority.scope.user_id
            or row.workspace_id != authority.scope.workspace_id
            or row.generation > current.generation
            or row.owner_scope_id != (current.state.id if current.state else None)
        ):
            raise schedule.StudentError(404, "Calendar event receipt not found")
        payload = _open(row, settings)
        draft = CalendarEventPreview.model_validate(payload["draft"])
        previous_status = row.status
        observed_revision = row.revision
        await database.commit()

    async def check() -> None:
        await _check(factory, authority, session_hash)

    try:
        event = await provider.event(
            calendar_id=draft.calendar_id, event_id=event_id.hex, check=check
        )
    except GoogleCalendarWriteError:
        # Keep undoing as its own uncertainty: later absence confirms deletion.
        status = "undoing" if previous_status == "undoing" else "uncertain"
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            status,
            None,
            expected_revision=observed_revision,
        )
    if event is None:
        status = (
            "undone"
            if authority.provider != "microsoft"
            and previous_status in {"undoing", "created", "changed", "undone"}
            else "uncertain"
        )
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            status,
            None,
            expected_revision=observed_revision,
        )
    matches = _matches(row, draft, event, description=payload.get("provenance", ""))
    original_etag = payload.get("etag")
    if original_etag is not None and event.get("etag") != original_etag:
        matches = False
    status = "created" if matches else "changed"
    if previous_status == "undoing":
        # Existence cannot prove that an in-flight timed-out DELETE failed.
        # Keep its receipt held until absence or a conflicting revision is seen.
        status = "undoing" if matches else "changed"
    return await _finish(
        factory,
        settings,
        authority,
        session_hash,
        event_id,
        status,
        event.get("etag") if matches else None,
        expected_revision=observed_revision,
    )


async def reconcile(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    event_id: UUID,
    command: CalendarEventCommand,
    *,
    provider: BearerWriteProvider | AppleWriteProvider | None = None,
) -> CalendarEventView:
    async with factory() as database:
        current, row, payload = await _record(
            database,
            user_id,
            workspace_id,
            session_hash,
            event_id,
            command,
            settings,
            allow_earlier=True,
        )
        if row.status in {"pending", "failed", "undone"}:
            return _view(row, payload, current)
        authority = await _recover_authority(database, user_id, workspace_id, session_hash, payload)
        await database.commit()
    token = await _token(factory, settings, authority, session_hash)
    return await _observe(
        factory,
        settings,
        authority,
        session_hash,
        event_id,
        token,
        _writer(
            authority,
            token,
            settings,
            CalendarEventPreview.model_validate(payload["draft"]).timezone,
            provider,
        ),
    )


async def undo(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    event_id: UUID,
    command: CalendarEventApproval,
    *,
    provider: BearerWriteProvider | AppleWriteProvider | None = None,
) -> CalendarEventView:
    _admission(settings)
    async with factory() as database:
        current, row, payload = await _record(
            database, user_id, workspace_id, session_hash, event_id, command, settings
        )
        service.check_command(current, command)
        if (
            row.status != "created"
            or not _exact_version(payload.get("etag"))
            or payload.get("creation_etag") != payload.get("etag")
        ):
            raise schedule.StudentError(
                409, "Only a verified unchanged event can be undone. Check its status"
            )
        authority = await _recover_authority(database, user_id, workspace_id, session_hash, payload)
        draft = CalendarEventPreview.model_validate(payload["draft"])
        await database.commit()
    _admission(settings, authority.provider)
    token = await _token(factory, settings, authority, session_hash)

    async def check() -> None:
        _admission(settings, authority.provider)
        await _check(factory, authority, session_hash)

    writer = _writer(authority, token, settings, draft.timezone, provider)
    try:
        event = await writer.event(
            calendar_id=draft.calendar_id, event_id=event_id.hex, check=check
        )
    except GoogleCalendarWriteError:
        raise schedule.StudentError(
            503,
            f"{_label(authority.provider)} could not confirm the event. No Undo was submitted",
        ) from None
    if event is None:
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            "undone",
            None,
            expected_revision=command.event_revision,
        )
    if (
        not _matches(row, draft, event, description=payload.get("provenance", ""))
        or event.get("etag") != payload["etag"]
    ):
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            "changed",
            None,
            expected_revision=command.event_revision,
        )
    async with factory() as database:
        current, row, latest = await _record(
            database, user_id, workspace_id, session_hash, event_id, command, settings
        )
        service.check_command(current, command)
        if (
            row.status != "created"
            or latest.get("etag") != payload["etag"]
            or latest.get("creation_etag") != payload["etag"]
        ):
            raise schedule.StudentError(409, "This event changed. Review its status")
        if (
            await _recover_authority(database, user_id, workspace_id, session_hash, latest)
            != authority
        ):
            raise schedule.StudentError(409, "Scheduling permission changed. Review again")
        assert current.state is not None
        service.advance(current.state)
        await database.flush()
        authority = await _recover_authority(database, user_id, workspace_id, session_hash, latest)
        row.status, row.started_at = "undoing", current.now
        row.revision += 1
        await database.commit()
    try:
        await writer.delete(
            calendar_id=draft.calendar_id,
            event_id=event_id.hex,
            etag=payload["etag"],
            check=check,
        )
    except CalendarWriteChanged:
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            "changed",
            None,
            expected_revision=command.event_revision + 1,
        )
    except CalendarWriteRejected:
        return await _finish(
            factory,
            settings,
            authority,
            session_hash,
            event_id,
            "created",
            None,
            expected_revision=command.event_revision + 1,
        )
    except GoogleCalendarWriteError:
        pass
    return await _observe(factory, settings, authority, session_hash, event_id, token, writer)
