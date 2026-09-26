"""Shared drafting boundary for generation and synthetic provider evaluation."""

from typing import Any

from navox.communication.schemas import DraftContent
from navox.intelligence.extraction import INSTRUCTION_LIKE_MARKERS


def generated_draft(output: Any, *, recipient: str) -> DraftContent:
    if not isinstance(output, dict) or set(output) != {"subject", "body"}:
        raise ValueError("Generated draft must contain only subject and body")
    content = DraftContent(to=[recipient], subject=output["subject"], body=output["body"])
    text = (content.subject + "\n" + content.body).casefold()
    if any(marker in text for marker in INSTRUCTION_LIKE_MARKERS):
        raise ValueError("Draft contains an instruction-like proposal")
    return content
