import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type DayReviewApi,
  DayReviewError,
  dayReviewLinkAllowed,
  dayReviewMilliseconds,
  observeDayReviewPrivacy,
  type ReviewGoal,
  readStudentDayReview,
  StudentDayReviewController,
  type StudentDayReviewView,
  studentDayReviewApi,
} from "./student-day-review";

const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const courseId = "33333333-3333-4333-8333-333333333333";
const meetingId = "44444444-4444-4444-8444-444444444444";
const taskId = "55555555-5555-4555-8555-555555555555";
const goalId = "66666666-6666-4666-8666-666666666666";
const countGoalId = "77777777-7777-4777-8777-777777777777";
const fixedId = "88888888-8888-4888-8888-888888888888";
const accountId = `${workspaceId}:${userId}`;
const windowFixture = {
  starts_at: "2026-10-19T00:00:00Z",
  ends_at: "2026-10-20T00:00:00Z",
  observed_through: "2026-10-19T12:00:00Z",
};

function goalFixture(changes: Partial<ReviewGoal> = {}): ReviewGoal {
  return {
    source: "self_report",
    id: goalId,
    revision: 2,
    title: "Read a few pages",
    category: "habit",
    tracking: "qualitative",
    unit: null,
    cadence: "daily",
    starts_on: "2026-10-19",
    ends_on: null,
    progress: {
      source: "self_report",
      timezone: "UTC",
      week_starts_on: "monday",
      period_start: "2026-10-19",
      period_end: "2026-10-19",
      days_reported: 0,
      reported_count: null,
      target: null,
      remaining_count: null,
      target_met_from_reports: null,
      qualitative_latest: "unknown",
      latest_reported_on: null,
    },
    review_href: "/goals",
    ...changes,
  };
}

function reviewFixture(
  changes: Partial<StudentDayReviewView> = {},
): StudentDayReviewView {
  const selectedWindow = {
    ...windowFixture,
    ends_at:
      changes.kind === "weekly"
        ? "2026-10-26T00:00:00Z"
        : windowFixture.ends_at,
  };
  const occurrence = {
    id: `${meetingId}:2026-10-19`,
    meeting_id: meetingId,
    course_id: courseId,
    course_code: "BIO 101",
    course_name: "Biology",
    kind: "lecture" as const,
    start_at: "2026-10-19T13:00:00Z",
    end_at: "2026-10-19T14:00:00Z",
    timezone: "UTC",
    building: "Science",
    room: "204",
    instructor: null,
    walk_minutes: null,
    cancelled: false,
    exception: false,
  };
  const workTotals = {
    active_tasks: 1,
    overdue_tasks: 0,
    due_in_window_tasks: 1,
  };
  const outcomeTotals = {
    done_blocks: 1,
    took_longer_blocks: 0,
    skipped_blocks: 1,
    recorded_minutes: 25,
  };
  return {
    kind: "morning",
    server_now: windowFixture.observed_through,
    timezone: "UTC",
    local_date: "2026-10-19",
    week_starts_on: "monday",
    window: { ...selectedWindow },
    schedule: {
      source: "saved_timetable",
      status: "complete",
      window: { ...selectedWindow },
      classes: [occurrence],
      fixed_blocks: [
        {
          id: `${fixedId}:2026-10-19`,
          fixed_block_id: fixedId,
          title: "Lunch",
          start_at: "2026-10-19T14:00:00Z",
          end_at: "2026-10-19T14:30:00Z",
        },
      ],
      classes_complete: true,
      fixed_blocks_complete: true,
      class_count: 1,
      fixed_block_count: 1,
      next_class: occurrence,
      review_href: "/classes",
    },
    outlook: {
      source: "saved_timetable",
      status: "complete",
      window: {
        starts_at: selectedWindow.ends_at,
        ends_at:
          changes.kind === "weekly"
            ? "2026-11-02T00:00:00Z"
            : "2026-10-21T00:00:00Z",
        observed_through: windowFixture.observed_through,
      },
      classes: [],
      fixed_blocks: [],
      classes_complete: true,
      fixed_blocks_complete: true,
      class_count: 0,
      fixed_block_count: 0,
      next_class: null,
      review_href: "/classes",
    },
    work: {
      source: "saved_task",
      status: "complete",
      window: { ...selectedWindow },
      items: [
        {
          source: "saved_task",
          source_kind: "email",
          id: taskId,
          revision: 3,
          title: "Review biology notes",
          course_id: courseId,
          due_at: "2026-10-19T17:00:00Z",
          deadline_meaning: "saved_task_deadline",
          remaining_minutes: 30,
          priority: 3,
          review_href: "/planner",
        },
      ],
      totals: { ...workTotals },
      known_totals: { ...workTotals },
      has_more: false,
    },
    outcomes: {
      source: "recorded_planner_outcome",
      status: "complete",
      window: { ...selectedWindow },
      totals: { ...outcomeTotals },
      known_totals: { ...outcomeTotals },
      done_block_is_assignment_submission: false,
    },
    goals: {
      source: "self_report",
      status: "complete",
      items: [goalFixture()],
      periods_meaning: "each_goal_has_its_own_progress_period",
    },
    summary_lines: [
      "1 class is saved for today.",
      "You recorded 1 Done study block today.",
    ],
    notices: [
      {
        code: "saved_records",
        message: "Only saved records and explicit reports are included.",
        review_href: "/connections",
      },
    ],
    question: {
      code: "goal_checkin",
      text: "Would you like to record a goal check-in?",
      review_href: "/goals",
    },
    basis: "saved_records_and_explicit_reports_only",
    automatic_rescheduling: false,
    attendance_sleep_grades_observed: false,
    ...changes,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const controllers = new Set<StudentDayReviewController>();
function fixture(view = reviewFixture(), clock: () => number = () => 0) {
  const api = {
    account: vi.fn<DayReviewApi["account"]>().mockResolvedValue(accountId),
    review: vi.fn<DayReviewApi["review"]>().mockImplementation(async (kind) => {
      const windows = reviewFixture({ kind });
      return {
        ...view,
        kind,
        window: windows.window,
        schedule: { ...view.schedule, window: windows.schedule.window },
        work: { ...view.work, window: windows.work.window },
        outcomes: { ...view.outcomes, window: windows.outcomes.window },
        outlook: { ...view.outlook, window: windows.outlook.window },
      };
    }),
  };
  const controller = new StudentDayReviewController(accountId, api, clock);
  controllers.add(controller);
  return { api, controller };
}

async function microtasks() {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  for (const controller of controllers) controller.dispose();
  controllers.clear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("day review facts and completeness", () => {
  it("projects the exact bounded facts and strips undeclared private or inferred fields", () => {
    const view = reviewFixture();
    expect(
      readStudentDayReview({
        ...view,
        raw_email: "private body",
        inferred_grade: "A",
        schedule: { ...view.schedule, attendance: "present" },
        work: {
          ...view.work,
          items: [
            {
              ...view.work.items[0],
              submitted: true,
              provider_token: "secret",
            },
          ],
        },
      }),
    ).toEqual(view);
  });

  it("allows a future outlook observed before its own starts_at", () => {
    const view = readStudentDayReview(reviewFixture());
    expect(Date.parse(view.outlook.window.observed_through)).toBeLessThan(
      Date.parse(view.outlook.window.starts_at),
    );
    expect(view.outlook.window.starts_at).toBe(view.window.ends_at);
  });

  it("accepts a Monday-based weekly window and the immediately following outlook", () => {
    const view = readStudentDayReview(reviewFixture({ kind: "weekly" }));
    expect(view.window.starts_at).toBe("2026-10-19T00:00:00Z");
    expect(view.window.ends_at).toBe("2026-10-26T00:00:00Z");
    expect(view.outlook.window.ends_at).toBe("2026-11-02T00:00:00Z");
  });

  it("rejects a selected period outside server_now or a disconnected outlook", () => {
    const view = reviewFixture();
    for (const starts_at of ["2026-10-19T13:00:00Z", "2026-10-18T00:00:00Z"]) {
      const selected = {
        ...view.window,
        starts_at,
        ends_at:
          starts_at === "2026-10-18T00:00:00Z"
            ? "2026-10-19T12:00:00Z"
            : view.window.ends_at,
      };
      expect(() =>
        readStudentDayReview({
          ...view,
          window: selected,
          schedule: { ...view.schedule, window: selected },
          work: { ...view.work, window: selected },
          outcomes: { ...view.outcomes, window: selected },
          outlook: {
            ...view.outlook,
            window: { ...view.outlook.window, starts_at: selected.ends_at },
          },
        }),
      ).toThrow();
    }
    expect(() =>
      readStudentDayReview({
        ...view,
        outlook: {
          ...view.outlook,
          window: {
            ...view.outlook.window,
            starts_at: "2026-10-20T01:00:00Z",
          },
        },
      }),
    ).toThrow();
  });

  it("keeps incomplete totals unknown while preserving the verified subset", () => {
    const view = reviewFixture();
    const parsed = readStudentDayReview({
      ...view,
      schedule: {
        ...view.schedule,
        status: "incomplete",
        classes_complete: false,
        class_count: null,
        next_class: null,
      },
      work: { ...view.work, status: "incomplete", totals: null },
      outcomes: { ...view.outcomes, status: "incomplete", totals: null },
      goals: { ...view.goals, status: "incomplete" },
    });
    expect(parsed.schedule.class_count).toBeNull();
    expect(parsed.schedule.fixed_block_count).toBe(1);
    expect(parsed.schedule.classes).toHaveLength(1);
    expect(parsed.work.totals).toBeNull();
    expect(parsed.work.known_totals?.active_tasks).toBe(1);
    expect(parsed.outcomes.totals).toBeNull();
    expect(parsed.outcomes.known_totals?.done_blocks).toBe(1);
  });

  it.each(["disabled", "unavailable"] as const)(
    "accepts a truthful empty %s section without turning it into zero",
    (status) => {
      const view = reviewFixture();
      const parsed = readStudentDayReview({
        ...view,
        schedule: {
          ...view.schedule,
          status,
          classes: [],
          fixed_blocks: [],
          class_count: null,
          fixed_block_count: null,
          next_class: null,
          classes_complete: false,
          fixed_blocks_complete: false,
        },
        work: {
          ...view.work,
          status,
          items: [],
          totals: null,
          known_totals: null,
          has_more: false,
        },
        outcomes: {
          ...view.outcomes,
          status,
          totals: null,
          known_totals: null,
        },
        goals: { ...view.goals, status, items: [] },
      });
      expect(parsed.work.status).toBe(status);
      expect(parsed.work.totals).toBeNull();
      expect(parsed.work.known_totals).toBeNull();
      expect(parsed.outcomes.totals).toBeNull();
      expect(parsed.schedule.class_count).toBeNull();
      expect(parsed.goals.items).toEqual([]);
    },
  );

  it("distinguishes an explicit count of zero from an unreported qualitative goal", () => {
    const unknown = goalFixture();
    const zero = goalFixture({
      id: countGoalId,
      tracking: "count",
      unit: "pages",
      progress: {
        ...unknown.progress,
        days_reported: 1,
        reported_count: 0,
        target: 10,
        remaining_count: 10,
        target_met_from_reports: false,
        latest_reported_on: "2026-10-19",
      },
    });
    const view = reviewFixture();
    const goals = readStudentDayReview({
      ...view,
      goals: { ...view.goals, items: [unknown, zero] },
    }).goals.items;
    expect(goals[0].progress.reported_count).toBeNull();
    expect(goals[0].progress.qualitative_latest).toBe("unknown");
    expect(goals[1].progress.reported_count).toBe(0);
    expect(goals[1].progress.days_reported).toBe(1);
    expect(goals[1].unit).toBe("pages");
  });

  it.each([
    { basis: "model_inferred" },
    { automatic_rescheduling: true },
    { attendance_sleep_grades_observed: true },
    { week_starts_on: "sunday" },
    { kind: "continuous" },
    { timezone: "Not/A_Zone" },
    { local_date: "2026-10-18" },
    { server_now: "2026-10-19T12:00:00" },
  ])(
    "rejects unsupported or contradictory top-level semantics: %j",
    (changes) => {
      expect(() =>
        readStudentDayReview({ ...reviewFixture(), ...changes }),
      ).toThrow();
    },
  );

  it("rejects inferred attendance, submission, source kinds, and mutable action links", () => {
    const view = reviewFixture();
    const bad = [
      { schedule: { ...view.schedule, source: "observed_attendance" } },
      {
        schedule: { ...view.schedule, review_href: "/classes?auto_edit=true" },
      },
      {
        work: {
          ...view.work,
          items: [
            {
              ...view.work.items[0],
              deadline_meaning: "verified_submission_deadline",
            },
          ],
        },
      },
      {
        work: {
          ...view.work,
          items: [{ ...view.work.items[0], source_kind: "inferred_from_mail" }],
        },
      },
      {
        outcomes: {
          ...view.outcomes,
          done_block_is_assignment_submission: true,
        },
      },
      { goals: { ...view.goals, source: "observed_behavior" } },
      { question: { ...view.question, review_href: "https://evil.example" } },
      {
        notices: [
          { code: "link", message: "Go", review_href: "javascript:alert(1)" },
        ],
      },
    ];
    for (const changes of bad)
      expect(() => readStudentDayReview({ ...view, ...changes })).toThrow();
  });

  it("rejects complete totals marked unknown and unavailable sections with retained facts", () => {
    const view = reviewFixture();
    const bad = [
      { work: { ...view.work, totals: null } },
      { work: { ...view.work, status: "incomplete" } },
      { work: { ...view.work, status: "disabled" } },
      { outcomes: { ...view.outcomes, totals: null } },
      { outcomes: { ...view.outcomes, status: "incomplete" } },
      { outcomes: { ...view.outcomes, status: "unavailable" } },
      { schedule: { ...view.schedule, status: "unavailable" } },
      { schedule: { ...view.schedule, class_count: 2 } },
      { schedule: { ...view.schedule, classes_complete: false } },
      { goals: { ...view.goals, status: "disabled" } },
    ];
    for (const changes of bad)
      expect(() => readStudentDayReview({ ...view, ...changes })).toThrow();
  });

  it("rejects a next class that is past, cancelled, or outside the saved returned set", () => {
    const view = reviewFixture();
    for (const changes of [
      { start_at: "2026-10-19T11:00:00Z" },
      { cancelled: true },
      { id: "different-occurrence" },
      { end_at: "2026-10-19T12:00:00Z" },
    ]) {
      expect(() =>
        readStudentDayReview({
          ...view,
          schedule: {
            ...view.schedule,
            next_class: { ...view.schedule.next_class, ...changes },
          },
        }),
      ).toThrow();
    }
  });

  it("checks shared observation windows and each goal's authoritative zone", () => {
    const view = reviewFixture();
    const bad = [
      { window: { ...view.window, observed_through: "2026-10-19T11:00:00Z" } },
      {
        work: {
          ...view.work,
          window: { ...view.window, ends_at: "2026-10-21T00:00:00Z" },
        },
      },
      {
        outcomes: {
          ...view.outcomes,
          window: { ...view.window, starts_at: "2026-10-18T00:00:00Z" },
        },
      },
      {
        outlook: {
          ...view.outlook,
          window: {
            ...view.outlook.window,
            observed_through: "2026-10-19T13:00:00Z",
          },
        },
      },
      {
        goals: {
          ...view.goals,
          items: [
            goalFixture({
              progress: {
                ...goalFixture().progress,
                timezone: "America/New_York",
              },
            }),
          ],
        },
      },
    ];
    for (const changes of bad)
      expect(() => readStudentDayReview({ ...view, ...changes })).toThrow();
  });

  it("bounds lists and source text and rejects invalid goal measurement metadata", () => {
    const view = reviewFixture();
    for (const changes of [
      { summary_lines: Array.from({ length: 11 }, () => "line") },
      { summary_lines: ["🧪".repeat(2001)] },
      {
        work: {
          ...view.work,
          items: Array.from({ length: 21 }, () => view.work.items[0]),
        },
      },
      {
        work: {
          ...view.work,
          totals: { active_tasks: 1, overdue_tasks: 2, due_in_window_tasks: 1 },
        },
      },
      {
        goals: {
          ...view.goals,
          items: Array.from({ length: 4 }, () => goalFixture()),
        },
      },
      {
        goals: {
          ...view.goals,
          items: [goalFixture({ tracking: "count", unit: null })],
        },
      },
      { goals: { ...view.goals, items: [goalFixture({ unit: "pages" })] } },
    ])
      expect(() => readStudentDayReview({ ...view, ...changes })).toThrow();
  });

  it("allows only deployment-enabled destinations for optional sections", () => {
    const links = { planning: false, goals: false, email: false };
    expect(dayReviewLinkAllowed("/classes", links)).toBe(true);
    expect(dayReviewLinkAllowed("/connections", links)).toBe(true);
    expect(dayReviewLinkAllowed("/planner", links)).toBe(false);
    expect(dayReviewLinkAllowed("/goals", links)).toBe(false);
    expect(dayReviewLinkAllowed("/email-actions", links)).toBe(false);
    expect(dayReviewLinkAllowed("/planner", { ...links, planning: true })).toBe(
      true,
    );
    expect(dayReviewLinkAllowed("/goals", { ...links, goals: true })).toBe(
      true,
    );
    expect(
      dayReviewLinkAllowed("/email-actions", { ...links, email: true }),
    ).toBe(true);
  });
});

describe("manual day review transport", () => {
  it("sends only an enum query with no body through an uncached credentialed GET", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      async () =>
        new Response(JSON.stringify(reviewFixture({ kind: "weekly" })), {
          status: 200,
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    const review = await studentDayReviewApi.review("weekly", signal);
    expect(review.kind).toBe("weekly");
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [path, options] = fetcher.mock.calls[0];
    const url = new URL(String(path));
    expect(url.pathname).toBe("/api/v1/student/coach/review");
    expect([...url.searchParams]).toEqual([["kind", "weekly"]]);
    expect(options).toMatchObject({
      credentials: "include",
      cache: "no-store",
      redirect: "error",
    });
    expect(options?.method ?? "GET").toBe("GET");
    expect(options?.body).toBeUndefined();
    expect(options?.signal).toBeInstanceOf(AbortSignal);
  });

  it("rejects injected query values before making any request", async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    await expect(
      studentDayReviewApi.review(
        "morning&reason=private" as "morning",
        new AbortController().signal,
      ),
    ).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("checks exact account IDs through the fixed private account endpoint", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: userId,
          workspace: { id: workspaceId },
          token: "secret",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    expect(
      await studentDayReviewApi.account(new AbortController().signal),
    ).toBe(accountId);
    expect(fetcher.mock.calls[0][0]).toMatch(/\/api\/v1\/auth\/me$/);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      credentials: "include",
      cache: "no-store",
      redirect: "error",
    });
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined();
  });

  it.each([401, 403, 404, 503])(
    "uses bounded static errors for status %s",
    async (status) => {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ detail: "private mood reason or email content" }),
            { status },
          ),
        );
      vi.stubGlobal("fetch", fetcher);
      await expect(
        studentDayReviewApi.review("morning", new AbortController().signal),
      ).rejects.toMatchObject({ status });
      try {
        await studentDayReviewApi.review(
          "morning",
          new AbortController().signal,
        );
      } catch (error) {
        expect(String(error)).not.toMatch(/private mood|email content/);
      }
      expect(fetcher).toHaveBeenCalledTimes(2);
    },
  );

  it("does not retry a failed read or return an unchecked payload", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      studentDayReviewApi.review("evening", new AbortController().signal),
    ).rejects.toThrow("offline");
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ ...reviewFixture(), automatic_rescheduling: true }),
        { status: 200 },
      ),
    );
    await expect(
      studentDayReviewApi.review("morning", new AbortController().signal),
    ).rejects.toBeInstanceOf(DayReviewError);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("owned manual day review lifecycle", () => {
  it("does not read until explicitly opened, including review-type selection", async () => {
    const rig = fixture();
    rig.controller.choose("weekly");
    expect(rig.controller.getSnapshot().kind).toBe("weekly");
    expect(rig.api.account).not.toHaveBeenCalled();
    expect(rig.api.review).not.toHaveBeenCalled();
    await rig.controller.open();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    expect(rig.api.review.mock.calls[0][0]).toBe("weekly");
    expect(rig.controller.getSnapshot().review?.kind).toBe("weekly");
  });

  it("proves the owner before reading and again before exposing success", async () => {
    const rig = fixture();
    const postProof = deferred<string>();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockReturnValueOnce(postProof.promise);
    const work = rig.controller.open();
    await microtasks();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    expect(rig.api.account).toHaveBeenCalledTimes(2);
    expect(rig.controller.getSnapshot().review).toBeNull();
    postProof.resolve(accountId);
    await work;
    expect(rig.controller.getSnapshot()).toMatchObject({
      busy: false,
      blocked: false,
    });
    expect(rig.controller.getSnapshot().review?.work.items[0].title).toBe(
      "Review biology notes",
    );
  });

  it("proves the owner after a failed read before exposing its bounded error", async () => {
    const rig = fixture();
    const postProof = deferred<string>();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockReturnValueOnce(postProof.promise);
    rig.api.review.mockRejectedValueOnce(
      new DayReviewError("The day review is unavailable.", 503),
    );
    const work = rig.controller.open();
    await microtasks();
    expect(rig.api.account).toHaveBeenCalledTimes(2);
    expect(rig.controller.getSnapshot().error).toBe("");
    postProof.resolve(accountId);
    await work;
    expect(rig.controller.getSnapshot()).toMatchObject({
      review: null,
      busy: false,
      blocked: false,
      error: "The day review is unavailable.",
    });
  });

  it.each(["mismatch", "offline"])(
    "does not read when the initial owner proof is %s",
    async (failure) => {
      const rig = fixture();
      if (failure === "mismatch")
        rig.api.account.mockResolvedValueOnce(`${workspaceId}:${goalId}`);
      else rig.api.account.mockRejectedValueOnce(new Error("offline"));
      await rig.controller.open();
      expect(rig.api.review).not.toHaveBeenCalled();
      expect(rig.controller.getSnapshot()).toMatchObject({
        review: null,
        busy: false,
        blocked: true,
      });
      await rig.controller.open();
      expect(rig.api.account).toHaveBeenCalledTimes(1);
    },
  );

  it.each([false, true])(
    "discards success if the final owner proof fails (read error: %s)",
    async (readError) => {
      const rig = fixture();
      rig.api.account
        .mockResolvedValueOnce(accountId)
        .mockResolvedValueOnce(`${workspaceId}:${goalId}`);
      if (readError)
        rig.api.review.mockRejectedValueOnce(
          new DayReviewError("Sensitive-looking domain detail", 503),
        );
      await rig.controller.open();
      const state = rig.controller.getSnapshot();
      expect(state).toMatchObject({ review: null, blocked: true, busy: false });
      expect(state.error).toMatch(/intended account/);
      expect(JSON.stringify(state)).not.toMatch(/biology|Sensitive-looking/);
    },
  );

  it.each([401, 403])(
    "blocks further reads after lost authorization %s",
    async (status) => {
      const rig = fixture();
      rig.api.review.mockRejectedValueOnce(
        new DayReviewError("Access could not be checked.", status),
      );
      await rig.controller.open();
      expect(rig.api.account).toHaveBeenCalledTimes(2);
      expect(rig.controller.getSnapshot()).toMatchObject({
        blocked: true,
        review: null,
      });
      await rig.controller.open();
      expect(rig.api.review).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["clear", "suspend"] as const)(
    "never begins a read after %s interrupts an in-flight owner proof",
    async (action) => {
      const rig = fixture();
      const proof = deferred<string>();
      rig.api.account.mockReturnValueOnce(proof.promise);
      const work = rig.controller.open();
      rig.controller[action]();
      expect(rig.api.account.mock.calls[0][0].aborted).toBe(true);
      proof.resolve(accountId);
      await work;
      expect(rig.api.review).not.toHaveBeenCalled();
      expect(rig.controller.getSnapshot().review).toBeNull();
    },
  );

  it.each(["clear", "suspend", "dispose"] as const)(
    "fences a late read after %s and aborts its signal",
    async (action) => {
      const rig = fixture();
      const pending = deferred<StudentDayReviewView>();
      rig.api.review.mockReturnValueOnce(pending.promise);
      const work = rig.controller.open();
      await microtasks();
      rig.controller[action]();
      expect(rig.api.review.mock.calls[0][1].aborted).toBe(true);
      pending.resolve(reviewFixture());
      await work;
      expect(rig.controller.getSnapshot().review).toBeNull();
      expect(rig.api.account).toHaveBeenCalledTimes(1);
    },
  );

  it("cannot overwrite a newer explicitly opened review with an old response", async () => {
    const rig = fixture();
    const old = deferred<StudentDayReviewView>();
    rig.api.review.mockReturnValueOnce(old.promise);
    const earlier = rig.controller.open();
    await microtasks();
    rig.controller.clear();
    rig.controller.choose("evening");
    await rig.controller.open();
    old.resolve(reviewFixture());
    await earlier;
    expect(rig.controller.getSnapshot().review?.kind).toBe("evening");
    expect(rig.api.review).toHaveBeenCalledTimes(2);
  });

  it("rejects a response for a different review type", async () => {
    const rig = fixture();
    rig.controller.choose("weekly");
    rig.api.review.mockResolvedValueOnce(reviewFixture({ kind: "morning" }));
    await rig.controller.open();
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().error).toMatch(/type changed/);
  });

  it("requires another explicit open after refocus owner verification and discards the old snapshot", async () => {
    const rig = fixture();
    await rig.controller.open();
    const epoch = rig.controller.getSnapshot().epoch;
    rig.controller.suspend();
    expect(rig.controller.getSnapshot().masked).toBe(true);
    await rig.controller.open();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    const proofs = rig.api.account.mock.calls.length;
    await rig.controller.resume();
    expect(rig.api.account).toHaveBeenCalledTimes(proofs + 1);
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot()).toMatchObject({
      review: null,
      busy: false,
      masked: false,
      epoch: epoch + 1,
    });
    await rig.controller.open();
    expect(rig.api.review).toHaveBeenCalledTimes(2);
  });

  it.each(["mismatch", "offline"])(
    "clears and blocks after a %s refocus proof",
    async (failure) => {
      const rig = fixture();
      await rig.controller.open();
      rig.controller.suspend();
      if (failure === "mismatch")
        rig.api.account.mockResolvedValueOnce(`${workspaceId}:${goalId}`);
      else rig.api.account.mockRejectedValueOnce(new Error("offline"));
      await rig.controller.resume();
      expect(rig.controller.getSnapshot()).toMatchObject({
        review: null,
        masked: false,
        blocked: true,
        busy: false,
      });
      expect(JSON.stringify(rig.controller.getSnapshot())).not.toMatch(
        /biology|Read a few/,
      );
      expect(rig.api.review).toHaveBeenCalledTimes(1);
    },
  );

  it("fences a late refocus proof after a session clear", async () => {
    const rig = fixture();
    await rig.controller.open();
    rig.controller.suspend();
    const proof = deferred<string>();
    rig.api.account.mockReturnValueOnce(proof.promise);
    const work = rig.controller.resume();
    rig.controller.clear("Session changed.");
    proof.resolve(accountId);
    await work;
    expect(rig.controller.getSnapshot()).toMatchObject({
      review: null,
      notice: "Session changed.",
      busy: false,
    });
    expect(rig.api.review).toHaveBeenCalledTimes(1);
  });

  it("clears private facts on explicit Clear while retaining only the chosen enum", async () => {
    const rig = fixture();
    rig.controller.choose("evening");
    await rig.controller.open();
    rig.controller.clear();
    const state = rig.controller.getSnapshot();
    expect(state).toMatchObject({
      kind: "evening",
      review: null,
      busy: false,
      error: "",
    });
    expect(JSON.stringify(state)).not.toMatch(
      /biology|BIO|self_report|Read a few/,
    );
    expect(rig.api.review).toHaveBeenCalledTimes(1);
  });

  it("notifies subscribers and detaches them without starting a read", () => {
    const rig = fixture();
    const listener = vi.fn();
    const detach = rig.controller.subscribe(listener);
    rig.controller.choose("weekly");
    expect(listener).toHaveBeenCalled();
    detach();
    listener.mockClear();
    rig.controller.clear();
    expect(listener).not.toHaveBeenCalled();
    expect(rig.api.review).not.toHaveBeenCalled();
  });
});

describe("server-clock freshness without automatic refresh", () => {
  it("expires at the nearest upcoming saved class, task, or fixed-block boundary", () => {
    const view = reviewFixture();
    expect(dayReviewMilliseconds(view)).toBe(5 * 60_000);
    const task = { ...view.work.items[0], due_at: "2026-10-19T12:00:45Z" };
    expect(
      dayReviewMilliseconds({ ...view, work: { ...view.work, items: [task] } }),
    ).toBe(45_000);
    const block = {
      ...view.schedule.fixed_blocks[0],
      start_at: "2026-10-19T12:00:20Z",
    };
    expect(
      dayReviewMilliseconds({
        ...view,
        schedule: { ...view.schedule, fixed_blocks: [block] },
      }),
    ).toBe(20_000);
    const occurrence = {
      ...view.schedule.classes[0],
      start_at: "2026-10-19T11:00:00Z",
      end_at: "2026-10-19T12:00:15Z",
    };
    expect(
      dayReviewMilliseconds({
        ...view,
        schedule: { ...view.schedule, classes: [occurrence] },
      }),
    ).toBe(15_000);
  });

  it("uses the saved server zone's midnight even when the device date differs", () => {
    const view = reviewFixture({
      server_now: "2026-10-20T03:59:30Z",
      timezone: "America/New_York",
      local_date: "2026-10-19",
    });
    expect(dayReviewMilliseconds(view)).toBe(30_000);
  });

  it("clears at a saved deadline without any background API read", async () => {
    vi.useFakeTimers();
    const view = reviewFixture();
    view.work.items[0].due_at = "2026-10-19T12:00:30Z";
    const rig = fixture(view);
    await rig.controller.open();
    await vi.advanceTimersByTimeAsync(29_999);
    expect(rig.controller.getSnapshot().review).not.toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().notice).toMatch(/fresh review/);
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    expect(rig.api.account).toHaveBeenCalledTimes(2);
  });

  it("subtracts elapsed monotonic request time and rejects an already stale response", async () => {
    vi.useFakeTimers();
    const view = reviewFixture();
    view.work.items[0].due_at = "2026-10-19T12:00:30Z";
    let now = 0;
    const rig = fixture(view, () => now);
    rig.api.review.mockImplementationOnce(async () => {
      now = 20_000;
      return view;
    });
    await rig.controller.open();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    rig.api.review.mockImplementationOnce(async () => {
      now += 31_000;
      return view;
    });
    await rig.controller.open();
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().busy).toBe(false);
    expect(rig.api.review).toHaveBeenCalledTimes(2);
  });

  it("does not extend the original lifetime while hidden", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.controller.open();
    rig.controller.suspend();
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(rig.controller.getSnapshot().review).toBeNull();
    await rig.controller.resume();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
  });
});

describe("day review privacy surfaces", () => {
  function surfaces(
    focused = true,
    visible: DocumentVisibilityState = "visible",
  ) {
    const window = new EventTarget();
    const document = Object.assign(new EventTarget(), {
      visibilityState: visible,
      hasFocus: () => focused,
    });
    const channel = Object.assign(new EventTarget(), { close: vi.fn() });
    return { window, document, channel };
  }

  it("masks on blur/hide and only verifies refocus when visible and focused", () => {
    const surface = surfaces();
    const clear = vi.fn(),
      suspend = vi.fn(),
      resume = vi.fn();
    const detach = observeDayReviewPrivacy(clear, suspend, resume, surface);
    expect(suspend).not.toHaveBeenCalled();
    surface.window.dispatchEvent(new Event("blur"));
    surface.document.visibilityState = "hidden";
    surface.document.dispatchEvent(new Event("visibilitychange"));
    expect(suspend).toHaveBeenCalledTimes(2);
    surface.document.visibilityState = "visible";
    surface.document.dispatchEvent(new Event("visibilitychange"));
    expect(resume).not.toHaveBeenCalled();
    surface.window.dispatchEvent(new Event("focus"));
    expect(resume).toHaveBeenCalledTimes(1);
    expect(clear).not.toHaveBeenCalled();
    detach();
  });

  it.each([
    [false, "visible"],
    [true, "hidden"],
  ] as const)("starts masked for focused=%s visible=%s", (focused, visible) => {
    const surface = surfaces(focused, visible);
    const suspend = vi.fn();
    const detach = observeDayReviewPrivacy(vi.fn(), suspend, vi.fn(), surface);
    expect(suspend).toHaveBeenCalledTimes(1);
    detach();
  });

  it("clears for page/session events, closes the channel, and removes all listeners", () => {
    const surface = surfaces();
    const clear = vi.fn(),
      suspend = vi.fn(),
      resume = vi.fn();
    const detach = observeDayReviewPrivacy(clear, suspend, resume, surface);
    for (const event of ["pagehide", "storage"])
      surface.window.dispatchEvent(new Event(event));
    surface.channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(3);
    detach();
    expect(clear).toHaveBeenCalledTimes(4);
    expect(surface.channel.close).toHaveBeenCalledTimes(1);
    for (const event of ["blur", "focus", "pagehide", "storage"])
      surface.window.dispatchEvent(new Event(event));
    surface.document.dispatchEvent(new Event("visibilitychange"));
    surface.channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(4);
    expect(suspend).not.toHaveBeenCalled();
    expect(resume).not.toHaveBeenCalled();
  });

  it("never reopens automatically after hide/refocus or a session event", async () => {
    const rig = fixture();
    const surface = surfaces();
    const detach = observeDayReviewPrivacy(
      () => rig.controller.clear(),
      rig.controller.suspend,
      () => {
        void rig.controller.resume();
      },
      surface,
    );
    await rig.controller.open();
    surface.window.dispatchEvent(new Event("blur"));
    surface.window.dispatchEvent(new Event("focus"));
    await microtasks();
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    surface.window.dispatchEvent(new Event("storage"));
    surface.channel.dispatchEvent(new Event("message"));
    await microtasks();
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    detach();
  });
});
