import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr

from navox.ai.errors import AIProviderError
from navox.ai.gateway import AIGateway, StructuredOutputResponse
from navox.ai.openai_provider import OpenAIResponsesProvider
from navox.evaluation.intelligence_smoke import CASES, OfflineSmokeProvider, main, run_smoke


def test_dry_run_and_offline_modes_never_load_private_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    settings = Mock(side_effect=AssertionError("Private settings must not be read"))
    network = Mock(side_effect=AssertionError("Network must not be accessed"))
    monkeypatch.setattr("navox.evaluation.intelligence_smoke.Settings", settings)
    monkeypatch.setattr(httpx, "AsyncClient", network)
    assert main([]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["mode"] == "dry_run" and dry_run["executed_cases"] == 0
    assert dry_run["passed"] is None
    output = tmp_path / "report.json"
    assert main(["--offline", "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text()) == report
    assert report["passed"] is True
    assert report["executed_cases"] == len(CASES)
    assert report["failed_case_ids"] == []
    assert report["production_quality_measured"] is False
    assert report["live_google_chain_verified"] is False
    assert report["mode"] == "offline_fixture"
    settings.assert_not_called()
    network.assert_not_called()


def test_invalid_live_configuration_is_sanitized_and_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "navox.evaluation.intelligence_smoke.Settings",
        Mock(side_effect=ValueError("private-key-and-private-source")),
    )
    assert main(["--live"]) == 2
    text = capsys.readouterr().out
    assert "private-key-and-private-source" not in text
    report = json.loads(text)
    assert report["error"] == "provider_configuration_unavailable"
    assert report["passed"] is False and report["executed_cases"] == 0
    assert report["skipped_cases"] == len(CASES)


@pytest.mark.parametrize("mode", ["--live", "--offline"])
def test_case_option_executes_only_the_selected_source(
    mode: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = OfflineSmokeProvider()
    calls: list[str] = []
    original_generate = provider.generate_json

    async def generate(**kwargs: Any) -> StructuredOutputResponse:
        calls.append(json.loads(kwargs["input_text"])["external_id"])
        return await original_generate(**kwargs)

    monkeypatch.setattr(provider, "generate_json", generate)
    monkeypatch.setattr(
        "navox.evaluation.intelligence_smoke.OfflineSmokeProvider", lambda: provider
    )
    monkeypatch.setattr("navox.evaluation.intelligence_smoke.Settings", lambda: None)
    monkeypatch.setattr(
        "navox.evaluation.intelligence_smoke.build_ai_gateway", lambda _: AIGateway(provider)
    )
    assert main([mode, "--case", "explicit-promise"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls == ["explicit-promise"]
    assert report["planned_cases"] == report["executed_cases"] == report["passed_cases"] == 1
    assert report["skipped_cases"] == report["failed_cases"] == 0
    assert report["cases"][0]["id"] == "explicit-promise"


def test_case_option_rejects_unknown_sources(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--offline", "--case", "user-supplied-source"])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_provider_failure_reports_safe_category_and_skips_remaining_requests() -> None:
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "invalid_api_key",
                    "message": "private-provider-body secret-key https://private.example/source",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = AIGateway(
            OpenAIResponsesProvider(api_key=SecretStr("secret-key"), model="fixture", client=client)
        )
        report = await run_smoke(gateway, mode="live_model_smoke")
    assert len(requests) == report["executed_cases"] == report["failed_cases"] == 1
    assert report["planned_cases"] == len(CASES)
    assert report["passed_cases"] == 0
    assert report["passed"] is False
    assert report["failed_case_ids"] == [CASES[0].id]
    assert report["skipped_cases"] == len(CASES) - 1
    assert report["skipped_case_ids"] == [case.id for case in CASES[1:]]
    assert report["cases"][0]["provider_error"] == {
        "code": "authentication_failed",
        "http_status": 401,
    }
    assert "OPENAI_API_KEY" in report["next_step"]
    serialized = json.dumps(report)
    for sensitive in ("private-provider-body", "secret-key", "private.example", "invalid_api_key"):
        assert sensitive not in serialized


@pytest.mark.asyncio
async def test_unknown_provider_diagnostic_cannot_expose_untrusted_text() -> None:
    class UnsafeErrorProvider:
        async def generate_json(self, **kwargs: Any) -> StructuredOutputResponse:
            raise AIProviderError(
                "private exception content",
                code="private provider code",
                http_status=999,
            )

    report = await run_smoke(AIGateway(UnsafeErrorProvider()), mode="live_model_smoke")
    assert report["executed_cases"] == 1
    assert report["cases"][0]["provider_error"] == {"code": "provider_error"}
    assert "private" not in json.dumps(report)


@pytest.mark.asyncio
async def test_timeout_skips_remaining_cases() -> None:
    class TimeoutProvider:
        async def generate_json(self, **kwargs: Any) -> StructuredOutputResponse:
            raise TimeoutError("private network details")

    report = await run_smoke(AIGateway(TimeoutProvider()), mode="live_model_smoke")
    assert report["executed_cases"] == report["failed_cases"] == 1
    assert report["skipped_cases"] == len(CASES) - 1
    assert report["cases"][0]["reason"] == "provider_timeout"
    assert report["cases"][0]["provider_error"] == {"code": "transport_error"}
    assert "private network details" not in json.dumps(report)


@pytest.mark.asyncio
async def test_provider_http_path_is_real_but_reports_never_include_payloads_or_keys() -> None:
    reference = OfflineSmokeProvider()
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer test-secret-do-not-report"
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        response = await reference.generate_json(
            schema_name="test", schema={}, instructions="test", input_text=payload["input"]
        )
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "model-metadata-not-reported",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(response.data)}],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        gateway = AIGateway(
            OpenAIResponsesProvider(
                api_key=SecretStr("test-secret-do-not-report"), model="fixture", client=client
            )
        )
        report = await run_smoke(gateway, mode="live_model_smoke")
    assert len(requests) == len(CASES)
    assert report["passed"] is True
    serialized = json.dumps(report)
    assert "test-secret-do-not-report" not in serialized
    assert "model-metadata-not-reported" not in serialized
    assert all(case.content not in serialized for case in CASES)


class BrokenProvider:
    def __init__(self, output: dict[str, Any] | None = None) -> None:
        self.output = output

    async def generate_json(self, **kwargs: Any) -> StructuredOutputResponse:
        if self.output is None:
            raise RuntimeError("private-payload-must-stay-private")
        return StructuredOutputResponse(self.output, "fixture", "fixture")


@pytest.mark.asyncio
async def test_provider_errors_fail_every_case_including_the_adversarial_case() -> None:
    report = await run_smoke(AIGateway(BrokenProvider()), mode="offline_fixture")
    assert report["passed"] is False
    assert report["failed_case_ids"] == [case.id for case in CASES]
    assert all(case["reason"] == "runtime_error" for case in report["cases"])
    assert "private-payload-must-stay-private" not in json.dumps(report)


@pytest.mark.asyncio
async def test_empty_valid_extraction_does_not_pass_positive_cases() -> None:
    report = await run_smoke(AIGateway(BrokenProvider({})), mode="offline_fixture")
    assert report["passed"] is False
    assert report["failed_case_ids"] == [case.id for case in CASES if case.accepted_types]


@pytest.mark.asyncio
async def test_invalid_proposal_only_counts_as_safe_rejection_for_adversarial_input() -> None:
    report = await run_smoke(
        AIGateway(BrokenProvider({"permission": "private-forged-value"})), mode="offline_fixture"
    )
    assert report["passed"] is False
    assert report["passed_cases"] == 1
    assert report["cases"][-1]["reason"] == "untrusted_proposal_rejected"
    assert "private-forged-value" not in json.dumps(report)
