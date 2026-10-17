import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  StudyPlan,
  StudyPlanPreview,
  StudyPlanResult,
  StudyTask,
} from "../lib/student-planning";
import type { StudentTimetable } from "../lib/student-schedule";
import {
  StudentDailyPlan,
  StudentStudyAgenda,
  StudentStudyPlanPreview,
  StudentStudyTaskForm,
  StudyUnfittedWork,
} from "./student-daily-plan";

const taskId = "11111111-1111-4111-8111-111111111111",
  blockId = "22222222-2222-4222-8222-222222222222",
  planId = "33333333-3333-4333-8333-333333333333",
  previewId = "44444444-4444-4444-8444-444444444444";
const task: StudyTask = {
  id: taskId,
  title: "Finish the lab",
  course_id: null,
  due_at: "2026-10-20T03:59:00Z",
  estimated_minutes: 90,
  remaining_minutes: 90,
  actual_minutes: 0,
  difficulty: 4,
  priority: 3,
  course_weight: 1,
  status: "active",
  revision: 1,
  source_item_id: null,
  source_revision: null,
  source_current_revision: null,
  source_current_title: null,
  source_current_due_at: null,
  source_needs_review: false,
  source_reviewable: false,
  source_review_notice: null,
};
const proposed = {
  task_id: taskId,
  title: task.title,
  start_at: "2026-10-19T13:30:00Z",
  end_at: "2026-10-19T14:30:00Z",
  minutes: 60,
  focus_window: true,
};
const plan: StudyPlan = {
  revision: 1,
  blocks: [
    {
      ...proposed,
      id: blockId,
      plan_id: planId,
      revision: 1,
      status: "planned",
      actual_minutes: 0,
      outcome_at: null,
    },
  ],
  needs_review: false,
  review_notice: null,
  has_more_history: false,
};
const result: StudyPlanResult = {
  start_at: "2026-10-19T13:00:00Z",
  end_at: "2026-10-20T04:00:00Z",
  blocks: [proposed],
  unfitted_work: [
    {
      task_id: taskId,
      title: task.title,
      minutes: 30,
      due_at: task.due_at,
      reason: "cannot_meet_deadline",
    },
  ],
  capacities: [
    {
      day: "2026-10-19",
      free_minutes: 120,
      buffer_minutes: 18,
      planned_minutes: 60,
    },
  ],
  warnings: [],
  planning_only: true,
};
const preview: StudyPlanPreview = {
  preview_id: previewId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T13:20:00Z",
  base_revision: 1,
  result,
  requires_acceptance: true,
};
const table: StudentTimetable = {
  server_now: "2026-10-19T14:00:00Z",
  timezone: "America/New_York",
  date: "2026-10-19",
  days: 7,
  courses: [],
  fixed_blocks: [],
  breaks: [],
  exceptions: [],
  occurrences: [
    {
      id: "lecture:2026-10-19",
      meeting_id: "meeting-1",
      course_id: "course-1",
      course_code: "CS 100",
      course_name: "Programming",
      kind: "lecture",
      start_at: "2026-10-19T15:00:00Z",
      end_at: "2026-10-19T15:50:00Z",
      timezone: "America/New_York",
      building: "Science Center",
      room: "120",
      instructor: null,
      walk_minutes: 8,
      cancelled: false,
      exception: false,
    },
  ],
  fixed_occurrences: [
    {
      id: "shift:2026-10-19",
      fixed_block_id: "shift-1",
      title: "Library desk",
      start_at: "2026-10-19T21:00:00Z",
      end_at: "2026-10-20T01:00:00Z",
    },
  ],
  conflicts: [],
  warnings: [],
};
const render = (component: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(component);
describe("reviewable daily study plans", () => {
  it("starts from a truthful loading state and makes no request during render", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentDailyPlan, { accountId: "workspace:user" }),
      );
      expect(html).toContain("Opening your saved tasks");
      expect(html).not.toContain("You finished");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("shows actual proposed minutes, buffer and work that does not fit", () => {
    const html = render(
      createElement(StudentStudyPlanPreview, {
        preview,
        timezone: table.timezone,
        busy: false,
        confirmed: false,
        onConfirm: vi.fn(),
        onAccept: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Preview · nothing changed yet");
    expect(html).toContain("30m");
    expect(html).toContain("18m");
    expect(html).toContain("Some work needs another plan");
    expect(html).toContain("Not enough free time before the deadline");
    expect(html).toContain("NavoX hasn’t pushed");
    expect(html).toMatch(
      /<button[^>]*disabled=""[^>]*>Use this plan<\/button>/,
    );
    expect(html).not.toContain("Everything fits");
  });
  it("allows acceptance only after explicit review and describes the write scope", () => {
    const html = render(
      createElement(StudentStudyPlanPreview, {
        preview,
        timezone: table.timezone,
        busy: false,
        confirmed: true,
        onConfirm: vi.fn(),
        onAccept: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("replace my future study blocks in NavoX");
    expect(html).toContain("No school or Google Calendar events are changed");
    expect(html).not.toMatch(
      /<button[^>]*disabled=""[^>]*>Use this plan<\/button>/,
    );
  });
  it("explains the result of accepting an empty plan instead of implying study time exists", () => {
    const html = render(
      createElement(StudentStudyPlanPreview, {
        preview: {
          ...preview,
          result: { ...result, blocks: [], unfitted_work: [] },
        },
        timezone: table.timezone,
        busy: false,
        confirmed: false,
        onConfirm: vi.fn(),
        onAccept: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain(
      "Accepting this preview will clear future study blocks",
    );
    expect(html).not.toContain("9:30 AM");
  });
  it("renders classes, shifts and study blocks together with their real local times", () => {
    const html = render(
      createElement(StudentStudyAgenda, {
        plan,
        tasks: [task],
        timetable: table,
        disabled: false,
        onOutcome: vi.fn(),
      }),
    );
    expect(html).toContain("Finish the lab");
    expect(html).toContain("CS 100");
    expect(html).toContain("Science Center · 120");
    expect(html).toContain("Library desk");
    expect(html).toContain("9:30 AM");
    expect(html).toContain("5:00 PM");
    expect(html).toContain("Took longer");
    expect(html).toContain("Skip");
  });
  it("keeps an unchecked past block visible without marking it completed", () => {
    const old = {
      ...plan.blocks[0],
      start_at: "2026-10-18T13:30:00Z",
      end_at: "2026-10-18T14:30:00Z",
    };
    const html = render(
      createElement(StudentStudyAgenda, {
        plan: { ...plan, blocks: [old] },
        tasks: [task],
        timetable: { ...table, occurrences: [], fixed_occurrences: [] },
        disabled: false,
        onOutcome: vi.fn(),
      }),
    );
    expect(html).toContain("Earlier blocks to check (1)");
    expect(html).toContain("NavoX hasn’t marked them complete");
    expect(html).toContain("Finish the lab");
  });
  it("does not allow future blocks to report study progress before server time reaches them", () => {
    const html = render(
      createElement(StudentStudyAgenda, {
        plan: {
          ...plan,
          blocks: [
            {
              ...plan.blocks[0],
              start_at: "2026-10-19T20:00:00Z",
              end_at: "2026-10-19T21:00:00Z",
            },
          ],
        },
        tasks: [task],
        timetable: { ...table, occurrences: [], fixed_occurrences: [] },
        disabled: false,
        onOutcome: vi.fn(),
      }),
    );
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Done<\/button>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Took longer<\/button>/);
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>Skip<\/button>/);
  });
  it("shows unresolved deadlines and warnings without claiming that tasks were moved or sent", () => {
    const html = render(
      createElement(StudyUnfittedWork, {
        result: {
          ...result,
          warnings: ["A clock-change window was left free."],
        },
        timezone: table.timezone,
      }),
    );
    expect(html).toContain("A clock-change window was left free");
    expect(html).toContain("Monday, Oct 19 at 11:59 PM");
    expect(html).not.toContain("extension sent");
  });
  it("reviews a feed assignment as a separate editable task in the saved zone", () => {
    const html = render(
      createElement(StudentStudyTaskForm, {
        assignment: {
          id: taskId,
          revision: 3,
          course_id: null,
          title: task.title,
          due_at: task.due_at,
        },
        timezone: table.timezone,
        courses: [],
        disabled: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Review this assignment as a study task");
    expect(html).toContain('value="2026-10-19T23:59"');
    expect(html).toContain("America/New York");
    expect(html).toContain(
      "It won’t submit work or change the original assignment",
    );
    expect(html).toContain("Add reviewed study task");
  });
  it("offers both occurrences of an ambiguous deadline rather than silently choosing an offset", () => {
    const html = render(
      createElement(StudentStudyTaskForm, {
        task: { ...task, due_at: "2026-11-01T05:30:00Z" },
        timezone: table.timezone,
        courses: [],
        disabled: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("This clock time happens twice");
    expect(html).toContain("Earlier");
    expect(html).toContain("Later");
  });
});

describe("stale study-plan controls", () => {
  it("makes changed assignment details an explicit review without checking consent automatically", () => {
    const html = render(
      createElement(StudentStudyTaskForm, {
        task: {
          ...task,
          source_item_id: blockId,
          source_current_revision: 2,
          source_current_title: "Updated lab",
          source_needs_review: true,
          source_reviewable: true,
          source_review_notice: "The deadline changed.",
        },
        timezone: table.timezone,
        courses: [],
        disabled: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("The deadline changed.");
    expect(html).toContain("Use latest title and deadline");
    expect(html).toContain("I reviewed this update");
    expect(html).not.toContain('checked=""');
    expect(html).toContain(`value="${task.title}"`);
  });
  it("does not offer acknowledgment for a canceled or unavailable source", () => {
    const html = render(
      createElement(StudentStudyTaskForm, {
        task: {
          ...task,
          source_item_id: blockId,
          source_needs_review: true,
          source_reviewable: false,
          source_review_notice: "The assignment was canceled.",
        },
        timezone: table.timezone,
        courses: [],
        disabled: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("archive this task");
    expect(html).not.toContain("I reviewed this update");
  });
  it("blocks all outcomes for a virtual needs-review block even when the caller is otherwise idle", () => {
    const html = render(
      createElement(StudentStudyAgenda, {
        plan: {
          ...plan,
          needs_review: true,
          review_notice: "Your timetable changed.",
          blocks: [{ ...plan.blocks[0], status: "needs_review" }],
        },
        tasks: [task],
        timetable: { ...table, occurrences: [], fixed_occurrences: [] },
        disabled: false,
        onOutcome: vi.fn(),
      }),
    );
    expect(html).toContain("Review needed");
    for (const label of ["Done", "Took longer", "Skip"])
      expect(html).toMatch(
        new RegExp(`<button[^>]*disabled=""[^>]*>${label}<\\/button>`),
      );
    expect(html).not.toContain("Completed");
  });
});
