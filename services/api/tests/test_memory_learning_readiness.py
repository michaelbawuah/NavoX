"""Native status keeps the browser's existing owner lock and private transaction."""

import asyncio
from decimal import Decimal

import pytest
from pydantic import SecretStr
from test_conversation_memory import enable
from test_conversation_memory import history_env as shared_history_fixture

from navox.api import memory_learning
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.usage import update_policy
from navox.vox.usage_schemas import ModelPricing, UsagePolicy

history_env = shared_history_fixture


@pytest.mark.asyncio
async def test_native_ready_status_reuses_the_browser_owner_transaction(
    history_env, tmp_path, monkeypatch
):
    env = history_env
    await enable(env)
    config = VoxInferenceConfig()
    path = tmp_path / "synthetic-inference.json"
    path.write_text(config.model_dump_json())
    env.settings.student_vox_enabled = True
    env.settings.student_vox_media_enabled = True
    env.settings.student_vox_worker_qualified = True
    env.settings.student_vox_livekit_cloud_qualified = True
    env.settings.student_vox_inference_config_path = str(path)
    env.settings.openai_api_key = SecretStr("synthetic-unused-credential")
    assert env.settings.ai_provider == "disabled"
    assert env.settings.memory_semantic_learning_enabled is False
    async with env.factory() as database:
        await update_policy(
            database,
            UsagePolicy(
                model_pricing=[
                    ModelPricing(
                        provider=config.realtime.provider,
                        model=config.realtime.model,
                        version="synthetic-readiness.v1",
                        input_text=Decimal("1"),
                        output_text=Decimal("1"),
                    )
                ]
            ),
            expected_revision=0,
        )
        await database.commit()
    # In PostgreSQL the route dependency already holds User FOR UPDATE.
    # Readiness must not try to obtain that owner lock in a separate session.
    # Keeping this factory override also makes any accidental nested read hit
    # the real fixture database, rather than an unrelated default database.
    monkeypatch.setattr(memory_learning, "get_session_factory", lambda: env.factory)
    response = await asyncio.wait_for(env.client.get("/api/v1/memory/learning"), timeout=5)
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["configured"] is True
    assert response.json()["status_reason"] == "ready"
    assert response.json()["pending"] == []
    assert "no-store" in response.headers["cache-control"]
