"""Transaction-preserving Outlook cleanup shared by reset and connection controls."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from navox.db.models import ConnectorConnection, OAuthAuthorizationAttempt
from navox.db.outlook import OutlookMailDraft


async def lock_for_reset(
    database: AsyncSession, *, user_id: UUID, workspace_id: UUID
) -> list[UUID]:
    """Acquire provider authority before the shared membership/User writer lock."""
    return list(
        await database.scalars(
            select(ConnectorConnection.id)
            .where(
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.provider == "microsoft",
            )
            .order_by(ConnectorConnection.id)
            .with_for_update()
        )
    )


async def clear_private_data(
    database: AsyncSession,
    *,
    user_id: UUID,
    workspace_id: UUID,
    connector_ids: list[UUID] | None = None,
    advance_generation: bool = False,
) -> None:
    """Clear text and cancel in-flight consent, retaining content-free send fences.

    Caller owns the connector → membership → User locks and its transaction.
    Reset must advance the generation so reads/draft generation started before
    the reset cannot restore private content afterwards.
    """
    query = select(ConnectorConnection.id).where(
        ConnectorConnection.user_id == user_id,
        ConnectorConnection.workspace_id == workspace_id,
        ConnectorConnection.provider == "microsoft",
    )
    if advance_generation and connector_ids is not None:
        current = set(await database.scalars(query))
        if current != set(connector_ids):
            # A callback could insert a mailbox after the pre-lock snapshot.
            # Do not acquire its connector lock while the caller holds User.
            raise HTTPException(409, "Outlook connections changed. Review and retry the reset")
    if connector_ids is not None:
        query = query.where(ConnectorConnection.id.in_(connector_ids))
    identifiers = list(await database.scalars(query))
    if connector_ids is not None and not identifiers:
        return
    from navox.outlook.service import mailbox_binding

    connections = (
        await database.scalars(
            select(ConnectorConnection).where(
                ConnectorConnection.id.in_(identifiers),
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
        )
    ).all()
    bindings = [value for row in connections if (value := mailbox_binding(row)) is not None]
    await database.execute(
        update(OAuthAuthorizationAttempt)
        .where(
            OAuthAuthorizationAttempt.user_id == user_id,
            OAuthAuthorizationAttempt.workspace_id == workspace_id,
            OAuthAuthorizationAttempt.provider == "microsoft",
        )
        .values(purpose="outlook-cancelled")
    )
    await database.execute(
        update(OutlookMailDraft)
        .where(
            OutlookMailDraft.user_id == user_id,
            OutlookMailDraft.workspace_id == workspace_id,
            or_(
                OutlookMailDraft.connection_id.in_(identifiers),
                OutlookMailDraft.mailbox_binding.in_(bindings),
            ),
        )
        .values(
            encrypted_payload="",
            sent_at=None,
            reply_conversation_id=None,
            reply_recipient_hash=None,
            reply_state="not_checked",
            reply_checked_at=None,
            reply_message_id=None,
            reply_received_at=None,
            reply_scan_count=None,
            reply_scan_complete=None,
            status=case(
                (OutlookMailDraft.status == "review", "discarded"),
                (OutlookMailDraft.status == "executing", "uncertain"),
                else_=OutlookMailDraft.status,
            ),
        )
    )
    if advance_generation:
        await database.execute(
            update(ConnectorConnection)
            .where(
                ConnectorConnection.id.in_(identifiers),
                ConnectorConnection.user_id == user_id,
                ConnectorConnection.workspace_id == workspace_id,
            )
            .values(sync_generation=ConnectorConnection.sync_generation + 1)
        )
