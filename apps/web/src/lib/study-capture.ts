import type { StudentCourse } from "./student-schedule";
import { addStudentDays, studentDateInZone } from "./student-schedule";
export function parseStudyCapture(
  text: string,
  courses: Pick<StudentCourse, "id" | "code" | "name">[],
  timezone: string,
  now = Date.now(),
) {
  const original = text.trim().slice(0, 200),
    notes: string[] = [];
  const numbers: Record<string, number> = {
    one: 1,
    two: 2,
    three: 3,
    four: 4,
    five: 5,
    six: 6,
    seven: 7,
    eight: 8,
    nine: 9,
    ten: 10,
    eleven: 11,
    twelve: 12,
    half: 0.5,
    an: 1,
    a: 1,
  };
  const effort =
    /\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|half|an|a)\s*(hours?|hrs?|h|minutes?|mins?|m)\b/gi;
  let minutes = 0,
    effortFound = false;
  const spans: string[] = [];
  for (const m of original.matchAll(effort)) {
    const n = Number(m[1]) || numbers[m[1].toLowerCase()] || 0;
    minutes += n * (m[2].toLowerCase().startsWith("h") ? 60 : 1);
    effortFound = true;
    spans.push(m[0]);
  }
  minutes = effortFound ? Math.round(minutes) : 60;
  if (minutes < 1 || minutes > 10080) {
    minutes = 60;
    notes.push("Check the effort estimate; that amount could not be used.");
  } else if (!effortFound)
    notes.push("No effort was given; 60 minutes is a starting estimate.");
  const tokens = original.toLowerCase().match(/[a-z]+|\d+[a-z]?/g) ?? [];
  const phrase = (value: string) => {
    const parts = value.toLowerCase().match(/[a-z]+|\d+[a-z]?/g) ?? [];
    return (
      parts.length > 0 &&
      tokens.some((_, i) => parts.every((part, j) => tokens[i + j] === part))
    );
  };
  const exact = courses.filter(
    (c) => (c.code && phrase(c.code)) || (c.name.length > 3 && phrase(c.name)),
  );
  const prefix = courses.filter((c) => {
    const dept = c.code.match(/^[a-z]+/i)?.[0];
    return dept && new RegExp(`\\b${dept}\\b`, "i").test(original);
  });
  const matched =
    exact.length === 1
      ? exact
      : exact.length === 0 && prefix.length === 1
        ? prefix
        : [];
  const course_id = matched[0]?.id ?? null;
  if (!course_id) notes.push("Choose the course if this task belongs to one.");
  const today = studentDateInZone(new Date(now).toISOString(), timezone);
  let date = original.match(/\b\d{4}-\d{2}-\d{2}\b/)?.[0] ?? "";
  const weekdayNames = [
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
  ];
  const weekday = new RegExp(
    "\\b(next\\s+)?(" +
      weekdayNames.map((d) => `${d}|${d.slice(0, 3)}`).join("|") +
      ")\\b",
    "i",
  ).exec(original);
  if (!date) {
    const relative = /\bin\s+(\d{1,2})\s+days?\b/i.exec(original);
    if (/\btomorrow\b/i.test(original)) date = addStudentDays(today, 1);
    else if (/\btoday\b/i.test(original)) date = today;
    else if (relative) date = addStudentDays(today, Number(relative[1]));
    else if (weekday) {
      const target = weekdayNames.findIndex((d) =>
        d.startsWith(weekday[2].toLowerCase()),
      );
      let delta = (target - new Date(`${today}T12:00:00Z`).getUTCDay() + 7) % 7;
      if (weekday[1] && delta === 0) delta = 7;
      date = addStudentDays(today, delta);
    }
  }
  let deadlineLocal = "";
  if (date) {
    const clock = /\b(?:at|by)\s+(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b/i.exec(
      original,
    );
    let time = "23:59";
    if (clock && Number(clock[1]) >= 1 && Number(clock[1]) <= 12)
      time =
        String(
          (Number(clock[1]) % 12) + (clock[3].toLowerCase() === "pm" ? 12 : 0),
        ).padStart(2, "0") +
        ":" +
        (clock[2] ?? "00");
    else if (/\bnoon\b/i.test(original)) time = "12:00";
    else if (/\bmidnight\b/i.test(original)) time = "00:00";
    else
      notes.push(
        "No deadline time was given; 11:59 PM is suggested. Confirm the date and time.",
      );
    deadlineLocal = `${date}T${time}`;
  } else
    notes.push("Add the deadline so NavoX can check whether the work fits.");
  let title = original;
  for (const span of spans) title = title.replace(span, "");
  title = title
    .replace(/\b(?:about|roughly|approximately)\s*(?=,|$)/gi, "")
    .replace(
      /[,;]\s*(?:due\s+)?(?:next\s+)?(?:today|tomorrow|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)(?:\s+(?:at|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))?/gi,
      "",
    )
    .replace(/\s*[,;]\s*$/g, "")
    .trim();
  return {
    title: title.length >= 2 ? title : original,
    course_id,
    estimated_minutes: minutes,
    deadlineLocal,
    notes,
  };
}
