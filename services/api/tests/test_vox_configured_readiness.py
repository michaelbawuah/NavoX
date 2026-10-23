"""Configured voice admission is read-only and never calls a provider."""

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_vox_calls import vox_env as shared_vox_fixture

from navox.vox import readiness
from navox.vox.inference_config import VoxInferenceConfig
from navox.vox.provider import MediaUnavailable
from navox.vox.service import VoxError

vox_env = shared_vox_fixture


def configure(env, tmp_path):
    path = tmp_path / "voice-config.json"
    path.write_text(VoxInferenceConfig().model_dump_json())
    path.chmod(0o600)
    env.settings.student_vox_inference_config_path = str(path)
    env.settings.openai_api_key = SecretStr("synthetic-test-key-not-a-real-secret")
    return path


@pytest.mark.asyncio
async def test_configured_preflight_resolves_routes_and_budget_without_legacy_gateway(
    vox_env,
    tmp_path,
    monkeypatch,
):
    env = vox_env
    configure(env, tmp_path)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Configured preflight must not construct or call legacy providers")

    monkeypatch.setattr(readiness, "build_runtime", forbidden)
    await readiness.require_voice_models(
        env.settings,
        env.factory,
        workspace_id=env.workspace_id,
        user_id=env.user_id,
    )


@pytest.mark.asyncio
async def test_missing_fallback_credential_and_bad_config_do_not_fall_through(
    vox_env,
    tmp_path,
    monkeypatch,
):
    env = vox_env
    path = configure(env, tmp_path)
    config = json.loads(path.read_text())
    config["tts"]["api_key_env"] = "UNSET_NAVOX_SYNTHETIC_TTS_KEY"
    path.write_text(json.dumps(config))
    monkeypatch.delenv("UNSET_NAVOX_SYNTHETIC_TTS_KEY", raising=False)
    with pytest.raises(MediaUnavailable, match="not configured"):
        await readiness.require_voice_models(
            env.settings,
            env.factory,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
        )
    path.write_text('{"bad": "NEVER_ECHO_CONFIGURATION"}')
    with pytest.raises(MediaUnavailable) as caught:
        await readiness.require_voice_models(
            env.settings,
            env.factory,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
        )
    assert "NEVER_ECHO" not in str(caught.value)


@pytest.mark.asyncio
async def test_exhausted_configured_budget_rejects_admission(vox_env, tmp_path, monkeypatch):
    env = vox_env
    configure(env, tmp_path)

    async def exhausted(*args, **kwargs):
        return SimpleNamespace(blocked=True)

    monkeypatch.setattr(readiness, "budget_status", exhausted)
    with pytest.raises(VoxError) as caught:
        await readiness.require_voice_models(
            env.settings,
            env.factory,
            workspace_id=env.workspace_id,
            user_id=env.user_id,
        )
    assert caught.value.status_code == 429
