"""Operational domain checks use authored outputs, never live qualification."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from test_ai_evaluation import model_for, policy_for
from test_ai_gateway_foundation import USER, WORKSPACE
from test_ai_runtime import FakeAdapter

from navox.ai.catalog import catalog_template
from navox.ai.context import ContextDenied
from navox.ai.control import record_evaluation
from navox.ai.domain_corpus import CORPORA, FOREIGN, A, B, DomainCase, item
from navox.ai.domain_evaluation import DomainEvaluationReport, evaluate_domain
from navox.ai.domains import DOMAIN_TASKS, Domain, DomainInput, domain_reference, validate_domain
from navox.ai.foundation.adapter import ErrorCode, ProviderResponse
from navox.ai.foundation.contracts import (
    FinishReason,
    JSONDocument,
    Provider,
    ProviderGrant,
    Sensitivity,
    Usage,
)
from navox.ai.foundation.persistence import RegistryStore, canonical, digest, model_key
from navox.ai.operational_context import OperationalContext
from navox.ai.operational_service import OperationalRequest, advise
from navox.ai.providers import AdapterFailure
from navox.ai.routing import EvaluationEvidence, PolicyRules
from navox.ai.runtime import GatewayRuntime, GatewayUnavailable
from navox.ai.sessions import create_session
from navox.ai.store import GatewayStore
from navox.ai.validation import OutputRejected
from navox.core.settings import Settings
from navox.db.ai_registry import AIProfileAssignment, AITaskRun
from navox.db.communications import AssistantTurn
from navox.db.models import Action, Commitment, User, Workspace, WorkspaceMembership


def authored_output(domain: Domain, fixture: DomainCase):
    expected = fixture.expected
    base = {
        "facts": [f.model_dump(mode="json") for f in expected.required_facts],
        "unknowns": [u.model_dump(mode="json") for u in expected.required_unknowns],
    }
    if domain == Domain.PLANNING:
        base.update(
            steps=[
                {"action_type": action, "item_id": str(expected.step_item)}
                for action in expected.step_actions
            ],
            insufficient_context=expected.decline,
        )
    elif domain == Domain.MEETING:
        base.update(
            meeting_id=str(expected.meeting_id) if expected.meeting_id else None,
            related_ids=[str(i) for i in expected.related_ids],
        )
    elif domain == Domain.ASSISTANT:
        base.update(insufficient_context=expected.decline)
    else:
        base.update(ordered_ids=[str(i) for i in expected.ordered_ids])
    return base


class CorpusAdapter(FakeAdapter):
    def __init__(self, provider=Provider.OPENAI, *, alter=None, fail=False):
        super().__init__(provider, fail=fail)
        self.alter = alter

    async def execute(self, request):
        self.calls.append(request)
        if self.fail:
            raise AdapterFailure(ErrorCode.RATE_LIMIT)
        domain = Domain(request.prompt_ref.name)
        context = json.loads(request.context.text)["operational_context"]
        fixture = next(c for c in CORPORA[domain] if c.context.model_dump(mode="json") == context)
        output = authored_output(domain, fixture)
        if self.alter:
            output = self.alter(fixture, output)
        return ProviderResponse(
            model=request.model,
            output=JSONDocument(text=json.dumps(output)),
            finish_reason=FinishReason.STOP,
            usage=Usage(input_tokens=100, output_tokens=100),
        )


async def run_fixture(domain, adapter=None, **options):
    adapter = adapter or CorpusAdapter()
    return await evaluate_domain(
        domain=domain,
        adapter=adapter,
        registry=options.pop("registry", catalog_template()),
        model=model_for(adapter.provider),
        policy=policy_for(adapter.provider),
        max_cost=options.pop("max_cost", Decimal("2")),
        mode="offline_fixture",
        **options,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", tuple(Domain))
async def test_full_identical_corpora_and_offline_evidence_cannot_qualify(domain):
    envelopes = []
    for provider in Provider:
        adapter = CorpusAdapter(provider)
        report = await run_fixture(domain, adapter)
        assert len(adapter.calls) == len(report.cases) == 12
        assert all(c.passed and c.schema_validated for c in report.cases)
        assert all(
            "expected" not in r.context.text and "rubric" not in r.context.text
            for r in adapter.calls
        )
        assert report.prompt == report.output_schema == domain_reference(domain)
        assert all(c.estimated_cost == Decimal("0.0003") for c in report.cases)
        envelopes.append([r.context.text for r in adapter.calls])
        with pytest.raises(ValueError, match="Complete live"):
            report.evidence()
        # Only an isolated contract test; never published as live measurements.
        contract = report.model_copy(update={"mode": "live_provider"})
        evidence = contract.evidence()
        assert evidence.quality == evidence.reliability == 1 and evidence.safety_passed
        assert evidence.samples == 12 and evidence.task_type == DOMAIN_TASKS[domain][1]
        assert (
            DomainEvaluationReport.model_validate_json(contract.model_dump_json()).evidence()
            == evidence
        )
    assert all(envelope == envelopes[0] for envelope in envelopes)


@pytest.mark.asyncio
async def test_schema_valid_wrong_order_is_a_quality_failure_and_cannot_be_relabelled():
    def reverse(fixture, output):
        if fixture.id == "priority-order":
            output["ordered_ids"] = [str(A), str(B)]
        return output

    report = (await run_fixture(Domain.RANKING, CorpusAdapter(alter=reverse))).model_copy(
        update={"mode": "live_provider"}
    )
    first = report.cases[0]
    assert first.schema_validated and first.provider_succeeded and not first.passed
    assert report.evidence().quality == 11 / 12
    changed = first.model_copy(update={"passed": True})
    with pytest.raises(ValueError, match="quality score"):
        report.model_copy(update={"cases": (changed, *report.cases[1:])}).evidence()
    with pytest.raises(ValueError, match="Complete live"):
        report.model_copy(update={"cases": report.cases[:4]}).evidence()


@pytest.mark.asyncio
async def test_safety_failure_cannot_hide_inside_a_passing_mean():
    def unsafe(fixture, output):
        if fixture.id == "source-routing-injection":
            output["steps"][0]["action_type"] = "gmail.send"
        return output

    report = (await run_fixture(Domain.PLANNING, CorpusAdapter(alter=unsafe))).model_copy(
        update={"mode": "live_provider"}
    )
    evidence = report.evidence()
    assert evidence.quality == 11 / 12 and not evidence.safety_passed


@pytest.mark.asyncio
async def test_pacing_budget_and_rate_limit_keep_all_failure_slots(monkeypatch):
    from navox.ai import domain_evaluation as evaluation

    clock = [0.0]

    async def sleep(delay):
        clock[0] += delay

    monkeypatch.setattr(evaluation, "monotonic", lambda: clock[0])
    monkeypatch.setattr(evaluation.asyncio, "sleep", sleep)
    adapter = CorpusAdapter()
    report = await run_fixture(Domain.ASSISTANT, adapter, minimum_start_interval_seconds=6)
    assert clock[0] == 66 and all(c.latency_ms == 0 for c in report.cases)
    limited = CorpusAdapter(fail=True)
    stopped = await run_fixture(Domain.ASSISTANT, limited)
    assert len(limited.calls) == 1 and len(stopped.cases) == 12
    assert all(not c.passed and c.error_code == "rate_limit" for c in stopped.cases)
    none = CorpusAdapter()
    budget = await run_fixture(Domain.ASSISTANT, none, max_cost=Decimal("0.000001"))
    assert not none.calls and all(not c.passed for c in budget.cases)


@pytest.mark.parametrize(
    "change",
    [
        "foreign",
        "invented_date",
        "concealed_date",
        "free_text",
        "send",
        "duplicate_rank",
        "past_meeting",
    ],
)
def test_semantic_contracts_reject_invented_state_and_execution(change):
    context = DomainInput(
        items=(item(), item(B)), instructions="Inspect my saved state.", as_of=datetime.now(UTC)
    )
    domain = Domain.ASSISTANT
    output = {
        "facts": [{"item_id": str(A), "field": "status"}],
        "unknowns": [],
        "insufficient_context": False,
    }
    if change == "foreign":
        output["facts"][0]["item_id"] = str(FOREIGN)
    elif change == "invented_date":
        context = context.model_copy(update={"items": (item(due_at=None), item(B))})
        output["facts"][0]["field"] = "due_at"
    elif change == "concealed_date":
        output["unknowns"] = [{"item_id": str(A), "field": "due_at"}]
    elif change == "free_text":
        output["answer"] = "I sent the email and completed the purchase."
    elif change == "send":
        domain = Domain.PLANNING
        output["steps"] = [{"item_id": str(A), "action_type": "gmail.send"}]
    elif change == "duplicate_rank":
        domain = Domain.RANKING
        output.pop("insufficient_context")
        output["ordered_ids"] = [str(A), str(A)]
    else:
        domain = Domain.MEETING
        context = context.model_copy(
            update={"items": (item(kind="meeting", due_at=datetime.now(UTC) - timedelta(days=1)),)}
        )
        output.pop("insufficient_context")
        output.update(meeting_id=str(A), related_ids=[])
    with pytest.raises((OutputRejected, ValidationError)):
        validate_domain(domain, output, context)


async def operational_runtime(
    factory, *, user_id=USER, workspace_id=WORKSPACE, existing=False, during=None
):
    providers = (Provider.OPENAI, Provider.GEMINI)
    models = tuple(
        model_for(p).model_copy(
            update={
                "enabled": True,
                "allowed_sensitivities": frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL}),
            }
        )
        for p in providers
    )
    template = catalog_template()
    registry = template.model_copy(
        update={
            "models": models,
            "profiles": tuple(
                p.model_copy(
                    update={
                        "assignments": tuple(m.reference for m in models)
                        if p.profile in {binding[0] for binding in DOMAIN_TASKS.values()}
                        else ()
                    }
                )
                for p in template.profiles
            ),
        }
    )
    async with factory() as db:
        if not existing:
            db.add_all(
                [
                    User(id=user_id, email="operations@example.com"),
                    Workspace(id=workspace_id, name="Operations"),
                ]
            )
            await db.flush()
            db.add(WorkspaceMembership(workspace_id=workspace_id, user_id=user_id))
        db.add(
            Commitment(
                id=A,
                user_id=user_id,
                workspace_id=workspace_id,
                commitment_type="task",
                title="Review project brief",
                description="Private saved notes",
                status="waiting",
                priority=3,
                dedupe_key=str(A),
                created_by="user",
            )
        )
        await db.flush()
        await RegistryStore(db).publish(registry, expected_revision=0)
        for assignment in (await db.scalars(select(AIProfileAssignment))).all():
            assignment.rollout_percent = 100
        for model in models:
            for domain in Domain:
                profile, task = DOMAIN_TASKS[domain]
                ref = domain_reference(domain)
                evidence = EvaluationEvidence(
                    profile=profile,
                    task_type=task,
                    prompt=ref,
                    output_schema=ref,
                    quality=1,
                    reliability=1,
                    p95_latency_ms=10,
                    samples=12,
                    safety_passed=True,
                    evaluated_at=datetime.now(UTC),
                    corpus_version="authored-runtime-fixture",
                )
                await record_evaluation(
                    db,
                    model_id=model_key(model.reference),
                    model_digest=digest(canonical(model)),
                    registry_revision=1,
                    evidence=evidence,
                )
        await db.commit()
    rules = PolicyRules(
        grants=tuple(
            ProviderGrant(
                provider=p, sensitivities=frozenset({Sensitivity.PUBLIC, Sensitivity.PERSONAL})
            )
            for p in providers
        ),
        allow_fallback=True,
        max_fallbacks=1,
        preferred_provider=Provider.OPENAI,
        max_cost=Decimal("2"),
    )
    output = json.dumps(
        {
            "facts": [{"item_id": str(A), "field": "status"}],
            "unknowns": [],
            "insufficient_context": False,
        }
    )
    adapters = {
        p: FakeAdapter(p, output=output, during=during if p == Provider.OPENAI else None)
        for p in providers
    }
    return GatewayRuntime(GatewayStore(factory, rules), adapters), adapters


@pytest.mark.asyncio
async def test_real_feature_boundary_minimizes_state_and_session_survives_provider_change(
    ai_database,
):
    runtime, adapters = await operational_runtime(ai_database)
    settings = Settings(_env_file=None)
    async with ai_database() as db:
        session = await create_session(db, user_id=USER, workspace_id=WORKSPACE)
        session_id = session.id
        await db.commit()
    request = OperationalRequest(
        item_ids=(A,), instructions="What is the saved status?", session_id=session_id
    )
    first = await advise(
        runtime,
        settings,
        workspace_id=WORKSPACE,
        user_id=USER,
        domain=Domain.ASSISTANT,
        request=request,
    )
    adapters[Provider.OPENAI].fail = True
    second = await advise(
        runtime,
        settings,
        workspace_id=WORKSPACE,
        user_id=USER,
        domain=Domain.ASSISTANT,
        request=request,
    )
    assert first.session_id == second.session_id == session_id
    assert (first.turn_sequence, second.turn_sequence) == (1, 2)
    assert not first.actions_executed and "waiting" in first.details[0]
    payload = json.loads(adapters[Provider.OPENAI].calls[0].context.text)
    assert set(payload) == {"operational_context"}
    assert len(payload["operational_context"]["items"]) == 1
    assert "workspace_id" not in payload["operational_context"]["items"][0]
    async with ai_database() as db:
        turns = list(await db.scalars(select(AssistantTurn).order_by(AssistantTurn.sequence)))
        assert [t.provider for t in turns] == ["openai", "gemini"]
        assert all(t.action_refs == [] for t in turns)
        assert await db.scalar(select(func.count()).select_from(Action)) == 0
        runs = list(await db.scalars(select(AITaskRun)))
        assert len(runs) == 3
        assert all("Private saved notes" not in json.dumps(r.usage) for r in runs)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "pause", "membership", "delete"])
async def test_mid_call_state_or_authority_change_blocks_the_result(ai_database, change):
    async def mutate():
        async with ai_database() as db:
            if change == "status":
                (await db.get(Commitment, A)).status = "completed"
            elif change == "pause":
                (await db.get(User, USER)).agent_paused = True
            elif change == "membership":
                await db.delete(await db.get(WorkspaceMembership, (WORKSPACE, USER)))
            else:
                await db.delete(await db.get(Commitment, A))
            await db.commit()

    runtime, adapters = await operational_runtime(ai_database, during=mutate)
    with pytest.raises(GatewayUnavailable):
        await advise(
            runtime,
            Settings(_env_file=None),
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            request=OperationalRequest(item_ids=(A,), instructions="Show status."),
        )
    assert not adapters[Provider.GEMINI].calls
    assert len(adapters[Provider.OPENAI].calls) == 1


@pytest.mark.asyncio
async def test_unselected_foreign_unattributed_and_secret_context_denied_before_calls(ai_database):
    runtime, adapters = await operational_runtime(ai_database)
    settings = Settings(_env_file=None)
    for selected in ((FOREIGN,), (A, A)):
        with pytest.raises(ContextDenied):
            await advise(
                runtime,
                settings,
                workspace_id=WORKSPACE,
                user_id=USER,
                domain=Domain.ASSISTANT,
                request=OperationalRequest(item_ids=selected, instructions="Read status."),
            )
    async with ai_database() as db:
        (await db.get(Commitment, A)).created_by = "ai"
        await db.commit()
    with pytest.raises(ContextDenied):
        await advise(
            runtime,
            settings,
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            request=OperationalRequest(item_ids=(A,), instructions="Read status."),
        )
    async with ai_database() as db:
        row = await db.get(Commitment, A)
        row.created_by = "user"
        row.description = "api_key=" + "a" * 24
        await db.commit()
    with pytest.raises(ContextDenied):
        await OperationalContext(
            ai_database,
            settings,
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            item_ids=(A,),
            instructions="Read status.",
        ).prepare()
    assert all(not a.calls for a in adapters.values())


@pytest.mark.asyncio
async def test_authenticated_api_cannot_accept_policy_models_or_execute_actions(
    subscription_env, monkeypatch
):
    from navox.api import ai_operations

    env = subscription_env
    runtime, adapters = await operational_runtime(
        env.factory, user_id=env.user_id, workspace_id=env.workspace_id, existing=True
    )

    async def build(_settings):
        return runtime

    monkeypatch.setattr(ai_operations, "build_runtime", build)
    url = "/api/v1/ai/operations/assistant"
    body = {"item_ids": [str(A)], "instructions": "Show the saved status."}
    response = await env.client.post(url, json=body)
    assert response.status_code == 200 and response.json()["actions_executed"] is False
    assert "waiting" in response.json()["details"][0]
    for extra in (
        {"provider": "xai"},
        {"sensitivity": "PUBLIC"},
        {"model": "unsafe"},
        {"execute": True},
    ):
        assert (await env.client.post(url, json={**body, **extra})).status_code == 422
    assert (
        await env.client.post(url, json={**body, "item_ids": [str(FOREIGN)]})
    ).status_code == 403
    assert (await env.client.post("/api/v1/ai/sessions")).status_code == 201
    assert len(adapters[Provider.OPENAI].calls) == 1


async def add_mail_provenance(factory):
    from uuid import uuid4

    from navox.connectors.builtin.google import mirror_google_document
    from navox.db.models import CommitmentSource, Connection
    from navox.intelligence.contracts import SourceDocument
    from navox.providers.google_sources import GMAIL_READ_SCOPE

    async with factory() as db:
        connection = Connection(
            user_id=USER,
            workspace_id=WORKSPACE,
            provider="google",
            external_account_id="operational-source",
            granted_scopes=[GMAIL_READ_SCOPE],
        )
        db.add(connection)
        await db.flush()
        document = SourceDocument(
            id=uuid4(),
            workspace_id=WORKSPACE,
            provider="google",
            source_type="gmail_message",
            external_id="operational-source",
            subject="Private source subject",
            content="Source body is authorized but must not be sent by saved-state assistance.",
            occurred_at=datetime.now(UTC),
            retrieved_at=datetime.now(UTC),
        )
        resource = await mirror_google_document(db, legacy_connection=connection, document=document)
        (await db.get(Commitment, A)).created_by = "ai"
        source = CommitmentSource(
            commitment_id=A,
            connection_id=connection.id,
            provider="google",
            source_type="gmail_message",
            external_resource_id=document.external_id,
        )
        db.add(source)
        await db.commit()
        return connection.id, resource.id, resource.connector_connection_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [None, "revoked", "deleted", "classification", "body", "capability"]
)
async def test_all_imported_source_authority_is_rechecked_without_sending_source_bodies(
    ai_database, change
):
    from navox.db.models import Connection, ConnectorConnection, ConnectorResource

    identifiers = ()

    async def mutate():
        if change is None:
            return
        connection_id, resource_id, connector_id = identifiers
        async with ai_database() as db:
            if change == "revoked":
                (await db.get(Connection, connection_id)).granted_scopes = []
            elif change == "deleted":
                (await db.get(ConnectorResource, resource_id)).deleted = True
            elif change == "classification":
                (await db.get(ConnectorConnection, connector_id)).config = {
                    "ai_sensitivity": "SENSITIVE"
                }
            elif change == "capability":
                (await db.get(ConnectorConnection, connector_id)).authorized_capabilities = []
            else:
                row = await db.get(ConnectorResource, resource_id)
                row.canonical = {**row.canonical, "content": "Changed source content"}
            await db.commit()

    runtime, adapters = await operational_runtime(ai_database, during=mutate)
    identifiers = await add_mail_provenance(ai_database)
    arguments = dict(
        workspace_id=WORKSPACE,
        user_id=USER,
        domain=Domain.ASSISTANT,
        request=OperationalRequest(item_ids=(A,), instructions="Read saved status."),
    )
    if change is None:
        response = await advise(runtime, Settings(_env_file=None), **arguments)
        assert response.details and response.actions_executed is False
    else:
        with pytest.raises(GatewayUnavailable):
            await advise(runtime, Settings(_env_file=None), **arguments)
    assert len(adapters[Provider.OPENAI].calls) == 1
    assert not adapters[Provider.GEMINI].calls
    context = adapters[Provider.OPENAI].calls[0].context.text
    assert "Private saved notes" in context
    assert "Private source subject" not in context and "Source body" not in context


@pytest.mark.asyncio
async def test_sensitive_source_and_foreign_session_never_reach_provider(ai_database):
    from uuid import uuid4

    from navox.db.communications import AssistantSession
    from navox.db.models import ConnectorConnection

    runtime, adapters = await operational_runtime(ai_database)
    _, _, connector_id = await add_mail_provenance(ai_database)
    async with ai_database() as db:
        (await db.get(ConnectorConnection, connector_id)).config = {"ai_sensitivity": "SENSITIVE"}
        await db.commit()
    with pytest.raises(GatewayUnavailable):
        await advise(
            runtime,
            Settings(_env_file=None),
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            request=OperationalRequest(item_ids=(A,), instructions="Read saved status."),
        )
    other_workspace = uuid4()
    async with ai_database() as db:
        db.add(Workspace(id=other_workspace, name="Other workspace"))
        await db.flush()
        session = AssistantSession(
            user_id=USER, workspace_id=other_workspace, mode="PERSONAL", status="active"
        )
        db.add(session)
        await db.commit()
        session_id = session.id
    with pytest.raises(ValueError, match="session"):
        await advise(
            runtime,
            Settings(_env_file=None),
            workspace_id=WORKSPACE,
            user_id=USER,
            domain=Domain.ASSISTANT,
            request=OperationalRequest(
                item_ids=(A,), instructions="Read saved status.", session_id=session_id
            ),
        )
    assert all(not adapter.calls for adapter in adapters.values())


@pytest.mark.asyncio
async def test_domain_cli_records_full_bound_evidence_without_promoting(
    ai_database, monkeypatch, tmp_path
):
    from argparse import Namespace

    from navox.ai import manage
    from navox.ai.foundation.persistence import RegistryConflict
    from navox.db.ai_registry import AIEvaluationRun

    await operational_runtime(ai_database)
    async with ai_database() as db:
        for row in (await db.scalars(select(AIProfileAssignment))).all():
            row.rollout_percent = 0
        for row in (await db.scalars(select(AIEvaluationRun))).all():
            await db.delete(row)
        registry = await RegistryStore(db).load()
        await db.commit()
    adapter = CorpusAdapter()
    monkeypatch.setattr(manage, "get_session_factory", lambda: ai_database)
    monkeypatch.setattr(manage, "configured_adapters", lambda *_: {Provider.OPENAI: adapter})
    monkeypatch.setattr(
        manage,
        "Settings",
        lambda: Settings(
            _env_file=None, ai_provider_policy=policy_for(Provider.OPENAI).model_dump(mode="json")
        ),
    )
    path = tmp_path / "operational.json"
    options = dict(
        command="evaluate",
        corpus="assistant",
        live=True,
        model=model_key(registry.models[0].reference),
        profile="ASSISTANT_INTERACTIVE",
        max_cost=Decimal("2"),
        output=path,
        minimum_start_interval_seconds=0,
    )
    raw = await manage.run(Namespace(**options))
    report = DomainEvaluationReport.model_validate(raw)
    assert len(report.cases) == len(adapter.calls) == 12
    path.write_text(report.model_dump_json())
    receipt = await manage.run(Namespace(command="record-evaluation", file=path))
    assert receipt["traffic_promoted"] is False
    async with ai_database() as db:
        rows = list(await db.scalars(select(AIEvaluationRun)))
        assert len(rows) == 1 and rows[0].prompt == "assistant@v2"
        assert "Private saved notes" not in rows[0].evidence
        assert all(a.rollout_percent == 0 for a in await db.scalars(select(AIProfileAssignment)))
        await RegistryStore(db).publish(
            registry.model_copy(update={"revision": 2}), expected_revision=1
        )
        await db.commit()
    with pytest.raises(RegistryConflict, match="Registry changed"):
        await manage.run(Namespace(command="record-evaluation", file=path))
    with pytest.raises(ValueError, match="exact profile"):
        await manage.run(Namespace(**{**options, "profile": "EXTRACTION_FAST"}))
