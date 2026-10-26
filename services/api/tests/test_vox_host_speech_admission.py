"""Host speech uses only the existing admitted operator-owned cascade route."""

import pytest
from test_vox_inference import settings

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
