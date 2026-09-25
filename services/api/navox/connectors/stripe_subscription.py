"""Owner-bound sandbox onboarding and the native Stripe cancellation capability."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from navox.connectors import stripe_sandbox as stripe
from navox.connectors.authorization import ConnectorAccessDenied, owned_connector
from navox.connectors.capabilities import CapabilityGateway
from navox.connectors.contracts import ConnectorRuntimeError
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.secrets import SecretBroker, SecretBrokerError
from navox.core.settings import Settings
from navox.db.models import (
    AuditEvent,
    Connection,
    ConnectorConnection,
    ConnectorDefinition,
    Merchant,
    ObligationPriceHistory,
    RecurringObligation,
    RecurringObligationEvidence,
)
from navox.subscriptions.cancellation_contracts import (
    CancellationPreview,
    CancellationSubmission,
    CancellationTarget,
    CancellationVerification,
)
from navox.subscriptions.discovery import EvidenceCandidate
from navox.subscriptions.events import queue_event
from navox.subscriptions.service import lock_owner

GRANT = "connector.stripe_sandbox.connected"


class StripeSandboxConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    token: SecretStr
    subscription_id: str = Field(pattern=r"^sub_[A-Za-z0-9]{1,120}$")
    name: str = Field(default="NavoX Cancellation Test", min_length=1, max_length=120)
    confirmed: bool = Field(strict=True)


def enabled(settings: Settings) -> bool:
    return settings.stripe_sandbox_enabled and settings.app_environment.casefold() not in {
        "prod",
        "production",
    }


async def connect(
    database: AsyncSession,
    settings: Settings,
    command: StripeSandboxConnect,
    *,
    workspace_id: UUID,
    user_id: UUID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> RecurringObligation:
    if not enabled(settings) or command.confirmed is not True:
        raise stripe.denied()
    token = stripe.test_key(command.token.get_secret_value())
    await lock_owner(database, workspace_id=workspace_id, user_id=user_id)
    account = await stripe.request(token, "/v1/account", transport=transport)
    account_id = stripe.identifier(account.get("id"), "acct")
    if account.get("object") != "account":
        raise stripe.invalid()
    state = stripe.parse_subscription(
        await stripe.request(
            token, f"/v1/subscriptions/{command.subscription_id}", transport=transport
        ),
        command.subscription_id,
    )
    if state.status != "active":
        raise stripe.unsupported()
    binding = stripe.StripeBinding(
        account_id=account_id, subscription_id=state.subscription_id, customer_id=state.customer_id
    )
    previous = await database.scalar(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.user_id == user_id,
            AuditEvent.event_type == GRANT,
            AuditEvent.event_metadata["request_id"].as_string() == str(command.request_id),
        )
    )
    if previous is not None:
        metadata = previous.event_metadata
        if (
            metadata.get("binding") != binding.model_dump(mode="json")
            or metadata.get("name") != command.name
        ):
            raise HTTPException(409, "Sandbox connection request changed")
        row_id = metadata.get("obligation_id")
        row = await database.get(RecurringObligation, UUID(str(row_id)))
        if row is None or (row.workspace_id, row.user_id) != (workspace_id, user_id):
            raise stripe.denied()
        return row
    external_id = f"{account_id}:{state.subscription_id}"
    existing = await database.scalar(
        select(ConnectorConnection.id).where(
            ConnectorConnection.workspace_id == workspace_id,
            ConnectorConnection.user_id == user_id,
            ConnectorConnection.provider == stripe.PROVIDER,
            ConnectorConnection.external_account_id == external_id,
        )
    )
    if existing is not None:
        raise HTTPException(
            409, "This sandbox subscription is already connected; use its existing record"
        )
    definition = await database.scalar(
        select(ConnectorDefinition).where(ConnectorDefinition.connector_key == stripe.CONNECTOR_ID)
    )
    if definition is None:
        definition = ConnectorDefinition(
            connector_key=stripe.CONNECTOR_ID,
            version=stripe.MANIFEST.version,
            display_name=stripe.MANIFEST.display_name,
            connector_class="TOKEN_API",
            trust_level="NAVOX_FIRST_PARTY",
            manifest=stripe.MANIFEST.model_dump(mode="json", by_alias=True),
        )
        database.add(definition)
        await database.flush()
    if not definition.active or definition.manifest != stripe.MANIFEST.model_dump(
        mode="json", by_alias=True
    ):
        raise stripe.denied()
    connection = ConnectorConnection(
        connector_definition_id=definition.id,
        workspace_id=workspace_id,
        user_id=user_id,
        provider=stripe.PROVIDER,
        external_account_id=external_id,
        config=binding.model_dump(mode="json"),
        authorized_capabilities=[stripe.READ, stripe.CANCEL],
        # Stripe advertises this API. Restricted-key write scope cannot be
        # introspected; a subsequent 403 is never reported as a successful write.
        provider_capabilities=[stripe.READ, stripe.CANCEL],
    )
    database.add(connection)
    await database.flush()
    try:
        await SecretBroker(settings).store(
            database,
            {stripe.SECRET_NAME: token},
            connection_id=connection.id,
            workspace_id=workspace_id,
            user_id=user_id,
        )
    except SecretBrokerError:
        raise stripe.denied() from None
    provenance = await ensure_provenance_connection(database, connection)
    merchant_name = f"Stripe sandbox {account_id}"
    merchant = await database.scalar(
        select(Merchant).where(
            Merchant.workspace_id == workspace_id,
            Merchant.user_id == user_id,
            Merchant.normalized_name == merchant_name.casefold(),
        )
    )
    if merchant is None:
        merchant = Merchant(
            workspace_id=workspace_id,
            user_id=user_id,
            canonical_name=merchant_name,
            normalized_name=merchant_name.casefold(),
            website_domain="stripe.com",
        )
        database.add(merchant)
        await database.flush()
    now = datetime.now(UTC)
    money = state.economics
    row = RecurringObligation(
        workspace_id=workspace_id,
        user_id=user_id,
        merchant_id=merchant.id,
        name=f"Sandbox: {command.name}",
        plan_name=money.plan_name,
        status="ACTIVE",
        confidence=Decimal(1),
        review_state="CONFIRMED",
        last_verified_at=now,
        billing_amount=money.billing_amount,
        billing_currency=money.billing_currency,
        billing_interval=money.billing_interval,
        interval_count=money.billing_interval_count,
        next_renewal_at=money.next_renewal_at,
        auto_renew=True,
        cancellation_connection_id=provenance.id,
        cancellation_external_resource_id=state.subscription_id,
        obligation_metadata={"stripe_sandbox": True, "user_overrides": []},
    )
    database.add(row)
    await database.flush()
    candidate = EvidenceCandidate(
        evidence_type="SIGNUP",
        merchant_text=merchant_name,
        website_domain="stripe.com",
        plan_text=money.plan_name,
        account_reference=external_id,
        amount=money.billing_amount,
        currency=money.billing_currency,
        billing_interval=money.billing_interval or "UNKNOWN",
        interval_count=money.billing_interval_count,
        effective_at=now,
        renewal_at=money.next_renewal_at,
        auto_renew=True,
        confidence=Decimal(1),
        needs_confirmation=False,
    )
    evidence = RecurringObligationEvidence(
        workspace_id=workspace_id,
        user_id=user_id,
        obligation_id=row.id,
        evidence_type="SIGNUP",
        source_type="stripe_sandbox",
        connection_id=provenance.id,
        external_resource_id=state.subscription_id,
        dedupe_key=stripe.fingerprint(
            {"binding": binding.model_dump(), "connection": str(connection.id)}
        ),
        merchant_text=merchant_name,
        plan_text=money.plan_name,
        amount=money.billing_amount,
        currency=money.billing_currency,
        billing_interval=money.billing_interval,
        interval_count=money.billing_interval_count,
        effective_at=now,
        renewal_at=money.next_renewal_at,
        confidence=Decimal(1),
        observed_at=now,
        evidence_metadata={
            "extracted_candidate": candidate.model_dump(mode="json"),
            "provider_revision": state.revision,
            "sandbox": True,
        },
    )
    database.add(evidence)
    await database.flush()
    database.add(
        ObligationPriceHistory(
            workspace_id=workspace_id,
            user_id=user_id,
            obligation_id=row.id,
            amount=money.billing_amount,
            currency="USD",
            billing_interval=money.billing_interval,
            interval_count=money.billing_interval_count,
            effective_from=now,
            evidence_id=evidence.id,
        )
    )
    database.add(
        AuditEvent(
            workspace_id=workspace_id,
            user_id=user_id,
            actor_type="user",
            event_type=GRANT,
            entity_type="connector_connection",
            entity_id=connection.id,
            event_metadata={
                "request_id": str(command.request_id),
                "obligation_id": str(row.id),
                "name": command.name,
                "binding": binding.model_dump(mode="json"),
                "binding_digest": stripe.fingerprint(binding.model_dump()),
                "credential_id": str(connection.credential_reference),
                "sandbox": True,
            },
        )
    )
    await queue_event(
        database, row, "obligation.discovered", f"stripe-sandbox:{connection.id}", {"sandbox": True}
    )
    await database.commit()
    await database.refresh(row)
    return row


class StripeSandboxCancellation:
    def __init__(
        self,
        database: AsyncSession,
        settings: Settings,
        connection_id: UUID,
        target: CancellationTarget,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.database, self.settings, self.connection_id, self.target, self.transport = (
            database,
            settings,
            connection_id,
            target,
            transport,
        )

    async def authorize(self) -> tuple[ConnectorConnection, stripe.StripeBinding]:
        if not enabled(self.settings):
            raise stripe.denied()
        try:
            connection = await owned_connector(
                self.database,
                connection_id=self.connection_id,
                workspace_id=self.target.workspace_id,
                user_id=self.target.user_id,
                require_active=True,
                lock_authority=True,
            )
            binding = stripe.StripeBinding.model_validate(connection.config)
        except (ConnectorAccessDenied, ValueError):
            raise stripe.denied() from None
        definition = await self.database.get(
            ConnectorDefinition, connection.connector_definition_id, populate_existing=True
        )
        legacy = await self.database.get(
            Connection, self.target.connection_id, populate_existing=True
        )
        if (
            definition is None
            or not definition.active
            or definition.connector_key != stripe.CONNECTOR_ID
            or definition.manifest != stripe.MANIFEST.model_dump(mode="json", by_alias=True)
            or connection.provider != stripe.PROVIDER
            or connection.status != "CONNECTED"
            or connection.health_state != "CONNECTED"
            or connection.external_account_id != f"{binding.account_id}:{binding.subscription_id}"
            or legacy is None
            or connection.legacy_connection_id != legacy.id
            or legacy.status != "active"
            or (legacy.workspace_id, legacy.user_id)
            != (self.target.workspace_id, self.target.user_id)
            or self.target.external_resource_id != binding.subscription_id
            or self.target.merchant_domain != "stripe.com"
        ):
            raise stripe.denied()
        grant = await self.database.scalar(
            select(AuditEvent.id).where(
                AuditEvent.workspace_id == connection.workspace_id,
                AuditEvent.user_id == connection.user_id,
                AuditEvent.entity_id == connection.id,
                AuditEvent.event_type == GRANT,
                AuditEvent.event_metadata["credential_id"].as_string()
                == str(connection.credential_reference),
                AuditEvent.event_metadata["binding_digest"].as_string()
                == stripe.fingerprint(binding.model_dump()),
            )
        )
        effective = CapabilityGateway().evaluate(
            manifest=stripe.MANIFEST,
            provider_capabilities=set(connection.provider_capabilities),
            user_authorized=set(connection.authorized_capabilities),
            policy_allowed={stripe.READ, stripe.CANCEL},
            health_state="CONNECTED",
        )
        if (
            grant is None
            or stripe.CANCEL not in effective.write
            or stripe.READ not in effective.read
        ):
            raise stripe.denied()
        return connection, binding

    async def state(self, purpose: str) -> tuple[stripe.StripeBinding, stripe.StripeState]:
        connection, binding = await self.authorize()
        try:
            with await SecretBroker(self.settings).lease(
                self.database,
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
                purpose=purpose,
                names={stripe.SECRET_NAME},
            ) as lease:
                state = await stripe.inspect(
                    lease.get(stripe.SECRET_NAME), binding, transport=self.transport
                )
        except SecretBrokerError:
            raise stripe.denied() from None
        return binding, state

    async def inspect(self, target: CancellationTarget) -> CancellationPreview:
        if target != self.target:
            raise stripe.denied()
        binding, state = await self.state("subscription.inspect")
        now = datetime.now(UTC)
        return CancellationPreview(
            target=target,
            method="CONNECTED_PROVIDER_ACTION",
            provider_revision=state.revision,
            provider_state="active" if state.status == "active" else "cancelled",
            economics=state.economics,
            payload={
                "sandbox": True,
                "binding": binding.model_dump(mode="json"),
                "invoice_now": False,
                "prorate": False,
            },
            expected_effect=(
                "Cancel this Stripe sandbox subscription immediately and stop its renewals. "
                "No final invoice or proration is requested."
            ),
            refund="This action does not issue a refund.",
            warnings=[
                f"Sandbox only. Account {binding.account_id}; customer {binding.customer_id}.",
                "Stripe cannot atomically reject a cancellation if its state changes after the "
                "final read. Keep this disposable test subscription unchanged during the test.",
                "Existing invoices and pending invoice items are not removed. "
                "Service access and merchant cancellation fees are unknown.",
            ],
            inspected_at=now,
            expires_at=now + timedelta(minutes=5),
        )

    async def execute(
        self, preview: CancellationPreview, idempotency_key: str
    ) -> CancellationSubmission:
        del idempotency_key  # Durable engine fence; Stripe DELETE ignores idempotency keys.
        if (
            preview.target != self.target
            or preview.expires_at.tzinfo is None
            or preview.expires_at <= datetime.now(UTC)
        ):
            raise stripe.denied()
        refreshed = await self.inspect(self.target)
        if refreshed.provider_state != "active" or refreshed.model_dump(
            exclude={"inspected_at", "expires_at"}
        ) != preview.model_dump(exclude={"inspected_at", "expires_at"}):
            raise stripe.denied()
        connection, binding = await self.authorize()
        try:
            with await SecretBroker(self.settings).lease(
                self.database,
                connection_id=connection.id,
                workspace_id=connection.workspace_id,
                user_id=connection.user_id,
                purpose="subscription.cancel",
                names={stripe.SECRET_NAME},
            ) as lease:
                response = await stripe.request(
                    lease.get(stripe.SECRET_NAME),
                    f"/v1/subscriptions/{binding.subscription_id}",
                    cancel=True,
                    transport=self.transport,
                )
                submitted = stripe.parse_subscription(response, binding.subscription_id)
                if submitted.status != "canceled" or submitted.customer_id != binding.customer_id:
                    raise stripe.invalid()
            return CancellationSubmission(status="SUBMITTED", metadata={"sandbox": True})
        except (ConnectorRuntimeError, SecretBrokerError):
            return CancellationSubmission(
                status="UNCERTAIN",
                metadata={"sandbox": True, "reason": "provider_submission_unconfirmed"},
            )

    async def verify(
        self, preview: CancellationPreview, submission: CancellationSubmission
    ) -> CancellationVerification:
        del submission
        if preview.target != self.target:
            raise stripe.denied()
        binding, state = await self.state("subscription.verify")
        if preview.payload.get("binding") != binding.model_dump(mode="json"):
            raise stripe.denied()
        result: Literal["VERIFIED_CANCELLED", "VERIFIED_ACTIVE", "VERIFICATION_PENDING"] = (
            "VERIFICATION_PENDING"
        )
        if state.status == "canceled" and state.revision != preview.provider_revision:
            result = "VERIFIED_CANCELLED"
        elif state.status == "active":
            result = "VERIFIED_ACTIVE"
        return CancellationVerification(
            status=result,
            source_type="provider_state",
            external_resource_id=binding.subscription_id,
            provider_revision=state.revision,
            observed_at=datetime.now(UTC),
            evidence={
                "sandbox": True,
                "account_id": binding.account_id,
                "customer_id": binding.customer_id,
                "state": "cancelled" if state.status == "canceled" else "active",
                "auto_renew": state.economics.auto_renew,
            },
        )
