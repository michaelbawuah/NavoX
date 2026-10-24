import {
  type StudentMeetingKind,
  StudentRequestError,
  studentMeetingKinds,
} from "./student-schedule";

export interface StudentScreenshotCandidate {
  course_name: string;
  course_code: string | null;
  kind: StudentMeetingKind;
  weekdays: number[];
  start_time: string | null;
  end_time: string | null;
  timezone: null;
  building: string | null;
  room: string | null;
  instructor: string | null;
  uncertainties: string[];
}
export interface StudentScreenshotPreview {
  extracted_text: string;
  candidates: StudentScreenshotCandidate[];
  warnings: string[];
  requires_review: true;
}
function fail(): never {
  throw new Error(
    "The screenshot preview could not be read. Try adding the class by hand.",
  );
}
export function readStudentScreenshotPreview(
  value: unknown,
): StudentScreenshotPreview {
  if (!value || typeof value !== "object") return fail();
  const v = value as Record<string, unknown>;
  if (
    typeof v.extracted_text !== "string" ||
    v.requires_review !== true ||
    !Array.isArray(v.candidates) ||
    !Array.isArray(v.warnings) ||
    v.warnings.some((row) => typeof row !== "string")
  )
    return fail();
  const candidates = v.candidates.map((row) => {
    if (!row || typeof row !== "object") return fail();
    const c = row as Record<string, unknown>;
    if (
      typeof c.course_name !== "string" ||
      !(c.course_code === null || typeof c.course_code === "string") ||
      !studentMeetingKinds.includes(c.kind as StudentMeetingKind) ||
      !Array.isArray(c.weekdays) ||
      c.weekdays.some(
        (day) =>
          typeof day !== "number" ||
          !Number.isInteger(day) ||
          day < 0 ||
          day > 6,
      ) ||
      !(
        c.start_time === null ||
        (typeof c.start_time === "string" &&
          /^([01]\d|2[0-3]):[0-5]\d$/.test(c.start_time))
      ) ||
      !(
        c.end_time === null ||
        (typeof c.end_time === "string" &&
          /^([01]\d|2[0-3]):[0-5]\d$/.test(c.end_time))
      ) ||
      c.timezone !== null ||
      ![c.building, c.room, c.instructor].every(
        (item) => item === null || typeof item === "string",
      ) ||
      !Array.isArray(c.uncertainties) ||
      c.uncertainties.some((item) => typeof item !== "string")
    )
      return fail();
    return c as unknown as StudentScreenshotCandidate;
  });
  return {
    extracted_text: v.extracted_text,
    candidates,
    warnings: v.warnings as string[],
    requires_review: true,
  };
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export async function readStudentScreenshot(
  file: File,
  pdf = false,
): Promise<StudentScreenshotPreview> {
  if (
    !(
      pdf ? ["application/pdf"] : ["image/png", "image/jpeg", "image/webp"]
    ).includes(
      file.type ||
        (pdf && file.name.toLowerCase().endsWith(".pdf")
          ? "application/pdf"
          : ""),
    )
  )
    throw new Error(
      pdf
        ? "Choose a PDF schedule."
        : "Choose a PNG, JPEG, or WebP screenshot.",
    );
  if (file.size > 5 * 1024 * 1024)
    throw new Error("Choose a screenshot smaller than 5 MB.");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 35_000);
  try {
    const response = await fetch(
      `${apiBaseUrl}/student/imports/${pdf ? "pdf" : "screenshot"}/preview`,
      {
        method: "POST",
        headers: { "Content-Type": pdf ? "application/pdf" : file.type },
        body: file,
        credentials: "include",
        cache: "no-store",
        signal: controller.signal,
      },
    );
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? payload.detail
          : null;
      throw new StudentRequestError(
        typeof detail === "string"
          ? detail
          : "The screenshot could not be read. You can still add the classes by hand.",
      );
    }
    return readStudentScreenshotPreview(payload);
  } catch (cause) {
    if (cause instanceof StudentRequestError) throw cause;
    if (cause instanceof Error && cause.name === "AbortError")
      throw new Error(
        "Reading the screenshot took too long. You can add the class by hand.",
      );
    throw cause;
  } finally {
    clearTimeout(timer);
  }
}

/** Best-effort local draft only. An ambiguous spoken clock stays blank for review. */
export function studentClassPrefill(
  transcript: string,
): StudentScreenshotCandidate | null {
  if (
    !transcript.trim() ||
    transcript.length > 500 ||
    Array.from(transcript).some(
      (character) =>
        character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127,
    ) ||
    !/^\s*(?:please\s+)?(?:add|create)\s+/i.test(transcript)
  )
    return null;
  const text = transcript.trim().replace(/[’]/g, "'");
  const kindMatch =
    /\b(lecture|lab|discussion|recitation|seminar|exam)\b/i.exec(text);
  const codeMatch =
    /^\s*(?:please\s+)?(?:add|create)\s+(?:(?:a|my|the)\s+)?(?:(?:class|lecture|lab|discussion|seminar|recitation|exam)\s+)?([a-z]{2,8})\s*(\d{2,5}[a-z]?)\b/i.exec(
      text,
    );
  const kind = (kindMatch?.[1]?.toLowerCase() ?? "other") as StudentMeetingKind;
  const code = codeMatch
    ? `${codeMatch[1].toUpperCase()} ${codeMatch[2].toUpperCase()}`
    : null;
  const clock = "\\d{1,2}(?::\\d{2})?\\s*(?:a\\.?m\\.?|p\\.?m\\.?)?";
  const range = new RegExp(
    `(?<!\\d)(${clock})\\s*(?:to|–|—|-|until|through)\\s*(${clock})(?!\\d)`,
    "i",
  ).exec(text);
  const warnings: string[] = [];
  function parseClock(raw: string | undefined, label: string): string | null {
    if (!raw) {
      warnings.push(`${label} was not clear. Enter it in the review form.`);
      return null;
    }
    const value = raw.toLowerCase().replaceAll(".", "").replaceAll(" ", "");
    const match = /^(\d{1,2})(?::(\d{2}))?(am|pm)?$/.exec(value);
    if (!match) {
      warnings.push(`Check the ${label.toLowerCase()}.`);
      return null;
    }
    let hour = Number(match[1]);
    const minute = Number(match[2] ?? "0");
    const meridiem = match[3];
    if (minute > 59 || hour > 23 || (meridiem && (hour < 1 || hour > 12))) {
      warnings.push(`Check the ${label.toLowerCase()}.`);
      return null;
    }
    if (!meridiem && !/^(?:0\d|1[3-9]|2[0-3]):[0-5]\d$/.test(value)) {
      warnings.push(`${label} needs AM or PM. NavoX did not guess.`);
      return null;
    }
    if (meridiem) hour = (hour % 12) + (meridiem === "pm" ? 12 : 0);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
  const start = parseClock(range?.[1], "Start time");
  const end = parseClock(range?.[2], "End time");
  if (start && end && end <= start)
    warnings.push(
      "The end is not after the start on the same day. Review both times.",
    );
  const daySource = range ? text.slice(0, range.index) : text;
  const weekdays: number[] = [];
  const dayPattern =
    /\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)s?(?=\d|[^a-z]|$)/gi;
  const aliases: Record<string, number> = {
    mon: 0,
    tue: 1,
    wed: 2,
    thu: 3,
    fri: 4,
    sat: 5,
    sun: 6,
  };
  for (const match of daySource.matchAll(dayPattern)) {
    const day = aliases[match[1].slice(0, 3).toLowerCase()];
    if (!weekdays.includes(day)) weekdays.push(day);
  }
  weekdays.sort((a, b) => a - b);
  if (!weekdays.length)
    warnings.push("Choose the class days in the review form.");
  let building: string | null = null;
  let room: string | null = null;
  const tail = range
    ? text
        .slice(range.index + range[0].length)
        .trim()
        .replace(/^[,;]\s*/, "")
        .replace(/^(?:in|at)\s+/i, "")
        .replace(/[.!]$/, "")
        .trim()
    : "";
  if (
    tail &&
    !/\b(?:and|then|starting|until|from|semester|term|email|send|professor)\b/i.test(
      tail,
    ) &&
    tail.length <= 160
  ) {
    const roomMatch =
      /^(.+?)\s+(?:room|rm\.?)\s+([a-z]?\d{1,4}[a-z]?)$/i.exec(tail) ??
      /^(.+?[a-z])\s*(\d{2,4}[a-z]?)$/i.exec(tail);
    if (roomMatch) {
      building = roomMatch[1].trim();
      room = roomMatch[2];
    } else building = tail;
  } else warnings.push("Confirm the building and room in the review form.");
  let name = code ?? "";
  if (!code) {
    name = text.replace(
      /^\s*(?:please\s+)?(?:add|create)\s+(?:(?:a|my|the)\s+)?(?:class\s+)?/i,
      "",
    );
    if (kindMatch)
      name = name
        .slice(
          0,
          Math.max(0, name.toLowerCase().indexOf(kindMatch[1].toLowerCase())),
        )
        .trim();
    else name = "";
    warnings.push(
      "Confirm the course name and add a course code if you have one.",
    );
  } else
    warnings.push(
      "The course code is used as its label. Add the full course name if you like.",
    );
  if (!kindMatch)
    warnings.push(
      "Choose a section type if this is a lecture, lab, or discussion.",
    );
  warnings.push("Confirm the term dates and time zone before saving.");
  return {
    course_name: name,
    course_code: code,
    kind,
    weekdays,
    start_time: start,
    end_time: end,
    timezone: null,
    building,
    room,
    instructor: null,
    uncertainties: warnings,
  };
}
