"""Bounded email triage from visible text; source text never supplies instructions.

These rules suggest review categories, not sender trust, course association, or
permission to act. Only one unambiguous, explicitly zoned timestamp can become
a proposed deadline. The caller still owns confirmation and source freshness.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_SUBJECT = 2_000
MAX_CONTENT = 12_000
MAX_QUOTE = 240
Source = Literal["subject", "content"]


class Evidence(TypedDict):
    source: Source
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class _Deadline:
    evidence: Evidence
    timestamp_span: tuple[Source, int, int] | None
    timestamp: datetime | None
    ambiguity: str | None


_CUE = re.compile(r"\b(?:deadline|due(?!\s+(?:to|diligence|process)\b)|submit)\b", re.I)
_STAMP = re.compile(
    r"(?<![\w\d])\d{4}-\d{2}-\d{2}[T ](?:[01]\d|2[0-3]):[0-5]\d"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
    r"(?![\w\d:+-])"
)
_DATE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}|\b\d{1,2}:\d{2}|"
    r"\b(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{1,2}\s*(?:am|pm)|"
    r"today|tomorrow|tonight|midnight|noon|next\s+week|end\s+of\s+(?:day|week|month)|"
    r"in\s+\d{1,3}\s+(?:days?|hours?|weeks?)|"
    r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2})\b",
    re.I,
)
_RELATIVE = re.compile(
    r"\b(?:today|tomorrow|tonight|next\s+week|end\s+of\s+(?:day|week|month)|"
    r"in\s+\d{1,3}\s+(?:days?|hours?|weeks?)|"
    r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?)\b",
    re.I,
)
_REPLY = re.compile(
    r"\b(?:please\s+(?:reply|respond|confirm|rsvp|let\s+(?:me|us)\s+know)|"
    r"(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:reply|respond|confirm|rsvp)|"
    r"(?:reply|respond|rsvp)\s+(?:by|with|to\s+this\s+(?:message|email))|"
    r"let\s+(?:me|us)\s+know|please\s+(?:send|email)\s+(?:me|us)|"
    r"(?:your\s+)?(?:response|reply|confirmation)\s+(?:is\s+)?(?:required|requested|needed))\b",
    re.I,
)
_NEWSLETTER = re.compile(r"\bnewsletter\b", re.I)
_UNSUBSCRIBE = re.compile(r"\bunsubscribe\b", re.I)
_NEGATIVE_BEFORE = re.compile(
    r"\b(?:no|not|without|do\s+not|don['’]t|never)\s+(?:an?\s+|any\s+)?$", re.I
)
_UNCERTAIN = re.compile(
    r"\b(?:if|unless|maybe|might|tentative|tentatively|example|not|no\s+longer|"
    r"cancelled|canceled|withdrawn|removed|superseded|former|previous)\b",
    re.I,
)


def _evidence(source: Source, text: str, start: int, end: int) -> Evidence:
    end = min(end, start + MAX_QUOTE)
    return {"source": source, "quote": text[start:end], "start": start, "end": end}


def _window(text: str, start: int) -> int:
    end = min(len(text), start + MAX_QUOTE)
    boundary = re.search(r"[.!?;\n\r]", text[start:end])
    return start + boundary.start() if boundary else end


def _negated(text: str, start: int) -> bool:
    if re.match(r"due\s+until\b", text[start:], re.I):
        # "Not due until" still identifies timing, but its negation requires review.
        return False
    return _NEGATIVE_BEFORE.search(text[max(0, start - 60) : start]) is not None


def _deadlines(source: Source, text: str) -> list[_Deadline]:
    mentions = []
    seen: set[tuple[Source, int, int]] = set()
    for cue in _CUE.finditer(text):
        if _negated(text, cue.start()):
            continue
        end = _window(text, cue.start())
        tail = text[cue.end() : end]
        if cue[0].lower() == "submit" and not _DATE.search(tail):
            continue
        stamps = list(_STAMP.finditer(tail))
        evidence_end = cue.end() + stamps[-1].end() if stamps else end
        evidence = _evidence(source, text, cue.start(), evidence_end)
        if not stamps:
            missing_ambiguity = (
                "Relative deadline dates need confirmation."
                if _RELATIVE.search(tail)
                else "The deadline needs an explicit date, time and UTC offset."
            )
            mentions.append(_Deadline(evidence, None, None, missing_ambiguity))
            continue
        for stamp in stamps:
            span = (source, cue.end() + stamp.start(), cue.end() + stamp.end())
            if span in seen:
                continue
            seen.add(span)
            try:
                timestamp = datetime.fromisoformat(stamp[0].replace("Z", "+00:00"))
            except ValueError:
                mentions.append(
                    _Deadline(
                        evidence, span, None, "A deadline timestamp is invalid or unsupported."
                    )
                )
                continue
            # Do not choose between a literal time and another date/relative cue.
            remaining = tail[: stamp.start()] + tail[stamp.end() :]
            ambiguity: str | None = None
            if _DATE.search(remaining):
                ambiguity = "Multiple deadline timing references need review."
            elif _UNCERTAIN.search(text[max(0, cue.start() - 30) : end]):
                ambiguity = "Conditional or negated deadline wording needs confirmation."
            mentions.append(_Deadline(evidence, span, timestamp, ambiguity))
    return mentions


def _append_unique(values: list[str], value: str) -> None:
    if value not in values and len(values) < 3:
        values.append(value)


def _add_evidence(values: list[Evidence], value: Evidence) -> None:
    if value not in values and len(values) < 3:
        values.append(value)


def classify(*, subject: str, content: str, timezone: str, now: datetime) -> dict[str, object]:
    """Return a private, deterministic review suggestion with exact source spans.

    ``timezone`` and ``now`` are validated, never used to guess a missing date or
    offset. Invalid input raises a content-free ValueError. No model, network or
    provider calls are performed.
    """
    if (
        not isinstance(subject, str)
        or not isinstance(content, str)
        or len(subject) > MAX_SUBJECT
        or len(content) > MAX_CONTENT
        or any(ord(char) < 32 and char not in "\n\r\t" for char in subject + content)
    ):
        raise ValueError("Email text is invalid or exceeds the triage limits")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Email triage requires an aware current time")
    try:
        if not isinstance(timezone, str) or not 1 <= len(timezone) <= 64:
            raise ValueError
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("Email triage requires a valid IANA timezone") from None

    texts: list[tuple[Source, str]] = [("subject", subject), ("content", content)]
    deadlines = [item for source, text in texts for item in _deadlines(source, text)]
    replies = [
        _evidence(source, text, match.start(), min(_window(text, match.start()), match.end() + 100))
        for source, text in texts
        for match in _REPLY.finditer(text)
        if not _negated(text, match.start())
        and not re.search(
            r"\b(?:you\s+(?:can|may)|feel\s+free\s+to)\s*$", text[: match.start()], re.I
        )
        and not re.search(
            r"\bif\s+you\s+(?:want|wish|like)\b", text[match.end() : match.end() + 60], re.I
        )
    ]
    evidence: list[Evidence] = []
    reasons: list[str] = []
    ambiguities: list[str] = []
    proposed: str | None = None
    category = "fyi"
    if deadlines:
        category = "has_deadline"
        reasons.append("Contains an explicit deadline cue; review its timing.")
        for item in deadlines:
            if item.ambiguity:
                _append_unique(ambiguities, item.ambiguity)
        spans = {item.timestamp_span for item in deadlines if item.timestamp_span is not None}
        if len(spans) > 1:
            _append_unique(ambiguities, "Multiple deadline references need review.")
        if len(spans) == 1 and not ambiguities:
            timestamp = next(item.timestamp for item in deadlines if item.timestamp is not None)
            assert timestamp is not None
            proposed = timestamp.isoformat()
        for item in deadlines[: 2 if replies else 3]:
            _add_evidence(evidence, item.evidence)
    if replies:
        if not deadlines:
            category = "needs_reply"
        reasons.append("Contains an explicit request for a response.")
        for reply_evidence in replies:
            _add_evidence(evidence, reply_evidence)
        if not deadlines and any(_DATE.search(item["quote"]) for item in replies):
            _append_unique(ambiguities, "The response timing reference needs review.")
    if not deadlines and not replies:
        newsletters = [
            _evidence(source, text, match.start(), match.end())
            for source, text in texts
            for match in _NEWSLETTER.finditer(text)
        ]
        unsubscribe = [
            _evidence(source, text, match.start(), match.end())
            for source, text in texts
            for match in _UNSUBSCRIBE.finditer(text)
        ]
        if newsletters and unsubscribe:
            category = "ignore"
            reasons.append(
                "Contains newsletter and unsubscribe markers without a matched action cue."
            )
            _add_evidence(evidence, newsletters[0])
            _add_evidence(evidence, unsubscribe[0])
        else:
            reasons.append("No explicit deadline or response request matched these rules.")
            for marker in (newsletters + unsubscribe)[:3]:
                _add_evidence(evidence, marker)
    return {
        "category": category,
        "reasons": reasons[:3],
        "evidence": evidence,
        "proposed_deadline": proposed,
        "ambiguities": ambiguities,
    }
