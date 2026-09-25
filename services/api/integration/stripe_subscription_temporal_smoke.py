"""Native Stripe adapter through real API, PostgreSQL and Temporal; HTTPS is a fixture."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.client import Client
from temporalio.worker import Worker

from integration.subscription_temporal_smoke import IntegrationSettings
from navox.api.main import create_app
from navox.connectors import stripe_sandbox as stripe
from navox.core.settings import get_settings
from navox.db.models import User, Workspace
from navox.db.session import get_database_session
from navox.subscriptions.activities import (
    cancellation_state_activity,
    execute_cancellation_activity,
    verify_cancellation_activity,
)
from navox.workflows.subscriptions import CancelSubscriptionWorkflow, VerifyCancellationWorkflow

TOKEN = "rk_test_disposableComposeFixture123"
SUBSCRIPTION = "sub_DisposableCompose"


class DisposableStripe:
    def __init__(self):
        self.writes = self.independent_reads = 0
        self.body = {
            "id": SUBSCRIPTION,
            "object": "subscription",
            "livemode": False,
            "customer": "cus_DisposableCompose",
            "status": "active",
            "currency": "usd",
            "collection_method": "charge_automatically",
            "automatic_tax": {"enabled": False},
            "cancel_at_period_end": False,
            "items": {
                "has_more": False,
                "data": [
                    {
                        "quantity": 1,
                        "current_period_end": int(
                            (datetime.now(UTC) + timedelta(days=30)).timestamp()
                        ),
                        "price": {
                            "id": "price_DisposableCompose",
                            "object": "price",
                            "product": "prod_DisposableCompose",
                            "livemode": False,
                            "currency": "usd",
                            "unit_amount": 1000,
                            "unit_amount_decimal": "1000",
                            "billing_scheme": "per_unit",
                            "type": "recurring",
                            "recurring": {
                                "interval": "month",
                                "interval_count": 1,
                                "usage_type": "licensed",
                            },
                        },
                    }
                ],
            },
        }

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.stripe.com"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["stripe-version"] == stripe.API_VERSION
        assert "stripe-account" not in request.headers
        assert "idempotency-key" not in request.headers and "if-match" not in request.headers
        if request.url.path == "/v1/account":
            assert request.method == "GET"
            return httpx.Response(200, json={"object": "account", "id": "acct_DisposableCompose"})
        assert request.url.path == f"/v1/subscriptions/{SUBSCRIPTION}"
        if request.method == "DELETE":
            assert dict(request.url.params) == {"invoice_now": "false", "prorate": "false"}
            self.writes += 1
            assert self.writes == 1
            self.body.update(status="canceled", ended_at=int(datetime.now(UTC).timestamp()))
        else:
            assert request.method == "GET"
            self.independent_reads += int(self.writes > 0)
        return httpx.Response(200, json=self.body)


async def run() -> None:
    settings = IntegrationSettings(
        app_environment="test",
        ai_provider="disabled",
        stripe_sandbox_enabled=True,
        temporal_task_queue=f"navox-stripe-ci-{uuid4()}",
        connector_secret_encryption_key=Fernet.generate_key().decode(),
    )
    assert settings.database_url.startswith("postgresql+asyncpg://")
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    provider = DisposableStripe()
    app = create_app()

    async def database_override():
        async with sessions() as database:
            yield database

    app.dependency_overrides[get_database_session] = database_override
    app.dependency_overrides[get_settings] = lambda: settings
    client = await Client.connect(settings.temporal_target)
    user_id = workspace_id = None
    try:
        with (
            patch("navox.subscriptions.activities.get_settings", return_value=settings),
            patch("navox.subscriptions.activities.get_session_factory", return_value=sessions),
            patch(
                "navox.connectors.stripe_sandbox.ApprovedHTTPSTransport",
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
                    registered = await api.post(
                        "/api/v1/auth/register",
                        json={
                            "email": f"stripe-ci-{uuid4()}@example.com",
                            "password": "disposable-twelve-character-password",
                            "display_name": "Stripe fixture owner",
                        },
                    )
                    assert registered.status_code == 201, registered.text
                    user_id = UUID(registered.json()["id"])
                    workspace_id = UUID(registered.json()["workspace"]["id"])
                    headers = {"Origin": settings.web_origin}
                    connected = await api.post(
                        "/api/v1/subscriptions/stripe-sandbox",
                        headers=headers,
                        json={
                            "request_id": str(uuid4()),
                            "subscription_id": SUBSCRIPTION,
                            "token": TOKEN,
                            "confirmed": True,
                        },
                    )
                    assert connected.status_code == 201, connected.text
                    obligation_id = connected.json()["id"]
                    summary = await api.get("/api/v1/subscriptions/summary")
                    assert summary.json()["currency_totals"] == [], summary.text
                    prepared = await api.post(
                        f"/api/v1/subscriptions/{obligation_id}/cancel",
                        headers=headers,
                        json={"request_id": str(uuid4())},
                    )
                    assert prepared.status_code == 200, prepared.text
                    attempt = prepared.json()
                    assert attempt["preview"]["payload"]["sandbox"] is True
                    assert attempt["preview"]["economics"]["billing_amount"] == "10"
                    assert provider.writes == 0
                    confirmed = await api.post(
                        f"/api/v1/cancellations/{attempt['id']}/confirm",
                        headers=headers,
                        json={"request_id": str(uuid4()), "preview_hash": attempt["payload_hash"]},
                    )
                    assert confirmed.status_code == 200, confirmed.text
                    result = await asyncio.wait_for(
                        client.get_workflow_handle(
                            f"subscription-cancel:{workspace_id}:{user_id}:{attempt['id']}"
                        ).result(),
                        timeout=90,
                    )
                    assert result == "VERIFIED_CANCELLED", result
                    final = await api.get(f"/api/v1/subscriptions/{obligation_id}")
                    assert final.json()["status"] == "CANCELLED", final.text
                    assert provider.writes == 1 and provider.independent_reads >= 1
                    metric = await api.get("/api/v1/subscriptions/prevented-renewals")
                    assert metric.json()["count"] == 0, metric.text
        print(
            json.dumps(
                {
                    "passed": True,
                    "mode": "native_stripe_fixture_real_postgresql_temporal",
                    "writes": provider.writes,
                    "independent_reads": provider.independent_reads,
                    "sandbox_prevented_renewals": 0,
                }
            )
        )
    finally:
        async with sessions() as database:
            if workspace_id:
                await database.execute(delete(Workspace).where(Workspace.id == workspace_id))
            if user_id:
                await database.execute(delete(User).where(User.id == user_id))
            await database.commit()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
