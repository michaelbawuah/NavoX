"""Private receipt retention runs even when recovery creation is disabled."""

from temporalio import activity

from navox.db.session import get_session_factory
from navox.student_recovery.service import purge_expired_recovery_previews


@activity.defn
async def purge_expired_recovery_previews_activity() -> int:
    factory = get_session_factory()
    removed = 0
    # At most 5,000 ciphertexts per activity, releasing row locks each batch.
    for _ in range(10):
        async with factory() as database:
            count = await purge_expired_recovery_previews(database, limit=500)
            await database.commit()
        removed += count
        if count < 500:
            break
    return removed
