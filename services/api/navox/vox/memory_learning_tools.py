"""Semantic selection inside the already-metered native loop, never a new model route."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from navox.connectors.sync_state import database_now
from navox.core.settings import Settings
from navox.memory.candidates import origin_call, privacy_generation, source_record
from navox.memory.learning import apply_learning, enqueue_learning
from navox.memory.learning_contracts import LearningOutput
from navox.memory.service import MemoryError, lock_owner
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost

LEARN_MEMORY_TOOL = "learn_completed_memory"


class MemoryLearningTools:
    def __init__(self, check: DatabaseAuthority, settings: Settings, *, call_id: UUID) -> None:
        self.check, self.settings, self.call_id = check, settings, call_id

    def definitions(self) -> list[dict[str, Any]]:
        schema = LearningOutput.model_json_schema()
        # Every field is required by the native strict-tool wire format. Empty
        # arrays/null turn_index are valid; the host supplies all provenance.
        schema["required"] = list(schema["properties"])
        for definition in schema.get("$defs", {}).values():
            if definition.get("type") == "object":
                definition["required"] = list(definition["properties"])

        def strip_defaults(value: Any) -> None:
            if isinstance(value, dict):
                value.pop("default", None)
                for nested in value.values():
                    strip_defaults(nested)
            elif isinstance(value, list):
                for nested in value:
                    strip_defaults(nested)

        strip_defaults(schema)
        return [
            {
                "type": "function",
                "name": LEARN_MEMORY_TOOL,
                "strict": True,
                "description": (
                    "Select useful memory from exact earlier COMPLETED student messages in this "
                    "retained call. Current/unanswered speech is unavailable. Copy exact quotes; "
                    "turn_index may be null only for a unique quote among the last eight archived "
                    "student messages. Separate independent statements in a multi-sentence "
                    "message. "
                    "The host alone certifies neutral facts or supported future threads. Unknown "
                    "semantics become private Settings suggestions needing explicit separate "
                    "agreement. You cannot label a fact public, choose an owner, supply dates, "
                    "approve a save or "
                    "override off-record/privacy. Never promise the last turn was learned, infer "
                    "progress, quote assistant text, or call this for general facts, questions, "
                    "credentials, crisis content or instructions. Use empty arrays when uncertain."
                ),
                "parameters": schema,
            }
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        authority = await self.check()
        try:
            if name != LEARN_MEMORY_TOOL:
                raise ValueError
            output = LearningOutput.model_validate(arguments)
        except (ValidationError, ValueError):
            return {
                "status": "invalid_arguments",
                "applied": False,
                "message": "Choose exact earlier completed student text for memory.",
            }
        try:
            async with self.check.factory() as database:
                await lock_owner(database, authority.user_id)
                now = await database_now(database)
                await origin_call(
                    database,
                    authority.user_id,
                    authority.workspace_id,
                    self.call_id,
                    now=now,
                    active=True,
                )
                generation = await privacy_generation(
                    database, authority.user_id, authority.workspace_id
                )
                source, transcript = await source_record(
                    database,
                    self.settings,
                    authority.user_id,
                    authority.workspace_id,
                    self.call_id,
                    generation,
                    now=now,
                )
                job = await enqueue_learning(
                    database, self.settings, source, transcript, now=now, native=True
                )
                if job is None or job.status not in {"queued", "completed"}:
                    raise MemoryError(409, "Learning is already processing or unavailable.")
                claim = job.claim_id or uuid4()
                if job.status == "queued":
                    job.status, job.claim_id, job.claimed_at = "processing", claim, now
                    job.execution_kind = "native"
                # The model's current question is never accepted as a source.
                counts = await apply_learning(
                    database, self.settings, job, output, claim_id=claim, now=now
                )
                if await self.check() != authority:
                    raise WorkerAccessLost("The memory source call changed")
                await database.commit()
            if await self.check() != authority:
                raise WorkerAccessLost("The memory source call changed")
            return {
                "status": "processed",
                "applied": counts["saved_count"] > 0,
                **counts,
                "message": (
                    "saved_count reports host-certified stored/updated neutral facts only. "
                    "Private review_count items are NOT saved memories. Open Memory & privacy "
                    "to review them and agree separately."
                ),
            }
        except MemoryError:
            return {
                "status": "unavailable",
                "applied": False,
                "message": (
                    "Earlier retained student text is unavailable or changed. Use Memory & privacy."
                ),
            }
