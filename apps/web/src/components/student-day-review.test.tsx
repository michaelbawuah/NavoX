import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  DayReviewKind,
  DayReviewLinks,
  ReviewGoal,
  ReviewGoals,
  ReviewOutcomes,
  ReviewSchedule,
  ReviewWindow,
  ReviewWork,
  StudentDayReviewView,
} from "../lib/student-day-review";
import {
  DayReviewContent,
  DayReviewGoals,
  DayReviewOutcomes,
  DayReviewPrivateSurface,
  DayReviewSchedule,
  DayReviewWork,
  StudentDayReview,
} from "./student-day-review";
import { StudentDayReviewEntry } from "./student-day-review-entry";

const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const courseId = "33333333-3333-4333-8333-333333333333";
const meetingId = "44444444-4444-4444-8444-444444444444";
const taskId = "55555555-5555-4555-8555-555555555555";
const goalId = "66666666-6666-4666-8666-666666666666";
const fixedId = "77777777-7777-4777-8777-777777777777";
const accountId = `${workspaceId}:${userId}`;
const links: DayReviewLinks = { planning: true, goals: true, email: true };
const noOptionalLinks: DayReviewLinks = {
  planning: false,
  goals: false,
  email: false,
};
const selectedWindow: ReviewWindow = {
  starts_at: "2026-10-21T00:00:00Z",
  ends_at: "2026-10-22T00:00:00Z",
  observed_through: "2026-10-21T12:00:00Z",
};
const render = (element: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(element);
const plain = (html: string) =>
  html
    .replace(/<[^>]*>/g, " ")
    .replaceAll("&#x27;", "'")
    .replaceAll("&quot;", '"')
    .replace(/\s+/g, " ")
    .trim();
const metric = (html: string, label: string) =>
  html.match(new RegExp(`<dt>${label}</dt><dd>([^<]+)</dd>`))?.[1];

function scheduleFixture(
  changes: Partial<ReviewSchedule> = {},
): ReviewSchedule {
  const occurrence = {
    id: `${meetingId}:2026-10-21`,
    meeting_id: meetingId,
    course_id: courseId,
    course_code: "BIO 101",
    course_name: "Biology",
    kind: "lecture" as const,
    start_at: "2026-10-21T13:00:00Z",
    end_at: "2026-10-21T14:00:00Z",
    timezone: "UTC",
    building: "Science",
    room: "204",
    instructor: null,
    walk_minutes: null,
    cancelled: false,
    exception: false,
  };
  return {
    source: "saved_timetable",
    status: "complete",
    window: selectedWindow,
    classes: [occurrence],
    fixed_blocks: [
      {
        id: `${fixedId}:2026-10-21`,
        fixed_block_id: fixedId,
        title: "Lunch",
        start_at: "2026-10-21T14:00:00Z",
        end_at: "2026-10-21T14:30:00Z",
      },
    ],
    classes_complete: true,
    fixed_blocks_complete: true,
    class_count: 1,
    fixed_block_count: 1,
    next_class: occurrence,
    review_href: "/classes",
    ...changes,
  };
}

function workFixture(changes: Partial<ReviewWork> = {}): ReviewWork {
  const totals = { active_tasks: 3, overdue_tasks: 1, due_in_window_tasks: 1 };
  const task = {
    source: "saved_task" as const,
    source_kind: "manual" as const,
    id: taskId,
    revision: 2,
    title: "Earlier notes",
    course_id: courseId,
    due_at: "2026-10-19T17:00:00Z",
    deadline_meaning: "saved_task_deadline" as const,
    remaining_minutes: 30,
    priority: 3,
    review_href: "/planner" as const,
  };
  return {
    source: "saved_task",
    status: "complete",
    window: selectedWindow,
    items: [
      task,
      {
        ...task,
        id: courseId,
        title: "Today’s reading",
        due_at: "2026-10-21T17:00:00Z",
        source_kind: "lms",
      },
      {
        ...task,
        id: goalId,
        title: "Next month’s project",
        due_at: "2026-11-01T17:00:00Z",
        source_kind: "email",
      },
    ],
    totals: { ...totals },
    known_totals: { ...totals },
    has_more: false,
    ...changes,
  };
}

function outcomesFixture(
  changes: Partial<ReviewOutcomes> = {},
): ReviewOutcomes {
  const totals = {
    done_blocks: 2,
    took_longer_blocks: 3,
    skipped_blocks: 1,
    recorded_minutes: 90,
  };
  return {
    source: "recorded_planner_outcome",
    status: "complete",
    window: selectedWindow,
    totals: { ...totals },
    known_totals: { ...totals },
    done_block_is_assignment_submission: false,
    ...changes,
  };
}

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
    starts_on: "2026-10-01",
    ends_on: null,
    progress: {
      source: "self_report",
      timezone: "UTC",
      week_starts_on: "monday",
      period_start: "2026-10-21",
      period_end: "2026-10-21",
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

function goalsFixture(changes: Partial<ReviewGoals> = {}): ReviewGoals {
  return {
    source: "self_report",
    status: "complete",
    items: [goalFixture()],
    periods_meaning: "each_goal_has_its_own_progress_period",
    ...changes,
  };
}

function viewFixture(kind: DayReviewKind = "morning"): StudentDayReviewView {
  const window =
    kind === "weekly"
      ? {
          ...selectedWindow,
          starts_at: "2026-10-19T00:00:00Z",
          ends_at: "2026-10-26T00:00:00Z",
        }
      : selectedWindow;
  return {
    kind,
    server_now: selectedWindow.observed_through,
    timezone: "UTC",
    local_date: "2026-10-21",
    week_starts_on: "monday",
    window,
    schedule: scheduleFixture({ window }),
    outlook: scheduleFixture({
      window: {
        starts_at: window.ends_at,
        ends_at:
          kind === "weekly" ? "2026-11-02T00:00:00Z" : "2026-10-23T00:00:00Z",
        observed_through: window.observed_through,
      },
      classes: [],
      fixed_blocks: [],
      class_count: 0,
      fixed_block_count: 0,
      next_class: null,
    }),
    work: workFixture({ window }),
    outcomes: outcomesFixture({ window }),
    goals: goalsFixture(),
    summary_lines: ["1 class is saved for today."],
    notices: [],
    question: {
      code: "goal_checkin",
      text: "Would you like to record a goal check-in?",
      review_href: "/goals",
    },
    basis: "saved_records_and_explicit_reports_only",
    automatic_rescheduling: false,
    attendance_sleep_grades_observed: false,
  };
}

function renderSchedule(section: ReviewSchedule) {
  return render(
    createElement(DayReviewSchedule, {
      section,
      title: "Saved for today",
      timezone: "UTC",
      links,
    }),
  );
}
function renderWork(section: ReviewWork, destinations = links) {
  return render(
    createElement(DayReviewWork, {
      section,
      timezone: "UTC",
      links: destinations,
    }),
  );
}
function renderOutcomes(section: ReviewOutcomes) {
  return render(
    createElement(DayReviewOutcomes, { section, timezone: "UTC", links }),
  );
}
function renderGoals(section: ReviewGoals, destinations = links) {
  return render(
    createElement(DayReviewGoals, { section, links: destinations }),
  );
}

afterEach(() => vi.restoreAllMocks());

describe("manual day review entry and private surface", () => {
  it("starts closed without reading any records or showing sample progress", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const html = render(createElement(StudentDayReview, { accountId }));
    const text = plain(html);
    expect(fetch).not.toHaveBeenCalled();
    expect(text).toContain("Open review");
    expect(text).toContain("Clear review");
    expect(text).toContain("Choose Open review to load your records");
    expect(text).toContain("Morning");
    expect(text).toContain("Evening");
    expect(text).toContain("Week so far");
    expect(text).not.toContain("Biology");
    expect(text).not.toContain("As of");
    expect(html).not.toMatch(/<(form|input|textarea)\b/);
    expect(text).not.toMatch(/How.*feel|mood|reschedule|Mark done|Send email/i);
  });

  it("keeps the entry off by default and when explicitly disabled", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    expect(render(createElement(StudentDayReviewEntry))).toBe("");
    expect(
      render(createElement(StudentDayReviewEntry, { enabled: false })),
    ).toBe("");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("offers a fixed link only when enabled without reading a review", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const html = render(
      createElement(StudentDayReviewEntry, { enabled: true }),
    );
    expect(html).toContain('href="/day-review"');
    expect(plain(html)).toContain("Review your day");
    expect(plain(html)).toContain("Morning, evening or week so far");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("hides private content and removes it from interaction while masked", () => {
    const html = render(
      createElement(
        DayReviewPrivateSurface,
        { masked: true },
        createElement("a", { href: "/planner" }, "Private saved record"),
      ),
    );
    expect(html).toMatch(/^<div[^>]*hidden=""[^>]*inert=""/);
    expect(html).toContain("Private saved record");
    const visible = render(
      createElement(DayReviewPrivateSurface, { masked: false }, "Saved record"),
    );
    expect(visible).not.toMatch(/\b(hidden|inert)=/);
  });
});

describe("scheduled records without attendance claims", () => {
  it("labels classes and fixed blocks as scheduled and counts each separately", () => {
    const html = renderSchedule(scheduleFixture());
    const text = plain(html);
    expect(metric(html, "Saved class sessions")).toBe("1");
    expect(metric(html, "Saved time blocks")).toBe("1");
    expect(text).toContain("BIO 101 · Biology");
    expect(text).toContain("Science · 204 · Scheduled");
    expect(text).toContain("Scheduled time block");
    expect(text).toContain("Next class");
    expect(text).not.toMatch(/attended|attendance recorded|completed class/i);
  });

  it("keeps an unknown class count independent of a known zero time-block count", () => {
    const html = renderSchedule(
      scheduleFixture({
        status: "incomplete",
        class_count: null,
        classes_complete: false,
        next_class: null,
        fixed_blocks: [],
        fixed_block_count: 0,
      }),
    );
    expect(metric(html, "Saved class sessions")).toBe("Unknown");
    expect(metric(html, "Saved time blocks")).toBe("0");
    expect(plain(html)).toContain("The next class is unknown");
    expect(plain(html)).toContain("Known records only");
    expect(plain(html)).not.toContain("No later class is saved");
  });

  it("keeps known class absence separate from an unknown time-block count", () => {
    const html = renderSchedule(
      scheduleFixture({
        status: "incomplete",
        classes: [],
        class_count: 0,
        next_class: null,
        fixed_blocks: [],
        fixed_blocks_complete: false,
        fixed_block_count: null,
      }),
    );
    expect(metric(html, "Saved class sessions")).toBe("0");
    expect(metric(html, "Saved time blocks")).toBe("Unknown");
    expect(plain(html)).toContain("No class sessions saved for these dates");
    expect(plain(html)).toContain(
      "No time blocks could be listed from the known records",
    );
    expect(plain(html)).not.toContain("No time blocks saved for these dates");
  });

  it("does not claim no saved classes when an incomplete projection lists none", () => {
    const html = renderSchedule(
      scheduleFixture({
        status: "incomplete",
        classes: [],
        classes_complete: false,
        class_count: null,
        next_class: null,
      }),
    );
    expect(plain(html)).toContain(
      "No class sessions could be listed from the known records",
    );
    expect(plain(html)).not.toContain(
      "No class sessions saved for these dates",
    );
  });

  it.each(["disabled", "unavailable"] as const)(
    "shows %s timetable records without fabricated metrics",
    (status) => {
      const html = renderSchedule(
        scheduleFixture({
          status,
          classes: [],
          fixed_blocks: [],
          class_count: null,
          fixed_block_count: null,
          next_class: null,
          classes_complete: false,
          fixed_blocks_complete: false,
        }),
      );
      expect(html).not.toContain("<dd>");
      expect(plain(html)).not.toContain("No class sessions saved");
      expect(plain(html)).not.toContain("No time blocks saved");
      expect(html).toContain('href="/classes"');
      expect(plain(html)).toContain(
        status === "disabled" ? "Not enabled here" : "totals are unknown",
      );
    },
  );

  it("keeps classes after the first five in an explicit collapsed details list", () => {
    const base = scheduleFixture();
    const classes = Array.from({ length: 7 }, (_, index) => ({
      ...base.classes[0],
      id: `${meetingId}:${index}`,
      course_name: `Course ${index + 1}`,
    }));
    const html = renderSchedule({
      ...base,
      classes,
      class_count: 7,
      next_class: null,
      fixed_blocks: [],
      fixed_block_count: 0,
    });
    const [initial, rest] = html.split("<details");
    expect(initial.match(/<li>/g)).toHaveLength(5);
    expect(initial).toContain("Course 5");
    expect(initial).not.toContain("Course 6");
    expect(rest).toContain("Show 2 more saved class sessions");
    expect(rest).toContain("Course 6");
    expect(rest).toContain("Course 7");
    expect(rest).not.toMatch(/^[^>]*\bopen[=> ]/);
  });

  it("keeps additional fixed blocks in their own collapsed details list", () => {
    const base = scheduleFixture();
    const fixed_blocks = Array.from({ length: 6 }, (_, index) => ({
      ...base.fixed_blocks[0],
      id: `${fixedId}:${index}`,
      title: `Block ${index + 1}`,
    }));
    const html = renderSchedule({
      ...base,
      classes: [],
      class_count: 0,
      next_class: null,
      fixed_blocks,
      fixed_block_count: 6,
    });
    const [initial, rest] = html.split("<details");
    expect(initial.match(/<li>/g)).toHaveLength(5);
    expect(initial).not.toContain("Block 6");
    expect(rest).toContain("Show 1 more saved time blocks");
    expect(rest).toContain("Block 6");
  });
});

describe("saved task records and partial totals", () => {
  it("distinguishes all remaining work from deadlines within the selected dates", () => {
    const html = renderWork(workFixture());
    const text = plain(html);
    expect(metric(html, "Tasks with work remaining")).toBe("3");
    expect(metric(html, "Saved deadlines overdue")).toBe("1");
    expect(metric(html, "Saved deadlines for these dates")).toBe("1");
    expect(text).toContain("across all dates");
    expect(text).toContain("include upcoming ones");
    expect(text).toContain("Next month’s project");
    expect(text).toContain("Saved deadline: Sunday, Nov 1");
    expect(text).toContain("30m remaining estimate");
    expect(text).not.toContain("3 tasks due today");
    expect(text).toContain("Manually saved");
    expect(text).toContain("Saved from a school source");
    expect(text).toContain("Saved from an email source");
  });

  it("labels verified subset totals as known only rather than all tasks", () => {
    const html = renderWork(
      workFixture({ status: "incomplete", totals: null }),
    );
    expect(plain(html)).toContain("Known task totals only");
    expect(plain(html)).toContain("partial view");
    expect(metric(html, "Tasks with work remaining")).toBe("3");
    expect(plain(html)).not.toContain("Saved task totals");
  });

  it("preserves unknown totals and unknown task absence for a partial empty list", () => {
    const html = renderWork(
      workFixture({
        status: "incomplete",
        items: [],
        totals: null,
        known_totals: null,
      }),
    );
    expect(plain(html)).toContain("Task totals are unknown");
    expect(plain(html)).toContain(
      "No tasks could be listed from the known records",
    );
    expect(html).not.toContain("<dd>");
    expect(plain(html)).not.toContain(
      "No eligible saved tasks with work remaining",
    );
  });

  it("shows a complete known zero without treating it as unknown", () => {
    const zero = { active_tasks: 0, overdue_tasks: 0, due_in_window_tasks: 0 };
    const html = renderWork(
      workFixture({ items: [], totals: zero, known_totals: zero }),
    );
    expect(metric(html, "Tasks with work remaining")).toBe("0");
    expect(plain(html)).toContain(
      "No eligible saved tasks with work remaining",
    );
    expect(plain(html)).not.toContain("Task totals are unknown");
  });

  it.each(["disabled", "unavailable"] as const)(
    "leaves %s task totals unknown",
    (status) => {
      const html = renderWork(
        workFixture({ status, items: [], totals: null, known_totals: null }),
      );
      expect(html).not.toContain("<dd>");
      expect(plain(html)).not.toContain("No eligible saved tasks");
      expect(plain(html)).toContain(
        status === "disabled"
          ? "Study tasks aren’t available here yet"
          : "Their totals are unknown",
      );
    },
  );

  it("calls out truncation and a missing saved deadline without guessing one", () => {
    const base = workFixture();
    const html = renderWork({
      ...base,
      items: [{ ...base.items[0], due_at: null }],
      has_more: true,
    });
    expect(plain(html)).toContain("No deadline saved");
    expect(plain(html)).toContain("first 20 eligible saved tasks");
    expect(plain(html)).toContain("Open Planner to review the rest");
  });

  it("omits a planner link when that optional destination is disabled", () => {
    expect(renderWork(workFixture(), noOptionalLinks)).not.toContain(
      'href="/planner"',
    );
    expect(renderWork(workFixture(), links)).toContain('href="/planner"');
  });
});

describe("recorded study outcomes without submission inference", () => {
  it("keeps Done, Took longer, skipped, and minutes as independent recorded measures", () => {
    const html = renderOutcomes(outcomesFixture());
    expect(metric(html, "Done study blocks")).toBe("2");
    expect(metric(html, "Took longer")).toBe("3");
    expect(metric(html, "Skipped blocks")).toBe("1");
    expect(metric(html, "Recorded study minutes")).toBe("90");
    expect(plain(html)).toContain(
      "A Done block does not mean an assignment was submitted",
    );
    expect(plain(html)).toContain("through Wednesday, Oct 21 at 12:00 PM");
    expect(plain(html)).not.toMatch(/5.*completed|2 assignments submitted/);
  });

  it("labels known outcome totals as a partial view", () => {
    const html = renderOutcomes(
      outcomesFixture({ status: "incomplete", totals: null }),
    );
    expect(plain(html)).toContain("Known records only");
    expect(plain(html)).toContain("partial view");
    expect(metric(html, "Done study blocks")).toBe("2");
  });

  it("leaves partial outcomes unknown when there is no verified subset", () => {
    const html = renderOutcomes(
      outcomesFixture({
        status: "incomplete",
        totals: null,
        known_totals: null,
      }),
    );
    expect(plain(html)).toContain("Recorded outcome totals are unknown");
    expect(html).not.toContain("<dd>");
  });

  it.each(["disabled", "unavailable"] as const)(
    "does not invent zero study outcomes for %s records",
    (status) => {
      const html = renderOutcomes(
        outcomesFixture({ status, totals: null, known_totals: null }),
      );
      expect(html).not.toContain("<dd>");
      expect(plain(html)).not.toContain("0 study blocks");
      expect(plain(html)).toContain(
        status === "disabled" ? "Not enabled here" : "totals are unknown",
      );
    },
  );
});

describe("goal reports keep their own periods and units", () => {
  it("leaves a qualitative period with no report unknown", () => {
    const text = plain(renderGoals(goalsFixture()));
    expect(text).toContain("Self-reported");
    expect(text).toContain("No report in this period. Progress is unknown");
    expect(text).toContain("0 days reported");
    expect(text).not.toMatch(/failed|0%|Latest report: No/);
  });

  it("keeps an unreported count unknown instead of replacing it with zero", () => {
    const goal = goalFixture();
    const count = {
      ...goal,
      tracking: "count" as const,
      unit: "pages",
      progress: { ...goal.progress, target: 8 },
    };
    const text = plain(renderGoals(goalsFixture({ items: [count] })));
    expect(text).toContain("No count reported");
    expect(text).toContain("Progress is unknown for this period");
    expect(text).not.toContain("0 of 8 pages");
  });

  it("shows an explicitly reported zero with its saved unit and remaining target", () => {
    const goal = goalFixture();
    const count = {
      ...goal,
      tracking: "count" as const,
      unit: "pages",
      progress: {
        ...goal.progress,
        days_reported: 1,
        reported_count: 0,
        target: 8,
        remaining_count: 8,
        target_met_from_reports: false,
        latest_reported_on: "2026-10-21",
      },
    };
    const text = plain(renderGoals(goalsFixture({ items: [count] })));
    expect(text).toContain("0 of 8 pages");
    expect(text).toContain(
      "8 pages remain toward the target, based on your reports",
    );
    expect(text).toContain("1 day reported");
    expect(text).not.toContain("No count reported");
  });

  it("labels a met count target as reported progress rather than automatic goal completion", () => {
    const goal = goalFixture();
    const count = {
      ...goal,
      tracking: "count" as const,
      unit: "visits",
      progress: {
        ...goal.progress,
        days_reported: 1,
        reported_count: 3,
        target: 3,
        remaining_count: 0,
        target_met_from_reports: true,
      },
    };
    const text = plain(renderGoals(goalsFixture({ items: [count] })));
    expect(text).toContain("3 of 3 visits");
    expect(text).toContain("Your reported counts meet this period’s target");
    expect(text).not.toMatch(/goal completed|automatically completed/);
  });

  it("shows a dated qualitative Yes without applying it to the whole week", () => {
    const goal = goalFixture();
    const reported = {
      ...goal,
      cadence: "weekly" as const,
      progress: {
        ...goal.progress,
        period_start: "2026-10-19",
        period_end: "2026-10-25",
        days_reported: 1,
        qualitative_latest: "yes" as const,
        latest_reported_on: "2026-10-20",
      },
    };
    const text = plain(renderGoals(goalsFixture({ items: [reported] })));
    expect(text).toContain("Latest report: Yes for Tuesday, Oct 20");
    expect(text).toContain("This answer describes the date you reported");
    expect(text).toContain("Monday, Oct 19 – Sunday, Oct 25");
    expect(text).toContain("both dates included");
    expect(text).toContain("Weeks start Monday");
    expect(text).not.toMatch(/week completed|7 days complete/);
  });

  it("shows each goal's own reporting dates instead of the review window", () => {
    const goal = goalFixture();
    const ownPeriod = {
      ...goal,
      cadence: "total" as const,
      progress: {
        ...goal.progress,
        period_start: "2026-10-01",
        period_end: "2026-12-01",
      },
    };
    const text = plain(renderGoals(goalsFixture({ items: [ownPeriod] })));
    expect(text).toContain("Thursday, Oct 1 – Tuesday, Dec 1");
    expect(text).toContain("This goal’s own period");
    expect(text).toContain("Each goal has its own reporting period");
  });

  it("does not claim no active goals from an empty incomplete section", () => {
    const text = plain(
      renderGoals(goalsFixture({ status: "incomplete", items: [] })),
    );
    expect(text).toContain("Known records only");
    expect(text).not.toContain("No active goals saved");
  });

  it("truthfully offers optional goals when a complete section is empty", () => {
    const text = plain(renderGoals(goalsFixture({ items: [] })));
    expect(text).toContain(
      "No active goals saved. You can choose whether to add one",
    );
  });

  it.each(["disabled", "unavailable"] as const)(
    "does not invent zero progress or absent goals when %s",
    (status) => {
      const text = plain(renderGoals(goalsFixture({ status, items: [] })));
      expect(text).not.toContain("No active goals saved");
      expect(text).not.toContain("0 days reported");
      expect(text).toContain(
        status === "disabled" ? "Not enabled here" : "totals are unknown",
      );
    },
  );
});

describe("review context, destinations, and safe text", () => {
  it("labels the weekly source windows and snapshot time without implying a completed week", () => {
    const html = render(
      createElement(DayReviewContent, { review: viewFixture("weekly"), links }),
    );
    const text = plain(html);
    expect(text).toContain("Week so far");
    expect(text).toContain("Monday, Oct 19 – Sunday, Oct 25");
    expect(text).toContain("Saved for this week");
    expect(text).toContain("Saved for next week");
    expect(text).toContain("Monday, Oct 26 – Sunday, Nov 1");
    expect(text).toContain("As of Wednesday, Oct 21 at 12:00 PM · UTC");
    expect(text).toContain("Recorded outcomes are week so far");
    expect(text).toContain("also include upcoming days");
    expect(text).toContain(
      "Attendance, sleep, grades and assignment submission are unknown",
    );
    expect(text).toContain("This review does not change your plan");
  });

  it.each(["morning", "evening"] as const)(
    "labels %s as a snapshot with tomorrow’s outlook",
    (kind) => {
      const text = plain(
        render(
          createElement(DayReviewContent, { review: viewFixture(kind), links }),
        ),
      );
      expect(text).toContain(
        kind === "morning" ? "Morning review" : "Evening review",
      );
      expect(text).toContain("Saved for today");
      expect(text).toContain("Saved for tomorrow");
      expect(text).toContain("can include times after this snapshot");
    },
  );

  it("omits optional section links and questions whose destination is not enabled", () => {
    const view = viewFixture();
    view.notices = [
      {
        code: "email",
        message: "An email source needs review.",
        review_href: "/email-actions",
      },
      {
        code: "connection",
        message: "A connection needs review.",
        review_href: "/connections",
      },
    ];
    const html = render(
      createElement(DayReviewContent, { review: view, links: noOptionalLinks }),
    );
    expect(html).not.toContain('href="/planner"');
    expect(html).not.toContain('href="/goals"');
    expect(html).not.toContain('href="/email-actions"');
    expect(html).toContain('href="/classes"');
    expect(html).toContain('href="/connections"');
    expect(plain(html)).toContain("An email source needs review");
    expect(plain(html)).not.toContain(
      "Would you like to record a goal check-in?",
    );
  });

  it("shows an allowed optional question as a navigation choice without a write control", () => {
    const view = viewFixture();
    view.question = {
      code: "email_review",
      text: "Would you like to review an email source?",
      review_href: "/email-actions",
    };
    const html = render(
      createElement(DayReviewContent, { review: view, links }),
    );
    expect(plain(html)).toContain("Would you like to review an email source?");
    expect(html).toContain('href="/email-actions"');
    expect(plain(html)).toContain("Review when ready");
    expect(html).not.toMatch(/<(form|input|textarea|button)\b/);
  });

  it("escapes untrusted saved titles, notices, summary lines, and question text", () => {
    const view = viewFixture();
    const hostile = '<img src=x onerror="send()"><script>alert(1)</script>';
    view.schedule.classes[0].course_name = hostile;
    view.work.items[0].title = hostile;
    view.goals.items[0].title = hostile;
    view.summary_lines = [hostile];
    view.notices = [
      { code: "unsafe_text", message: hostile, review_href: null },
    ];
    view.question = {
      code: "question",
      text: hostile,
      review_href: "/classes",
    };
    const html = render(
      createElement(DayReviewContent, { review: view, links }),
    );
    expect(html).toContain("&lt;img");
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toMatch(/<(form|input|textarea|button)\b/);
  });
});
