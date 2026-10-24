"""Owner-bound, bounded reply evidence for already verified Outlook sends."""

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.core.settings import Settings
from navox.db.models import ConnectorConnection
from navox.db.outlook import OutlookMailDraft
from navox.outlook import service
from navox.providers.microsoft_mail import (
    REPLY_SCAN_LIMIT,
    MailReceipt,
    MailReply,
    MailReplyScan,
    MicrosoftMailError,
)


@dataclass(frozen=True)
class AuthorizedMailbox:
    connection_id: UUID
    token: str = field(repr=False)
    generation: int


@dataclass(frozen=True)
class Observation:
    state: str
    reply: MailReply | None = None
    count: int | None = None
    complete: bool = False


def recipient_digest(binding: str, address: str) -> str:
    return sha256((binding + "\n" + address.casefold()).encode()).hexdigest()


def record_receipt(draft: OutlookMailDraft, receipt: MailReceipt) -> None:
    """Only provider-verified MIME supplies the reviewed recipient and sent time."""
    draft.sent_message_id = receipt.id
    draft.sent_at = receipt.sent_at
    draft.reply_conversation_id = receipt.conversation_id or None
    draft.reply_recipient_hash = recipient_digest(draft.mailbox_binding, receipt.recipient)


def tracking_ready(draft: OutlookMailDraft) -> bool:
    return bool(
        draft.status == "completed"
        and draft.verified_at
        and draft.sent_at
        and draft.reply_conversation_id
        and draft.reply_recipient_hash
        and draft.internet_message_id
        and draft.receipt_hash
    )


async def require_automatic_ready(
    database: AsyncSession, draft: OutlookMailDraft, connection_id: UUID
) -> None:
    if (
        draft.connection_id != connection_id
        or not tracking_ready(draft)
        or not draft.encrypted_payload
        or service.utc(draft.payload_expires_at) <= service.now()
    ):
        # owned_draft may have cleared expired content within this transaction.
        # Persist only that erasure, then stop before reading or restoring evidence.
        await database.commit()
        raise service.OutlookError(
            "Open this reply and choose Check for replies to review its status", 409
        )


def observe(
    scan: MailReplyScan,
    *,
    binding: str,
    conversation_id: str,
    sent_at: datetime,
    recipient_hash: str,
    mailbox: str,
    internet_message_id: str,
) -> Observation:
    """An exact participant/thread/reference match can prove existence, never absence."""
    complete = not scan.has_more and not scan.unreadable
    if not complete:
        return Observation("unknown", count=scan.checked_count, complete=False)
    unknown = not complete
    owner = mailbox.casefold()
    for message in scan.messages:
        if message.conversation_id != conversation_id or service.utc(
            message.received_at
        ) <= service.utc(sent_at):
            unknown = True
            continue
        if message.is_draft or owner in {
            message.sender.casefold(),
            message.from_address.casefold(),
        }:
            continue
        if (
            owner not in {address.casefold() for address in message.recipients}
            or message.sender.casefold() != message.from_address.casefold()
            or recipient_digest(binding, message.from_address) != recipient_hash
            or message.automated
            or internet_message_id not in message.references
        ):
            # Aliases, forwarding, delegates and third parties need human review.
            unknown = True
            continue
        return Observation("replied", message, scan.checked_count, complete)
    return Observation(
        "unknown" if unknown else "waiting", count=scan.checked_count, complete=not unknown
    )


def view(draft: OutlookMailDraft) -> dict[str, object]:
    state = draft.reply_state or "not_checked"
    if draft.status != "completed" or draft.verified_at is None:
        state = "not_checked"
    notices = {
        "not_checked": "Later replies have not been checked.",
        "waiting": (
            "No later reply from the reviewed recipient was found in the checked conversation. "
            "This is the result of the last limited check, not continuous monitoring."
        ),
        "replied": (
            "A later message from the reviewed recipient was found in the same conversation "
            "and references this sent reply."
        ),
        "unknown": (
            "A later reply could not be confirmed. This check may be incomplete; "
            "missing messages, aliases or forwarded replies may need review in Outlook."
        ),
    }
    if state not in notices:
        state = "unknown"
    return {
        "sent_at": service.utc(draft.sent_at).isoformat() if draft.sent_at else None,
        "reply_state": state,
        "reply_checked_at": service.utc(draft.reply_checked_at).isoformat()
        if draft.reply_checked_at
        else None,
        "reply_received_at": service.utc(draft.reply_received_at).isoformat()
        if draft.reply_received_at
        else None,
        "reply_scan_count": draft.reply_scan_count,
        "reply_scan_complete": draft.reply_scan_complete,
        "reply_scan_limit": REPLY_SCAN_LIMIT,
        "reply_tracking_ready": tracking_ready(draft),
        "reply_notice": notices[state],
    }


async def reconcile(
    database: AsyncSession,
    settings: Settings,
    scope: service.Scope,
    draft_id: UUID,
    *,
    authorized: AuthorizedMailbox | None = None,
    automatic: bool = False,
) -> dict[str, object]:
    draft = await service.owned_draft(database, scope, draft_id)
    if draft.status != "completed" or draft.verified_at is None:
        raise service.OutlookError("Confirm this send outcome before checking for a reply", 409)
    rows = (
        await database.scalars(
            select(ConnectorConnection)
            .where(
                ConnectorConnection.user_id == scope.user_id,
                ConnectorConnection.workspace_id == scope.workspace_id,
                ConnectorConnection.provider == "microsoft",
                ConnectorConnection.status.in_(("CONNECTED", "DEGRADED")),
            )
            .order_by(ConnectorConnection.created_at.desc())
        )
    ).all()
    target = next(
        (row for row in rows if service.mailbox_binding(row) == draft.mailbox_binding), None
    )
    if target is None:
        raise service.OutlookError("Reconnect the same Microsoft mailbox to check for a reply", 409)
    connection_id = target.id
    if automatic:
        await require_automatic_ready(database, draft, connection_id)
    await database.commit()
    if authorized is None or authorized.connection_id != connection_id:
        token, generation = await service.access(database, settings, scope, connection_id)
    else:
        token, generation = authorized.token, authorized.generation
    current = await service.recheck(database, scope, connection_id, generation)
    draft = await service.owned_draft(database, scope, draft_id)
    if service.mailbox_binding(current) != draft.mailbox_binding:
        raise service.OutlookError("The Microsoft mailbox changed. Reopen this reply", 409)
    if automatic:
        await require_automatic_ready(database, draft, connection_id)
    owner = service.mailbox(current)
    binding = draft.mailbox_binding
    message_id, receipt_hash = draft.internet_message_id, draft.receipt_hash
    sent_at, conversation_id, recipient_hash = (
        draft.sent_at,
        draft.reply_conversation_id,
        draft.reply_recipient_hash,
    )
    await database.commit()
    receipt: MailReceipt | None = None
    observation = Observation("unknown")
    checked_at = service.now()
    try:
        gateway = service.MicrosoftMailGateway()
        if not (sent_at and conversation_id and recipient_hash) and message_id and receipt_hash:
            # Older or privacy-cleared receipts can recover proof from the exact sent MIME.
            receipt = await gateway.sent_receipt(token, message_id, receipt_hash)
            if receipt is not None:
                sent_at, conversation_id = receipt.sent_at, receipt.conversation_id
                recipient_hash = recipient_digest(binding, receipt.recipient)
            await service.recheck(database, scope, connection_id, generation)
            await database.commit()
        if (
            sent_at
            and conversation_id
            and recipient_hash
            and message_id
            and service.utc(sent_at) <= service.now()
        ):
            checked_at = service.now()
            scan = await gateway.conversation_replies(token, conversation_id, service.utc(sent_at))
            observation = observe(
                scan,
                binding=binding,
                conversation_id=conversation_id,
                sent_at=sent_at,
                recipient_hash=recipient_hash,
                mailbox=owner,
                internet_message_id=message_id,
            )
    except MicrosoftMailError:
        # Provider failures do not turn into "no reply" and never trigger a write to Outlook.
        observation = Observation("unknown")
    current = await service.recheck(database, scope, connection_id, generation)
    draft = await service.owned_draft(database, scope, draft_id)
    if service.mailbox_binding(current) != binding or draft.mailbox_binding != binding:
        raise service.OutlookError("The Microsoft mailbox changed. Reopen this reply", 409)
    if draft.status != "completed" or draft.verified_at is None:
        raise service.OutlookError("This send record changed. Reopen it before continuing", 409)
    if automatic:
        await require_automatic_ready(database, draft, connection_id)
    if receipt is not None:
        record_receipt(draft, receipt)
    # A slower concurrent empty scan cannot erase already-observed reply evidence.
    if observation.reply is not None:
        draft.reply_state = "replied"
        draft.reply_message_id = observation.reply.id
        draft.reply_received_at = observation.reply.received_at
    elif draft.reply_state != "replied":
        draft.reply_state = observation.state
        draft.reply_message_id = None
        draft.reply_received_at = None
    draft.reply_checked_at = checked_at
    draft.reply_scan_count = observation.count
    draft.reply_scan_complete = observation.complete
    await database.commit()
    return service.draft_view(settings, draft, connection_id=connection_id)
