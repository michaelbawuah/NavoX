"""Narrow user-request phrases for bounded research, never source instructions."""

import re
from typing import Literal

ResearchIntent = Literal["TIMELINE", "BACKGROUND", "COVERAGE_COMPARISON", "DEEP_RESEARCH"]
PATTERNS: tuple[tuple[ResearchIntent, re.Pattern[str]], ...] = (
    (
        "TIMELINE",
        re.compile(
            r"^(?:(?:show|give) me |build |create )?(?:a |the )?timeline(?: of| for| about)?\b"
        ),
    ),
    (
        "BACKGROUND",
        re.compile(
            r"^(?:give me (?:the )?background(?: on| to)?|"
            r"what(?:'s| is) the background(?: on| to| of)?|"
            r"explain the background(?: on| to| of)?|catch me up on)\b"
        ),
    ),
    (
        "COVERAGE_COMPARISON",
        re.compile(r"^compare (?:the )?(?:sources|coverage|reports)(?: on| of| about| for)?\b"),
    ),
    (
        "DEEP_RESEARCH",
        re.compile(r"^(?:do (?:some )?deep research|research in depth)(?: on| into| about)?\b"),
    ),
)


def research_phrase(question: str) -> tuple[ResearchIntent | None, str]:
    text = " ".join(question.casefold().replace("’", "'").split())
    for intent, pattern in PATTERNS:
        match = pattern.match(text)
        if match:
            return intent, text[match.end() :].strip(" :?!.")
    return None, text
