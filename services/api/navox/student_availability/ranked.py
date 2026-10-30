"""Fresh server-only study feasibility before ranking meeting proposals.

This helper creates no receipt, changes no study block and makes no provider
request itself. The exact event preview must recompute the replacement review;
the returned IDs are a disclosure, never client-supplied execution authority.
"""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now
from navox.student import service as schedule
from navox.student_availability.schemas import AvailabilitySnapshot
from navox.student_availability.selector import (
    SOFT_CRUNCH,
    CertifiedCandidate,
    Crunch,
    SuggestionRequest,
    SuggestionResult,
    _suggest_certified,
    duration_for,
    study_candidates,
    suggest,
)
from navox.student_planning import service as planning

MAX_DEADLINES = 200


async def _deadlines(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    snapshot: AvailabilitySnapshot,
) -> tuple[Crunch, ...]:
    # The task service bounds its owned rows and checks current school/email
    # source eligibility. Titles are neither inferred as exams nor published.
    views = await planning.tasks(database, user_id, workspace_id, include_source_review=True)
    eligible = await planning.eligible_tasks(database, user_id, workspace_id, views)
    instants = set()
    for row in eligible:
        if (
            row.status == "active"
            and not row.source_needs_review
            and row.due_at is not None
            and snapshot.checked_at <= row.due_at <= snapshot.input.end_at + SOFT_CRUNCH
        ):
            instants.add(row.due_at)
    if len(instants) > MAX_DEADLINES:
        raise schedule.StudentError(409, "Too many deadlines in this window. Narrow the search")
    return tuple(Crunch(at=at, kind="deadline") for at in sorted(instants))


def _with_deadlines(
    request: SuggestionRequest, now: datetime, deadlines: tuple[Crunch, ...]
) -> SuggestionRequest:
    combined = {(row.at, row.kind): row for row in (*request.crunch, *deadlines)}
    if len(combined) > MAX_DEADLINES:
        raise schedule.StudentError(409, "Too many deadlines in this window. Narrow the search")
    return request.model_copy(
        update={"now": now, "crunch": tuple(combined[key] for key in sorted(combined))}
    )


async def ranked_suggestions(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    snapshot: AvailabilitySnapshot,
    request: SuggestionRequest,
    *,
    allow_study_displacement: bool = False,
) -> SuggestionResult:
    """Certify at most 64 slots from one current, locked owned planning context.

    The caller supplies a freshly server-computed snapshot, holds its browser or
    native-call authority boundary, and rechecks that boundary plus local source
    revisions and expiry before publication. No HTTP/model input accepts a
    certificate or a planning Context. Provider sources must all be complete;
    unknown coverage/travel/routine is never converted into movable study time.
    """
    if (
        (snapshot.input.user_id, snapshot.input.workspace_id) != (user_id, workspace_id)
        or request.availability != snapshot.result
        or not snapshot.checked_at <= request.now < snapshot.expires_at
    ):
        raise schedule.StudentError(409, "The meeting choices no longer match this account")
    await schedule.owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    if not snapshot.checked_at <= now < snapshot.expires_at:
        raise schedule.StudentError(409, "Your study plan changed. Check meeting times again")
    deadlines = await _deadlines(database, user_id, workspace_id, snapshot)
    request = _with_deadlines(request, now, deadlines)

    async def publish(result: SuggestionResult) -> SuggestionResult:
        if await _deadlines(database, user_id, workspace_id, snapshot) != deadlines:
            raise schedule.StudentError(409, "Your deadlines changed. Check meeting times again")
        if await database_now(database) >= snapshot.expires_at:
            raise schedule.StudentError(409, "This availability check expired. Check again")
        return result

    if not allow_study_displacement:
        return await publish(suggest(request))
    candidates = study_candidates(request)
    if not candidates or any(row.state != "complete" for row in snapshot.source_summary):
        return await publish(suggest(request))
    minutes = duration_for(request.purpose, request.duration_minutes)
    assert minutes is not None

    # Deliberately private server seam. This import also keeps the conservative
    # confirmed-only selector independent of scheduling/storage integrations.
    from navox.student_calendars import displacement

    context = await displacement.load_context(
        database,
        user_id,
        workspace_id,
        snapshot,
        planning_enabled=allow_study_displacement,
    )
    now = await database_now(database)
    if (
        not snapshot.checked_at <= now < snapshot.expires_at
        or (context.state.user_id, context.state.workspace_id) != (user_id, workspace_id)
        or context.state.revision != snapshot.revision.planning_revision
        or context.state.accepted_source_hash != context.source_hash
    ):
        raise schedule.StudentError(409, "Your study plan changed. Check meeting times again")
    fresh = _with_deadlines(request, now, deadlines)
    # Context loading may take provider/lock time. Reposition the earliest edge
    # from the fresh instant instead of publishing a just-expired old edge.
    candidates = study_candidates(fresh)
    certified: list[CertifiedCandidate] = []
    for candidate in candidates:
        try:
            review = displacement.certify(
                context,
                snapshot,
                candidate.start_at,
                candidate.start_at + timedelta(minutes=minutes),
                now,
            )
        except schedule.StudentError as error:
            if error.status_code != 409:
                raise
            # A bounded allocator may reject one interval without proving the
            # rest impossible. Unknown/context changes are separately fenced.
            continue
        certified.append(
            CertifiedCandidate(
                candidate.opening,
                candidate.start_at,
                tuple(str(move.before.id) for move in review.moves) if review else (),
            )
        )
    return await publish(_suggest_certified(fresh, tuple(certified)))
