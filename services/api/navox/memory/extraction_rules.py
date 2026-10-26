"""Small source-literal memory rules for completed, owner-authorized student turns.

This is deliberately not a general fact extractor or a safety classifier. Callers
must supply student text, keep provenance/generation fences, and apply the sticky
conversation-wide exclusion before publishing any automatic derivative. Nothing
here changes routines, creates reminders, or establishes that an event happened.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from navox.memory.sensitivity import SENSITIVE


@dataclass(frozen=True)
class LiteralFact:
    key: str
    label: str
    content: str


@dataclass(frozen=True)
class LiteralThread:
    key: str
    topic: str
    statement: str
    event_date: date
    timezone: str
    prompt: str


@dataclass(frozen=True)
class LiteralExtraction:
    facts: tuple[LiteralFact, ...] = ()
    threads: tuple[LiteralThread, ...] = ()
    excluded: bool = False


_PRIVATE_SOURCE = re.compile(
    r"\b(?:anxious|panic|hopeless|worthless|psychiatr\w*|psycholog\w*|clinic\w*|mum|mom|dad|"
    r"overdos\w*|self\s+harm|password|passcode|credentials?|api\s*key|"
    r"access\s*token|secret\s*key|private\s*key|credit\s*card|bank\s*account|"
    r"social\s*security|ssn)\b",
    re.IGNORECASE,
)
_CRISIS = re.compile(
    r"\b(?:(?:kill|hurt|harm|cut)\s+myself|"
    r"(?:want|going|plan|planning|intend|ready)\s+(?:to\s+)?die|"
    r"(?:end|ending)\s+(?:my\s+life|it\s+all)|"
    r"wish\s+I\s+(?:was|were)\s+dead|better\s+off\s+dead|tired\s+of\s+living|"
    r"(?:no\s+(?:reason|point)\s+to\s+live)|"
    r"(?:can(?:not|'t)|unable\s+to)\s+(?:keep\s+myself\s+safe|go\s+on)|"
    r"(?:don'?t|do\s+not)\s+want\s+to\s+(?:live|be\s+here\s+anymore))\b",
    re.IGNORECASE,
)
_UNSAFE_WORDS = re.compile(
    r"\b(?:not|never|no|if|unless|except|would|could|should|might|maybe|perhaps|"
    r"used|formerly|previously|because|but|then|also|said|says|example|pretend|"
    r"suppose|imagine|everything|anything|every|either|both|or|quote|quoted|ignore|"
    r"instruction\w*|system|assistant|model|"
    r"prompt|rules?|delete|send|approve|execute|remind|forget|remember|please)\b",
    re.IGNORECASE,
)
_LANGUAGES = (
    "Python",
    "C",
    "C++",
    "C#",
    "Java",
    "JavaScript",
    "TypeScript",
    "Go",
    "Rust",
    "Swift",
    "Kotlin",
    "Ruby",
    "Julia",
    "MATLAB",
    "SQL",
    "R",
    "Scala",
    "Dart",
)
_LANGUAGE = "(?:" + "|".join(re.escape(value) for value in _LANGUAGES) + ")"
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_TOPICS = "(?:interview|exam|midterm|presentation|project review)"
_THREAD = re.compile(
    rf"I have (?:a|an) (?P<topic>{_TOPICS}) "
    rf"(?P<when>today|tomorrow|on (?:{'|'.join(_WEEKDAYS)}|[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}))",
    re.IGNORECASE,
)
_NAME_TOKEN = re.compile(r"(?:[A-Z][A-Za-z0-9-]*|[0-9]+[A-Z][A-Za-z0-9-]*|of|and|the|&)")


def _safety_text(text: str) -> str:
    # Do not let invisible separators hide a sensitive/crisis keyword. This
    # normalization is for exclusion only; retained facts remain source-literal.
    value = unicodedata.normalize("NFKC", text).replace("’", "'").replace("‘", "'")
    return " ".join(
        "".join(
            char
            if not unicodedata.category(char).startswith("C")
            else " "
            if char.isspace()
            else ""
            for char in value
        ).split()
    )


def excluded_source(texts: Sequence[str]) -> bool:
    """Conservatively exclude automatic derivatives for a sensitive/crisis source.

    This does not erase permitted transcripts or independently reviewed facts.
    The storage layer must make the result sticky and remove earlier automatic
    derivatives if a later completed student turn causes exclusion.
    """
    values = []
    for text in texts:
        value = _safety_text(text)
        values.append(value)
        if SENSITIVE.search(value) or _PRIVATE_SOURCE.search(value) or _CRISIS.search(value):
            return True
    # Consecutive completed fragments can carry one sensitive/crisis phrase.
    combined = " ".join(values)
    return bool(
        SENSITIVE.search(combined) or _PRIVATE_SOURCE.search(combined) or _CRISIS.search(combined)
    )


def _neutral_name(value: str) -> bool:
    tokens = value.split(" ")
    return (
        1 <= len(value) <= 80
        and 1 <= len(tokens) <= 8
        and bool(re.match(r"[A-Z0-9]", value))
        and all(token != "I" and bool(_NAME_TOKEN.fullmatch(token)) for token in tokens)
        and not _UNSAFE_WORDS.search(value)
        and not excluded_source((value,))
    )


def _event_date(when: str, today: date) -> date | None:
    value = when.casefold()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    requested = value.removeprefix("on ")
    if requested in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS.index(requested) - today.weekday()) % 7)
    try:
        result = date.fromisoformat(requested)
    except ValueError:
        return None
    return result if today <= result <= today + timedelta(days=90) else None


def derive_literal_turn(text: str, *, now: datetime, timezone: str) -> LiteralExtraction:
    """Extract one narrow declaration, preserving its exact trimmed source text.

    Dates use the trusted aware server instant and confirmed student timezone.
    A date-only event is a possible future check-in, never a scheduled reminder;
    a caller must wait until after its local date before asking how it went.
    """
    if excluded_source((text,)):
        return LiteralExtraction(excluded=True)
    if (
        not 1 <= len(text) <= 500
        or any(unicodedata.category(char).startswith("C") for char in text)
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return LiteralExtraction()
    try:
        zone = ZoneInfo(timezone)
        today = now.astimezone(zone).date()
    except (ZoneInfoNotFoundError, ValueError, OverflowError):
        return LiteralExtraction()
    statement = text.strip()
    body = statement
    for prefix in ("Remember that ", "Actually, "):
        if body.casefold().startswith(prefix.casefold()):
            body = body[len(prefix) :]
            break
    if body.endswith("."):
        body = body[:-1]
    if (
        any(char in body for char in "\"'‘’“”`.,;:!?/\\(){}[]<>=|\n\r\t")
        or _UNSAFE_WORDS.search(body)
        or "  " in body
    ):
        return LiteralExtraction()

    key, label = "", ""
    if re.fullmatch(r"I prefer (?:short|brief|detailed|balanced) (?:replies|answers)", body, re.I):
        key, label = "reply_length", "Reply length"
    elif re.fullmatch(r"I prefer (?:a )?(?:friendly|professional|direct) tone", body, re.I):
        key, label = "tone", "Writing tone"
    elif re.fullmatch(
        r"I (?:study|focus) best (?:in the (?:morning|afternoon|evening)|at night)", body, re.I
    ):
        key, label = "study_time", "Study time"
    elif re.fullmatch(
        rf"I (?:prefer programming|prefer coding|program|code) in {_LANGUAGE}", body, re.I
    ) or re.fullmatch(rf"I prefer {_LANGUAGE} for (?:programming|coding)", body, re.I):
        key, label = "programming_language", "Programming language"
    else:
        employment = re.fullmatch(r"I work at (.+)", body, re.I)
        school = re.fullmatch(r"I study (.+?) at (.+)", body, re.I)
        if employment and _neutral_name(employment[1]):
            key, label = "employer", "Employer"
        elif school and all(_neutral_name(part) for part in school.groups()):
            key, label = "school", "School"
    if key:
        return LiteralExtraction(facts=(LiteralFact(key, label, statement),))

    thread = _THREAD.fullmatch(body)
    if thread is None:
        return LiteralExtraction()
    try:
        event_date = _event_date(thread["when"], today)
    except OverflowError:
        return LiteralExtraction()
    if event_date is None:
        return LiteralExtraction()
    topic = thread["topic"].casefold()
    return LiteralExtraction(
        threads=(
            LiteralThread(
                key=f"event:{topic}:{event_date.isoformat()}",
                topic=topic,
                statement=statement,
                event_date=event_date,
                timezone=timezone,
                prompt=f"How did your {topic} go?",
            ),
        ),
    )
