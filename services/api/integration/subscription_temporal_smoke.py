"""Real PostgreSQL + Temporal cancellation with an isolated disposable provider.

Only HTTPS transport is replaced. API auth, connector grants, encrypted credentials,
R4 approvals, durable workflow dispatch and independent verification are production code.
No live merchant account is contacted or cancelled by this harness.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from pydantic_settings import SettingsConfigDict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.worker import Worker

from navox.api.main import create_app
from navox.connectors.generic_registration import approved_generic_connectors
from navox.connectors.provenance import ensure_provenance_connection
from navox.connectors.secrets import SecretBroker
from navox.core.settings import Settings, get_settings
from navox.db.models import (
    CancellationEvidence,
    ConnectorConnection,
    ConnectorDefinition,
    RecurringObligation,
    User,
    Workspace,
)
from navox.db.session import get_database_session
from navox.subscriptions.activities import (
    cancellation_state_activity,
    execute_cancellation_activity,
    verify_cancellation_activity,
)
from navox.workflows.subscriptions import CancelSubscriptionWorkflow, VerifyCancellationWorkflow


class IntegrationSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


class DisposableProvider:
    def __init__(self) -> None:
        self.writes = 0
        self.post_write_reads = 0
        self.state: dict[str, object] = {
            "id": "subscription-1",
            "account_id": "fixture-account",
            "revision": "r1",
            "status": "active",
            "cancel_allowed": True,
            "plan_name": "Fixture Professional",
            "amount": "12.34",
            "currency": "USD",
            "interval": "MONTH",
            "interval_count": 1,
            "auto_renew": True,
            "renewal_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
            "access_ends_at": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
            "cancellation_fee": "0",
            "refund": "No current-period refund.",
        }

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "fixture.example"
        assert request.headers["authorization"] == "Bearer disposable-subscription-secret"
        if request.url.path == "/account" and request.method == "GET":
            return httpx.Response(
                200, json={"account_id": "fixture-account", "capabilities": ["subscription.cancel"]}
            )
        if request.url.path == "/subscriptions/subscription-1" and request.method == "GET":
            self.post_write_reads += int(self.writes > 0)
            return httpx.Response(200, json=self.state)
        if request.url.path == "/subscriptions/subscription-1/cancel" and request.method == "POST":
            assert request.headers["if-match"] == "r1"
            assert request.headers["idempotency-key"].startswith("subscription-cancel:")
            assert json.loads(request.content) == {
                "at_period_end": True,
                "subscription_id": "subscription-1",
                "expected_revision": "r1",
            }
            self.writes += 1
            assert self.writes == 1
            self.state.update(status="cancelled", auto_renew=False, revision="r2")
            return httpx.Response(
                200,
                json={
                    "id": "subscription-1",
                    "account_id": "fixture-account",
                    "status": "submitted",
                    "request_id": "fixture-request",
                },
            )
        raise AssertionError("Unexpected provider request")


async def run() -> None:
    identifier = f"fixture-{uuid4().hex[:8]}"
    settings = IntegrationSettings(
        app_environment="test",
        ai_provider="disabled",
        temporal_task_queue=f"navox-subscriptions-ci-{uuid4()}",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
        generic_rest_connectors=[
            {
                "id": identifier,
                "config": {
                    "display_name": "Disposable subscription provider",
                    "provider": "fixture_subscriptions",
                    "base_url": "https://fixture.example",
                    "endpoints": [
                        {
                            "name": "subscriptions",
                            "path": "/subscriptions",
                            "capability": "subscription.read",
                            "resource_type": "recurring_obligation",
                            "subject_field": "plan_name",
                        }
                    ],
                },
            }
        ],
        subscription_cancellation_profiles=[
            {
                "id": identifier,
                "configuration_id": identifier,
                "display_name": "Disposable provider",
                "merchant_domain": "fixture.example",
                "base_url": "https://fixture.example",
                "account_path": "/account",
                "inspect_path": "/subscriptions/{subscription_id}",
                "cancel_path": "/subscriptions/{subscription_id}/cancel",
                "enabled": True,
                "expected_effect": "Stop the next renewal; keep access until the paid period ends.",
                "supports_idempotency": True,
                "supports_revision_precondition": True,
                "cancel_payload": {"at_period_end": True},
            }
        ],
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    provider = DisposableProvider()
    app = create_app()

    async def database_override():
        async with sessions() as database:
            yield database

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    client = await Client.connect(settings.temporal_target)
    user_id = workspace_id = definition_id = None
    try:
        with (
            patch("navox.subscriptions.activities.get_settings", return_value=settings),
            patch("navox.subscriptions.activities.get_session_factory", return_value=sessions),
            patch(
                "navox.connectors.subscription_cancellation.ApprovedHTTPSTransport",
                side_effect=lambda *args, **kwargs: httpx.MockTransport(provider.handle),
            ),
        ):
            async with Worker(
                client,
                task_queue=settings.temporal_task_queue,
                workflows=[CancelSubscriptionWorkflow, VerifyCancellationWorkflow],
                activities=[
                    cancellation_state_activity,
                    execute_cancellation_activity,
                    verify_cancellation_activity,
                ],
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://fixture-api"
                ) as api:
                    response = await api.post(
                        "/api/v1/auth/register",
                        json={
                            "email": f"subscription-ci-{uuid4()}@example.com",
                            "password": "disposable-twelve-character-password",
                            "display_name": "Fixture owner",
                        },
                    )
                    assert response.status_code == 201, response.text
                    user_id = UUID(response.json()["id"])
                    workspace_id = UUID(response.json()["workspace"]["id"])
                    headers = {"Origin": settings.web_origin}
                    generic = approved_generic_connectors(settings)[0]
                    async with sessions() as database:
                        definition = ConnectorDefinition(
                            connector_key=generic.connector_key,
                            version=generic.manifest.version,
                            display_name=generic.config.display_name,
                            connector_class="GENERIC_API",
                            trust_level="WORKSPACE_PRIVATE",
                            manifest=generic.manifest.model_dump(mode="json", by_alias=True),
                        )
                        database.add(definition)
                        await database.flush()
                        definition_id = definition.id
                        connection = ConnectorConnection(
                            connector_definition_id=definition.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            provider="fixture_subscriptions",
                            external_account_id=f"configured:{user_id}:{generic.connector_key}",
                            authorized_capabilities=["subscription.read"],
                            provider_capabilities=["subscription.read"],
                            config=generic.config.model_dump(mode="json"),
                        )
                        database.add(connection)
                        await database.flush()
                        await SecretBroker(settings).store(
                            database,
                            {"API_TOKEN": "disposable-subscription-secret"},
                            connection_id=connection.id,
                            workspace_id=workspace_id,
                            user_id=user_id,
                        )
                        await ensure_provenance_connection(database, connection)
                        await database.commit()
                        connection_id = connection.id
                    created = await api.post(
                        "/api/v1/subscriptions",
                        headers=headers,
                        json={
                            "name": "Disposable subscription",
                            "billing_amount": "10",
                            "billing_currency": "USD",
                            "billing_interval": "MONTH",
                            "auto_renew": True,
                            "request_id": str(uuid4()),
                        },
                    )
                    assert created.status_code == 201, created.text
                    obligation_id = created.json()["id"]
                    grant = await api.post(
                        f"/api/v1/connectors/generic-rest-api/connections/{connection_id}/cancellation-grant",
                        headers=headers,
                        json={
                            "profile_id": identifier,
                            "confirmed": True,
                            "request_id": str(uuid4()),
                        },
                    )
                    assert grant.status_code == 200, grant.text
                    binding = await api.post(
                        f"/api/v1/subscriptions/{obligation_id}/cancellation-binding",
                        headers=headers,
                        json={
                            "profile_id": identifier,
                            "connection_id": str(connection_id),
                            "external_resource_id": "subscription-1",
                            "confirmed": True,
                            "request_id": str(uuid4()),
                        },
                    )
                    assert binding.status_code == 200, binding.text
                    prepared = await api.post(
                        f"/api/v1/subscriptions/{obligation_id}/cancel",
                        headers=headers,
                        json={"request_id": str(uuid4())},
                    )
                    assert prepared.status_code == 200, prepared.text
                    attempt = prepared.json()
                    assert provider.writes == 0
                    assert attempt["preview"]["economics"]["billing_amount"] == "12.34"
                    confirm_url = f"/api/v1/cancellations/{attempt['id']}/confirm"
                    wrong = await api.post(
                        confirm_url,
                        headers=headers,
                        json={"request_id": str(uuid4()), "preview_hash": "0" * 64},
                    )
                    assert wrong.status_code == 409
                    confirmed = await api.post(
                        confirm_url,
                        headers=headers,
                        json={"request_id": str(uuid4()), "preview_hash": attempt["payload_hash"]},
                    )
                    assert confirmed.status_code == 200, confirmed.text
                    workflow_id = f"subscription-cancel:{workspace_id}:{user_id}:{attempt['id']}"
                    result = await asyncio.wait_for(
                        client.get_workflow_handle(workflow_id).result(), timeout=90
                    )
                    assert result == "VERIFIED_CANCELLED", result
                    final = await api.get(f"/api/v1/subscriptions/{obligation_id}")
                    assert final.json()["status"] == "CANCELLED", final.text
                    assert provider.writes == 1 and provider.post_write_reads >= 1
                    metric = await api.get("/api/v1/subscriptions/prevented-renewals")
                    assert metric.json()["count"] == 1, metric.text
                    assert metric.json()["currency_totals"][0]["renewal_amount"] == "12.34"
                    replay = await api.post(
                        confirm_url,
                        headers=headers,
                        json={"request_id": str(uuid4()), "preview_hash": attempt["payload_hash"]},
                    )
                    assert replay.status_code == 409 and provider.writes == 1
                    async with sessions() as database:
                        evidence = await database.scalar(
                            select(CancellationEvidence).where(
                                CancellationEvidence.cancellation_attempt_id == UUID(attempt["id"])
                            )
                        )
                        assert (
                            evidence is not None
                            and evidence.verification_status == "VERIFIED_CANCELLED"
                        )
                        row = await database.get(RecurringObligation, UUID(obligation_id))
                        assert row is not None and row.auto_renew is False
        print(
            json.dumps(
                {
                    "passed": True,
                    "mode": "real_postgresql_temporal_disposable_provider",
                    "writes": provider.writes,
                    "independent_reads": provider.post_write_reads,
                    "false_confirmations": 0,
                }
            )
        )
    finally:
        async with sessions() as database:
            if workspace_id:
                await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            if user_id:
                await database.execute(delete(User).where(User.id == user_id))
            if definition_id:
                await database.execute(
                    delete(ConnectorDefinition).where(ConnectorDefinition.id == definition_id)
                )
            await database.commit()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
