"""Real NavoX routes/approvals/persistence with a disposable Stripe HTTP boundary."""

import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from navox.connectors import stripe_sandbox as stripe
from navox.connectors.contracts import ConnectorActionRequest, ConnectorRuntimeError
from navox.connectors.outbound import ApprovedHTTPSTransport
from navox.connectors.secrets import SecretBroker
from navox.connectors.stripe_subscription import StripeSandboxCancellation
from navox.connectors.subscription_cancellation import resolve_subscription_cancellation
from navox.db.models import (
    AuditEvent,
    ConnectorConnection,
    RecurringObligation,
)
from navox.subscriptions.cancellation import execute_cancellation, verify_cancellation
from navox.subscriptions.cancellation_contracts import CancellationPreview

TOKEN = "rk_test_disposableStripeFixture123456"
SUBSCRIPTION = "sub_Disposable123456"
ACCOUNT = "acct_Disposable123456"
CUSTOMER = "cus_Disposable123456"


class StripeProvider:
    def __init__(self):
        self.account = {"id": ACCOUNT, "object": "account"}
        self.body = {
            "id": SUBSCRIPTION,
            "object": "subscription",
            "livemode": False,
            "customer": CUSTOMER,
            "status": "active",
            "currency": "usd",
            "collection_method": "charge_automatically",
            "automatic_tax": {"enabled": False},
            "cancel_at_period_end": False,
            "cancel_at": None,
            "canceled_at": None,
            "ended_at": None,
            "discounts": [],
            "default_tax_rates": [],
            "items": {
                "has_more": False,
                "data": [
                    {
                        "quantity": 1,
                        "current_period_end": int(
                            (datetime.now(UTC) + timedelta(days=30)).timestamp()
                        ),
                        "price": {
                            "id": "price_Disposable",
                            "product": "prod_Disposable",
                            "object": "price",
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
                        "tax_rates": [],
                        "discounts": [],
                    }
                ],
            },
        }
        self.requests = []
        self.writes = []
        self.write_status = 200
        self.timeout = False
        self.apply_write = True

    def handle(self, request):
        assert request.url.host == "api.stripe.com"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["stripe-version"] == stripe.API_VERSION
        assert "if-match" not in request.headers and "idempotency-key" not in request.headers
        assert "stripe-account" not in request.headers
        self.requests.append((request.method, request.url.path))
        if request.url.path == "/v1/account":
            assert request.method == "GET"
            return httpx.Response(200, json=self.account)
        assert request.url.path == f"/v1/subscriptions/{SUBSCRIPTION}"
        if request.method == "DELETE":
            assert dict(request.url.params) == {"invoice_now": "false", "prorate": "false"}
            self.writes.append(request)
            if self.write_status != 200:
                return httpx.Response(self.write_status, json={"error": {"message": TOKEN}})
            if self.apply_write:
                self.body.update(
                    status="canceled",
                    canceled_at=int(datetime.now(UTC).timestamp()),
                    ended_at=int(datetime.now(UTC).timestamp()),
                )
            if self.timeout:
                raise httpx.ReadTimeout("disposable", request=request)
        return httpx.Response(200, json=self.body)


@pytest.fixture
def stripe_env(subscription_env, monkeypatch):
    env = subscription_env
    env.settings.stripe_sandbox_enabled = True
    env.settings.connector_secret_encryption_key = SecretStr(Fernet.generate_key().decode())
    provider = StripeProvider()
    original_request = stripe.request

    async def bounded_request(token, path, **kwargs):
        kwargs["transport"] = httpx.MockTransport(provider.handle)
        return await original_request(token, path, **kwargs)

    monkeypatch.setattr(stripe, "request", bounded_request)
    env.provider = provider
    return env


async def connected(env, **overrides):
    body = {
        "request_id": str(uuid4()),
        "token": TOKEN,
        "subscription_id": SUBSCRIPTION,
        "confirmed": True,
        **overrides,
    }
    response = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox", headers=env.headers, json=body
    )
    assert response.status_code == 201, response.text
    assert TOKEN not in response.text
    return response.json(), body


async def prepared(env):
    row, _ = await connected(env)
    response = await env.client.post(
        f"/api/v1/subscriptions/{row['id']}/cancel",
        headers=env.headers,
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["method"] == "CONNECTED_PROVIDER_ACTION"
    bad = await env.client.post(
        f"/api/v1/cancellations/{attempt['id']}/confirm",
        headers=env.headers,
        json={"request_id": str(uuid4()), "preview_hash": "0" * 64},
    )
    assert bad.status_code == 409
    accepted = await env.client.post(
        f"/api/v1/cancellations/{attempt['id']}/confirm",
        headers=env.headers,
        json={"request_id": str(uuid4()), "preview_hash": attempt["payload_hash"]},
    )
    assert accepted.status_code == 200, accepted.text
    preview = CancellationPreview.model_validate(attempt["preview"])
    adapter = await resolve_subscription_cancellation(env.database, env.settings, preview.target)
    assert isinstance(adapter, StripeSandboxCancellation)
    return row, UUID(attempt["id"]), adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_exact_approval_independent_verification_and_no_retry(stripe_env, timeout):
    env = stripe_env
    row, attempt_id, adapter = await prepared(env)
    env.provider.timeout = timeout
    assert env.provider.writes == []
    attempt = await execute_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert attempt.status == ("VERIFICATION_PENDING" if timeout else "SUBMITTED")
    assert len(env.provider.writes) == 1
    await execute_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert len(env.provider.writes) == 1
    assert (await env.database.get(RecurringObligation, UUID(row["id"]))).status == "CANCEL_PENDING"
    attempt = await verify_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert attempt.status == "VERIFIED_CANCELLED"
    assert env.provider.requests[-2:] == [
        ("GET", "/v1/account"),
        ("GET", f"/v1/subscriptions/{SUBSCRIPTION}"),
    ]
    assert (await env.client.get("/api/v1/subscriptions/prevented-renewals")).json()["count"] == 0
    assert (await env.client.get("/api/v1/subscriptions/summary")).json()["currency_totals"] == []
    audits = list(await env.database.scalars(select(AuditEvent)))
    assert TOKEN not in json.dumps([audit.event_metadata for audit in audits])


@pytest.mark.asyncio
async def test_changed_price_invalidates_confirmation_without_write(stripe_env):
    env = stripe_env
    _, attempt_id, adapter = await prepared(env)
    env.provider.body["items"]["data"][0]["price"]["unit_amount"] = 2000
    env.provider.body["items"]["data"][0]["price"]["unit_amount_decimal"] = "2000"
    attempt = await execute_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert attempt.status == "FAILED"
    assert env.provider.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "account",
        "customer",
        "live_object",
        "revoked",
        "rotated",
        "paused",
        "disabled",
        "other_owner",
    ],
)
async def test_authority_changes_block_writes(stripe_env, field):
    env = stripe_env
    _, attempt_id, adapter = await prepared(env)
    connection = await env.database.get(ConnectorConnection, adapter.connection_id)
    if field == "account":
        env.provider.account["id"] = "acct_Other"
    elif field == "customer":
        env.provider.body["customer"] = "cus_Other"
    elif field == "live_object":
        env.provider.body["livemode"] = True
    elif field == "revoked":
        connection.authorized_capabilities = [stripe.READ]
        await env.database.commit()
    elif field == "rotated":
        await SecretBroker(env.settings).store(
            env.database,
            {stripe.SECRET_NAME: TOKEN},
            connection_id=connection.id,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
        )
        await env.database.commit()
    elif field == "paused":
        connection.status = "PAUSED"
        await env.database.commit()
    elif field == "disabled":
        env.settings.stripe_sandbox_enabled = False
    elif field == "other_owner":
        adapter.target = adapter.target.model_copy(update={"user_id": uuid4()})
    with pytest.raises(ConnectorRuntimeError):
        await execute_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert env.provider.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [False, True])
async def test_unconfirmed_or_rejected_write_cannot_claim_success(stripe_env, rejected):
    env = stripe_env
    _, attempt_id, adapter = await prepared(env)
    env.provider.apply_write = False
    env.provider.write_status = 403 if rejected else 200
    result = await execute_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert result.status == "VERIFICATION_PENDING"
    result = await verify_cancellation(env.database, attempt_id=attempt_id, capability=adapter)
    assert result.status != "VERIFIED_CANCELLED"
    assert (await env.client.get("/api/v1/subscriptions/prevented-renewals")).json()["count"] == 0


@pytest.mark.asyncio
async def test_onboarding_is_idempotent_and_origin_and_consent_protected(stripe_env):
    env = stripe_env
    row, command = await connected(env)
    replay = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox", headers=env.headers, json=command
    )
    assert replay.status_code == 201 and replay.json()["id"] == row["id"]
    assert len(list(await env.database.scalars(select(ConnectorConnection)))) == 1
    for changed, expected in [
        ({"confirmed": False}, 403),
        ({"confirmed": "true"}, 422),
        ({"name": "Another record"}, 409),
    ]:
        response = await env.client.post(
            "/api/v1/subscriptions/stripe-sandbox", headers=env.headers, json={**command, **changed}
        )
        assert response.status_code == expected
        assert TOKEN not in response.text
    response = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox",
        headers={"Origin": "https://outside.example"},
        json=command,
    )
    assert response.status_code == 403
    assert env.provider.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "sk_live_disposable123",
        "rk_live_disposable123",
        "pk_test_disposable123",
        "rk_test_injected\r\nheader",
    ],
)
async def test_live_publishable_and_malformed_keys_never_leave_server(stripe_env, key):
    env = stripe_env
    response = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox",
        headers=env.headers,
        json={
            "request_id": str(uuid4()),
            "token": key,
            "subscription_id": SUBSCRIPTION,
            "confirmed": True,
        },
    )
    assert response.status_code == 403 and key not in response.text
    assert env.provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", ["missing", "json", "extra", "large"])
async def test_invalid_setup_never_echoes_credentials(stripe_env, malformed):
    env = stripe_env
    command = {"token": TOKEN, "subscription_id": SUBSCRIPTION, "request_id": str(uuid4())}
    if malformed == "extra":
        command.update(confirmed=True, unexpected=TOKEN)
    content = json.dumps(command)
    if malformed == "json":
        content = content[:-1]
    elif malformed == "large":
        content += " " * 20_001
    response = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox",
        headers={**env.headers, "Content-Type": "application/json"},
        content=content,
    )
    assert response.status_code == (413 if malformed == "large" else 422)
    assert TOKEN not in response.text
    assert env.provider.requests == []


@pytest.mark.asyncio
async def test_disabled_and_production_are_not_available(stripe_env):
    env = stripe_env
    env.settings.stripe_sandbox_enabled = False
    assert (await env.client.get("/api/v1/subscriptions/stripe-sandbox")).json() == {
        "enabled": False
    }
    env.settings.stripe_sandbox_enabled = True
    env.settings.app_environment = "production"
    assert (await env.client.get("/api/v1/subscriptions/stripe-sandbox")).json() == {
        "enabled": False
    }
    response = await env.client.post(
        "/api/v1/subscriptions/stripe-sandbox",
        json={
            "request_id": str(uuid4()),
            "token": TOKEN,
            "subscription_id": SUBSCRIPTION,
            "confirmed": True,
        },
    )
    assert response.status_code == 403 and env.provider.requests == []


@pytest.mark.parametrize(
    "change",
    [
        {"livemode": True},
        {"livemode": None},
        {"customer": "cus_invalid/path"},
        {"status": "trialing"},
        {"discounts": ["di_Discount"]},
        {"schedule": "sub_sched_Any"},
        {"cancel_at_period_end": True},
        {"automatic_tax": {"enabled": True}},
        {"currency": "jpy"},
        {"pause_collection": {"behavior": "void"}},
        {"pending_update": {"items": []}},
    ],
)
def test_complex_or_live_subscriptions_fail_closed(change):
    body = copy.deepcopy(StripeProvider().body)
    body.update(change)
    with pytest.raises(ConnectorRuntimeError):
        stripe.parse_subscription(body, SUBSCRIPTION)


@pytest.mark.parametrize("amount", [None, "NaN", "1000.5", "1001", 1000])
def test_fractional_or_inconsistent_prices_are_not_approved(amount):
    body = copy.deepcopy(StripeProvider().body)
    body["items"]["data"][0]["price"]["unit_amount_decimal"] = amount
    with pytest.raises(ConnectorRuntimeError):
        stripe.parse_subscription(body, SUBSCRIPTION)


@pytest.mark.asyncio
async def test_erasure_removes_stripe_derived_records_and_prices(stripe_env):
    env = stripe_env
    row, _ = await connected(env)
    connection = await env.database.scalar(select(ConnectorConnection))
    response = await env.client.request(
        "DELETE",
        f"/api/v1/connections/{connection.id}",
        headers=env.headers,
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    response = await env.client.post(
        f"/api/v1/connections/{connection.id}/delete-data",
        headers=env.headers,
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    assert (await env.client.get(f"/api/v1/subscriptions/{row['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_generic_scheduler_cannot_degrade_explicit_stripe_connection(stripe_env, monkeypatch):
    from navox.connectors import activities

    env = stripe_env
    await connected(env)
    monkeypatch.setattr(activities, "get_settings", lambda: env.settings)
    monkeypatch.setattr(activities, "get_session_factory", lambda: env.factory)
    assert await activities.connector_reconciliation_activity() == []
    connection = await env.database.scalar(select(ConnectorConnection))
    response = await env.client.post(
        f"/api/v1/connections/{connection.id}/sync",
        headers=env.headers,
        json={"request_id": str(uuid4()), "source": "resources"},
    )
    assert response.status_code == 409, response.text
    await env.database.refresh(connection)
    assert connection.status == connection.health_state == "CONNECTED"
    assert env.provider.writes == []


@pytest.mark.asyncio
async def test_sdk_write_cannot_bypass_exact_approval():
    binding = stripe.StripeBinding(
        account_id=ACCOUNT, subscription_id=SUBSCRIPTION, customer_id=CUSTOMER
    )
    sdk = stripe.StripeSandboxConnector(binding.model_dump(mode="json"), None)
    with pytest.raises(ConnectorRuntimeError):
        await sdk.execute(
            ConnectorActionRequest(
                connection_id=uuid4(), workspace_id=uuid4(), capability=stripe.CANCEL, payload={}
            )
        )


@pytest.mark.asyncio
async def test_transport_delete_is_opt_in_and_bound_to_one_path():
    calls = []

    async def resolver(host):
        assert host == "api.stripe.com"
        return ["8.8.8.8"]

    def handler(request):
        calls.append(request)
        assert request.url.host == "8.8.8.8" and request.headers["host"] == "api.stripe.com"
        return httpx.Response(200, json={})

    path = f"/v1/subscriptions/{SUBSCRIPTION}"
    for allowed in [frozenset(), frozenset({path})]:
        async with httpx.AsyncClient(
            transport=ApprovedHTTPSTransport(
                stripe.ORIGIN,
                resolver=resolver,
                transport=httpx.MockTransport(handler),
                allowed_delete_paths=allowed,
            )
        ) as client:
            if allowed:
                assert (await client.delete(stripe.ORIGIN + path)).status_code == 200
            else:
                with pytest.raises(ConnectorRuntimeError):
                    await client.delete(stripe.ORIGIN + path)
            with pytest.raises(ConnectorRuntimeError):
                await client.delete(stripe.ORIGIN + "/v1/subscriptions/sub_Other")
    assert len(calls) == 1
