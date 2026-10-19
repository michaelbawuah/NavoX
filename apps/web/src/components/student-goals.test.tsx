import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  GoalCheckIn,
  GoalReportDraft,
  GoalsPlannerMetrics,
  StudentGoal,
} from "../lib/student-goals";
import {
  GoalDefinitionForm,
  GoalPrivateSurface,
  GoalProgressSummary,
  GoalRecordedStudy,
  GoalReportEditor,
  StudentGoals,
  StudentGoalsCard,
} from "./student-goals";

const goalId = "11111111-1111-4111-8111-111111111111",
  entryId = "22222222-2222-4222-8222-222222222222";
const goal: StudentGoal = {
  id: goalId,
  revision: 1,
  title: "My own goal",
  description: "",
  category: "personal",
  tracking: "qualitative",
  cadence: "weekly",
  target: null,
  unit: null,
  starts_on: "2026-10-01",
  ends_on: "2026-12-01",
  status: "active",
  status_source: "student_selected",
  tracking_definition_locked: false,
  progress: {
    source: "self_report",
    timezone: "America/New_York",
    week_starts_on: "monday",
    period_start: "2026-10-12",
    period_end: "2026-10-18",
    days_reported: 0,
    reported_count: null,
    target: null,
    remaining_count: null,
    target_met_from_reports: null,
    qualitative_latest: "unknown",
    latest_reported_on: null,
  },
  recent_checkins: [],
};
const countGoal: StudentGoal = {
  ...goal,
  tracking: "count",
  target: 3,
  unit: "visits",
  progress: { ...goal.progress, target: 3 },
};
const entry: GoalCheckIn = {
  id: entryId,
  goal_id: goalId,
  revision: 2,
  goal_revision_when_reported: 1,
  on_date: "2026-10-16",
  reported_timezone: "America/Chicago",
  count: null,
  completed: false,
  note: "A <private> note",
  recorded_at: "2026-10-17T00:00:00Z",
  source: "self_report",
  count_meaning: "count_for_reported_local_day",
};
const draft: GoalReportDraft = {
  goal_id: goalId,
  on_date: "2026-10-16",
  entry: null,
  goal_revision: 1,
  timezone: "America/New_York",
};
const metrics: GoalsPlannerMetrics = {
  source: "recorded_planner_outcomes",
  timezone: "America/New_York",
  period_start: "2026-10-12",
  period_end: "2026-10-18",
  completed_blocks: 0,
  skipped_blocks: 0,
  recorded_minutes: 0,
  attendance_sleep_and_grades_observed: false,
};
const render = (element: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(element);
describe("private, explicitly reported goals", () => {
  it("starts without a provider call, fake goals or an automatic coach", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentGoals, { accountId: `${goalId}:${entryId}` }),
      );
      expect(html).toContain("Loading your saved goals");
      expect(html).toContain("up to three active goals, or none");
      expect(html).toContain("Clear private text");
      expect(html).not.toContain("Gym 3x");
      expect(html).not.toContain("You attended");
      expect(html).not.toContain("Best week yet");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("offers an explicit entry without claiming tracking has occurred", () => {
    const html = render(createElement(StudentGoalsCard));
    expect(html).toContain('href="/goals"');
    expect(html).toContain("an unreported day stays unknown");
    expect(html).not.toContain("We tracked");
  });
  it("keeps an unreported qualitative period unknown rather than calling it a failure", () => {
    const html = render(createElement(GoalProgressSummary, { goal }));
    expect(html).toContain("No report in this period");
    expect(html).toContain("Progress is unknown");
    expect(html).toContain("both dates included");
    expect(html).toContain("Weeks start Monday");
    expect(html).toContain("Self-reported");
    expect(html).not.toContain("0%");
    expect(html).not.toContain("failed");
  });
  it("does not turn a missing numeric report into a zero count", () => {
    const html = render(
      createElement(GoalProgressSummary, { goal: countGoal }),
    );
    expect(html).toContain("No count reported");
    expect(html).toContain("Progress is unknown");
    expect(html).not.toContain("0</strong> of 3");
  });
  it("distinguishes an explicitly reported zero from missing progress", () => {
    const html = render(
      createElement(GoalProgressSummary, {
        goal: {
          ...countGoal,
          progress: {
            ...countGoal.progress,
            days_reported: 1,
            reported_count: 0,
            remaining_count: 3,
            target_met_from_reports: false,
          },
        },
      }),
    );
    expect(html).toContain("<strong>0</strong> of 3 visits");
    expect(html).toContain("3 visits remain");
    expect(html).not.toContain("No count reported");
  });
  it("reports a met count target as self-reported without marking the goal completed", () => {
    const html = render(
      createElement(GoalProgressSummary, {
        goal: {
          ...countGoal,
          progress: {
            ...countGoal.progress,
            days_reported: 2,
            reported_count: 4,
            remaining_count: 0,
            target_met_from_reports: true,
          },
        },
      }),
    );
    expect(html).toContain("reported counts meet this period’s target");
    expect(html).not.toContain("Goal completed");
    expect(html).not.toContain("Attendance verified");
  });
  it("shows an explicit no only for its chosen date", () => {
    const html = render(
      createElement(GoalProgressSummary, {
        goal: {
          ...goal,
          progress: {
            ...goal.progress,
            days_reported: 1,
            qualitative_latest: "no",
            latest_reported_on: "2026-10-16",
          },
        },
      }),
    );
    expect(html).toContain("Latest report: No");
    expect(html).toContain("only the date you chose");
    expect(html).not.toContain("missed class");
  });
  it("lets students choose a meaningful goal rather than inserting a sample target", () => {
    const html = render(
      createElement(GoalDefinitionForm, {
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onCancel: vi.fn(),
      }),
    );
    expect(html).toContain("Write a goal in your own words");
    expect(html).toContain("A yes or no for a chosen date");
    expect(html).toContain("Monday–Sunday");
    expect(html).toContain("Start date · included");
    expect(html).not.toContain("MATH 2940");
    expect(html).not.toContain("20 internships");
    expect(html).not.toContain("Your goal changes your study plan");
  });
  it("locks measurement and period fields after history exists while keeping wording editable", () => {
    const html = render(
      createElement(GoalDefinitionForm, {
        goal: { ...countGoal, tracking_definition_locked: true },
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onCancel: vi.fn(),
      }),
    );
    expect(html).toContain("older reports keep their meaning");
    expect(html).toContain('<fieldset disabled=""');
    expect(html).toContain('value="3"');
    expect(html).toContain('value="visits"');
    expect(html).toContain("Save goal changes");
  });
  it("requires explicit yes/no and never preselects the answer for a new report", () => {
    const html = render(
      createElement(GoalReportEditor, {
        goal,
        report: draft,
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onErase: vi.fn(),
      }),
    );
    expect(html).toContain('type="radio"');
    expect(html).not.toContain('checked=""');
    expect(html).toContain("Local date:");
    expect(html).toContain("America/New York");
    expect(html).toContain("NavoX has not observed attendance, sleep, grades");
  });
  it("replaces the exact day report, preserves a false answer, and makes erasure become unknown", () => {
    const html = render(
      createElement(GoalReportEditor, {
        goal,
        report: { ...draft, entry },
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onErase: vi.fn(),
      }),
    );
    expect(html).toContain("A report already exists for this date");
    expect(html).toContain("does not add a second report");
    expect(html).toMatch(/<input(?=[^>]*value="no")(?=[^>]*checked="")[^>]*>/);
    expect(html).toContain("Replace this day’s report");
    expect(html).toContain("unknown, not a “No”");
    expect(html).toContain("original report used America/Chicago");
    expect(html).toContain("A &lt;private&gt; note");
  });
  it("asks for an absolute day count including zero, not an increment", () => {
    const html = render(
      createElement(GoalReportEditor, {
        goal: countGoal,
        report: { ...draft, entry: { ...entry, count: 0, completed: null } },
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onErase: vi.fn(),
      }),
    );
    expect(html).toContain("Absolute count for this local day");
    expect(html).toContain('value="0"');
    expect(html).toContain("This is not an amount to add");
  });
  it.each(["paused", "completed"] as const)(
    "keeps %s goal history readable and erasable while disabling new checkins",
    (status) => {
      const html = render(
        createElement(GoalReportEditor, {
          goal: { ...goal, status },
          report: { ...draft, entry },
          today: "2026-10-17",
          disabled: false,
          onSave: vi.fn(),
          onErase: vi.fn(),
        }),
      );
      expect(html).toContain("This report is read-only");
      expect(html).toContain('<fieldset disabled=""');
      expect(html).toContain("Erase this check-in");
    },
  );
  it("keeps older reports readable without offering an unsupported backfill save", () => {
    const html = render(
      createElement(GoalReportEditor, {
        goal,
        report: {
          ...draft,
          on_date: "2026-09-01",
          entry: { ...entry, on_date: "2026-09-01" },
        },
        today: "2026-10-17",
        disabled: false,
        onSave: vi.fn(),
        onErase: vi.fn(),
      }),
    );
    expect(html).toContain("from today back through 31 days ago");
    expect(html).toContain("This report is read-only");
  });
  it("separates recorded study outcomes from self-reported goals and shows honest empty metrics", () => {
    const html = render(createElement(GoalRecordedStudy, { metrics }));
    expect(html).toContain("No study outcomes recorded");
    expect(html).toContain("separate from your goal reports");
    expect(html).toContain("do not establish attendance, sleep, grades");
    expect(html).not.toContain("You slept");
    expect(html).not.toContain("classes attended");
  });
  it("labels real recorded block metrics without equating them with personal goal completion", () => {
    const html = render(
      createElement(GoalRecordedStudy, {
        metrics: {
          ...metrics,
          completed_blocks: 2,
          skipped_blocks: 1,
          recorded_minutes: 75,
        },
      }),
    );
    expect(html).toContain("Completed study blocks");
    expect(html).toContain("Skipped study blocks");
    expect(html).toContain("Recorded study minutes");
    expect(html).toContain("<dd>75</dd>");
    expect(html).toContain("completion of a personal goal");
  });
  it("masks the existing draft tree instead of removing it during an account recheck", () => {
    const editor = createElement("input", {
      defaultValue: "My unsaved goal wording",
      name: "private-draft",
    });
    const hidden = render(
      createElement(GoalPrivateSurface, { masked: true }, editor),
    );
    const shown = render(
      createElement(GoalPrivateSurface, { masked: false }, editor),
    );
    expect(hidden).toContain('hidden=""');
    expect(hidden).toContain('inert=""');
    expect(hidden).toContain('value="My unsaved goal wording"');
    expect(shown).toContain('value="My unsaved goal wording"');
    expect(shown).not.toContain('hidden=""');
  });
});
