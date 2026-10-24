import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readStudyOutcome,
  readStudyPlan,
  readStudyPreferences,
  readStudyPreview,
  readStudyTask,
  studyDeadlineChoices,
  studyLocalDateTime,
  studyPreferencesDraft,
  studyRequest,
  studyTaskDraft,
  studyTaskSourceAcknowledgement,
} from "./student-planning";

const taskId = "11111111-1111-4111-8111-111111111111",
  blockId = "22222222-2222-4222-8222-222222222222",
  planId = "33333333-3333-4333-8333-333333333333",
  previewId = "44444444-4444-4444-8444-444444444444";
const task = {
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
  status: "active" as const,
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
const block = {
  ...proposed,
  id: blockId,
  plan_id: planId,
  revision: 1,
  status: "planned",
  actual_minutes: 0,
  outcome_at: null,
};
const plan = {
  revision: 1,
  blocks: [block],
  needs_review: false,
  review_notice: null,
  has_more_history: false,
};
const result = {
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
const preview = {
  preview_id: previewId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T13:20:00Z",
  base_revision: 1,
  result,
  requires_acceptance: true,
  replaced_blocks: [],
};
const preferences = {
  wake_time: "07:00",
  sleep_time: "23:00",
  focus_windows: [{ weekdays: [0, 2], start_time: "09:00", end_time: "12:00" }],
  never_schedule: [],
  max_study_minutes: 90,
  break_minutes: 15,
  revision: 0,
  timezone: "America/New_York",
};
afterEach(() => vi.restoreAllMocks());
describe("study-plan response boundaries", () => {
  it("retains accepted revisions and distinguishes previews from saved plans", () => {
    expect(readStudyPreview(preview)).toMatchObject({
      base_revision: 1,
      requires_acceptance: true,
      replaced_blocks: [],
      result: { planning_only: true },
    });
    expect(readStudyPlan(plan).blocks[0]).toMatchObject({
      id: blockId,
      status: "planned",
      revision: 1,
    });
  });
  it("rejects a preview without explicit acceptance or a malformed content hash", () => {
    expect(() =>
      readStudyPreview({ ...preview, requires_acceptance: false }),
    ).toThrow("could not be checked");
    expect(() =>
      readStudyPreview({ ...preview, preview_hash: "not-a-hash" }),
    ).toThrow();
  });
  it("keeps infeasible minutes and their actual reason", () => {
    expect(readStudyPreview(preview).result.unfitted_work[0]).toMatchObject({
      minutes: 30,
      reason: "cannot_meet_deadline",
    });
  });
  it.each([
    { ...proposed, start_at: "2026-10-19T13:30:00" },
    { ...proposed, end_at: proposed.start_at },
    { ...proposed, minutes: 0 },
  ])("rejects an invalid proposed time block", (value) => {
    expect(() =>
      readStudyPreview({ ...preview, result: { ...result, blocks: [value] } }),
    ).toThrow();
  });
  it("does not accept foreign-shaped IDs or unknown task states", () => {
    expect(() => readStudyTask({ ...task, id: "../foreign" })).toThrow();
    expect(() => readStudyTask({ ...task, status: "submitted" })).toThrow();
  });
  it("drops response-only fields from task and preference edits", () => {
    expect(studyTaskDraft(task)).not.toHaveProperty("id");
    expect(studyTaskDraft(task)).not.toHaveProperty("actual_minutes");
    expect(studyTaskDraft(task)).not.toHaveProperty("source_item_id");
    const parsed = readStudyPreferences(preferences);
    const draft = studyPreferencesDraft(parsed);
    expect(draft).not.toHaveProperty("timezone");
    draft.focus_windows[0].weekdays.push(6);
    expect(parsed.focus_windows[0].weekdays).toEqual([0, 2]);
  });
  it("binds a progress response to its updated plan and undo receipt", () => {
    const value = readStudyOutcome({
      task,
      block,
      plan,
      result,
      undo_request_id: previewId,
    });
    expect(value.plan.revision).toBe(1);
    expect(value.undo_request_id).toBe(previewId);
    expect(() =>
      readStudyOutcome({
        task,
        block,
        plan,
        result,
        undo_request_id: "missing",
      }),
    ).toThrow();
  });
  it("requires history completeness metadata and a verified saved time zone", () => {
    expect(() =>
      readStudyPlan({ ...plan, has_more_history: undefined }),
    ).toThrow();
    expect(() =>
      readStudyPreferences({ ...preferences, timezone: "Made/Up" }),
    ).toThrow();
  });
});
describe("deadlines in the student's saved zone", () => {
  it("converts normal wall clocks without using the device zone", () => {
    expect(
      studyDeadlineChoices("2026-10-19T23:59", "America/New_York"),
    ).toEqual(["2026-10-20T03:59:00.000Z"]);
    expect(studyLocalDateTime("2026-10-20T03:59:00Z", "America/New_York")).toBe(
      "2026-10-19T23:59",
    );
  });
  it("returns no invented instant for a skipped daylight-saving time", () => {
    expect(
      studyDeadlineChoices("2027-03-14T02:30", "America/New_York"),
    ).toEqual([]);
  });
  it("requires a choice when a clock time occurs twice", () => {
    expect(
      studyDeadlineChoices("2026-11-01T01:30", "America/New_York"),
    ).toEqual(["2026-11-01T05:30:00.000Z", "2026-11-01T06:30:00.000Z"]);
  });
  it("handles non-hour offsets and half-hour clock changes", () => {
    expect(studyDeadlineChoices("2026-10-19T10:00", "Asia/Kathmandu")).toEqual([
      "2026-10-19T04:15:00.000Z",
    ]);
    expect(
      studyDeadlineChoices("2026-04-05T01:45", "Australia/Lord_Howe"),
    ).toHaveLength(2);
  });
  it.each(["2026-02-30T10:00", "2026-10-19T25:00", "tomorrow at noon"])(
    "rejects invalid local dates instead of rolling into a different day",
    (value) => {
      expect(() => studyDeadlineChoices(value, "UTC")).toThrow(
        "valid deadline",
      );
    },
  );
});
describe("study API requests", () => {
  it("only reads saved tasks until a user requests a change", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({
        revision: 0,
        blocks: [],
        needs_review: false,
        review_notice: null,
        has_more_history: false,
      }),
    );
    await studyRequest("/plan", readStudyPlan);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/student/planning/plan"),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("treats a lost progress response as an unknown write", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new TypeError("connection ended"),
    );
    await expect(
      studyRequest(`/blocks/${blockId}/outcome`, readStudyOutcome, "POST", {}),
    ).rejects.toMatchObject({ uncertain: true });
  });
});

describe("accepted-plan source review", () => {
  it("only acknowledges a current authorized source after explicit review", () => {
    const changed = readStudyTask({
      ...task,
      source_item_id: blockId,
      source_revision: 1,
      source_current_revision: 2,
      source_current_title: "Updated lab",
      source_current_due_at: "2026-10-22T03:59:00Z",
      source_needs_review: true,
      source_reviewable: true,
      source_review_notice: "The assignment changed.",
    });
    expect(studyTaskSourceAcknowledgement(changed, false)).toEqual({});
    expect(studyTaskSourceAcknowledgement(changed, true)).toEqual({
      reviewed_source_revision: 2,
      confirmed_source: true,
    });
    expect(
      studyTaskSourceAcknowledgement(
        { ...changed, source_reviewable: false, source_current_revision: null },
        true,
      ),
    ).toEqual({});
    expect(studyTaskDraft(changed)).not.toHaveProperty(
      "reviewed_source_revision",
    );
    expect(studyTaskDraft(changed).due_at).toBe(task.due_at);
  });
  it("preserves the server review marker and virtual stale-block state", () => {
    const value = readStudyPlan({
      ...plan,
      needs_review: true,
      review_notice: "Your timetable changed.",
      blocks: [{ ...block, status: "needs_review" }],
    });
    expect(value.needs_review).toBe(true);
    expect(value.review_notice).toBe("Your timetable changed.");
    expect(value.blocks[0].status).toBe("needs_review");
  });
});

it("rejects incomplete weekly wakes and invalid alarm choices", () => {
  expect(() =>
    readStudyPreferences({ ...preferences, wake_times: ["07:00"] }),
  ).toThrow();
  const alarm = {
    enabled: true,
    days: [0],
    smart: false,
    prep_minutes: 30,
    sound: "gentle",
    snooze_minutes: 7,
    skip_date: null,
  };
  expect(() => readStudyPreferences({ ...preferences, alarm })).toThrow();
  expect(() =>
    readStudyPreferences({
      ...preferences,
      alarm: { ...alarm, snooze_minutes: 5, days: [0, 0] },
    }),
  ).toThrow();
});
