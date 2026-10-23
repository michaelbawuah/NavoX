"""HTTP scope/config tests; no real costs or provider acceptance are claimed."""

from uuid import uuid4

import pytest
from test_vox_calls import start
from test_vox_calls import vox_env as shared_vox_fixture

from navox.vox.usage_schemas import UsagePolicy

vox_env = shared_vox_fixture
ROOT = "/api/v1/vox/usage"


@pytest.mark.asyncio
async def test_operator_is_deny_by_default_even_for_personal_workspace_owner(vox_env):
    env = vox_env
    body = {"revision": 0, "policy": UsagePolicy().model_dump(mode="json")}
    assert (await env.client.get(f"{ROOT}/policy")).status_code == 403
    assert (await env.client.get(f"{ROOT}/daily")).status_code == 403
    assert (await env.client.put(f"{ROOT}/policy", json=body)).status_code == 403
    response = await env.client.get(f"{ROOT}/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["remaining_voice_ms"] == 60 * 60_000
    assert response.json()["remaining_tokens"] == 300_000


@pytest.mark.asyncio
async def test_operator_can_change_persistent_budgets_without_restart_and_stale_save_fails(vox_env):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    initial = (await env.client.get(f"{ROOT}/policy")).json()
    policy = initial["policy"]
    assert policy["daily_voice_minutes"] == 60 and policy["monthly_voice_minutes"] == 600
    assert policy["daily_tokens"] == 300_000 and policy["monthly_tokens"] == 3_000_000
    policy["daily_voice_minutes"] = 45
    policy["daily_tokens"] = 200_000
    saved = await env.client.put(f"{ROOT}/policy", json={**initial, "policy": policy})
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert (await env.client.get(f"{ROOT}/policy")).json() == saved.json()
    status = (await env.client.get(f"{ROOT}/status")).json()
    assert status["remaining_voice_ms"] == 45 * 60_000
    assert status["remaining_tokens"] == 200_000
    assert (await env.client.put(f"{ROOT}/policy", json=initial)).status_code == 409
    totals = await env.client.get(f"{ROOT}/daily")
    assert totals.status_code == 200 and totals.json()["students"][0]["user_id"] == str(env.user_id)
    assert env.credentials["email"] not in totals.text


@pytest.mark.asyncio
async def test_cost_reads_do_not_claim_zero_or_expose_other_calls_and_mutations_require_origin(
    vox_env,
):
    env = vox_env
    row, _ = await start(env)
    response = await env.client.get(f"{ROOT}/calls/{row['id']}")
    assert response.status_code == 200
    assert response.json()["metering_started"] is False
    assert response.json()["totals"]["estimated_cost_usd"] is None
    assert (await env.client.get(f"{ROOT}/calls/{uuid4()}")).status_code == 404
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    env.client.headers.pop("Origin")
    response = await env.client.put(
        f"{ROOT}/policy",
        json={
            "revision": 0,
            "policy": UsagePolicy().model_dump(mode="json"),
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cost_routes_reject_account_switch_and_invalid_config_without_echo(vox_env):
    env = vox_env
    env.settings.student_vox_operator_user_ids = [str(env.user_id)]
    invalid = await env.client.put(
        f"{ROOT}/policy",
        json={
            "revision": 0,
            "policy": {"daily_tokens": -1, "private_payload": "NEVER_ECHO_ME"},
        },
    )
    assert invalid.status_code == 422 and "NEVER_ECHO_ME" not in invalid.text
    env.client.headers["X-NavoX-Account"] = f"{env.workspace_id}:{uuid4()}"
    assert (await env.client.get(f"{ROOT}/daily")).status_code == 409
    assert (await env.client.get(f"{ROOT}/status")).status_code == 409
