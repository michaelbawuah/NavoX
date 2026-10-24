"""Trusted server metering; caller owns commit and operator/authentication gates.

Never mount UsageUpdate as a browser request schema. Worker authority is checked
by the media worker before receiving/speaking; this ledger grants no media access.
Provider streams reserve a conservative total-token ceiling before paid I/O.
Unknown failed usage retains that ceiling, rather than granting free quota.

Voice credit is committed for at most one second before audio. Operator limit
reductions apply to new grants; an issued grant is honored until its deadline.
Logout, reset and authority pause still fence every media frame immediately.
Expired unsettled credit stays explicit uncertain quota, never inferred speech.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.sync_state import database_now, utc
from navox.db.models import User, UserSession
from navox.db.vox import VoxCall, VoxState
from navox.db.vox_usage import (
    VoxUsageDelta,
    VoxUsageEvent,
    VoxUsagePolicy,
    VoxUsageStream,
    VoxVoiceCredit,
)
from navox.student.service import owner
from navox.vox.usage_schemas import (
    BudgetStatus,
    ModelPricing,
    UsageCounters,
    UsagePolicy,
    UsageStreamView,
    UsageTotals,
    UsageUpdate,
    VoiceCreditGrant,
)

COUNTERS = tuple(UsageCounters.model_fields)
TOKEN_KEYS = ("input_text", "output_text", "input_audio", "output_audio")


class UsageError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def get_policy(database: AsyncSession) -> tuple[int, UsagePolicy]:
    row = await database.get(VoxUsagePolicy, 1, populate_existing=True)
    return (row.revision, UsagePolicy.model_validate(row.definition)) if row else (0, UsagePolicy())


async def update_policy(
    database: AsyncSession, policy: UsagePolicy, *, expected_revision: int
) -> tuple[int, UsagePolicy]:
    """Operator-only surface. Authentication belongs to the operator route/CLI."""
    if database.get_bind().dialect.name == "postgresql":
        await database.execute(text("SELECT pg_advisory_xact_lock(762490530)"))
    row = await database.scalar(
        select(VoxUsagePolicy).where(VoxUsagePolicy.id == 1).with_for_update()
    )
    revision = row.revision if row else 0
    if expected_revision != revision:
        raise UsageError(409, "Voice usage settings changed; reload before saving")
    if row is None:
        row = VoxUsagePolicy(
            id=1, revision=1, definition={}, updated_at=await database_now(database)
        )
        database.add(row)
    else:
        row.revision += 1
    row.definition = policy.model_dump(mode="json")
    row.updated_at = await database_now(database)
    await database.flush()
    return row.revision, policy


def local_bounds(
    moment: datetime, timezone: str, *, day: date | None = None
) -> tuple[datetime, datetime, datetime, datetime]:
    zone = ZoneInfo(timezone)
    selected = day or utc(moment).astimezone(zone).date()
    following_month = date(selected.year + (selected.month == 12), selected.month % 12 + 1, 1)
    return tuple(
        datetime.combine(value, time.min, zone).astimezone(UTC)
        for value in (
            selected,
            selected + timedelta(days=1),
            selected.replace(day=1),
            following_month,
        )
    )  # type: ignore[return-value]


def _counter(row: VoxUsageStream) -> UsageCounters:
    return UsageCounters.model_validate(row.cumulative)


def _view(row: VoxUsageStream) -> UsageStreamView:
    return UsageStreamView.model_validate(
        {
            "id": row.id,
            "call_id": row.call_id,
            "kind": row.kind,
            "status": row.status,
            "sequence": row.sequence,
            "reserved_tokens": row.reserved_tokens,
            "cumulative": row.cumulative,
            "pricing_version": row.pricing.get("version") if row.pricing else None,
        }
    )


async def _totals(
    database: AsyncSession,
    user_id: UUID,
    start: datetime,
    end: datetime,
    *,
    now: datetime | None = None,
) -> UsageTotals:
    values = (
        await database.execute(
            select(
                func.coalesce(func.sum(VoxUsageDelta.speaking_ms), 0),
                func.coalesce(func.sum(VoxUsageDelta.idle_ms), 0),
                func.coalesce(func.sum(VoxUsageDelta.connected_ms), 0),
                func.coalesce(func.sum(VoxUsageDelta.tokens), 0),
                func.coalesce(func.sum(VoxUsageDelta.estimated_cost_usd), 0),
                func.count().filter(VoxUsageDelta.estimated_cost_usd.is_(None)),
            ).where(
                VoxUsageDelta.user_id == user_id,
                VoxUsageDelta.occurred_at >= start,
                VoxUsageDelta.occurred_at < end,
            )
        )
    ).one()
    streams = list(
        await database.scalars(
            select(VoxUsageStream).where(
                VoxUsageStream.user_id == user_id,
                or_(VoxUsageStream.created_at >= start, VoxUsageStream.status == "open"),
                VoxUsageStream.created_at < end,
            )
        )
    )
    reserved = sum(
        max(0, s.reserved_tokens - _counter(s).total_tokens)
        for s in streams
        if s.status != "complete"
    )
    unknown = int(values[5]) + sum(
        s.status != "complete" and s.reserved_tokens > _counter(s).total_tokens for s in streams
    )
    now = now or await database_now(database)
    credits = list(
        await database.scalars(
            select(VoxVoiceCredit).where(
                VoxVoiceCredit.user_id == user_id,
                VoxVoiceCredit.granted_at >= start,
                VoxVoiceCredit.granted_at < end,
                VoxVoiceCredit.settled_at.is_(None),
            )
        )
    )
    reserved_voice = sum(c.granted_ms for c in credits)
    uncertain_voice = sum(c.granted_ms for c in credits if utc(c.expires_at) <= now)
    unknown += sum(utc(c.expires_at) <= now for c in credits)
    known = Decimal(values[4])
    # Reconciled invoices are exact per stream/call. A cross-period stream cannot
    # honestly be called a reconciled daily amount without invoice allocation.
    # A provider invoice can span local days. Reconciled costs are returned by
    # call_totals, never silently allocated to a daily/monthly estimate.
    reconciled = None
    return UsageTotals(
        speaking_ms=int(values[0]),
        idle_ms=int(values[1]),
        connected_ms=int(values[2]),
        tokens=int(values[3]),
        reserved_tokens=reserved,
        reserved_voice_ms=reserved_voice,
        uncertain_voice_ms=uncertain_voice,
        estimated_cost_usd=None if unknown else known,
        known_estimated_cost_usd=known,
        unknown_cost_components=unknown,
        reconciled_cost_usd=reconciled,
        cost_basis="reconciled" if reconciled is not None else "unknown" if unknown else "estimate",
    )


async def budget_status(database: AsyncSession, user_id: UUID, workspace_id: UUID) -> BudgetStatus:
    user = await owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    revision, policy = await get_policy(database)
    day_start, day_end, month_start, month_end = local_bounds(now, user.timezone)
    daily = await _totals(database, user_id, day_start, day_end, now=now)
    monthly = await _totals(database, user_id, month_start, month_end, now=now)
    limits = (
        (
            "daily voice",
            daily.speaking_ms + daily.reserved_voice_ms,
            policy.daily_voice_minutes * 60_000,
        ),
        (
            "monthly voice",
            monthly.speaking_ms + monthly.reserved_voice_ms,
            policy.monthly_voice_minutes * 60_000,
        ),
        ("daily tokens", daily.tokens + daily.reserved_tokens, policy.daily_tokens),
        ("monthly tokens", monthly.tokens + monthly.reserved_tokens, policy.monthly_tokens),
    )
    notices = [
        f"You have used at least {int(policy.notice_fraction * 100)}% of your {label} allowance."
        for label, used, limit in limits
        if Decimal(used) >= Decimal(limit) * policy.notice_fraction
    ]
    voice = max(0, min(limits[0][2] - limits[0][1], limits[1][2] - limits[1][1]))
    tokens = max(0, min(limits[2][2] - limits[2][1], limits[3][2] - limits[3][1]))
    return BudgetStatus(
        timezone=user.timezone,
        daily=daily,
        monthly=monthly,
        remaining_voice_ms=voice,
        remaining_tokens=tokens,
        blocked=voice == 0 or tokens == 0,
        notices=notices,
        policy_revision=revision,
    )


async def _active_call(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, call_id: UUID
) -> VoxCall:
    user = await owner(database, user_id, workspace_id, lock=True)
    now = await database_now(database)
    call = await database.scalar(
        select(VoxCall)
        .where(
            VoxCall.id == call_id, VoxCall.user_id == user_id, VoxCall.workspace_id == workspace_id
        )
        .execution_options(populate_existing=True)
    )
    if call is None:
        raise UsageError(404, "Voice call not found")
    session = await database.get(UserSession, call.auth_session_id, populate_existing=True)
    state = await database.scalar(
        select(VoxState).where(VoxState.user_id == user_id, VoxState.workspace_id == workspace_id)
    )
    if (
        user.agent_paused
        or call.status != "reserved"
        or utc(call.expires_at) <= now
        or state is None
        or state.generation != call.generation
        or session is None
        or session.user_id != user_id
        or session.client_type != "web"
        or session.revoked_at is not None
        or utc(session.expires_at) <= now
    ):
        raise UsageError(409, "Voice call authority ended")
    return call


async def start_usage_stream(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    *,
    stream_id: UUID,
    kind: str = "transport",
    provider: str | None = None,
    model: str | None = None,
    token_ceiling: int = 0,
    voice_credit: VoiceCreditGrant | None = None,
    voice_connection_id: UUID | None = None,
    voice_worker_nonce: UUID | None = None,
) -> UsageStreamView:
    """Server UUID only; provider ceiling bounds total input+output, not output alone."""
    await _active_call(database, user_id, workspace_id, call_id)
    if kind not in {"transport", "provider"} or type(token_ceiling) is not int or token_ceiling < 0:
        raise UsageError(422, "Invalid usage stream")
    existing = await database.get(VoxUsageStream, stream_id, populate_existing=True)
    if existing is not None:
        if (
            existing.user_id,
            existing.workspace_id,
            existing.call_id,
            existing.kind,
            existing.provider,
            existing.model,
        ) != (user_id, workspace_id, call_id, kind, provider, model):
            raise UsageError(409, "Usage stream identity changed")
        if existing.reserved_tokens != token_ceiling:
            raise UsageError(409, "Usage reservation ceiling changed")
        if kind == "provider" and existing.status != "open":
            raise UsageError(409, "This provider attempt has already ended")
        return _view(existing)
    status = await budget_status(database, user_id, workspace_id)
    own_voice = False
    if voice_credit is not None:
        credit = await _credit_row(database, voice_credit)
        transport = await database.get(VoxUsageStream, credit.stream_id, populate_existing=True)
        if (
            transport is None
            or transport.kind != "transport"
            or transport.status != "open"
            or (transport.user_id, transport.workspace_id, transport.call_id)
            != (user_id, workspace_id, call_id)
            or transport.sequence != voice_credit.base_sequence
            or _counter(transport) != voice_credit.base_cumulative
        ):
            raise UsageError(409, "Voice transport checkpoint changed")
        now = await database_now(database)
        own_voice = (
            kind == "provider"
            and credit.settled_at is None
            and utc(credit.expires_at) > now
            and (credit.connection_id, credit.worker_nonce)
            == (voice_connection_id, voice_worker_nonce)
            and (credit.user_id, credit.workspace_id, credit.call_id)
            == (user_id, workspace_id, call_id)
        )
        if not own_voice:
            raise UsageError(409, "Voice credit does not belong to this provider call")
    if (
        (status.remaining_voice_ms == 0 and not own_voice)
        or status.remaining_tokens == 0
        or token_ceiling > status.remaining_tokens
    ):
        raise UsageError(429, "Your voice allowance has been reached")
    _, policy = await get_policy(database)
    pricing: dict[str, object] | None = None
    if kind == "provider":
        if not provider or not model or token_ceiling < 1:
            raise UsageError(422, "A provider usage reservation is required")
        # Validate metadata without accepting arbitrary bodies, credentials or URLs.
        ModelPricing(provider=provider, model=model, version="metadata-only")
        configured = next(
            (p for p in policy.model_pricing if (p.provider, p.model) == (provider, model)), None
        )
        pricing = configured.model_dump(mode="json") if configured else None
    else:
        if token_ceiling or provider is not None or model is not None:
            raise UsageError(422, "Transport usage cannot carry provider tokens")
        if await database.scalar(
            select(VoxUsageStream.id).where(
                VoxUsageStream.call_id == call_id, VoxUsageStream.kind == "transport"
            )
        ):
            raise UsageError(409, "This call already has a transport meter")
        if policy.transport_usd_per_minute is not None:
            pricing = {
                "version": policy.transport_pricing_version,
                "usd_per_minute": str(policy.transport_usd_per_minute),
            }
    now = await database_now(database)
    row = VoxUsageStream(
        id=stream_id,
        user_id=user_id,
        workspace_id=workspace_id,
        call_id=call_id,
        kind=kind,
        provider=provider,
        model=model,
        status="open",
        sequence=0,
        cumulative=UsageCounters().model_dump(),
        reserved_tokens=token_ceiling,
        pricing=pricing,
        created_at=now,
        observed_at=now,
    )
    database.add(row)
    await database.flush()
    return _view(row)


async def admit_provider_stream(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    *,
    stream_id: UUID,
    provider: str,
    model: str,
    token_ceiling: int,
    voice_credit: VoiceCreditGrant | None = None,
    voice_connection_id: UUID | None = None,
    voice_worker_nonce: UUID | None = None,
) -> UsageStreamView:
    return await start_usage_stream(
        database,
        user_id,
        workspace_id,
        call_id,
        stream_id=stream_id,
        kind="provider",
        provider=provider,
        model=model,
        token_ceiling=token_ceiling,
        voice_credit=voice_credit,
        voice_connection_id=voice_connection_id,
        voice_worker_nonce=voice_worker_nonce,
    )


def estimate(
    counters: UsageCounters, pricing: dict[str, object] | None, kind: str
) -> Decimal | None:
    if kind == "transport":
        if counters.connected_ms == 0:
            return Decimal(0)
        return (
            (Decimal(str(pricing["usd_per_minute"])) * counters.connected_ms / 60_000)
            if pricing
            else None
        )
    if counters.total_tokens == 0 and counters.billed_audio_ms == 0:
        return Decimal(0)
    if counters.unclassified_input or counters.unclassified_output:
        return None
    if pricing is None:
        return None
    rates = ModelPricing.model_validate(pricing)
    if rates.max_input_tokens is not None and (
        counters.input_text + counters.input_audio > rates.max_input_tokens
    ):
        return None  # Do not apply a standard-context price to another billing tier.
    quantities = {
        "input_text": counters.input_text - counters.cached_text,
        "cached_text": counters.cached_text,
        "output_text": counters.output_text,
        "input_audio": counters.input_audio - counters.cached_audio,
        "cached_audio": counters.cached_audio,
        "output_audio": counters.output_audio,
    }
    result = Decimal(0)
    if counters.billed_audio_ms:
        if rates.audio_usd_per_minute is None:
            return None
        result += rates.audio_usd_per_minute * counters.billed_audio_ms / 60_000
    for key, count in quantities.items():
        rate: Decimal | None = getattr(rates, key)
        if count and rate is None:
            return None
        result += (rate or Decimal(0)) * count / 1_000_000
    return result


def _segments(start: datetime, end: datetime, timezone: str) -> list[tuple[datetime, datetime]]:
    result = []
    while start < end:
        boundary = local_bounds(start, timezone)[1]
        finish = min(end, boundary)
        result.append((start, finish))
        start = finish
    return result


async def _locked_stream(database: AsyncSession, stream_id: UUID) -> tuple[VoxUsageStream, User]:
    row = await database.get(VoxUsageStream, stream_id)
    if row is None:
        raise UsageError(404, "Usage stream not found")
    user = await owner(database, row.user_id, row.workspace_id, lock=True)
    row = await database.get(VoxUsageStream, stream_id, populate_existing=True)
    if row is None:
        raise UsageError(404, "Usage stream not found")
    return row, user


async def apply_usage_update(
    database: AsyncSession,
    stream_id: UUID,
    update: UsageUpdate,
    *,
    _voice_at: datetime | None = None,
) -> UsageStreamView:
    row, user = await _locked_stream(database, stream_id)
    digest = sha256(update.model_dump_json().encode()).hexdigest()
    prior = await database.scalar(
        select(VoxUsageEvent).where(
            VoxUsageEvent.stream_id == stream_id, VoxUsageEvent.event_id == update.event_id
        )
    )
    if prior is not None:
        if prior.digest != digest:
            raise UsageError(409, "Usage event replay changed")
        return _view(row)
    if row.status == "complete" or update.sequence <= row.sequence:
        raise UsageError(409, "Usage sequence is stale or terminal")
    before, after = _counter(row), update.cumulative
    a, b = after.model_dump(), before.model_dump()
    if any(a[k] < b[k] for k in COUNTERS):
        raise UsageError(409, "Cumulative usage cannot reset")
    delta = UsageCounters.model_validate({key: a[key] - b[key] for key in COUNTERS})
    now = await database_now(database)
    if row.kind == "provider":
        if after.connected_ms:
            raise UsageError(422, "Provider tokens cannot set transport time")
        # Attribute a request to the local period of its pre-I/O reservation.
        segments = [(utc(row.created_at), delta)]
    else:
        if after.total_tokens or after.billed_audio_ms:
            raise UsageError(422, "Transport meter cannot set provider usage")
        if after.connected_ms > int((now - utc(row.created_at)).total_seconds() * 1000) + 1000:
            raise UsageError(422, "Transport time exceeds the server clock")
        intervals = _segments(utc(row.observed_at), now, user.timezone)
        if not intervals:
            intervals = [(now, now)]
        duration = max(1, int((now - utc(row.observed_at)).total_seconds() * 1_000_000))
        left_speaking, left_idle = delta.speaking_ms, delta.idle_ms
        segments = []
        for index, (start, end) in enumerate(intervals):
            weight = int((end - start).total_seconds() * 1_000_000)
            speaking = (
                0
                if _voice_at is not None
                else left_speaking
                if index == len(intervals) - 1
                else delta.speaking_ms * weight // duration
            )
            idle = left_idle if index == len(intervals) - 1 else delta.idle_ms * weight // duration
            left_speaking -= speaking
            left_idle -= idle
            segments.append(
                (
                    start,
                    UsageCounters(speaking_ms=speaking, idle_ms=idle, connected_ms=speaking + idle),
                )
            )
        if _voice_at is not None and delta.speaking_ms:
            segments.append(
                (
                    utc(_voice_at),
                    UsageCounters(speaking_ms=delta.speaking_ms, connected_ms=delta.speaking_ms),
                )
            )
    database.add(
        VoxUsageEvent(
            stream_id=stream_id, event_id=update.event_id, sequence=update.sequence, digest=digest
        )
    )
    for at, counters in segments:
        database.add(
            VoxUsageDelta(
                stream_id=stream_id,
                user_id=row.user_id,
                workspace_id=row.workspace_id,
                occurred_at=at,
                speaking_ms=counters.speaking_ms,
                idle_ms=counters.idle_ms,
                connected_ms=counters.connected_ms,
                tokens=counters.total_tokens,
                estimated_cost_usd=estimate(counters, row.pricing, row.kind),
                pricing_version=str(row.pricing["version"]) if row.pricing else None,
            )
        )
    row.sequence, row.cumulative, row.observed_at = update.sequence, after.model_dump(), now
    await database.flush()
    return _view(row)


async def finish_usage_stream(
    database: AsyncSession, stream_id: UUID, *, usage_complete: bool
) -> UsageStreamView:
    row, _ = await _locked_stream(database, stream_id)
    target = "complete" if usage_complete else "uncertain"
    if row.status == "complete" and target != "complete":
        raise UsageError(409, "Usage completion is already terminal")
    row.status = target
    await database.flush()
    return _view(row)


async def reconcile_usage_cost(
    database: AsyncSession, stream_id: UUID, *, reconciliation_id: UUID, cost_usd: Decimal
) -> UsageStreamView:
    """Operator/provider billing evidence only; never infer an invoice from prices."""
    if not cost_usd.is_finite() or cost_usd < 0:
        raise UsageError(422, "Invalid reconciled cost")
    row, _ = await _locked_stream(database, stream_id)
    if row.status == "open":
        raise UsageError(409, "Finish usage before reconciling billing")
    if row.reconciliation_id is not None and (
        row.reconciliation_id != reconciliation_id or row.reconciled_cost_usd != cost_usd
    ):
        raise UsageError(409, "Reconciliation is already recorded")
    row.reconciliation_id, row.reconciled_cost_usd = reconciliation_id, cost_usd
    await database.flush()
    return _view(row)


async def call_totals(
    database: AsyncSession, user_id: UUID, workspace_id: UUID, call_id: UUID
) -> UsageTotals:
    await owner(database, user_id, workspace_id)
    streams = list(
        await database.scalars(
            select(VoxUsageStream).where(
                VoxUsageStream.call_id == call_id,
                VoxUsageStream.user_id == user_id,
                VoxUsageStream.workspace_id == workspace_id,
            )
        )
    )
    result = UsageTotals(estimated_cost_usd=Decimal(0), cost_basis="estimate")
    for stream in streams:
        counters = _counter(stream)
        result.speaking_ms += counters.speaking_ms
        result.idle_ms += counters.idle_ms
        result.connected_ms += counters.connected_ms
        result.tokens += counters.total_tokens
        result.reserved_tokens += (
            max(0, stream.reserved_tokens - counters.total_tokens)
            if stream.status != "complete"
            else 0
        )
        cost = estimate(counters, stream.pricing, stream.kind)
        if cost is None or (
            stream.status != "complete" and stream.reserved_tokens > counters.total_tokens
        ):
            result.unknown_cost_components += 1
        else:
            result.known_estimated_cost_usd += cost
    now = await database_now(database)
    credits = list(
        await database.scalars(
            select(VoxVoiceCredit).where(
                VoxVoiceCredit.user_id == user_id,
                VoxVoiceCredit.workspace_id == workspace_id,
                VoxVoiceCredit.call_id == call_id,
                VoxVoiceCredit.settled_at.is_(None),
            )
        )
    )
    result.reserved_voice_ms = sum(c.granted_ms for c in credits)
    result.uncertain_voice_ms = sum(c.granted_ms for c in credits if utc(c.expires_at) <= now)
    result.unknown_cost_components += sum(utc(c.expires_at) <= now for c in credits)
    result.estimated_cost_usd = (
        None if result.unknown_cost_components else result.known_estimated_cost_usd
    )
    if streams and all(s.reconciled_cost_usd is not None for s in streams):
        result.reconciled_cost_usd = sum(
            (s.reconciled_cost_usd or Decimal(0) for s in streams), Decimal(0)
        )
    result.cost_basis = (
        "reconciled"
        if result.reconciled_cost_usd is not None
        else "unknown"
        if result.unknown_cost_components
        else "estimate"
    )
    return result


async def admin_daily_totals(
    database: AsyncSession,
    *,
    day: date | None = None,
    limit: int = 100,
    after_user_id: UUID | None = None,
) -> list[dict[str, object]]:
    """Operator-only read; the API must authenticate its operator role first."""
    if not 1 <= limit <= 500:
        raise UsageError(422, "Invalid totals page size")
    query = select(User).order_by(User.id).limit(limit)
    if after_user_id is not None:
        query = query.where(User.id > after_user_id)
    users = list(await database.scalars(query))
    now = await database_now(database)
    result: list[dict[str, object]] = []
    for user in users:
        start, end, _, _ = local_bounds(now, user.timezone, day=day)
        totals = await _totals(database, user.id, start, end)
        result.append(
            {
                "user_id": str(user.id),
                "timezone": user.timezone,
                "local_day": start.astimezone(ZoneInfo(user.timezone)).date().isoformat(),
                "totals": totals.model_dump(mode="json"),
            }
        )
    return result


def _credit_view(row: VoxVoiceCredit) -> VoiceCreditGrant:
    return VoiceCreditGrant.model_validate(
        {
            name: utc(getattr(row, name))
            if name in {"granted_at", "expires_at", "day_end", "month_end"}
            else getattr(row, name)
            for name in VoiceCreditGrant.model_fields
        }
    )


async def _credit_row(database: AsyncSession, grant: VoiceCreditGrant) -> VoxVoiceCredit:
    row = await database.get(VoxVoiceCredit, grant.id, populate_existing=True)
    if row is None or _credit_view(row) != grant:
        raise UsageError(409, "Voice credit identity changed")
    return row


async def reserve_voice_credit(
    database: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
    call_id: UUID,
    *,
    stream_id: UUID,
    connection_id: UUID,
    generation: int,
    worker_nonce: UUID,
    grant_id: UUID,
) -> VoiceCreditGrant:
    """Commit before audio. Issued credit survives prospective policy reductions.

    Expiration stops new audio but never refunds possibly spent, unreported credit.
    A crashed grant remains an explicit uncertain quota hold, not actual speech.
    """
    from navox.db.vox_media import VoxMediaCleanup, VoxMediaConnection
    from navox.vox import media

    call = await _active_call(database, user_id, workspace_id, call_id)
    if database.get_bind().dialect.name == "postgresql":
        # Same order as operator PUT: User, then the singleton policy fence.
        await database.execute(text("SELECT pg_advisory_xact_lock_shared(762490530)"))
    user = await database.get(User, user_id)
    assert user is not None  # _active_call retains this fresh owner's row lock.
    now = await database_now(database)
    connection = await database.get(VoxMediaConnection, connection_id, populate_existing=True)
    if (
        type(generation) is not int
        or connection is None
        or connection.user_id != user_id
        or connection.workspace_id != workspace_id
        or connection.call_id != call_id
        or connection.generation != generation
        or connection.status != "confirmed"
        or await media._invalid_reason(database, connection, user, now) is not None
    ):
        raise UsageError(409, "Voice credit authority ended")
    # Cleanup's primary key is independent of connection_id.
    cleanup = await database.scalar(
        select(VoxMediaCleanup)
        .where(VoxMediaCleanup.connection_id == connection_id)
        .execution_options(populate_existing=True)
    )
    if (
        cleanup is None
        or cleanup.status != "armed"
        or utc(cleanup.connection_expires_at) != utc(connection.expires_at)
        or cleanup.owner_scope_id != connection.owner_scope_id
        or cleanup.room_name != connection.room_name
        or cleanup.client_identity != connection.client_identity
        or cleanup.agent_identity != connection.agent_identity
        or utc(cleanup.created_at) != utc(connection.created_at)
    ):
        raise UsageError(409, "Voice credit authority ended")
    stream = await database.get(VoxUsageStream, stream_id, populate_existing=True)
    if stream is None or (
        stream.user_id,
        stream.workspace_id,
        stream.call_id,
        stream.kind,
        stream.status,
    ) != (user_id, workspace_id, call_id, "transport", "open"):
        raise UsageError(409, "Voice transport meter ended")
    existing = await database.get(VoxVoiceCredit, grant_id, populate_existing=True)
    identity = (stream_id, user_id, workspace_id, call_id, connection_id, generation, worker_nonce)
    if existing is not None:
        if (
            tuple(
                getattr(existing, key)
                for key in (
                    "stream_id",
                    "user_id",
                    "workspace_id",
                    "call_id",
                    "connection_id",
                    "generation",
                    "worker_nonce",
                )
            )
            != identity
        ):
            raise UsageError(409, "Voice credit identity changed")
        if existing.settled_at is not None or utc(existing.expires_at) <= now:
            raise UsageError(409, "Voice credit has ended")
        if stream.sequence != existing.base_sequence or _counter(
            stream
        ) != UsageCounters.model_validate(existing.base_cumulative):
            raise UsageError(409, "Voice transport checkpoint changed")
        return _credit_view(existing)
    status = await budget_status(database, user_id, workspace_id)
    if status.blocked:
        raise UsageError(429, "Your voice allowance has been reached")
    _, day_end, _, month_end = local_bounds(now, user.timezone)
    expires = min(
        now + timedelta(seconds=1),
        day_end,
        month_end,
        utc(connection.expires_at),
        utc(call.expires_at),
    )
    granted_ms = min(1000, status.remaining_voice_ms, int((expires - now).total_seconds() * 1000))
    if granted_ms < 1:
        raise UsageError(429, "Your voice allowance has been reached")
    row = VoxVoiceCredit(
        id=grant_id,
        stream_id=stream_id,
        user_id=user_id,
        workspace_id=workspace_id,
        call_id=call_id,
        connection_id=connection_id,
        generation=generation,
        worker_nonce=worker_nonce,
        base_sequence=stream.sequence,
        base_cumulative=_counter(stream).model_dump(),
        policy_revision=status.policy_revision,
        granted_ms=granted_ms,
        granted_at=now,
        expires_at=expires,
        day_end=day_end,
        month_end=month_end,
    )
    database.add(row)
    await database.flush()
    return _credit_view(row)


async def settle_voice_credit(
    database: AsyncSession, grant: VoiceCreditGrant, *, cumulative: UsageCounters
) -> UsageStreamView:
    """Record only observed usage, including after revocation; exact replay is safe."""
    row, _ = await _locked_stream(database, grant.stream_id)
    credit = await _credit_row(database, grant)
    digest = sha256((grant.model_dump_json() + cumulative.model_dump_json()).encode()).hexdigest()
    if credit.settled_at is not None:
        if credit.settlement_digest != digest:
            raise UsageError(409, "Voice credit settlement changed")
        return _view(row)
    if row.sequence != grant.base_sequence or _counter(row) != grant.base_cumulative:
        raise UsageError(409, "Voice transport checkpoint changed")
    delta = cumulative.speaking_ms - grant.base_cumulative.speaking_ms
    if not 0 <= delta <= credit.granted_ms:
        raise UsageError(409, "Observed speech exceeds its reserved voice credit")
    view = await apply_usage_update(
        database,
        grant.stream_id,
        UsageUpdate(event_id=grant.id, sequence=row.sequence + 1, cumulative=cumulative),
        _voice_at=grant.granted_at,
    )
    credit.settled_at = await database_now(database)
    credit.settled_speaking_ms = delta
    credit.settlement_digest = digest
    await database.flush()
    return view
