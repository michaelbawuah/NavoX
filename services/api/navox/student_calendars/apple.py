"""Exact-owner Apple busy consent, verified reads and immediately encrypted credentials."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from navox.core.credential_vault import CredentialVault, CredentialVaultError
from navox.core.settings import Settings
from navox.db.apple_calendars import AppleCalendarGrant
from navox.db.models import Connection, ConnectionCredential
from navox.providers.apple_availability import AppleAvailabilityProvider, AppleAvailabilitySnapshot
from navox.student import service as schedule
from navox.student.schemas import Input
from navox.student_calendars import service as calendars
from navox.student_calendars.schemas import MAX_REVISION, CalendarCommand

CONSENT_VERSION: Literal["apple-calendar-availability.v1"] = "apple-calendar-availability.v1"
BUSY_PURPOSE = "navox:apple-calendar-busy.v1"
MAX_ACCOUNTS = 8


@dataclass(frozen=True)
class AppleGrantSnapshot(calendars.CalendarGrantSnapshot):
    provider: str = "icloud"


class AppleCalendarConnect(CalendarCommand):
    consent_version: Literal["apple-calendar-availability.v1"]
    confirmed: Literal[True]
    apple_id: str = Field(min_length=3, max_length=256, repr=False)
    app_password: SecretStr = Field(repr=False)

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit busy-time permission is required")
        return value

    @field_validator("apple_id")
    @classmethod
    def account(cls, value: str) -> str:
        value = value.strip().casefold()
        if not re.fullmatch(r"[^\s:@]+@[^\s:@]+\.[^\s:@]+", value):
            raise ValueError("Use the email address for your Apple Account")
        return value

    @field_validator("app_password")
    @classmethod
    def app_specific(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"[a-z]{4}(?:-[a-z]{4}){3}", value.get_secret_value()):
            raise ValueError("Use an Apple app-specific password")
        return value


class AppleGrantView(Input):
    id: UUID
    provider: Literal["icloud"] = "icloud"
    connection_id: UUID
    account_label: str | None
    status: Literal["connected", "unavailable"]
    revision: int


class AppleCalendarView(Input):
    generation: int
    revision: int
    consent_version: Literal["apple-calendar-availability.v1"] = CONSENT_VERSION
    configured: bool
    grants: list[AppleGrantView]
    changes_applied: Literal[False] = False


def _vault(settings: Settings) -> CredentialVault:
    try:
        return CredentialVault(settings)
    except CredentialVaultError:
        raise schedule.StudentError(503, "Apple calendar connection is unavailable here") from None


def _digest(payload: AppleCalendarConnect) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "account": payload.apple_id,
                "app_password": payload.app_password.get_secret_value(),
                "consent": payload.consent_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def _grants(
    database: AsyncSession, user_id: UUID, workspace_id: UUID
) -> list[AppleCalendarGrant]:
    rows = list(
        await database.scalars(
            select(AppleCalendarGrant)
            .where(
                AppleCalendarGrant.user_id == user_id,
                AppleCalendarGrant.workspace_id == workspace_id,
            )
            .order_by(AppleCalendarGrant.id)
            .limit(MAX_ACCOUNTS + 1)
            .execution_options(populate_existing=True)
        )
    )
    if len(rows) > MAX_ACCOUNTS:
        raise schedule.StudentError(409, "Too many Apple calendar connections to check safely")
    return rows


async def _connection(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, connection_id: UUID
) -> Connection | None:
    row: Connection | None = await database.scalar(
        select(Connection)
        .where(
            Connection.id == connection_id,
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
            Connection.provider == "icloud",
        )
        .execution_options(populate_existing=True)
    )
    return row


def _ready(connection: Connection | None) -> bool:
    return bool(
        connection is not None
        and connection.status == "active"
        and connection.credential_reference is not None
        and BUSY_PURPOSE in connection.granted_scopes
    )


async def view(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, session_hash: str, settings: Settings
) -> AppleCalendarView:
    current = await calendars.boundary(
        database, user_id, workspace_id, session_hash, lock=False, allow_paused=True
    )
    items = []
    for grant in await _grants(database, user_id, workspace_id):
        connection = await _connection(database, user_id, workspace_id, grant.connection_id)
        items.append(
            AppleGrantView(
                id=grant.id,
                connection_id=grant.connection_id,
                account_label=connection.external_email if connection else None,
                status="connected"
                if _ready(connection)
                and grant.generation == current.generation
                and current.state is not None
                and grant.owner_scope_id == current.state.id
                and grant.consent_version == CONSENT_VERSION
                else "unavailable",
                revision=grant.revision,
            )
        )
    try:
        CredentialVault(settings)
        configured = True
    except CredentialVaultError:
        configured = False
    return AppleCalendarView(
        generation=current.generation,
        revision=current.revision,
        configured=configured,
        grants=items,
    )


async def _replay(
    database: AsyncSession,
    current: calendars.Boundary,
    user_id: UUID,
    workspace_id: UUID,
    payload: AppleCalendarConnect,
) -> bool:
    row = await database.scalar(
        select(AppleCalendarGrant)
        .where(
            AppleCalendarGrant.user_id == user_id,
            AppleCalendarGrant.workspace_id == workspace_id,
            AppleCalendarGrant.request_id == payload.request_id,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return False
    if (
        row.generation != current.generation
        or not hmac.compare_digest(row.request_digest, _digest(payload))
        or row.auth_session_id != current.session.id
    ):
        raise schedule.StudentError(409, "Apple calendar setup changed. Review and try again")
    return True


async def connect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    payload: AppleCalendarConnect,
    settings: Settings,
    *,
    provider: AppleAvailabilityProvider | None = None,
) -> AppleCalendarView:
    vault = _vault(settings)
    current = await calendars.boundary(database, user_id, workspace_id, session_hash)
    if await _replay(database, current, user_id, workspace_id, payload):
        return await view(database, user_id, workspace_id, session_hash, settings)
    calendars.check_command(current, payload)
    await calendars.ensure_state(database, current, user_id, workspace_id)
    session_id = current.session.id
    # Owner/session locks never remain held during provider I/O. Concurrent
    # connect attempts are reads only; final publication serializes once more.
    await database.commit()
    assert database.bind is not None
    factory = async_sessionmaker(database.bind, expire_on_commit=False)

    async def check() -> None:
        async with factory() as fresh:
            boundary = await calendars.boundary(fresh, user_id, workspace_id, session_hash)
            calendars.check_command(boundary, payload)
            if boundary.session.id != session_id:
                raise schedule.StudentError(
                    409, "Your sign-in changed. Review Apple calendar setup"
                )

    now = current.now.replace(microsecond=0)
    snapshot = await (provider or AppleAvailabilityProvider()).collect(
        apple_id=payload.apple_id,
        app_password=payload.app_password.get_secret_value(),
        start_at=now,
        end_at=now + timedelta(days=1),
        check=check,
    )
    if snapshot.state != "complete":
        raise schedule.StudentError(
            503,
            "Apple could not confirm calendar access. "
            "Check the app-specific password and try again",
        )
    current = await calendars.boundary(database, user_id, workspace_id, session_hash)
    if await _replay(database, current, user_id, workspace_id, payload):
        return await view(database, user_id, workspace_id, session_hash, settings)
    calendars.check_command(current, payload)
    assert current.state is not None
    external_id = "apple:" + hashlib.sha256(payload.apple_id.encode()).hexdigest()
    connection = await database.scalar(
        select(Connection)
        .where(
            Connection.user_id == user_id,
            Connection.workspace_id == workspace_id,
            Connection.provider == "icloud",
            Connection.external_account_id == external_id,
        )
        .execution_options(populate_existing=True)
    )
    existing = await _grants(database, user_id, workspace_id)
    grant = next(
        (
            item
            for item in existing
            if connection is not None and item.connection_id == connection.id
        ),
        None,
    )
    if grant is None and len(existing) >= MAX_ACCOUNTS:
        raise schedule.StudentError(409, "Disconnect an Apple account before adding another")
    if connection is None:
        connection = Connection(
            id=uuid4(),
            user_id=user_id,
            workspace_id=workspace_id,
            provider="icloud",
            external_account_id=external_id,
            external_email=payload.apple_id,
            status="active",
            granted_scopes=[BUSY_PURPOSE],
        )
        database.add(connection)
        await database.flush()
    old_credential = connection.credential_reference
    envelope = json.dumps(
        {
            "version": 1,
            "user_id": str(user_id),
            "workspace_id": str(workspace_id),
            "connection_id": str(connection.id),
            "apple_id": payload.apple_id,
            "app_password": payload.app_password.get_secret_value(),
        },
        separators=(",", ":"),
    )
    credential = ConnectionCredential(
        id=uuid4(),
        encrypted_refresh_token=vault.seal_refresh_token(envelope),
        key_version="apple-app-password.v1",
    )
    database.add(credential)
    await database.flush()
    connection.credential_reference, connection.status = credential.id, "active"
    connection.external_email, connection.granted_scopes = payload.apple_id, [BUSY_PURPOSE]
    connection.last_checked_at, connection.last_error = current.now, None
    if grant is None:
        grant = AppleCalendarGrant(
            id=uuid4(),
            owner_scope_id=current.state.id,
            user_id=user_id,
            workspace_id=workspace_id,
            connection_id=connection.id,
            generation=current.generation,
            revision=1,
            consent_version=CONSENT_VERSION,
            accepted_at=current.now,
            request_id=payload.request_id,
            request_digest=_digest(payload),
            auth_session_id=session_id,
        )
        database.add(grant)
    else:
        grant.request_id, grant.request_digest = payload.request_id, _digest(payload)
        grant.generation, grant.auth_session_id = current.generation, session_id
        grant.revision = min(MAX_REVISION, grant.revision + 1)
        grant.accepted_at = current.now
    calendars.advance(current.state)
    await database.flush()
    if old_credential is not None:
        await _erase_credential(database, old_credential)
    schedule.audit(database, user_id, workspace_id, "student.calendar.apple.connected", grant.id)
    await database.commit()
    return await view(database, user_id, workspace_id, session_hash, settings)


async def _erase_credential(database: AsyncSession, credential_id: UUID) -> None:
    other = await database.scalar(
        select(Connection.id).where(Connection.credential_reference == credential_id).limit(1)
    )
    if other is None:
        await database.execute(
            delete(ConnectionCredential).where(ConnectionCredential.id == credential_id)
        )


async def _retire(database: AsyncSession, grant: AppleCalendarGrant) -> None:
    connection = await _connection(database, grant.user_id, grant.workspace_id, grant.connection_id)
    credential_id = None
    if connection is not None:
        credential_id = connection.credential_reference
        connection.credential_reference, connection.status = None, "disconnected"
        connection.granted_scopes, connection.external_email, connection.last_error = [], None, None
    await database.delete(grant)
    await database.flush()
    if credential_id is not None:
        await _erase_credential(database, credential_id)


async def disconnect(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    session_hash: str,
    grant_id: UUID,
    payload: CalendarCommand,
    settings: Settings,
) -> AppleCalendarView:
    current = await calendars.boundary(
        database, user_id, workspace_id, session_hash, allow_paused=True
    )
    calendars.check_command(current, payload, admitting=False)
    grant = next(
        (item for item in await _grants(database, user_id, workspace_id) if item.id == grant_id),
        None,
    )
    if grant is None:
        raise schedule.StudentError(404, "Apple calendar permission not found")
    await _retire(database, grant)
    assert current.state is not None
    calendars.advance(current.state)
    schedule.audit(database, user_id, workspace_id, "student.calendar.apple.disconnected", grant_id)
    await database.commit()
    return await view(database, user_id, workspace_id, session_hash, settings)


async def forget_calendar_data(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> None:
    """Caller holds the common owner lock and advances CalendarState generation."""
    for grant in await _grants(database, user_id, workspace_id):
        await _retire(database, grant)


async def snapshots(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, current: calendars.Boundary
) -> tuple[calendars.CalendarGrantSnapshot, ...]:
    items = []
    for grant in await _grants(database, user_id, workspace_id):
        if (
            current.state is None
            or grant.owner_scope_id != current.state.id
            or grant.generation != current.generation
            or grant.consent_version != CONSENT_VERSION
        ):
            raise schedule.StudentError(409, "Apple calendar permission needs review")
        connection = await _connection(database, user_id, workspace_id, grant.connection_id)
        items.append(
            AppleGrantSnapshot(
                id=grant.id,
                revision=grant.revision,
                status="active",
                connection_id=connection.id if connection else None,
                credential_id=connection.credential_reference if connection else None,
                external_account_id=connection.external_account_id if connection else None,
                granted_scopes=frozenset(connection.granted_scopes) if connection else frozenset(),
                connection_status=connection.status if connection else None,
                provider="icloud",
            )
        )
    return tuple(items)


async def credentials(
    database: AsyncSession,
    snapshot: calendars.CalendarScope,
    grant: calendars.CalendarGrantSnapshot,
    settings: Settings,
) -> tuple[str, str]:
    """Only after check_scope; envelope protects against cross-owner credential swaps."""
    if (
        grant not in snapshot.grants
        or grant.connection_id is None
        or grant.credential_id is None
        or grant.connection_status != "active"
        or BUSY_PURPOSE not in grant.granted_scopes
    ):
        raise schedule.StudentError(409, "Reconnect Apple calendar access")
    row = await database.get(ConnectionCredential, grant.credential_id)
    if row is None or row.key_version != "apple-app-password.v1":
        raise schedule.StudentError(409, "Reconnect Apple calendar access")
    try:
        value = json.loads(_vault(settings).reveal_refresh_token(row.encrypted_refresh_token))
        if (
            value.get("version") != 1
            or value.get("user_id") != str(snapshot.user_id)
            or value.get("workspace_id") != str(snapshot.workspace_id)
            or value.get("connection_id") != str(grant.connection_id)
            or not isinstance(value.get("apple_id"), str)
            or not isinstance(value.get("app_password"), str)
            or not re.fullmatch(r"[a-z]{4}(?:-[a-z]{4}){3}", value["app_password"])
            or grant.external_account_id
            != "apple:" + hashlib.sha256(value["apple_id"].encode()).hexdigest()
        ):
            raise ValueError
        return value["apple_id"], value["app_password"]
    except (CredentialVaultError, ValueError, TypeError, AttributeError):
        raise schedule.StudentError(409, "Reconnect Apple calendar access") from None


async def read(
    database: AsyncSession,
    snapshot: calendars.CalendarScope,
    grant: calendars.CalendarGrantSnapshot,
    settings: Settings,
    *,
    start_at: datetime,
    end_at: datetime,
    session_hash: str,
    provider: AppleAvailabilityProvider | None = None,
) -> AppleAvailabilitySnapshot:
    await calendars.check_scope(database, snapshot, session_hash)
    account, password = await credentials(database, snapshot, grant, settings)
    assert database.bind is not None
    factory = async_sessionmaker(database.bind, expire_on_commit=False)
    await database.rollback()

    async def check() -> None:
        async with factory() as fresh:
            await calendars.check_scope(fresh, snapshot, session_hash)

    result = await (provider or AppleAvailabilityProvider()).collect(
        apple_id=account,
        app_password=password,
        start_at=start_at,
        end_at=end_at,
        check=check,
    )
    await calendars.check_scope(database, snapshot, session_hash)
    return result
