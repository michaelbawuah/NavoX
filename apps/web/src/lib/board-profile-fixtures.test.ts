import { describe, expect, it } from "vitest";
import rawCorpus from "../../../../evals/board/student-profiles.json";

type Card = {
  id: string;
  kind: "exam" | "missed" | "topic" | "assignment" | "starter";
  courseId?: string;
  mode: "explain" | "solve" | "check" | "recap" | "exam";
  label?: string;
};
type Evidence =
  | { kind: "quick_check"; at: string; correct: boolean; attempt: number }
  | { kind: "self_report"; at: string; value: "already_know" };
type Topic = {
  id: string;
  courseId: string;
  name: string;
  status: "not_started" | "shaky" | "getting_it" | "mastered";
  evidence: Evidence[];
};
type Profile = {
  id: string;
  description: string;
  studentName: string;
  now: string;
  timeZone: string;
  wakingAt: string;
  bedtime: string;
  courseIds: string[];
  busyIntervals: { start: string; end: string }[];
  exams: {
    id: string;
    courseId: string;
    at: string;
    scopeConfirmed: boolean;
    topicIds: string[];
  }[];
  deadlines: { id: string; courseId: string; at: string; graded: boolean }[];
  missedLectures: {
    id: string;
    courseId: string;
    at: string;
    confirmed: boolean;
    state: "open" | "resolved";
  }[];
  topics: Topic[];
  dismissedCards: { id: string; localDate: string }[];
  expected: {
    cards: Card[];
    topicEvidenceLabels: Record<string, string>;
    calendarProposals: {
      examId: string;
      topicIds: string[];
      kind: "practice" | "final_review";
      start: string;
      end: string;
      status: "proposed";
      requiresReview: boolean;
      calendarWritePerformed: boolean;
    }[];
    proposalReason: string;
  };
};
const corpus = rawCorpus as unknown as {
  version: number;
  synthetic: boolean;
  purpose: string;
  fixturePolicy: Record<string, unknown>;
  courses: { id: string; code: string; name: string }[];
  starterCards: Card[];
  profiles: Profile[];
};

function localParts(at: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(at));
  const part = (name: string) =>
    parts.find((item) => item.type === name)?.value;
  return {
    date: `${part("year")}-${part("month")}-${part("day")}`,
    minutes: Number(part("hour")) * 60 + Number(part("minute")),
  };
}

const minutes = (clock: string) => {
  const [hours, mins] = clock.split(":").map(Number);
  return hours * 60 + mins;
};

// This small reference selection checks the hand-specified expectations only.
// It neither imports a production selector nor claims a working Board home API.
function referenceCards(profile: Profile): Card[] {
  const now = Date.parse(profile.now);
  const inWindow = (at: string, days: number) => {
    const elapsed = Date.parse(at) - now;
    return elapsed >= 0 && elapsed <= days * 86_400_000;
  };
  const owned = (item: { courseId: string }) =>
    profile.courseIds.includes(item.courseId);
  const byTime = (
    a: { at: string; id: string },
    b: { at: string; id: string },
  ) => Date.parse(a.at) - Date.parse(b.at) || a.id.localeCompare(b.id, "en");
  const card = (
    item: { id: string; courseId: string },
    kind: Card["kind"],
    mode: Card["mode"],
  ): Card => ({
    id: `${kind}:${item.id}`,
    kind,
    courseId: item.courseId,
    mode,
  });
  const candidates = [
    ...profile.exams
      .filter((item) => owned(item) && inWindow(item.at, 7))
      .sort(byTime)
      .map((item) => card(item, "exam", "exam")),
    ...profile.missedLectures
      .filter(
        (item) =>
          owned(item) &&
          item.confirmed &&
          item.state === "open" &&
          Date.parse(item.at) <= now,
      )
      .sort(byTime)
      .map((item) => card(item, "missed", "recap")),
    ...profile.topics
      .filter((item) => owned(item) && item.status === "shaky")
      .sort(
        (a, b) =>
          a.name.localeCompare(b.name, "en") || a.id.localeCompare(b.id, "en"),
      )
      .map((item) => card(item, "topic", "solve")),
    ...profile.deadlines
      .filter((item) => owned(item) && inWindow(item.at, 3))
      .sort(byTime)
      .map((item) =>
        card(item, "assignment", item.graded ? "solve" : "explain"),
      ),
    ...corpus.starterCards,
  ];
  const today = localParts(profile.now, profile.timeZone).date;
  return candidates
    .filter(
      (item) =>
        !profile.dismissedCards.some(
          (dismissal) =>
            dismissal.id === item.id && dismissal.localDate === today,
        ),
    )
    .slice(0, 4);
}

function evidenceLabel(topic: Topic): string {
  const latest = topic.evidence.at(-1);
  if (!latest) return "No learning evidence";
  if (latest.kind === "self_report") return "Self-reported mastery";
  if (latest.attempt > 1) return "Edited answer: not first-attempt evidence";
  return `First-attempt quick check: ${latest.correct ? "correct" : "incorrect"}`;
}

function assertInstant(value: string) {
  expect(value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3})?Z$/u);
  expect(Number.isFinite(Date.parse(value))).toBe(true);
}

describe("synthetic Board profile corpus (fixture integrity, not API acceptance)", () => {
  it("contains ten bounded, synthetic profiles and documents its edge-case conventions", () => {
    expect(corpus.version).toBe(1);
    expect(corpus.synthetic).toBe(true);
    expect(corpus.profiles).toHaveLength(10);
    expect(new Set(corpus.profiles.map((item) => item.id)).size).toBe(10);
    expect(JSON.stringify(corpus).length).toBeLessThan(40_000);
    expect(corpus.purpose).toContain("no production home API");
    expect(corpus.fixturePolicy.fallback).toContain(
      "not specified by the guide",
    );
    expect(corpus.fixturePolicy.calendar).toContain("have not been written");
    expect(corpus.fixturePolicy.evidence).toContain(
      "do not establish real mastery",
    );
    expect(corpus.starterCards.map((item) => item.label)).toEqual([
      "Upload your slides",
      "Snap a problem",
      "Explain a concept",
      "Quiz me",
    ]);
  });

  it.each(corpus.profiles)(
    "$id has valid ownership references and aware clocks",
    (profile) => {
      assertInstant(profile.now);
      expect(() => localParts(profile.now, profile.timeZone)).not.toThrow();
      expect(profile.studentName).toBe("Student Example");
      expect(profile.wakingAt).toMatch(/^\d{2}:\d{2}$/u);
      expect(profile.bedtime).toMatch(/^\d{2}:\d{2}$/u);
      expect(minutes(profile.wakingAt)).toBeLessThan(minutes(profile.bedtime));
      const courseIds = new Set(corpus.courses.map((item) => item.id));
      for (const id of profile.courseIds) expect(courseIds.has(id)).toBe(true);
      for (const item of [
        ...profile.exams,
        ...profile.deadlines,
        ...profile.missedLectures,
        ...profile.topics,
      ]) {
        expect(profile.courseIds).toContain(item.courseId);
      }
      for (const item of [
        ...profile.exams,
        ...profile.deadlines,
        ...profile.missedLectures,
      ]) {
        assertInstant(item.at);
      }
      for (const interval of profile.busyIntervals) {
        assertInstant(interval.start);
        assertInstant(interval.end);
        expect(Date.parse(interval.start)).toBeLessThan(
          Date.parse(interval.end),
        );
      }
      expect(new Set(profile.expected.cards.map((item) => item.id)).size).toBe(
        4,
      );
    },
  );

  it.each(corpus.profiles)(
    "$id has independently consistent card ordering and modes",
    (profile) => {
      expect(referenceCards(profile)).toEqual(profile.expected.cards);
    },
  );

  it("exercises inclusive time windows and user-local dismissal days", () => {
    const get = (id: string) => {
      const profile = corpus.profiles.find((item) => item.id === id);
      if (!profile) throw new Error(`Missing profile ${id}`);
      return profile;
    };
    expect(
      referenceCards(get("inclusive-window-boundaries")).map(
        (item) => item.kind,
      ),
    ).toEqual(["exam", "assignment", "starter", "starter"]);
    expect(
      referenceCards(get("outside-and-expired-windows")).every(
        (item) => item.kind === "starter",
      ),
    ).toBe(true);
    const losAngeles = get("dismissed-on-local-day");
    expect(localParts(losAngeles.now, losAngeles.timeZone).date).toBe(
      "2026-10-19",
    );
    expect(losAngeles.now.slice(0, 10)).toBe("2026-10-20");
    expect(
      referenceCards(losAngeles).some((item) => item.kind === "exam"),
    ).toBe(false);
    const tokyo = get("previous-local-day-dismissal");
    expect(localParts(tokyo.now, tokyo.timeZone).date).toBe("2026-10-20");
    expect(referenceCards(tokyo)[0].kind).toBe("exam");
  });

  it.each(corpus.profiles)(
    "$id retains check and self-report evidence provenance",
    (profile) => {
      expect(
        Object.fromEntries(
          profile.topics.map((topic) => [topic.id, evidenceLabel(topic)]),
        ),
      ).toEqual(profile.expected.topicEvidenceLabels);
      for (const topic of profile.topics) {
        for (const evidence of topic.evidence) {
          assertInstant(evidence.at);
          expect(Date.parse(evidence.at)).toBeLessThanOrEqual(
            Date.parse(profile.now),
          );
          if (evidence.kind === "quick_check") {
            expect(Number.isInteger(evidence.attempt)).toBe(true);
            expect(evidence.attempt).toBeGreaterThan(0);
          } else {
            expect(topic.status).toBe("mastered");
            expect(evidence.value).toBe("already_know");
          }
        }
      }
    },
  );

  it.each(corpus.profiles)(
    "$id proposes only free waking-time sessions pending review",
    (profile) => {
      for (const proposal of profile.expected.calendarProposals) {
        expect(proposal.status).toBe("proposed");
        expect(proposal.requiresReview).toBe(true);
        expect(proposal.calendarWritePerformed).toBe(false);
        assertInstant(proposal.start);
        assertInstant(proposal.end);
        const start = Date.parse(proposal.start);
        const end = Date.parse(proposal.end);
        const exam = profile.exams.find((item) => item.id === proposal.examId);
        expect(exam).toBeDefined();
        if (!exam) throw new Error("Proposal references a missing exam");
        expect(exam.scopeConfirmed).toBe(true);
        expect(start).toBeGreaterThanOrEqual(Date.parse(profile.now));
        expect(end).toBeLessThanOrEqual(Date.parse(exam.at));
        expect((end - start) / 60_000).toBeGreaterThanOrEqual(15);
        expect((end - start) / 60_000).toBeLessThanOrEqual(25);
        const localStart = localParts(proposal.start, profile.timeZone);
        const localEnd = localParts(proposal.end, profile.timeZone);
        expect(localStart.date).toBe(localEnd.date);
        expect(localStart.minutes).toBeGreaterThanOrEqual(
          minutes(profile.wakingAt),
        );
        expect(localEnd.minutes).toBeLessThanOrEqual(minutes(profile.bedtime));
        for (const busy of profile.busyIntervals) {
          expect(
            start < Date.parse(busy.end) && end > Date.parse(busy.start),
          ).toBe(false);
        }
        for (const id of proposal.topicIds) {
          expect(exam.topicIds).toContain(id);
          expect(
            profile.topics.some(
              (topic) => topic.id === id && topic.courseId === exam.courseId,
            ),
          ).toBe(true);
        }
        if (proposal.kind === "final_review") {
          const previousDate = new Date(
            `${localParts(exam.at, profile.timeZone).date}T12:00:00Z`,
          );
          previousDate.setUTCDate(previousDate.getUTCDate() - 1);
          expect(localStart.date).toBe(previousDate.toISOString().slice(0, 10));
        }
      }
    },
  );

  it("does not invent a session when all pre-exam time is busy", () => {
    const busy = corpus.profiles.find(
      (item) => item.id === "exam-with-no-free-time",
    );
    if (!busy) throw new Error("Missing fully busy profile");
    expect(busy.expected.calendarProposals).toEqual([]);
    expect(busy.busyIntervals[0].start).toBe(busy.now);
    expect(busy.busyIntervals[0].end).toBe(busy.exams[0].at);
    expect(busy.expected.proposalReason).toContain("No free interval");
  });
});
