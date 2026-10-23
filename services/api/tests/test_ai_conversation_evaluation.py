"""Synthetic evaluator tests; no live provider or production evidence is created."""

import json
import os
import socket
import subprocess
import sys
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from navox.ai import manage
from navox.ai.control import evaluation_passes
from navox.ai.conversation import (
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    ConversationAnswer,
    conversation_artifacts,
    conversation_task,
)
from navox.ai.conversation_corpus import (
    CONVERSATION_CASES,
    CORPUS_DIGEST,
    ConversationCase,
    corpus_digest,
)
from navox.ai.conversation_evaluation import (
    EVALUATION_USER,
    EVALUATION_WORKSPACE,
    ConversationCaseReview,
    ConversationReview,
    case_context,
    evaluate_conversation,
)
from navox.ai.foundation.adapter import ProviderResponse
from navox.ai.foundation.contracts import (
    Capability,
    FinishReason,
    JSONDocument,
    Profile,
    Provider,
    ProviderGrant,
    ProviderPolicy,
    Sensitivity,
    TaskType,
    Usage,
    VersionedRef,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest
from navox.ai.foundation.registry import (
    ModelDefinition,
    ModelRef,
    ProfileDefinition,
    RegistrySnapshot,
)
from navox.ai.providers import OpenAIAdapter
from navox.ai.routing import PolicyRules, RoutingTaskScope
from navox.core.settings import Settings
from navox.db.ai_registry import AIEvaluationRun, AIProfileAssignment

CAPABILITIES = frozenset({Capability.TEXT, Capability.STRUCTURED_OUTPUT})


def postgres_test_addresses(dsn, *, allow_database):
    if not allow_database:
        return None
    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname
        if parsed.scheme.split("+")[0] != "postgresql" or host is None:
            return None
        port = parsed.port if parsed.port is not None else 5432
        if port == 0:
            return None
        if host == "localhost":
            # No DNS expansion: only these literal loopback addresses may reach
            # the configured test database port, regardless of resolver output.
            return frozenset({("127.0.0.1", port), ("::1", port)})
        if not ip_address(host).is_loopback:
            return None
        return frozenset({(host, port)})
    except ValueError:
        return None


def guarded_connect(connect, database_addresses):
    def guarded(sock, address):
        if (
            database_addresses is not None
            and isinstance(address, tuple)
            and address[:2] in database_addresses
        ):
            return connect(sock, address)
        pytest.fail("Conversation evaluation tests must not connect to a network")

    return guarded


@pytest.fixture(autouse=True)
def forbid_live_network(monkeypatch, request):
    # Only the ingestion test's database fixture may use its explicitly configured
    # loopback PostgreSQL endpoint. Explicit localhost permits both literal
    # loopback families; numeric hosts remain exact. Providers stay MockTransport-only.
    database_addresses = postgres_test_addresses(
        os.environ.get("NAVOX_CONNECTOR_TEST_DSN", ""),
        allow_database="ai_database" in request.fixturenames,
    )
    for method in ("connect", "connect_ex"):
        monkeypatch.setattr(
            socket.socket,
            method,
            guarded_connect(getattr(socket.socket, method), database_addresses),
        )


@pytest.mark.parametrize(
    ("dsn", "allow_database", "expected"),
    [
        (
            "postgresql+asyncpg://fixture@127.0.0.1:58500/test",
            True,
            frozenset({("127.0.0.1", 58500)}),
        ),
        (
            "postgresql+asyncpg://fixture@[::1]:58500/test",
            True,
            frozenset({("::1", 58500)}),
        ),
        (
            "postgresql+asyncpg://fixture@localhost:58500/test",
            True,
            frozenset({("127.0.0.1", 58500), ("::1", 58500)}),
        ),
        (
            "postgresql+asyncpg://fixture@localhost/test",
            True,
            frozenset({("127.0.0.1", 5432), ("::1", 5432)}),
        ),
        ("postgresql+asyncpg://fixture@127.0.0.1:58500/test", False, None),
        ("postgresql+asyncpg://fixture@localhost:58500/test", False, None),
        ("postgresql+asyncpg://fixture@192.0.2.1:58500/test", True, None),
        ("postgresql+asyncpg://fixture@[2001:db8::1]:58500/test", True, None),
        ("postgresql+asyncpg://fixture@database.example:58500/test", True, None),
        ("postgresql+asyncpg://fixture@localhost.:58500/test", True, None),
        ("postgresql+asyncpg://fixture@localhost:0/test", True, None),
        ("postgresql+asyncpg://fixture@127.0.0.1:0/test", True, None),
        ("postgresql+asyncpg://fixture@127.0.0.1:bad/test", True, None),
        ("https://127.0.0.1:58500/test", True, None),
        ("sqlite+aiosqlite://", True, None),
    ],
)
def test_network_exception_requires_database_fixture_and_explicit_loopback(
    dsn, allow_database, expected
):
    assert postgres_test_addresses(dsn, allow_database=allow_database) == expected


@pytest.mark.parametrize(
    ("database_address", "permitted_address", "other_family_address"),
    [
        (("127.0.0.1", 58500), ("127.0.0.1", 58500), ("::1", 58500, 0, 0)),
        (("::1", 58500), ("::1", 58500, 0, 0), ("127.0.0.1", 58500)),
    ],
)
def test_network_guard_allows_only_exact_numeric_database_endpoint(
    database_address, permitted_address, other_family_address
):
    calls = []

    def connect(sock, address):
        calls.append((sock, address))
        return "test-connect-result"

    guard = guarded_connect(connect, frozenset({database_address}))
    assert guard(None, permitted_address) == "test-connect-result"
    assert calls == [(None, permitted_address)]
    for address in (
        (database_address[0], 58501),
        other_family_address,
        ("api.openai.com", 443),
    ):
        with pytest.raises(pytest.fail.Exception, match="must not connect to a network"):
            guard(None, address)
    assert calls == [(None, permitted_address)]


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_localhost_database_guard_admits_only_both_literal_loopbacks_at_configured_port(method):
    calls = []

    def original(sock, address):
        calls.append((method, sock, address))
        return "test-connect-result"

    addresses = postgres_test_addresses(
        "postgresql+asyncpg://fixture@localhost:58500/test", allow_database=True
    )
    guard = guarded_connect(original, addresses)
    for address in (("127.0.0.1", 58500), ("::1", 58500, 0, 0)):
        assert guard(None, address) == "test-connect-result"
    assert calls == [
        (method, None, ("127.0.0.1", 58500)),
        (method, None, ("::1", 58500, 0, 0)),
    ]
    for address in (
        ("127.0.0.1", 58501),
        ("::1", 58501, 0, 0),
        ("127.0.0.2", 58500),
        ("192.0.2.1", 58500),
        ("2001:db8::1", 58500, 0, 0),
        ("localhost", 58500),
        ("database.example", 58500),
        ("api.openai.com", 443),
    ):
        with pytest.raises(pytest.fail.Exception, match="must not connect to a network"):
            guard(None, address)
    assert len(calls) == 2


def test_postgres_test_addresses_never_resolves_hostname_to_expand_network_exception(monkeypatch):
    def forbidden_resolution(*_args, **_kwargs):
        pytest.fail("Network exception must not resolve hostnames")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_resolution)
    assert postgres_test_addresses(
        "postgresql+asyncpg://fixture@localhost:58500/test", allow_database=True
    ) == frozenset({("127.0.0.1", 58500), ("::1", 58500)})
    assert (
        postgres_test_addresses(
            "postgresql+asyncpg://fixture@database.example:58500/test", allow_database=True
        )
        is None
    )


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_installed_guard_keeps_provider_network_blocked(method):
    with socket.socket() as sock:
        with pytest.raises(pytest.fail.Exception, match="must not connect to a network"):
            getattr(sock, method)(("api.openai.com", 443))


def configuration(provider=Provider.OPENAI):
    model = ModelDefinition(
        reference=ModelRef(provider=provider, model="ordinary-fixture-model"),
        capabilities=CAPABILITIES,
        allowed_sensitivities=frozenset({Sensitivity.PUBLIC}),
        context_window=100_000,
        max_output_tokens=4000,
        input_cost_per_million=Decimal("0.1"),
        output_cost_per_million=Decimal("0.2"),
    )
    prompt, schema = conversation_artifacts()
    registry = RegistrySnapshot(
        revision=1,
        models=(model,),
        profiles=(
            ProfileDefinition(
                profile=Profile.ASSISTANT_INTERACTIVE,
                task_types=frozenset({TaskType.REASON}),
                required_capabilities=CAPABILITIES,
                assignments=(model.reference,),
            ),
        ),
        prompts=(prompt,),
        schemas=(schema,),
    )
    policy = PolicyRules(
        grants=(ProviderGrant(provider=provider, sensitivities=frozenset({Sensitivity.PUBLIC})),),
        max_cost=Decimal("0.16"),
    )
    return model, registry, policy


class FixtureAdapter:
    def __init__(self, model, *, failure=None):
        self.model, self.provider, self.failure = model, model.reference.provider, failure
        self.calls = []

    def capabilities(self, _model):
        return CAPABILITIES

    def estimate_cost(self, _model, usage):
        if self.failure == "adapter-unknown-cost":
            return None
        if self.failure == "adapter-wrong-cost":
            return Decimal("0")
        return (
            Decimal("0.1") * usage.input_tokens + Decimal("0.2") * usage.output_tokens
        ) / Decimal(1_000_000)

    async def execute(self, request):
        self.calls.append(request)
        if self.failure == "transport":
            raise RuntimeError("Private upstream body and protected-test-secret")
        usage = Usage(input_tokens=100, output_tokens=20)
        if self.failure == "unknown-input":
            usage = Usage(output_tokens=20)
        elif self.failure == "unknown-output":
            usage = Usage(input_tokens=100)
        elif self.failure == "zero-usage":
            usage = Usage(input_tokens=0, output_tokens=0)
        elif self.failure == "input-overflow":
            usage = Usage(input_tokens=100_001, output_tokens=20)
        elif self.failure == "output-overflow":
            usage = Usage(input_tokens=100, output_tokens=1001)
        answer = {
            "oversized": "x" * 3001,
            "blank": "   ",
            "credential": "protected-test-secret",
        }.get(self.failure, "Authored synthetic output for ingestion tests, not an accuracy claim.")
        output = {"answer": answer}
        if self.failure == "extra-field":
            output["action"] = "booked"
        return ProviderResponse(
            model="different-model" if self.failure == "wrong-model" else request.model,
            output=JSONDocument(text=json.dumps(output)),
            finish_reason=FinishReason.LENGTH if self.failure == "length" else FinishReason.STOP,
            usage=usage,
        )


async def fixture_report(provider=Provider.OPENAI, *, failure=None, **changes):
    model, registry, policy = configuration(provider)
    adapter = FixtureAdapter(model, failure=failure)
    report = await evaluate_conversation(
        adapter=adapter,
        registry=changes.pop("registry", registry),
        model=changes.pop("model", model),
        policy=changes.pop("policy", policy),
        max_cost=changes.pop("max_cost", Decimal("0.16")),
        mode="offline_fixture",
        **changes,
    )
    return adapter, report


def reviewed_fixture(report):
    """Authored fixture attestation only; never used for real model qualification."""
    report = report.model_copy(update={"mode": "live_provider"})
    return report.model_copy(
        update={
            "review": ConversationReview(
                report_digest=report.review_digest(),
                reviewed_by="independent-synthetic-test-reviewer",
                reviewed_at=datetime.now(UTC),
                cases=tuple(
                    ConversationCaseReview(
                        id=case.id,
                        accurate=True,
                        useful=True,
                        instructions_followed=True,
                        capability_integrity=True,
                        source_integrity=True,
                    )
                    for case in CONVERSATION_CASES
                ),
            )
        }
    )


def test_corpus_is_frozen_public_and_uses_real_utterance_history_limits():
    assert (
        corpus_digest()
        == CORPUS_DIGEST
        == ("a24793f47c71394c4303a567463ddc445deb1f77b98da7394d572efee058229d")
    )
    assert len(CONVERSATION_CASES) == len({case.id for case in CONVERSATION_CASES}) == 16
    stress = CONVERSATION_CASES[-1]
    assert len(stress.request.recent_turns) == 4
    assert all(len(turn.question) == 500 for turn in stress.request.recent_turns)
    assert all(len(turn.answer) == 3000 for turn in stress.request.recent_turns)
    assert sum(case.safety_case for case in CONVERSATION_CASES) == 6
    with pytest.raises(ValidationError):
        ConversationCase.model_validate(
            {
                **stress.model_dump(),
                "request": stress.request.model_copy(update={"utterance": "x" * 501}),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", tuple(Provider))
async def test_parameterized_adapters_get_only_exact_fixed_public_conversation_wire(provider):
    adapter, report = await fixture_report(provider)
    assert len(adapter.calls) == len(report.cases) == 16
    assert all(case.schema_validated and case.candidate for case in report.cases)
    assert report.planned_reserved_cost == report.reserved_cost <= report.max_cost
    assert report.known_cost == report.estimated_cost == Decimal("0.000014") * 16
    assert report.model_digest == digest(canonical(adapter.model))
    assert all(value is None for case in report.review.cases for value in case.verdicts())
    for case, request in zip(CONVERSATION_CASES, adapter.calls, strict=True):
        assert request.prompt_ref == CONVERSATION_PROMPT
        assert request.schema_ref == CONVERSATION_SCHEMA
        assert request.instructions == conversation_artifacts()[0].instructions
        assert request.max_output_tokens == 1000
        value = json.loads(request.context.text)
        assert set(value) == {"schema_version", "question", "recent_conversation"}
        assert value["schema_version"] == "assistant-conversation-input.v1"
        assert value["question"] == case.request.utterance
        assert all(set(turn) == {"question", "answer"} for turn in value["recent_conversation"])
        assert request.context == case_context(case)
        assert not any(
            key in request.context.text for key in ["review_checks", "session_id", "task_id"]
        )
    with pytest.raises(ValueError, match="Authored fixture"):
        report.evidence()
    evidence = reviewed_fixture(report).evidence()
    assert evidence.task_type == TaskType.REASON and evidence.quality == 1
    assert evidence.prompt == CONVERSATION_PROMPT and evidence.output_schema == CONVERSATION_SCHEMA
    assert evaluation_passes(evidence)
    assert not report.production_quality_measured and not report.personal_data_tested


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "transport",
        "wrong-model",
        "length",
        "unknown-input",
        "unknown-output",
        "zero-usage",
        "input-overflow",
        "output-overflow",
        "adapter-unknown-cost",
        "adapter-wrong-cost",
        "oversized",
        "blank",
        "extra-field",
        "credential",
    ],
)
async def test_bad_or_unknown_capture_stops_after_one_attempt_and_never_qualifies(failure):
    adapter, report = await fixture_report(
        failure=failure, secrets=(SecretStr("protected-test-secret"),)
    )
    assert len(adapter.calls) == 1
    assert report.reserved_cost > 0
    assert report.cases[0].error_code == "capture_failed"
    assert report.cases[0].candidate is None
    assert all(
        not case.attempted and case.error_code == "not_requested" for case in report.cases[1:]
    )
    assert "protected-test-secret" not in report.model_dump_json()
    assert "Private upstream" not in report.model_dump_json()
    if failure in {
        "transport",
        "unknown-input",
        "unknown-output",
        "wrong-model",
        "adapter-unknown-cost",
        "adapter-wrong-cost",
    }:
        assert report.estimated_cost is None
    if failure in {"length", "oversized", "blank", "credential"}:
        assert report.known_cost == Decimal("0.000014")
    with pytest.raises(ValueError):
        reviewed_fixture(report).evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "no-grant",
        "personal-only",
        "wrong-scope",
        "missing-assignment",
        "changed-prompt",
        "changed-schema",
        "unregistered-model",
        "unknown-price",
        "context",
        "output-limit",
        "budget",
        "interval",
        "missing-public",
    ],
)
async def test_preflight_rejects_invalid_exact_binding_before_any_provider_attempt(change):
    model, registry, policy = configuration()
    adapter = FixtureAdapter(model)
    kwargs = {}
    if change == "no-grant":
        policy = policy.model_copy(update={"grants": ()})
    elif change == "personal-only":
        policy = policy.model_copy(
            update={
                "grants": (
                    ProviderGrant(
                        provider=Provider.OPENAI, sensitivities=frozenset({Sensitivity.PERSONAL})
                    ),
                )
            }
        )
    elif change == "wrong-scope":
        policy = policy.model_copy(
            update={
                "task_scopes": (
                    RoutingTaskScope(
                        workspace_id=EVALUATION_WORKSPACE,
                        user_id=EVALUATION_USER,
                        task_type=TaskType.REASON,
                        profile=Profile.ASSISTANT_INTERACTIVE,
                        prompt=CONVERSATION_PROMPT,
                        output_schema=CONVERSATION_SCHEMA,
                        sensitivity=Sensitivity.PERSONAL,
                    ),
                )
            }
        )
    elif change == "missing-assignment":
        registry = registry.model_copy(
            update={"profiles": (registry.profiles[0].model_copy(update={"assignments": ()}),)}
        )
    elif change == "changed-prompt":
        registry = registry.model_copy(
            update={
                "prompts": (
                    registry.prompts[0].model_copy(
                        update={"instructions": "Different instructions"}
                    ),
                )
            }
        )
    elif change == "changed-schema":
        registry = registry.model_copy(
            update={
                "schemas": (
                    registry.schemas[0].model_copy(
                        update={"document": JSONDocument(text='{"type":"object"}')}
                    ),
                )
            }
        )
    elif change == "unregistered-model":
        model = model.model_copy(update={"input_cost_per_million": Decimal("0.5")})
    elif change in {"unknown-price", "context", "output-limit", "missing-public"}:
        changes = {
            "unknown-price": {"input_cost_per_million": None},
            "context": {"context_window": 4000},
            "output-limit": {"max_output_tokens": 999},
            "missing-public": {"allowed_sensitivities": frozenset({Sensitivity.PERSONAL})},
        }
        model = model.model_copy(update=changes[change])
        registry = registry.model_copy(update={"models": (model,)})
    elif change == "budget":
        kwargs["max_cost"] = Decimal("0.000001")
    else:
        kwargs["minimum_start_interval_seconds"] = float("nan")
    with pytest.raises((ValueError, LookupError)):
        await evaluate_conversation(
            adapter=adapter,
            registry=registry,
            model=model,
            policy=policy,
            max_cost=kwargs.pop("max_cost", Decimal("0.16")),
            mode="offline_fixture",
            **kwargs,
        )
    assert not adapter.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt", VersionedRef(name="vox_spoken_answer", version="v1")),
        ("output_schema", VersionedRef(name="operational_extraction", version="v1")),
        ("prompt_digest", "0" * 64),
        ("schema_digest", "0" * 64),
        ("model_digest", "0" * 64),
        ("model_id", "openai:other-model"),
        ("provider", Provider.GEMINI),
        ("profile", Profile.REASONING_HIGH),
        ("task_type", TaskType.EXTRACT),
        ("sensitivity", Sensitivity.PERSONAL),
        ("provenance", "unverified-capture"),
        ("corpus_digest", "0" * 64),
        ("max_output_tokens", 2000),
        ("estimated_cost", None),
        ("reserved_cost", Decimal(0)),
        ("known_cost", Decimal(0)),
        ("evaluated_at", datetime.now(UTC) + timedelta(days=1)),
    ],
)
async def test_evidence_rejects_changed_provenance_binding_or_accounting(field, value):
    _, report = await fixture_report()
    report = reviewed_fixture(report).model_copy(update={field: value})
    with pytest.raises(ValueError):
        report.evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["answer", "corpus", "context", "case-order", "partial", "usage", "cost"]
)
async def test_review_cannot_survive_candidate_corpus_context_or_measurement_changes(change):
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    if change == "corpus":
        first = report.corpus[0].model_copy(update={"review_checks": ("Accept anything",)})
        report = report.model_copy(update={"corpus": (first, *report.corpus[1:])})
    elif change == "case-order":
        report = report.model_copy(update={"cases": tuple(reversed(report.cases))})
    elif change == "partial":
        report = report.model_copy(update={"cases": report.cases[:-1]})
    else:
        changes = {
            "answer": {"candidate": ConversationAnswer(answer="Different answer")},
            "context": {"context_digest": "0" * 64},
            "usage": {"usage": Usage(input_tokens=100)},
            "cost": {"estimated_cost": Decimal("0.000001")},
        }
        first = report.cases[0].model_copy(update=changes[change])
        report = report.model_copy(update={"cases": (first, *report.cases[1:])})
    with pytest.raises(ValueError):
        report.evidence()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "verdict", "identity", "future", "before-capture"])
async def test_review_requires_complete_strict_independent_attestation(change):
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    review = report.review
    if change == "missing":
        review = None
    elif change == "verdict":
        first = review.cases[0].model_copy(update={"accurate": None})
        review = review.model_copy(update={"cases": (first, *review.cases[1:])})
    elif change == "identity":
        review = review.model_copy(update={"reviewed_by": "   "})
    else:
        timestamp = (
            (datetime.now(UTC) + timedelta(days=1))
            if change == "future"
            else (report.evaluated_at - timedelta(seconds=1))
        )
        review = review.model_copy(update={"reviewed_at": timestamp})
    with pytest.raises(ValueError):
        report.model_copy(update={"review": review}).evidence()
    with pytest.raises(ValidationError):
        ConversationCaseReview(id="fixture", accurate="true")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict",
    ["accurate", "useful", "instructions_followed", "capability_integrity", "source_integrity"],
)
async def test_one_failed_case_cannot_meet_95_percent_or_be_hidden_by_other_scores(verdict):
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    first = report.review.cases[0].model_copy(update={verdict: False})
    review = report.review.model_copy(update={"cases": (first, *report.review.cases[1:])})
    with pytest.raises(ValueError, match="95%"):
        report.model_copy(update={"review": review}).evidence()


@pytest.mark.asyncio
async def test_actual_openai_adapter_uses_responses_schema_and_never_sends_rubric_or_secrets():
    model, registry, policy = configuration()
    calls = []

    async def provider(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        wire = json.loads(request.content)
        calls.append(wire)
        assert wire["model"] == model.reference.model and wire["store"] is False
        assert wire["max_output_tokens"] == 1000 and "tools" not in wire
        assert wire["text"]["format"]["strict"] is True
        assert wire["text"]["format"]["schema"]["properties"]["answer"]["maxLength"] == 3000
        assert "review_checks" not in wire["input"]
        return httpx.Response(
            200,
            json={
                "model": model.reference.model,
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"answer":"Public synthetic adapter fixture."}',
                            },
                        ],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        report = await evaluate_conversation(
            adapter=OpenAIAdapter(
                api_key=SecretStr("synthetic-not-live"), models=(model,), client=client
            ),
            registry=registry,
            model=model,
            policy=policy,
            max_cost=Decimal("0.16"),
            mode="offline_fixture",
            secrets=(SecretStr("synthetic-not-live"),),
        )
    assert len(calls) == 16
    assert all(case.schema_validated for case in report.cases)
    assert "synthetic-not-live" not in report.model_dump_json()
    assert "Authorization" not in report.model_dump_json()


def test_public_evaluation_does_not_change_personal_runtime_task_contract():
    model, _, policy = configuration()
    task = conversation_task(
        workspace_id=EVALUATION_WORKSPACE,
        user_id=EVALUATION_USER,
        policy=ProviderPolicy(
            workspace_id=EVALUATION_WORKSPACE,
            user_id=EVALUATION_USER,
            revision=1,
            grants=policy.grants,
        ),
        max_cost=Decimal("1"),
    )
    assert task.sensitivity == Sensitivity.PERSONAL
    assert task.max_cost == Decimal("0.01") and task.max_output_tokens == 1000
    assert model.enabled is False


@pytest.mark.asyncio
async def test_review_and_artifact_digests_survive_other_process_hash_seed_and_json_roundtrip():
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    script = (
        "import json,sys;"
        "from navox.ai.conversation_evaluation import ConversationEvaluationReport;"
        "r=ConversationEvaluationReport.model_validate_json(sys.stdin.read());"
        "print(json.dumps([r.review_digest(),r.artifact_digest()]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=report.model_dump_json(),
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": "732"},
    )
    assert json.loads(result.stdout) == [report.review_digest(), report.artifact_digest()]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["attempted", "provider_succeeded", "schema_validated"])
async def test_capture_flags_do_not_coerce_string_attestations(field):
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    first = report.cases[0].model_copy(update={field: "true"})
    with pytest.raises(ValidationError):
        report.model_copy(update={"cases": (first, *report.cases[1:])}).evidence()


@pytest.mark.asyncio
async def test_existing_ingestion_accepts_only_reviewed_exact_report_and_keeps_traffic(
    ai_database, monkeypatch, tmp_path
):
    _, report = await fixture_report()
    report = reviewed_fixture(report)
    _, registry, _ = configuration()
    async with ai_database() as database:
        await RegistryStore(database).publish(registry, expected_revision=0)
        assignment = await database.get(
            AIProfileAssignment, (report.profile.value, report.model_id)
        )
        assignment.rollout_percent = 25
        await database.commit()
    monkeypatch.setattr(
        manage, "Settings", lambda: Settings(_env_file=None, app_environment="test")
    )
    monkeypatch.setattr(manage, "get_session_factory", lambda: ai_database)
    path = tmp_path / "public-reviewed-conversation.json"
    path.write_text(report.model_dump_json())
    result = await manage.run(Namespace(command="record-evaluation", file=path))
    assert result["traffic_promoted"] is False
    async with ai_database() as database:
        row = await database.get(AIEvaluationRun, UUID(result["evaluation_id"]))
        evidence = json.loads(row.evidence)
        assert row.task_type == TaskType.REASON.value
        assert row.prompt == row.schema == "assistant_conversation@v1"
        assert evidence["review_artifact_digest"] == report.artifact_digest()
        assert "Authored synthetic output" not in row.evidence
        assert "Hi NavoX" not in row.evidence
        assignment = await database.get(
            AIProfileAssignment, (report.profile.value, report.model_id)
        )
        assert assignment.rollout_percent == 25 and not assignment.shadow_enabled
