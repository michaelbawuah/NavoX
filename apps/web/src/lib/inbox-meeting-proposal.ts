import { studyDeadlineChoices, studyLocalDateTime } from "./student-planning";
import { addStudentDays } from "./student-schedule";

/** Transient text from an explicitly opened, owner-bound provider message. */
export interface InboxMeetingSource {
  accountKey: string;
  key: string;
  subject: string;
  body: string;
  sentAt: string | null;
  truncated: boolean;
}

export interface GmailMeetingSource extends InboxMeetingSource {
  commitmentId: string;
  sourceId: string;
}

export interface MeetingEvidence {
  field: "subject" | "body";
  start: number;
  end: number;
  quote: string;
}

export interface InboxMeetingProposal {
  state: "none" | "proposed" | "needs_details";
  localStart: string;
  durationMinutes: number;
  evidence: MeetingEvidence[];
  assumptions: string[];
  issues: string[];
}

const MAX_BODY = 24_000;
const MAX_PASSAGE = 800;
const meetingNoun =
  "(?:meeting|appointment|interview|office hours|(?:video |phone )?call)";
const intent = new RegExp(
  `\\b(?:(?:can|could|shall|should|would) (?:we|you|I) (?:meet|chat|talk|have (?:a |an )?${meetingNoun})|let(?:['’]s|s| us) (?:meet|chat|talk|have (?:a |an )?${meetingNoun})|(?:join|attend|schedule|reschedule|arrange|book|confirm|move) (?:our |my |your |the |a |an )?${meetingNoun}|meet (?:on|at|today|tomorrow|next|this|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|${meetingNoun} (?:(?:is|will be) (?:on|at|for|scheduled)|(?:starts|scheduled) (?:on|at|for)|on|at))\\b`,
  "i",
);
const heading = new RegExp(
  `^(?:re:\\s*)?(?:${meetingNoun})(?:\\s*(?::|[-–—])|\\s+(?:invite|invitation|request|on|at)\\b)`,
  "i",
);
const suitability = new RegExp(
  `\\b(?:(?:would|does|will|could) [^?!.\\n]{0,160}\\b(?:work|suit)|(?:are you free|how about) [^?!.\\n]{0,160})[^?!.\\n]{0,80}\\b${meetingNoun}\\b`,
  "i",
);
const meetingIntent = (value: string) =>
  intent.test(value) || suitability.test(value);
const deadline =
  /\b(?:due|deadline|submit|submission|assignment|upload|turn in|hand in)\b/i;
const temporal =
  /\b(?:\d{4}-\d{2}-\d{2}|today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}\s*[:/]\s*\d{1,2}|\d{1,2}\s*[ap]\.?m\.?)\b/i;
const weekdays = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];
const months = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];
const monthName =
  "(?:January|February|March|April|May|June|July|August|September|October|November|December)";
const pad = (value: number) => String(value).padStart(2, "0");
const unique = <T>(values: T[]) => [...new Set(values)];

function realDate(value: string) {
  const date = new Date(`${value}T12:00:00Z`);
  return (
    Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value
  );
}

function passages(source: InboxMeetingSource): MeetingEvidence[] {
  const result: MeetingEvidence[] = [];
  const subject = source.subject.slice(0, 2000);
  const subjectIntent =
    meetingIntent(subject) ||
    (heading.test(subject) && !deadline.test(subject));
  if (subjectIntent)
    result.push({
      field: "subject",
      start: 0,
      end: subject.length,
      quote: subject,
    });
  const body = source.body.slice(0, MAX_BODY);
  const pieces = [...body.matchAll(/[^\r\n]+(?:\r?\n(?!\r?\n)[^\r\n]+)*/g)];
  const bodyIntent = pieces.some((piece) => meetingIntent(piece[0]));
  for (const piece of pieces) {
    const quote = piece[0];
    if (
      !meetingIntent(quote) &&
      !(
        (subjectIntent || bodyIntent) &&
        temporal.test(quote) &&
        !deadline.test(quote)
      )
    )
      continue;
    const start = piece.index;
    result.push({ field: "body", start, end: start + quote.length, quote });
    if (result.length >= 4) break;
  }
  return result;
}

/** Bounded deterministic hints, never an instruction to book, send or approve. */
export function inferInboxMeeting(
  source: InboxMeetingSource,
  timezone: string,
): InboxMeetingProposal {
  const result: InboxMeetingProposal = {
    state: "none",
    localStart: "",
    durationMinutes: 30,
    evidence: [],
    assumptions: [],
    issues: [],
  };
  const found = passages(source);
  if (!found.length) return result;
  result.state = "needs_details";
  result.evidence = found.slice(0, 3).map((part) => ({
    ...part,
    end: part.start + Math.min(part.quote.length, MAX_PASSAGE),
    quote: part.quote.slice(0, MAX_PASSAGE),
  }));
  if (
    found.length > 3 ||
    found.some((part) => part.quote.length > MAX_PASSAGE)
  ) {
    result.issues.push(
      "This passage has more context than a single clear proposal. Read the original and enter the intended date and time.",
    );
    return result;
  }
  if (
    source.truncated ||
    source.body.length > MAX_BODY ||
    source.subject.length > 2000
  )
    result.issues.push(
      "Only part of this email is available here. Check the complete original for changes or other proposals.",
    );
  const text = found.map((part) => part.quote).join("\n");
  const issues = result.issues;
  if (
    /\b(?:cancelled|canceled|cannot|can't|can’t|won't|won’t|no longer|do not|don't|don’t|not available)\b/i.test(
      text,
    )
  )
    issues.push(
      "The passage may cancel or rule out a time. Confirm which meeting, if any, is still being proposed.",
    );
  if (
    /(?:^|\n)\s*>|(?:^|\n)On [^\n]{1,180}wrote:|[- ]Original Message[- ]/i.test(
      source.body.slice(0, MAX_BODY),
    )
  )
    issues.push(
      "This email includes quoted conversation text. Check which proposal is current before choosing a time.",
    );
  let sentDate: string | null = null;
  try {
    if (source.sentAt && Number.isFinite(Date.parse(source.sentAt)))
      sentDate = studyLocalDateTime(source.sentAt, timezone).slice(0, 10);
    // Validate the saved zone even when the message has no usable timestamp.
    new Intl.DateTimeFormat("en-US", { timeZone: timezone });
  } catch {
    issues.push(
      "Your saved timezone could not be read. Check it before choosing a meeting time.",
    );
    return result;
  }

  const dates: string[] = [];
  const absoluteSpans: [number, number][] = [];
  for (const match of text.matchAll(/\b(\d{4}-\d{2}-\d{2})\b/g)) {
    absoluteSpans.push([match.index, match.index + match[0].length]);
    if (realDate(match[1])) dates.push(match[1]);
    else
      issues.push(
        "The email contains a date that is not a real calendar date.",
      );
  }
  const monthPattern = new RegExp(
    `\\b(${monthName})\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?\\b|\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(${monthName})(?:,?\\s+(\\d{4}))?\\b`,
    "gi",
  );
  for (const match of text.matchAll(monthPattern)) {
    if (match[3] || match[6]) {
      const year = match[3] || match[6];
      const month = months.indexOf((match[1] || match[5]).toLowerCase()) + 1;
      const date = `${year}-${pad(month)}-${pad(Number(match[2] || match[4]))}`;
      if (realDate(date)) dates.push(date);
      else
        issues.push(
          "The email contains a date that is not a real calendar date.",
        );
    } else
      issues.push(
        "The date does not include a year. Enter the full intended date.",
      );
  }
  for (const match of text.matchAll(
    /\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b/g,
  )) {
    if (
      !absoluteSpans.some(
        ([start, end]) =>
          match.index >= start && match.index + match[0].length <= end,
      )
    )
      issues.push(
        "The numeric date is ambiguous. Enter the intended day, month and year.",
      );
  }
  const relativeDates = [
    ...text.matchAll(/\b(day after tomorrow|today|tomorrow)\b/gi),
  ];
  for (const match of relativeDates) {
    if (!sentDate)
      issues.push(
        "A relative date needs the email’s sent date. Enter the full intended date.",
      );
    else {
      dates.push(
        addStudentDays(
          sentDate,
          match[1].toLowerCase() === "today"
            ? 0
            : match[1].toLowerCase() === "tomorrow"
              ? 1
              : 2,
        ),
      );
      result.assumptions.push(
        `“${match[1]}” is relative to the email’s sent date, ${sentDate} in ${timezone}.`,
      );
    }
  }
  const weekdayMatches = [
    ...text.matchAll(
      /\b(?:(this|next)\s+)?(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\b/gi,
    ),
  ];
  const absoluteDates = unique(dates);
  for (const match of weekdayMatches) {
    const weekday = weekdays.indexOf(match[2].toLowerCase());
    // A weekday next to an explicit date must agree with it; it is not a new date.
    if (absoluteDates.length === 1 && relativeDates.length === 0) {
      if (new Date(`${absoluteDates[0]}T12:00:00Z`).getUTCDay() !== weekday)
        issues.push(
          "The written weekday and calendar date disagree. Choose the intended date.",
        );
      continue;
    }
    if (!sentDate) {
      issues.push(
        "A weekday needs the email’s sent date. Enter the full intended date.",
      );
      continue;
    }
    const sentWeekday = new Date(`${sentDate}T12:00:00Z`).getUTCDay();
    const ahead = (weekday - sentWeekday + 7) % 7;
    const qualifier = match[1]?.toLowerCase();
    if (
      (qualifier === "next" &&
        ahead > 0 &&
        weekday > sentWeekday &&
        weekday !== 0) ||
      (!qualifier && ahead === 0)
    ) {
      issues.push(
        `“${match[0]}” could mean more than one date. Enter the date the sender intended.`,
      );
      continue;
    }
    const offset =
      qualifier === "this"
        ? ((weekday + 6) % 7) - ((sentWeekday + 6) % 7)
        : ahead || 7;
    dates.push(addStudentDays(sentDate, offset));
    result.assumptions.push(
      `“${match[0]}” was read from the sent date ${sentDate} in ${timezone}; it was not moved forward from today.`,
    );
  }

  const clocks: string[] = [];
  const clockSpans: [number, number][] = [];
  const meridiem = /\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])/gi;
  for (const match of text.matchAll(meridiem)) {
    clockSpans.push([match.index, match.index + match[0].length]);
    const hour = Number(match[1]),
      minute = Number(match[2] ?? "0");
    if (hour < 1 || hour > 12 || minute > 59)
      issues.push(
        "The written clock time is invalid. Correct it before checking.",
      );
    else
      clocks.push(
        `${pad((hour % 12) + (match[3].toLowerCase() === "p" ? 12 : 0))}:${pad(minute)}`,
      );
  }
  for (const match of text.matchAll(/\b(\d{1,2}):(\d{2})\b/g)) {
    if (
      clockSpans.some(
        ([start, end]) => match.index >= start && match.index < end,
      )
    )
      continue;
    clockSpans.push([match.index, match.index + match[0].length]);
    const hour = Number(match[1]),
      minute = Number(match[2]);
    if (hour > 23 || minute > 59)
      issues.push(
        "The written clock time is invalid. Correct it before checking.",
      );
    else if (hour >= 1 && hour <= 12 && !match[1].startsWith("0"))
      issues.push("The time does not say AM or PM. Choose the intended time.");
    else {
      clocks.push(`${pad(hour)}:${pad(minute)}`);
      result.assumptions.push(
        `“${match[0]}” was read as a 24-hour clock time.`,
      );
    }
  }
  for (const match of text.matchAll(/\b(noon|midnight)\b/gi)) {
    clocks.push(match[1].toLowerCase() === "noon" ? "12:00" : "00:00");
    if (match[1].toLowerCase() === "midnight")
      result.assumptions.push(
        "Midnight was read as the start of the chosen date. Confirm which night the sender means.",
      );
  }
  if (
    /\b(?:at|around|after|before|between|about)\s+\d{1,2}\b(?![:\d])(?:\s*(?![ap]\.?m\b)[a-z]|[?.!,]|$)/i.test(
      text,
    ) &&
    clocks.length === 0
  )
    issues.push("The time does not say AM or PM. Choose the intended time.");
  if (
    /\b(?:around|about|before|after|between|from)\s+(?:\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?|noon|midnight)(?![a-z0-9])/i.test(
      text,
    ) ||
    /\b(?:or|until|to)\s+\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?|:)/i.test(text)
  )
    issues.push(
      "The email gives a range, alternative or approximate time. Choose one exact start time.",
    );
  if (
    /\b(?:UTC|GMT|[ECMP][SD]?T|BST|CET|CEST|IST|AEST|AEDT|JST|local time|your time|my time|(?:New York|London|Paris|Tokyo|Sydney|Pacific|Eastern|Central|Mountain) (?:standard |daylight )?time)\b|\b[A-Za-z_]+\/[A-Za-z_]+\b|T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})/i.test(
      text,
    )
  )
    issues.push(
      "The email names a timezone or offset. Convert and confirm the time in your saved timezone below.",
    );
  else
    result.assumptions.push(
      `The email does not specify a timezone. The time below uses your saved zone, ${timezone}.`,
    );

  const durations: number[] = [];
  for (const match of text.matchAll(
    /\bfor\s+(\d{1,3}(?:\.5)?)\s*(minutes?|mins?|hours?|hrs?)\b|\b(\d{1,3})[- ](?:minute|min|hour|hr)[- ](?:meeting|call|appointment|interview)\b/gi,
  )) {
    const amount = Number(match[1] ?? match[3]);
    const unit =
      match[2] ?? (/[- ](?:hour|hr)[- ]/i.test(match[0]) ? "hour" : "minute");
    durations.push(amount * (/^(?:hour|hr)/i.test(unit) ? 60 : 1));
  }
  if (
    unique(durations).length === 1 &&
    Number.isInteger(durations[0]) &&
    durations[0] >= 1 &&
    durations[0] <= 480
  )
    result.durationMinutes = durations[0];
  else if (durations.length)
    issues.push(
      "The duration is unclear or outside 1–480 minutes. Correct it below.",
    );
  else
    result.assumptions.push(
      "The email does not give a clear duration. 30 minutes is a starting value for you to review.",
    );

  const distinctDates = unique(dates),
    distinctClocks = unique(clocks);
  if (distinctDates.length > 1 || distinctClocks.length > 1)
    issues.push(
      "More than one date or time appears in the proposal. Choose the intended option instead of assuming the first.",
    );
  if (
    !distinctDates.length &&
    !issues.some((issue) => /date|year|weekday/.test(issue))
  )
    issues.push("No full meeting date could be read. Enter the intended date.");
  if (
    !distinctClocks.length &&
    !issues.some((issue) => /time|AM or PM/.test(issue))
  )
    issues.push(
      "No exact meeting time could be read. Enter the intended time.",
    );
  if (
    !issues.length &&
    distinctDates.length === 1 &&
    distinctClocks.length === 1
  ) {
    const local = `${distinctDates[0]}T${distinctClocks[0]}`;
    if (studyDeadlineChoices(local, timezone).length !== 1)
      issues.push(
        "This time falls in a clock change and is missing or repeated. Choose an unambiguous local time.",
      );
    else {
      result.localStart = local;
      result.state = "proposed";
    }
  }
  result.issues = unique(issues);
  result.assumptions = unique(result.assumptions);
  return result;
}
