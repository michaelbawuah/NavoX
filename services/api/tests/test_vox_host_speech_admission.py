"""Host speech uses only the existing admitted operator-owned cascade route."""

import pytest
from test_vox_inference import settings

from navox.core.settings import Settings
from navox.vox import worker_livekit
from navox.vox.inference_config import ProviderRoute, VoxInferenceConfig
from navox.vox.worker_livekit import host_speech_available


def test_realtime_only_default_tts_does_not_confer_host_speech_authority():
    assert not host_speech_available(VoxInferenceConfig(fallback_enabled=False), settings())


@pytest.mark.parametrize("mode", ["realtime", "cascade"])
def test_existing_admitted_cascade_route_with_credential_allows_host_speech(mode):
    assert host_speech_available(VoxInferenceConfig(mode=mode), settings())


def test_missing_selected_tts_credential_withholds_host_speech(monkeypatch):
    monkeypatch.delenv("NAVOX_TEST_MISSING_TTS_KEY", raising=False)
    config = VoxInferenceConfig(
        tts=ProviderRoute(
            model="fixture-tts", voice="fixture", api_key_env="NAVOX_TEST_MISSING_TTS_KEY"
        )
    )
    assert not host_speech_available(config, settings())


@pytest.mark.parametrize("configured", [True, False])
def test_native_review_metadata_permission_is_independent_of_optional_web(monkeypatch, configured):
    class Server:
        def __init__(self, **options):
            self.options = options

        def rtc_session(self, entrypoint, **options):
            self.entrypoint = entrypoint

    monkeypatch.setattr(worker_livekit.agents, "AgentServer", Server)
    inference = VoxInferenceConfig(web_search=None) if configured else None
    monkeypatch.setattr(worker_livekit, "load_inference_config", lambda _: inference)
    server = worker_livekit.build_server(
        Settings(
            _env_file=None,
            student_vox_enabled=True,
            student_vox_media_enabled=True,
            student_vox_worker_qualified=True,
            student_vox_livekit_cloud_qualified=True,
            student_vox_livekit_url="wss://test.livekit.cloud",
            student_vox_livekit_api_key="synthetic-key",
            student_vox_livekit_api_secret="synthetic-secret-" * 2,
        )
    )
    permissions = server.options["permissions"]
    assert permissions.can_publish_data is configured
    assert permissions.can_update_metadata is False
    assert server.entrypoint is worker_livekit.job_entrypoint
