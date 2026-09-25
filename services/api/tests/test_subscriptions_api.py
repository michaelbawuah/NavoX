"""Exercise actual authenticated API contracts, origin protection and durable commands."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from navox.db.models import Commitment


async def create(env, **overrides):
    response = await env.client.post(
        "/api/v1/subscriptions",
        headers=env.headers,
        json={
            "name": "Example",
            "billing_amount": "119.99",
            "billing_currency": "USD",
            "billing_interval": "YEAR",
            "request_id": str(uuid4()),
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_manual_trial_reaches_existing_today_and_keep_suppresses(subscription_env):
    env = subscription_env
    row = await create(
        env, status="TRIAL", trial_ends_at=(datetime.now(UTC) + timedelta(days=2)).isoformat()
    )
    assert row["attention_reasons"] == ["trial.ending"]
    cards = list(await env.database.scalars(select(Commitment)))
    assert len(cards) == 1
    assert cards[0].intelligence_metadata["subscription_id"] == row["id"]
    response = await env.client.post(
        f"/api/v1/subscriptions/{row['id']}/keep", json={}, headers=env.headers
    )
    assert response.status_code == 200
    assert response.json()["attention_reasons"] == []
    changed = await env.client.patch(
        f"/api/v1/subscriptions/{row['id']}", json={"billing_amount": "149.99"}, headers=env.headers
    )
    assert changed.status_code == 200
    assert "obligation.price_changed" in changed.json()["attention_reasons"]
    evidence = await env.client.get(f"/api/v1/subscriptions/{row['id']}/evidence")
    assert len(evidence.json()) == 2


@pytest.mark.asyncio
async def test_routes_preserve_decimal_and_separate_currencies(subscription_env):
    env = subscription_env
    await create(env)
    await create(
        env, name="EUR plan", billing_currency="EUR", billing_amount="12", billing_interval="MONTH"
    )
    summary = (await env.client.get("/api/v1/subscriptions/summary")).json()
    assert summary["coverage"] == "INCOMPLETE"
    assert {row["currency"] for row in summary["currency_totals"]} == {"USD", "EUR"}
    query = await env.client.post(
        "/api/v1/subscriptions/query", json={"intent": "SEARCH", "currency": "EUR", "text": "plan"}
    )
    assert query.status_code == 200
    assert len(query.json()["subscriptions"]) == 1
    assert (await env.client.get("/api/v1/subscriptions/prevented-renewals")).json()["count"] == 0


@pytest.mark.asyncio
async def test_missing_capability_is_visible_unsupported_and_cannot_be_confirmed(subscription_env):
    env = subscription_env
    row = await create(env)
    response = await env.client.post(
        f"/api/v1/subscriptions/{row['id']}/cancel",
        headers=env.headers,
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert attempt["method"] == "UNSUPPORTED"
    assert attempt["verification_status"] == "NOT_VERIFIED"
    denied = await env.client.post(
        f"/api/v1/cancellations/{attempt['id']}/confirm",
        headers=env.headers,
        json={"request_id": str(uuid4()), "preview_hash": attempt["payload_hash"]},
    )
    assert denied.status_code == 409
    aborted = await env.client.post(
        f"/api/v1/cancellations/{attempt['id']}/abort", headers=env.headers
    )
    assert aborted.status_code == 200
    assert aborted.json()["status"] == "ABORTED"


@pytest.mark.asyncio
async def test_auth_origin_and_manual_success_bypass_are_rejected(subscription_env):
    env = subscription_env
    async with AsyncClient(
        transport=ASGITransport(app=env.app), base_url="http://testserver"
    ) as outsider:
        assert (await outsider.get("/api/v1/subscriptions")).status_code == 401
    rejected = await env.client.post(
        "/api/v1/subscriptions", headers={"Origin": "https://evil.example"}, json={"name": "X"}
    )
    assert rejected.status_code == 403
    row = await create(env)
    forged = await env.client.patch(
        f"/api/v1/subscriptions/{row['id']}", headers=env.headers, json={"status": "CANCELLED"}
    )
    assert forged.status_code == 422
    assert (await env.client.get(f"/api/v1/subscriptions/{uuid4()}")).status_code == 404
    preflight = await env.client.options(
        f"/api/v1/subscriptions/{row['id']}",
        headers={**env.headers, "Access-Control-Request-Method": "PATCH"},
    )
    assert preflight.status_code == 200
