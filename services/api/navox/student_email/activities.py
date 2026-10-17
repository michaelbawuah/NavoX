"""Bounded email review expiry cleanup, independent of creation and provider flags."""

from temporalio import activity

from navox.db.session import get_session_factory
from navox.student_email.service import cleanup_email_reviews


@activity.defn
async def cleanup_email_reviews_activity() -> int:
    factory = get_session_factory()
    erased = 0
    # At most 2,000 rows per activity. Release owner locks after every batch.
    for _ in range(10):
        async with factory() as database:
            count = await cleanup_email_reviews(database, limit=200)
            await database.commit()
        erased += count
        if count < 200:
            break
    return erased
