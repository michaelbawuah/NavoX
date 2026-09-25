"""Reviewed REST cancellation behind SPEC-003's ownership, capability and secret boundary.

Only deployment configuration supplies endpoints or response mappings. A separate owner
grant authenticates the provider account and is bound to the exact configuration and
credential version. Ordinary REST read consent never authorizes cancellation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.builtin.generic_api import _contains_credential
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import (
    CapabilityDefinition,
    ConnectorManifest,
    ConnectorRuntimeError,
)
from navox.connectors.generic_registration import ApprovedGenericConfig, approved_generic_connectors
from navox.connectors.network import join_relative_path
from navox.connectors.outbound import ApprovedHTTPSTransport, approved_origin, validate_target
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import AuditEvent, Connection, ConnectorConnection, ConnectorDefinition
from navox.subscriptions.cancellation_contracts import (
    CancellationEconomics,
    CancellationPreview,
    CancellationSubmission,
    CancellationTarget,
    CancellationVerification,
    CancelSubscriptionCapability,
)

CAPABILITY = "subscription.cancel"
GRANT_EVENT = "connector.subscription.cancellation_granted"
MAX_RESPONSE_BYTES = 128_000
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
ProviderStatus = Literal["active", "cancelled", "cancel_pending", "paused", "expired", "challenge"]


def default_status_mapping() -> dict[str, ProviderStatus]:
    return {
        "active": "active",
        "cancelled": "cancelled",
        "cancel_pending": "cancel_pending",
        "paused": "paused",
        "expired": "expired",
        "challenge": "challenge",
    }


class ProviderStateFields(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subscription_id: str = "id"
    account_id: str = "account_id"
    revision: str = "revision"
    status: str = "status"
    cancel_allowed: str = "cancel_allowed"
    plan_name: str = "plan_name"
    amount: str = "amount"
    currency: str = "currency"
    interval: str = "interval"
    interval_count: str = "interval_count"
    renewal_at: str = "renewal_at"
    auto_renew: str = "auto_renew"
    access_ends_at: str = "access_ends_at"
    cancellation_fee: str = "cancellation_fee"
    refund: str = "refund"

    @field_validator("*")
    @classmethod
    def valid_field(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,5}", value):
            raise ValueError("Provider field paths must be bounded object keys")
        return value


class ApprovedCancellationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,39}$")
    configuration_id: str = Field(min_length=2, max_length=27)
    display_name: str = Field(min_length=1, max_length=120)
    merchant_domain: str = Field(min_length=3, max_length=253)
    base_url: str
    account_path: str
    inspect_path: str
    cancel_path: str
    management_path: str | None = None
    expected_effect: str = Field(min_length=1, max_length=512)
    supports_idempotency: Literal[True]
    supports_revision_precondition: Literal[True]
    enabled: bool = False
    cancel_payload: dict[str, JsonValue] = Field(default_factory=dict)
    state_fields: ProviderStateFields = Field(default_factory=ProviderStateFields)
    status_mapping: dict[str, ProviderStatus] = Field(default_factory=default_status_mapping)

    @model_validator(mode="after")
    def reviewed_paths(self) -> ApprovedCancellationProfile:
        if approved_origin(self.base_url) != self.base_url:
            raise ValueError("Cancellation base_url must be a canonical HTTPS origin")
        if approved_origin(f"https://{self.merchant_domain}") != f"https://{self.merchant_domain}":
            raise ValueError("Merchant domain must be a canonical reviewed hostname")
        for field, path in (
            ("account", self.account_path),
            ("inspect", self.inspect_path),
            ("cancel", self.cancel_path),
            ("management", self.management_path),
        ):
            if path is None:
                continue
            if field == "account":
                if "{" in path or "}" in path:
                    raise ValueError("Account path cannot have substitutions")
            elif path.count("{subscription_id}") != 1:
                raise ValueError("Subscription paths must bind one exact subscription ID")
            replaced = path.replace("{subscription_id}", "reviewed-id")
            parsed = urlsplit(replaced)
            if (
                not path.startswith("/")
                or path.startswith("//")
                or parsed.query
                or parsed.fragment
                or "{" in replaced
                or "}" in replaced
                or "%" in replaced
            ):
                raise ValueError("Cancellation paths must be fixed relative paths")
            validate_target(httpx.URL(join_relative_path(self.base_url, replaced)), self.base_url)
        if self.cancel_path == self.inspect_path:
            raise ValueError("Read and cancel endpoints must be distinct")
        if {"subscription_id", "expected_revision"} & self.cancel_payload.keys():
            raise ValueError("Cancellation payload cannot override the target or revision")
        if (
            len(json.dumps(self.cancel_payload)) > 8_000
            or not self.status_mapping
            or len(self.status_mapping) > 20
        ):
            raise ValueError("Cancellation profile exceeds bounded configuration limits")
        return self

    @property
    def digest(self) -> str:
        return _digest(self.model_dump(mode="json"))

    def manifest(self, generic: ApprovedGenericConfig) -> ConnectorManifest:
        """A separate reviewed SDK capability declaration; generic reads stay unchanged."""
        data = generic.manifest.model_dump(mode="python")
        capabilities = generic.manifest.capabilities.model_copy(
            update={
                "write": [
                    CapabilityDefinition(
                        name=CAPABILITY, description=self.expected_effect, sensitive=True
                    )
                ]
            }
        )
        data["capabilities"] = capabilities
        return ConnectorManifest.model_validate(data)


def approved_cancellation_profiles(settings: Settings) -> tuple[ApprovedCancellationProfile, ...]:
    profiles = tuple(
        ApprovedCancellationProfile.model_validate(item)
        for item in settings.subscription_cancellation_profiles
    )
    if len(profiles) > 20 or len({profile.id for profile in profiles}) != len(profiles):
        raise ValueError("Cancellation profiles must have distinct bounded identifiers")
    configs = {item.id: item for item in approved_generic_connectors(settings)}
    for profile in profiles:
        generic = configs.get(profile.configuration_id)
        if (
            generic is None
            or generic.config.auth != "bearer"
            or generic.config.base_url != profile.base_url
        ):
            raise ValueError(
                "Cancellation profile must match an authenticated reviewed REST origin"
            )
    return profiles


def cancellation_profiles_public(settings: Settings) -> list[dict[str, object]]:
    return [
        {
            "id": profile.id,
            "configuration_id": profile.configuration_id,
            "name": profile.display_name,
            "merchant_domain": profile.merchant_domain,
            "expected_effect": profile.expected_effect,
            "profile_digest": profile.digest,
            "capability": CAPABILITY,
        }
        for profile in approved_cancellation_profiles(settings)
        if profile.enabled
    ]


async def available_cancellation_profiles(
    database: AsyncSession,
    settings: Settings,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> list[dict[str, object]]:
    """Owner-only configuration inventory. No account requests or credentials are returned."""
    rows = list(
        await database.scalars(
            select(ConnectorConnection).where(
                ConnectorConnection.workspace_id == workspace_id,
                ConnectorConnection.user_id == user_id,
            )
        )
    )
    result: list[dict[str, object]] = []
    for public in cancellation_profiles_public(settings):
        profile, generic = _selected(settings, str(public["id"]))
        connections: list[str] = []
        granted: list[str] = []
        legacy_ids: dict[str, str | None] = {}
        for row in rows:
            try:
                connection = await _owned_config(database, row.id, workspace_id, user_id, generic)
            except ConnectorRuntimeError:
                continue
            connections.append(str(connection.id))
            legacy_ids[str(connection.id)] = (
                str(connection.legacy_connection_id) if connection.legacy_connection_id else None
            )
            grant = await database.scalar(
                select(AuditEvent.id)
                .where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.user_id == user_id,
                    AuditEvent.event_type == GRANT_EVENT,
                    AuditEvent.entity_id == connection.id,
                    AuditEvent.event_metadata["profile_digest"].as_string() == profile.digest,
                    AuditEvent.event_metadata["credential_id"].as_string()
                    == str(connection.credential_reference),
                )
                .limit(1)
            )
            if (
                grant is not None
                and CAPABILITY in connection.authorized_capabilities
                and CAPABILITY in connection.provider_capabilities
            ):
                granted.append(str(connection.id))
        result.append(
            {
                **public,
                "connection_ids": connections,
                "granted_connection_ids": granted,
                "legacy_connection_ids": legacy_ids,
            }
        )
    return result


async def inspect_cancellation_binding(
    database: AsyncSession,
    settings: Settings,
    *,
    target: CancellationTarget,
    profile_id: str,
    connection_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CancellationPreview:
    """Independently inspect a requested manual binding using an existing exact profile grant.

    Returns the verified operator domain/legacy ID in preview.target; the registry
    records the owner's correction after this succeeds. It does not mutate merchants.
    """
    profile, generic = _selected(settings, profile_id)
    if target.merchant_domain is not None and target.merchant_domain != profile.merchant_domain:
        raise _deny()
    connection = await _owned_config(
        database, connection_id, target.workspace_id, target.user_id, generic
    )
    if connection.legacy_connection_id is None:
        raise _deny()
    bound = CancellationTarget.model_validate(
        {
            **target.model_dump(),
            "merchant_domain": profile.merchant_domain,
            "connection_id": connection.legacy_connection_id,
        }
    )
    capability = ReviewedRESTCancellation(
        database, settings, connection.id, profile.id, profile.digest, bound, transport=transport
    )
    return await capability.inspect(bound)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _deny() -> ConnectorRuntimeError:
    return ConnectorRuntimeError("PERMISSION_DENIED", "Subscription cancellation is not authorized")


def _invalid() -> ConnectorRuntimeError:
    return ConnectorRuntimeError(
        "INVALID_PROVIDER_RESPONSE", "Provider cancellation response is invalid"
    )


def _selected(
    settings: Settings, identifier: str
) -> tuple[ApprovedCancellationProfile, ApprovedGenericConfig]:
    try:
        profile = next(
            (
                p
                for p in approved_cancellation_profiles(settings)
                if p.id == identifier and p.enabled
            ),
            None,
        )
        generic = next(
            (
                c
                for c in approved_generic_connectors(settings)
                if profile and c.id == profile.configuration_id
            ),
            None,
        )
    except ValueError:
        raise _deny() from None
    if profile is None or generic is None:
        raise _deny()
    return profile, generic


async def _owned_config(
    database: AsyncSession,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    generic: ApprovedGenericConfig,
) -> ConnectorConnection:
    try:
        connection = await owned_connector(
            database,
            connection_id=connection_id,
            workspace_id=workspace_id,
            user_id=user_id,
            require_active=True,
            lock_authority=True,
        )
    except ConnectorAccessDenied:
        raise _deny() from None
    definition = await database.get(
        ConnectorDefinition, connection.connector_definition_id, populate_existing=True
    )
    if (
        definition is None
        or not definition.active
        or definition.connector_key != generic.connector_key
        or definition.manifest != generic.manifest.model_dump(mode="json", by_alias=True)
        or definition.connector_class != "GENERIC_API"
        or connection.config != generic.config.model_dump(mode="json")
        or connection.provider != generic.config.provider
        or connection.health_state != "CONNECTED"
        or connection.status != "CONNECTED"
    ):
        raise _deny()
    return connection


async def enable_subscription_cancellation(
    database: AsyncSession,
    settings: Settings,
    *,
    connection_id: UUID,
    workspace_id: UUID,
    user_id: UUID,
    profile_id: str,
    confirmed: bool,
    request_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Explicit consent route helper. Caller commits; this never performs a write at a provider."""
    if confirmed is not True:
        raise _deny()
    profile, generic = _selected(settings, profile_id)
    connection = await _owned_config(database, connection_id, workspace_id, user_id, generic)
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.user_id == user_id,
            AuditEvent.event_type == GRANT_EVENT,
            AuditEvent.event_metadata["request_id"].as_string() == str(request_id),
        )
    )
    if previous is not None:
        if (
            previous.entity_id != connection.id
            or previous.event_metadata.get("profile_digest") != profile.digest
            or previous.event_metadata.get("credential_id") != str(connection.credential_reference)
            or CAPABILITY not in connection.authorized_capabilities
            or CAPABILITY not in connection.provider_capabilities
        ):
            raise _deny()
        return {
            "granted": True,
            "reused": True,
            "profile_id": profile.id,
            "connection_id": str(connection.id),
            "legacy_connection_id": str(connection.legacy_connection_id),
        }
    payload = await _request(
        database,
        settings,
        connection,
        generic,
        profile,
        profile.account_path,
        purpose="subscription.inspect",
        transport=transport,
    )
    account_id = _identifier(_field(payload, "account_id"))
    capabilities = _field(payload, "capabilities")
    if not isinstance(capabilities, list) or CAPABILITY not in capabilities:
        raise _deny()
    # Both grants are separate from the existing read grants. Provider proof is the
    # authenticated account endpoint; user proof is this explicit consent command.
    connection.authorized_capabilities = sorted(
        set(connection.authorized_capabilities) | {CAPABILITY}
    )
    connection.provider_capabilities = sorted(set(connection.provider_capabilities) | {CAPABILITY})
    provenance = await ensure_provenance_connection(database, connection)
    database.add(
        AuditEvent(
            workspace_id=workspace_id,
            user_id=user_id,
            actor_type="user",
            event_type=GRANT_EVENT,
            entity_type="connector_connection",
            entity_id=connection.id,
            event_metadata={
                "profile_id": profile.id,
                "profile_digest": profile.digest,
                "credential_id": str(connection.credential_reference),
                "provider_account_id": account_id,
                "request_id": str(request_id),
                "capability": CAPABILITY,
            },
        )
    )
    await database.flush()
    return {
        "granted": True,
        "reused": False,
        "profile_id": profile.id,
        "connection_id": str(connection.id),
        "legacy_connection_id": str(provenance.id),
    }


async def resolve_subscription_cancellation(
    database: AsyncSession,
    settings: Settings,
    target: CancellationTarget,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CancelSubscriptionCapability | None:
    """No operator profile or explicit grant means no executable cancellation method."""
    if (
        target.connection_id is None
        or target.external_resource_id is None
        or target.merchant_domain is None
    ):
        return None
    try:
        _identifier(target.external_resource_id)
        profiles = approved_cancellation_profiles(settings)
    except (ValueError, ConnectorRuntimeError):
        return None
    connections = list(
        await database.scalars(
            select(ConnectorConnection).where(
                ConnectorConnection.legacy_connection_id == target.connection_id,
                ConnectorConnection.workspace_id == target.workspace_id,
                ConnectorConnection.user_id == target.user_id,
            )
        )
    )
    if len(connections) != 1:
        return None
    if connections[0].provider == "stripe_sandbox":
        from navox.connectors.stripe_subscription import StripeSandboxCancellation

        stripe = StripeSandboxCancellation(
            database, settings, connections[0].id, target, transport=transport
        )
        try:
            await stripe.authorize()
        except ConnectorRuntimeError:
            return None
        return stripe
    candidates: list[ReviewedRESTCancellation] = []
    for profile in profiles:
        if not profile.enabled or profile.merchant_domain != target.merchant_domain:
            continue
        capability = ReviewedRESTCancellation(
            database,
            settings,
            connections[0].id,
            profile.id,
            profile.digest,
            target,
            transport=transport,
        )
        try:
            await capability._authorize()
        except ConnectorRuntimeError:
            continue
        candidates.append(capability)
    # Multiple approved profiles for one target are ambiguous; don't choose a write.
    return candidates[0] if len(candidates) == 1 else None


class ReviewedRESTCancellation:
    def __init__(
        self,
        database: AsyncSession,
        settings: Settings,
        connection_id: UUID,
        profile_id: str,
        profile_digest: str,
        target: CancellationTarget,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.database, self.settings, self.connection_id = database, settings, connection_id
        self.profile_id, self.profile_digest, self.target, self.transport = (
            profile_id,
            profile_digest,
            target,
            transport,
        )

    async def _authorize(
        self,
    ) -> tuple[ConnectorConnection, ApprovedCancellationProfile, ApprovedGenericConfig, str]:
        profile, generic = _selected(self.settings, self.profile_id)
        if (
            profile.digest != self.profile_digest
            or profile.merchant_domain != self.target.merchant_domain
        ):
            raise _deny()
        connection = await _owned_config(
            self.database,
            self.connection_id,
            self.target.workspace_id,
            self.target.user_id,
            generic,
        )
        legacy = await self.database.get(
            Connection, self.target.connection_id, populate_existing=True
        )
        if (
            legacy is None
            or connection.legacy_connection_id != legacy.id
            or legacy.workspace_id != self.target.workspace_id
            or legacy.user_id != self.target.user_id
            or legacy.status != "active"
        ):
            raise _deny()
        grants = list(
            await self.database.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == connection.workspace_id,
                    AuditEvent.user_id == connection.user_id,
                    AuditEvent.event_type == GRANT_EVENT,
                    AuditEvent.entity_id == connection.id,
                    AuditEvent.event_metadata["profile_digest"].as_string() == profile.digest,
                    AuditEvent.event_metadata["credential_id"].as_string()
                    == str(connection.credential_reference),
                )
            )
        )
        accounts = {event.event_metadata.get("provider_account_id") for event in grants}
        if len(accounts) != 1:
            raise _deny()
        account_id = _identifier(accounts.pop())
        effective = CapabilityGateway().evaluate(
            manifest=profile.manifest(generic),
            provider_capabilities=set(connection.provider_capabilities),
            user_authorized=set(connection.authorized_capabilities),
            policy_allowed={CAPABILITY} if profile.enabled else set(),
            health_state="CONNECTED",
        )
        if CAPABILITY not in effective.write:
            raise _deny()
        return connection, profile, generic, account_id

    async def _state(self, purpose: str) -> tuple[ApprovedCancellationProfile, dict[str, object]]:
        connection, profile, generic, account_id = await self._authorize()
        identifier = _identifier(self.target.external_resource_id)
        payload = await _request(
            self.database,
            self.settings,
            connection,
            generic,
            profile,
            profile.inspect_path.replace("{subscription_id}", identifier),
            purpose=purpose,
            transport=self.transport,
        )
        fields = profile.state_fields
        if (
            _field(payload, fields.subscription_id) != identifier
            or _field(payload, fields.account_id) != account_id
        ):
            raise _invalid()
        revision = _safe_text(_field(payload, fields.revision), limit=128, required=True)
        if revision is None or any(ord(character) > 126 for character in revision):
            raise _invalid()
        raw_status = _field(payload, fields.status)
        status = profile.status_mapping.get(raw_status) if isinstance(raw_status, str) else None
        if status is None:
            raise _invalid()
        auto_renew, allowed = (
            _field(payload, fields.auto_renew),
            _field(payload, fields.cancel_allowed),
        )
        if (
            auto_renew is not None
            and not isinstance(auto_renew, bool)
            or not isinstance(allowed, bool)
        ):
            raise _invalid()
        currency = _safe_text(_field(payload, fields.currency), limit=3)
        if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
            raise _invalid()
        count = _field(payload, fields.interval_count)
        if count is not None and (type(count) is not int or not 1 <= count <= 365):
            raise _invalid()
        interval = _safe_text(_field(payload, fields.interval), limit=20)
        if interval is not None and interval not in {
            "DAY",
            "WEEK",
            "MONTH",
            "QUARTER",
            "YEAR",
            "OTHER",
            "UNKNOWN",
        }:
            raise _invalid()
        return profile, {
            "subscription_id": identifier,
            "account_id": account_id,
            "revision": revision,
            "status": status,
            "cancel_allowed": allowed,
            "plan_name": _safe_text(_field(payload, fields.plan_name), limit=120),
            "amount": _money(_field(payload, fields.amount)),
            "currency": currency,
            "interval": interval,
            "interval_count": count,
            "renewal_at": _time(_field(payload, fields.renewal_at)),
            "auto_renew": auto_renew,
            "access_ends_at": _time(_field(payload, fields.access_ends_at)),
            "cancellation_fee": _money(_field(payload, fields.cancellation_fee)),
            "refund": _safe_text(_field(payload, fields.refund), limit=512),
        }

    async def inspect(self, target: CancellationTarget) -> CancellationPreview:
        if target != self.target:
            raise _deny()
        profile, state = await self._state("subscription.inspect")
        now = datetime.now(UTC)
        management = (
            join_relative_path(
                profile.base_url,
                profile.management_path.replace(
                    "{subscription_id}", _identifier(target.external_resource_id)
                ),
            )
            if profile.management_path
            else None
        )
        warnings = [
            f"{label} is unknown."
            for field, label in (
                ("cancellation_fee", "Cancellation fee"),
                ("refund", "Refund"),
                ("access_ends_at", "Access end date"),
            )
            if state[field] is None
        ]
        if state["cancel_allowed"] is not True or state["status"] not in {"active", "paused"}:
            warnings.append("Provider state does not currently permit cancellation.")
        return CancellationPreview(
            target=target,
            method="CONNECTED_PROVIDER_ACTION",
            provider_revision=_digest({"profile": profile.digest, "state": state}),
            provider_state=str(state["status"]),
            economics=CancellationEconomics.model_validate(
                {
                    "plan_name": state["plan_name"],
                    "billing_amount": state["amount"],
                    "billing_currency": state["currency"],
                    "billing_interval": state["interval"],
                    "billing_interval_count": state["interval_count"] or 1,
                    "next_renewal_at": state["renewal_at"],
                    "auto_renew": state["auto_renew"],
                }
            ),
            payload={
                "profile_id": profile.id,
                "profile_digest": profile.digest,
                "provider_state": state,
            },
            expected_effect=profile.expected_effect,
            access_ends_at=_parse_time(state["access_ends_at"]),
            fee=Decimal(str(state["cancellation_fee"]))
            if state["cancellation_fee"] is not None
            else None,
            refund=str(state["refund"]) if state["refund"] is not None else None,
            management_url=management,
            warnings=warnings,
            inspected_at=now,
            expires_at=now + timedelta(minutes=5),
        )

    async def execute(
        self, preview: CancellationPreview, idempotency_key: str
    ) -> CancellationSubmission:
        if preview.target != self.target or not re.fullmatch(
            r"[A-Za-z0-9:_-]{1,200}", idempotency_key
        ):
            raise _deny()
        if preview.expires_at.tzinfo is None or preview.expires_at <= datetime.now(UTC):
            raise _deny()
        refreshed = await self.inspect(self.target)
        # Target, all economic consequences, approved configuration and provider
        # revision must still match the exact-action preview after confirmation.
        if refreshed.model_dump(exclude={"inspected_at", "expires_at"}) != preview.model_dump(
            exclude={"inspected_at", "expires_at"}
        ):
            raise _deny()
        connection, profile, generic, account_id = await self._authorize()
        state = preview.payload.get("provider_state")
        if (
            not isinstance(state, dict)
            or state.get("cancel_allowed") is not True
            or state.get("status") not in {"active", "paused"}
        ):
            raise _deny()
        identifier = _identifier(self.target.external_resource_id)
        revision = _safe_text(state.get("revision"), limit=128, required=True)
        try:
            result = await _request(
                self.database,
                self.settings,
                connection,
                generic,
                profile,
                profile.cancel_path.replace("{subscription_id}", identifier),
                purpose="subscription.cancel",
                transport=self.transport,
                body={
                    **profile.cancel_payload,
                    "subscription_id": identifier,
                    "expected_revision": revision,
                },
                extra_headers={"Idempotency-Key": idempotency_key, "If-Match": str(revision)},
            )
            if _field(result, "id") != identifier or _field(result, "account_id") != account_id:
                raise _invalid()
            status = _field(result, "status")
            if status not in {"submitted", "awaiting_user"}:
                raise _invalid()
            return CancellationSubmission(
                status="AWAITING_USER" if status == "awaiting_user" else "SUBMITTED",
                external_request_id=_safe_text(
                    _field(result, "request_id"), limit=128, required=True
                ),
                metadata={"profile_id": profile.id},
            )
        except ConnectorRuntimeError:
            # Even a malformed response can follow an applied provider write.
            # The engine must independently read state, never replay blindly.
            return CancellationSubmission(
                status="UNCERTAIN", metadata={"reason": "provider_submission_unconfirmed"}
            )

    async def verify(
        self, preview: CancellationPreview, submission: CancellationSubmission
    ) -> CancellationVerification:
        del submission
        if (
            preview.target != self.target
            or preview.payload.get("profile_digest") != self.profile_digest
        ):
            raise _deny()
        _, state = await self._state("subscription.verify")
        status: Literal["VERIFIED_CANCELLED", "VERIFIED_ACTIVE", "VERIFICATION_PENDING"] = (
            "VERIFICATION_PENDING"
        )
        previous = preview.payload.get("provider_state")
        previous_revision = previous.get("revision") if isinstance(previous, dict) else None
        if (
            state["status"] == "cancelled"
            and state["auto_renew"] is False
            and state["revision"] != previous_revision
        ):
            status = "VERIFIED_CANCELLED"
        elif state["status"] in {"active", "paused"} and state["auto_renew"] is True:
            status = "VERIFIED_ACTIVE"
        return CancellationVerification(
            status=status,
            source_type="provider_state",
            external_resource_id=self.target.external_resource_id,
            provider_revision=str(state["revision"]),
            evidence={
                "profile_id": self.profile_id,
                "subscription_id": state["subscription_id"],
                "state": state["status"],
                "auto_renew": state["auto_renew"],
                "access_ends_at": state["access_ends_at"],
            },
            observed_at=datetime.now(UTC),
        )


async def _request(
    database: AsyncSession,
    settings: Settings,
    connection: ConnectorConnection,
    generic: ApprovedGenericConfig,
    profile: ApprovedCancellationProfile,
    path: str,
    *,
    purpose: str,
    transport: httpx.AsyncBaseTransport | None,
    body: dict[str, JsonValue] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> object:
    url = join_relative_path(profile.base_url, path)
    validate_target(httpx.URL(url), profile.base_url)
    try:
        broker = SecretBroker(settings)
        with await broker.lease(
            database,
            connection_id=connection.id,
            workspace_id=connection.workspace_id,
            user_id=connection.user_id,
            purpose=purpose,
            names={generic.config.token_secret_name},
        ) as lease:
            token = lease.get(generic.config.token_secret_name)
            if not token or any(ord(char) < 33 or ord(char) > 126 for char in token):
                raise _deny()
            headers = {
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                **(extra_headers or {}),
            }
            async with httpx.AsyncClient(
                transport=transport
                or ApprovedHTTPSTransport(
                    profile.base_url,
                    allowed_post_paths=frozenset({path}) if body is not None else frozenset(),
                ),
                headers=headers,
                timeout=30,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST" if body is not None else "GET", url, json=body
                ) as response:
                    if response.status_code in {401, 403}:
                        raise _deny()
                    if not 200 <= response.status_code < 300:
                        raise ConnectorRuntimeError(
                            "PROVIDER_UNAVAILABLE", "Provider cancellation request was not accepted"
                        )
                    if response.headers.get("content-encoding", "identity").lower() not in {
                        "",
                        "identity",
                    }:
                        raise _invalid()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise _invalid()
                        content.extend(chunk)
                    try:
                        payload = json.loads(content, parse_float=Decimal)
                    except (UnicodeError, ValueError):
                        raise _invalid() from None
                    if _contains_credential(payload, token) or not isinstance(payload, dict):
                        raise _invalid()
                    return payload
    except SecretBrokerError:
        raise _deny() from None
    except (httpx.HTTPError, OSError, TimeoutError):
        raise ConnectorRuntimeError(
            "PROVIDER_UNAVAILABLE", "Provider cancellation request failed"
        ) from None


def _field(payload: object, path: str) -> object:
    for segment in path.split("."):
        if not isinstance(payload, dict):
            return None
        payload = payload.get(segment)
    return payload


def _identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise _invalid()
    return value


def _safe_text(value: object, *, limit: int, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise _invalid()
    return value


def _money(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str | int | Decimal) or isinstance(value, bool):
        raise _invalid()
    try:
        number = Decimal(str(value))
        exponent = number.as_tuple().exponent
        if (
            not number.is_finite()
            or number < 0
            or number >= Decimal("1000000000000")
            or not isinstance(exponent, int)
            or exponent < -4
        ):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        raise _invalid() from None
    return str(number)


def _time(value: object) -> str | None:
    if value is None:
        return None
    parsed = _parse_time(value)
    if parsed is None:
        raise _invalid()
    return parsed.isoformat()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo is not None else None
