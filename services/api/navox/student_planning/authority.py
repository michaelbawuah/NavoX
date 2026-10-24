"""Fresh initiating-session checks for reviewed local schedule writes."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.models import UserSession
from navox.student.service import StudentError


async def require_session(database: AsyncSession, user_id: UUID, session_hash: str) -> None:
    """Call after the User lock and again after any external availability read.

    Login replacement/logout serialize on User. The second check also catches
    natural session expiry while the calendar boundary was being checked.
    """
    identifier = await database.scalar(
        select(UserSession.id).where(
            UserSession.user_id == user_id,
            UserSession.token_hash == session_hash,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > utc(await database_now(database)),
        )
    )
    if identifier is None:
        raise StudentError(401, "Sign in again before approving this study time")
