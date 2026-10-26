"""Owned request choices and read-only ranked availability response.

The caller supplies search choices only. Source intervals, other people's hours,
exam/deadline context, ownership and observation times are never client facts.
"""

from typing import Literal

from pydantic import Field

from navox.student_availability.schemas import (
    AvailabilityPreview,
    AvailabilitySnapshot,
    FrozenInput,
)
from navox.student_availability.selector import Purpose, SuggestionResult


class AvailabilitySuggestionsRequest(AvailabilityPreview):
    duration_minutes: int = Field(ge=5, le=480, strict=True)
    purpose: Purpose = "other"


class AvailabilitySuggestionsView(FrozenInput):
    snapshot: AvailabilitySnapshot
    selection: SuggestionResult
    changes_applied: Literal[False] = False
