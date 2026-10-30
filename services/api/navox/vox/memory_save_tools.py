"""Prepare an exact, private browser memory review without granting a model writes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from navox.core.settings import Settings
from navox.memory.candidates import CandidateArguments, prepare_candidate
from navox.memory.service import MemoryError
from navox.vox.worker import DatabaseAuthority, WorkerAccessLost

SAVE_MEMORY_TOOL = "prepare_memory_save"
MEMORY_SAVE_REVIEW_TOPIC = "navox.vox.memory-save-review.v1"


class MemorySaveTools:
    def __init__(
        self,
        check: DatabaseAuthority,
        settings: Settings,
        *,
        call_id: UUID,
        publish: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        self.check, self.settings = check, settings
        self.call_id, self.publish = call_id, publish

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": SAVE_MEMORY_TOOL,
                "description": (
                    "Prepare a private on-screen review when the student wants to save a memory. "
                    "Quote one complete student message from an earlier completed exchange in "
                    "this call exactly, without paraphrase, inferred facts or assistant text. "
                    "Use a short descriptive label. The server checks the actual saved student "
                    "source. This tool cannot save anything, approve anything, or change privacy. "
                    "The student must review the quote and click Save this memory in Settings. "
                    "Unknown details are private by default and need separate agreement. Never "
                    "claim a fact was saved or read the private review aloud."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "minLength": 1, "maxLength": 80},
                        "quote": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "required": ["label", "quote"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        authority = await self.check()
        try:
            if name != SAVE_MEMORY_TOOL:
                raise ValueError
            query = CandidateArguments.model_validate(arguments)
        except (ValidationError, ValueError):
            return {
                "status": "invalid_arguments",
                "applied": False,
                "message": "Choose one complete earlier student statement for a memory review.",
            }
        try:
            async with self.check.factory() as database:
                review = await prepare_candidate(
                    database,
                    self.settings,
                    authority.user_id,
                    authority.workspace_id,
                    self.call_id,
                    query,
                )
            if await self.check() != authority:
                raise WorkerAccessLost("The memory review owner changed")
            await self.publish(review)
            if await self.check() != authority:
                raise WorkerAccessLost("The memory review owner changed")
        except MemoryError:
            if await self.check() != authority:
                raise WorkerAccessLost("The memory review owner changed") from None
            return {
                "status": "unavailable",
                "applied": False,
                "message": (
                    "That saved statement is not available for this review. You can add or "
                    "review a fact in Memory & privacy. Nothing was saved."
                ),
            }
        # The proof and quoted content go only to the exact browser participant,
        # never back to the model where they might be echoed or treated as consent.
        return {
            "status": "review_required",
            "applied": False,
            "message": "Review the exact statement in Memory & privacy. Nothing was saved.",
        }
