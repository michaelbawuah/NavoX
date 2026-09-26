import json
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_ai_gateway_foundation import USER, WORKSPACE, make_task
from test_ai_runtime import accepts_object, setup_runtime

from navox.agent.contracts import get_action_contract
from navox.ai.context import ContextBuilder
from navox.ai.foundation.contracts import JSONDocument, Provider
from navox.ai.sessions import append_turn, create_session
from navox.ai.tools import CanonicalTool, normalize_proposal, provider_tool
from navox.ai.validation import OutputRejected, compile_schema, validate_output
from navox.db.communications import AssistantSession, AssistantTurn
from navox.db.models import Action, User


@pytest.mark.asyncio
async def test_two_providers_share_one_navox_session_and_duplicate_turn_is_idempotent(ai_database):
    runtime, task, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        session = await create_session(db, user_id=USER, workspace_id=WORKSPACE)
        session_id = session.id
        await db.commit()
        for sequence, provider in enumerate((Provider.OPENAI, Provider.GEMINI), start=1):
            current_task = make_task(
                **{
                    **task.model_dump(),
                    "id": uuid4(),
                    "preferred_provider": provider,
                }
            )
            result = await runtime.execute(
                current_task,
                context_builder=ContextBuilder(db),
                documents={},
                semantic_validator=accepts_object,
            )
            assert result.provider == provider
            references = {
                field: f"navox:{WORKSPACE}:{USER}:{uuid4()}"
                for field in (
                    "user_input_reference",
                    "context_snapshot_reference",
                    "output_reference",
                )
            }
            turn = await append_turn(
                db,
                session_id=session_id,
                user_id=USER,
                workspace_id=WORKSPACE,
                result=result,
                **references,
            )
            assert turn.session_id == session_id and turn.sequence == sequence
            await db.commit()
            again = await append_turn(
                db,
                session_id=session_id,
                user_id=USER,
                workspace_id=WORKSPACE,
                result=result,
                **references,
            )
            assert again.sequence == sequence
            await db.commit()
        turns = (await db.scalars(select(AssistantTurn).order_by(AssistantTurn.sequence))).all()
        assert [t.provider for t in turns] == ["openai", "gemini"]
        assert all(t.action_refs == [] for t in turns)
        assert (await db.get(AssistantSession, session_id)).next_sequence == 3
        assert not (await db.scalars(select(Action))).all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation", ["owner", "raw_content", "trace", "validation", "paused", "closed"]
)
async def test_session_rejects_foreign_or_unvalidated_turns(ai_database, violation):
    runtime, task, _, _ = await setup_runtime(ai_database)
    async with ai_database() as db:
        session = await create_session(db, user_id=USER, workspace_id=WORKSPACE)
        session_id = session.id
        await db.commit()
        result = await runtime.execute(
            task,
            context_builder=ContextBuilder(db),
            documents={},
            semantic_validator=accepts_object,
        )
        reference = f"navox:{WORKSPACE}:{USER}:{uuid4()}"
        if violation == "owner":
            result = result.model_copy(update={"user_id": uuid4()})
        elif violation == "trace":
            result = result.model_copy(update={"task_id": uuid4()})
        elif violation == "validation":
            result = result.model_copy(update={"semantic_validated": False})
        elif violation == "paused":
            (await db.get(User, USER)).agent_paused = True
        elif violation == "closed":
            session.status = "closed"
        else:
            reference = "Actual email content must not be retained as a turn reference"
        await db.commit()
        with pytest.raises(ValueError):
            await append_turn(
                db,
                session_id=session_id,
                user_id=USER,
                workspace_id=WORKSPACE,
                result=result,
                user_input_reference=reference,
                context_snapshot_reference=reference,
                output_reference=reference,
            )
        await db.rollback()
        assert not (await db.scalars(select(AssistantTurn))).all()


def send_tool():
    contract = get_action_contract("gmail.send")
    return CanonicalTool(
        contract=contract,
        parameters=JSONDocument(
            text=json.dumps(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"to": {"type": "string"}, "body": {"type": "string"}},
                    "required": ["to", "body"],
                }
            )
        ),
    )


@pytest.mark.parametrize("provider", tuple(Provider))
def test_provider_tool_syntax_normalizes_to_canonical_unapproved_proposal(provider):
    tool = send_tool()
    wire = provider_tool(provider, tool)
    native = wire["functionDeclarations"][0] if provider == Provider.GEMINI else wire
    proposal = normalize_proposal(
        native["name"], '{"to":"maya@example.com","body":"Hello"}', (tool,)
    )
    assert proposal.action_type == "gmail.send"
    assert proposal.risk_level == "R3"
    assert proposal.required_permissions == ("https://www.googleapis.com/auth/gmail.send",)
    assert not hasattr(proposal, "approved") and not hasattr(proposal, "execute")


@pytest.mark.parametrize(
    "arguments",
    [
        '{"to":"maya@example.com","body":"Hello","approved":true}',
        '{"to":"a@example.com","to":"b@example.com","body":"Hello"}',
        '{"to":"a@example.com","body":"Hello","risk_level":"R0"}',
        "[]",
    ],
)
def test_model_cannot_add_authority_or_ambiguous_arguments(arguments):
    with pytest.raises(OutputRejected):
        normalize_proposal("gmail__send", arguments, (send_tool(),))


def test_forged_contract_and_unregistered_action_are_rejected():
    tool = send_tool()
    with pytest.raises(OutputRejected):
        provider_tool(
            Provider.OPENAI, replace(tool, contract=replace(tool.contract, risk_level="R0"))
        )
    with pytest.raises(OutputRejected):
        normalize_proposal("unknown__execute", "{}", (tool,))


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://untrusted.invalid/schema"},
        {"$id": "https://untrusted.invalid/schema", "$ref": "#definition"},
    ],
)
def test_schema_cannot_trigger_external_reference_resolution(schema):
    with pytest.raises(OutputRejected):
        compile_schema(JSONDocument(text=json.dumps(schema)))


def test_unknown_local_schema_reference_fails_without_a_raw_exception():
    with pytest.raises(OutputRejected):
        validate_output(
            JSONDocument(text="{}"), JSONDocument(text='{"$ref":"#/$defs/missing"}'), accepts_object
        )
