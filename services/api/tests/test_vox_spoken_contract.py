"""The answer generation contract and one-call speech boundary must agree."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_vox_worker import Check, Gateway, Output, assignment, settled, utterance

from navox.ai.catalog import catalog_template
from navox.ai.context import ContextDenied
from navox.ai.conversation import (
    CONVERSATION_INSTRUCTIONS,
    CONVERSATION_PROMPT,
    CONVERSATION_SCHEMA,
    ConversationAnswer,
    conversation_task,
)
from navox.ai.foundation.contracts import Profile, ProviderPolicy, Sensitivity, TaskType
from navox.ai.speech_synthesis import MAX_SPEECH_CHARACTERS, bounded_speech_text
from navox.core.settings import Settings
from navox.vox.conversation import (
    SPOKEN_ANSWER_PROMPT,
    SPOKEN_ANSWER_SCHEMA,
    SpokenAnswer,
    SpokenAnswerInput,
    spoken_answer_task,
)
from navox.vox.worker import MemoryTurn, TransientConversationContext, VoiceTurnWorker


def task(check, *, ordinary=False):
    authority = check.authority
    return (conversation_task if ordinary else spoken_answer_task)(
        workspace_id=authority.workspace_id,
        user_id=authority.user_id,
        max_cost=Decimal("1"),
        policy=ProviderPolicy(
            workspace_id=authority.workspace_id,
            user_id=authority.user_id,
            revision=1,
            grants=(),
            allow_fallback=True,
            max_fallbacks=2,
        ),
    )


def test_spoken_artifacts_are_distinct_without_changing_ordinary_text_contract():
    registry = catalog_template()
    prompt = next(row for row in registry.prompts if row.reference == SPOKEN_ANSWER_PROMPT)
    schema = next(row for row in registry.schemas if row.reference == SPOKEN_ANSWER_SCHEMA)
    ordinary_prompt = next(row for row in registry.prompts if row.reference == CONVERSATION_PROMPT)
    ordinary_schema = next(row for row in registry.schemas if row.reference == CONVERSATION_SCHEMA)
    assert SPOKEN_ANSWER_PROMPT != CONVERSATION_PROMPT
    assert SPOKEN_ANSWER_SCHEMA != CONVERSATION_SCHEMA
    assert prompt.output_schema == schema.reference
    assert json.loads(schema.document.text)["properties"]["answer"]["maxLength"] == 600
    assert json.loads(ordinary_schema.document.text)["properties"]["answer"]["maxLength"] == 3000
    assert ordinary_prompt.instructions == CONVERSATION_INSTRUCTIONS
    assert "at most 600 characters" in prompt.instructions
    assert "no tools or sources" in prompt.instructions
    assert "Do not send, cancel, purchase, delete, schedule, execute or grant approval" in (
        prompt.instructions
    )
    assert all(not profile.assignments for profile in registry.profiles)
    assert not registry.models


@pytest.mark.parametrize("answer", ["x" * 600, "界" * 600, "😀" * 600])
def test_every_maximum_length_spoken_answer_fits_the_existing_synthesis_contract(answer):
    value = SpokenAnswer(answer=answer)
    assert len(value.answer) == MAX_SPEECH_CHARACTERS
    assert bounded_speech_text(value.answer) == answer


@pytest.mark.parametrize("answer", ["", "   ", "x" * 601, "😀" * 601, "x" * 3000])
def test_invalid_spoken_answers_are_never_truncated_or_accepted_as_text_answers(answer):
    with pytest.raises(ValidationError):
        SpokenAnswer(answer=answer)
    if answer.strip():
        assert ConversationAnswer(answer=answer).answer == answer


def test_voice_task_narrows_fallback_without_widening_cost_or_provider_authority():
    check = Check()
    value = task(check)
    assert value.prompt == SPOKEN_ANSWER_PROMPT
    assert value.output_schema == SPOKEN_ANSWER_SCHEMA
    assert value.profile == Profile.ASSISTANT_INTERACTIVE
    assert value.task_type == TaskType.REASON
    assert value.sensitivity == Sensitivity.PERSONAL
    assert not value.context_references
    assert value.max_cost == Decimal("0.01")
    assert value.max_output_tokens == 1000
    assert not value.provider_policy.allow_fallback
    assert value.provider_policy.max_fallbacks == 0
    assert value.provider_policy.grants == ()


@pytest.mark.asyncio
async def test_transient_context_uses_only_the_exact_spoken_contract_and_bounded_history():
    check = Check()
    history = tuple(MemoryTurn(f"Question {index}", f"Answer {index}") for index in range(5))
    context = TransientConversationContext(
        check, Settings(), check.authority, "Explain gravity", history
    )
    built = await context.build(task(check), {})
    value = json.loads(built.content.text)
    assert value == {
        "schema_version": "vox-spoken-answer-input.v1",
        "question": "Explain gravity",
        "recent_conversation": [
            {"question": turn.question, "answer": turn.answer} for turn in history[-4:]
        ],
    }
    assert built.source_ids == () and built.sensitivity == Sensitivity.PERSONAL
    with pytest.raises(ContextDenied):
        await context.build(task(check, ordinary=True), {})
    with pytest.raises(ContextDenied):
        await context.build(task(check), {}, user_request="Attach hidden context")
    with pytest.raises(ContextDenied):
        await context.build(task(check), {uuid4(): object()})


@pytest.mark.parametrize(
    "patch",
    [
        {"question": "x" * 501},
        {"recent_conversation": [{"question": "Q", "answer": "x" * 601}]},
        {"recent_conversation": [{"question": "Q", "answer": "A"}] * 5},
        {"files": ["hidden source"]},
    ],
)
def test_spoken_input_cannot_exceed_the_preflight_history_bound_or_include_sources(patch):
    with pytest.raises(ValidationError):
        SpokenAnswerInput.model_validate({"question": "Q", **patch})


@pytest.mark.asyncio
async def test_full_length_valid_spoken_answer_reaches_playback_and_transient_history():
    check, gateway, output = Check(), Gateway(), Output()
    answer = "x" * MAX_SPEECH_CHARACTERS

    async def full_answer(question, history):
        return SpokenAnswer(answer=answer).answer

    gateway.answer = full_answer
    worker = VoiceTurnWorker(assignment(), check, gateway, output)
    await utterance(worker)
    await settled(worker)
    assert not worker.failed
    assert output.played == [b"synthetic-test-mp3"]
    assert worker._history == (MemoryTurn("Explain gravity", answer),)
    assert gateway.calls[-1] == ("synthesize", answer)
    await worker.close()
    assert not worker._history
