import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type GoalCheckIn,
  type GoalDefinition,
  type GoalsApi,
  GoalsRequestError,
  goalFreshMilliseconds,
  goalsRequest,
  goalToday,
  observeGoalsPrivacy,
  readGoalCheckIn,
  readGoalDefinition,
  readStudentGoal,
  readStudentGoalsBoard,
  type StudentGoal,
  type StudentGoalsBoard,
  StudentGoalsController,
  studentGoalsApi,
} from "./student-goals";

const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const goalId = "33333333-3333-4333-8333-333333333333";
const otherGoalId = "44444444-4444-4444-8444-444444444444";
const checkinId = "55555555-5555-4555-8555-555555555555";
const accountId = `${workspaceId}:${userId}`;
const definition: GoalDefinition = {
  title: "Read after class",
  description: "A few pages at my own pace.",
  category: "habit",
  tracking: "qualitative",
  cadence: "daily",
  target: null,
  unit: null,
  starts_on: "2026-10-19",
  ends_on: null,
};
function goalFixture(changes: Partial<StudentGoal> = {}): StudentGoal {
  return {
    ...definition,
    id: goalId,
    revision: 2,
    status: "active",
    progress: {
      source: "self_report",
      timezone: "America/New_York",
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
    recent_checkins: [],
    tracking_definition_locked: false,
    status_source: "student_selected",
    ...changes,
  };
}
function boardFixture(
  changes: Partial<StudentGoalsBoard> = {},
): StudentGoalsBoard {
  return {
    revision: 4,
    generation: 0,
    server_now: "2026-10-19T12:00:00Z",
    timezone: "America/New_York",
    active_limit: 3,
    goals: [goalFixture()],
    planner_metrics: {
      source: "recorded_planner_outcomes",
      timezone: "America/New_York",
      period_start: "2026-10-19",
      period_end: "2026-10-25",
      completed_blocks: 2,
      skipped_blocks: 1,
      recorded_minutes: 45,
      attendance_sleep_and_grades_observed: false,
    },
    notices: ["Reports describe what you chose to record."],
    ...changes,
  };
}
function checkinFixture(changes: Partial<GoalCheckIn> = {}): GoalCheckIn {
  return {
    id: checkinId,
    goal_id: goalId,
    revision: 3,
    goal_revision_when_reported: 2,
    on_date: "2026-10-19",
    reported_timezone: "America/New_York",
    count: null,
    completed: false,
    note: "My own note, with no inferred attendance.",
    recorded_at: "2026-10-19T12:00:00Z",
    source: "self_report",
    count_meaning: "count_for_reported_local_day",
    ...changes,
  };
}
const controllers: StudentGoalsController[] = [];
function fixture(initial = boardFixture()) {
  const server = structuredClone(initial);
  const api = {
    account: vi.fn<GoalsApi["account"]>(async () => accountId),
    board: vi.fn<GoalsApi["board"]>(async () => structuredClone(server)),
    day: vi.fn<GoalsApi["day"]>(async () => null),
    create: vi.fn<GoalsApi["create"]>(async (input) =>
      goalFixture({ ...input, id: otherGoalId, revision: 1 }),
    ),
    update: vi.fn<GoalsApi["update"]>(async (id, input) =>
      goalFixture({ ...input, id, revision: input.revision + 1 }),
    ),
    checkin: vi.fn<GoalsApi["checkin"]>(async (id, input) =>
      checkinFixture({
        goal_id: id,
        goal_revision_when_reported: input.goal_revision,
        on_date: input.on_date,
        reported_timezone: input.expected_timezone,
        revision: input.revision + 1,
        count: input.count,
        completed: input.completed,
        note: input.note,
      }),
    ),
    eraseGoal: vi.fn<GoalsApi["eraseGoal"]>(async () => {}),
    eraseCheckin: vi.fn<GoalsApi["eraseCheckin"]>(async () => {}),
    reset: vi.fn<GoalsApi["reset"]>(async () => {}),
  } satisfies GoalsApi;
  let sequence = 0;
  const requestId = vi.fn(
    () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++sequence).padStart(12, "0")}`,
  );
  const controller = new StudentGoalsController(accountId, api, requestId);
  controllers.push(controller);
  return { controller, api, requestId, server };
}
function unknownWrite() {
  return new GoalsRequestError("Response was lost.", null, true);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
afterEach(() => {
  for (const controller of controllers.splice(0)) controller.dispose();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("honest goal and report readers", () => {
  it("projects only the student's goal definition and explicit self-report fields", () => {
    expect(
      readGoalDefinition({
        ...definition,
        token: "private",
        inferred_grade: "A",
      }),
    ).toEqual(definition);
    const entry = readGoalCheckIn(checkinFixture());
    expect(entry.source).toBe("self_report");
    expect(entry.completed).toBe(false);
    expect(readStudentGoal(goalFixture()).progress.qualitative_latest).toBe(
      "unknown",
    );
    expect(
      readStudentGoalsBoard(boardFixture()).planner_metrics
        .attendance_sleep_and_grades_observed,
    ).toBe(false);
  });
  it("accepts zero as an explicit daily count rather than a missing report", () => {
    expect(
      readGoalCheckIn(checkinFixture({ count: 0, completed: null })).count,
    ).toBe(0);
    expect(() =>
      readGoalCheckIn(checkinFixture({ count: null, completed: null })),
    ).toThrow();
    expect(() =>
      readGoalCheckIn(checkinFixture({ count: 0, completed: false })),
    ).toThrow();
  });
  it.each([
    { source: "observed_attendance" },
    { count_meaning: "increment_previous_total" },
    { completed: "yes" },
    { count: 1.5, completed: null },
    { on_date: "2026-02-30" },
    { reported_timezone: "Not/A_Zone" },
    { recorded_at: "2026-10-19T12:00:00" },
  ])("rejects unsupported report semantics: %j", (changes) => {
    expect(() =>
      readGoalCheckIn({ ...checkinFixture(), ...changes }),
    ).toThrow();
  });
  it("bounds notes by Unicode code points and rejects unbounded server text", () => {
    expect(
      readGoalCheckIn(checkinFixture({ note: "🧪".repeat(1000) })).note,
    ).toHaveLength(2000);
    expect(() =>
      readGoalCheckIn(checkinFixture({ note: "🧪".repeat(1001) })),
    ).toThrow();
  });
  it("rejects contradictory measurement definitions and backwards dates", () => {
    for (const change of [
      { tracking: "count", target: null, unit: "pages" },
      { tracking: "count", target: 5, unit: "" },
      { tracking: "qualitative", target: 5 },
      { title: " " },
      { ends_on: "2026-10-18" },
    ]) {
      expect(() => readGoalDefinition({ ...definition, ...change })).toThrow();
    }
  });
  it("rejects reports belonging to a different goal and automatic completion claims", () => {
    expect(() =>
      readStudentGoal(
        goalFixture({
          recent_checkins: [checkinFixture({ goal_id: otherGoalId })],
        }),
      ),
    ).toThrow();
    expect(() =>
      readStudentGoal({ ...goalFixture(), status_source: "model_inferred" }),
    ).toThrow();
  });
  it("rejects duplicate goals, too many active goals, mixed zones, and observed grades", () => {
    const goal = goalFixture();
    for (const change of [
      { goals: [goal, goal] },
      {
        goals: [goal, goal, goal, goal].map((item, index) => ({
          ...item,
          id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, "0")}`,
        })),
      },
      { timezone: "UTC" },
      {
        planner_metrics: {
          ...boardFixture().planner_metrics,
          attendance_sleep_and_grades_observed: true,
        },
      },
    ]) {
      expect(() =>
        readStudentGoalsBoard({ ...boardFixture(), ...change }),
      ).toThrow();
    }
  });
  it("uses the server's saved zone and expires the current-day summary at midnight", () => {
    const board = boardFixture({ server_now: "2026-10-20T03:59:30Z" });
    expect(goalToday(board)).toBe("2026-10-19");
    expect(goalFreshMilliseconds(board)).toBe(30_000);
    expect(goalFreshMilliseconds(boardFixture())).toBe(15 * 60_000);
  });
});

describe("goals transport", () => {
  it("uses credentialed uncached fixed routes and does not automatically retry", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      goalsRequest("", readStudentGoal, "POST", definition),
    ).rejects.toMatchObject({ status: null, uncertain: true });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toMatch(/\/student\/goals$/);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      body: JSON.stringify(definition),
    });
  });
  it("reads an exact local date, including a truthful absent report", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response("null", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    expect(
      await studentGoalsApi.day(
        goalId,
        "2026-10-19",
        new AbortController().signal,
      ),
    ).toBeNull();
    expect(fetcher.mock.calls[0][0]).toMatch(
      new RegExp(`/${goalId}/checkins\\?on_date=2026-10-19$`),
    );
  });
  it("accepts empty 204 deletion responses with only revision and request ID", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    const input = { request_id: userId, revision: 4 };
    const signal = new AbortController().signal;
    await studentGoalsApi.eraseGoal(goalId, input, signal);
    await studentGoalsApi.eraseCheckin(goalId, checkinId, input, signal);
    await studentGoalsApi.reset(input, signal);
    expect(fetcher).toHaveBeenCalledTimes(3);
    for (const call of fetcher.mock.calls)
      expect(call[1]).toMatchObject({
        method: "DELETE",
        body: JSON.stringify(input),
      });
  });
  it("retains uncertain timeout/server/malformed writes and treats conflicts as known", async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    for (const status of [408, 503]) {
      fetcher.mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Unavailable." }), { status }),
      );
      await expect(
        goalsRequest("", readStudentGoal, "POST", definition),
      ).rejects.toMatchObject({ status, uncertain: true });
    }
    fetcher.mockResolvedValueOnce(new Response("{}", { status: 200 }));
    await expect(
      goalsRequest("", readStudentGoal, "POST", definition),
    ).rejects.toMatchObject({ status: 200, uncertain: true });
    fetcher.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Goal changed." }), {
        status: 409,
      }),
    );
    await expect(
      goalsRequest("", readStudentGoal, "PATCH", definition),
    ).rejects.toMatchObject({ status: 409, uncertain: false });
  });
});

describe("owned private goals workspace", () => {
  it("makes no request until explicitly loaded and does not infer any check-in", async () => {
    const rig = fixture();
    expect(rig.api.account).not.toHaveBeenCalled();
    await rig.controller.checkin({ count: null, completed: true, note: "" });
    expect(rig.api.checkin).not.toHaveBeenCalled();
    await rig.controller.load();
    expect(
      rig.controller.getSnapshot().board?.goals[0].progress.qualitative_latest,
    ).toBe("unknown");
    expect(rig.api.day).not.toHaveBeenCalled();
  });
  it("checks account ownership before reading and after successful I/O", async () => {
    const before = fixture();
    before.api.account.mockResolvedValueOnce(`${workspaceId}:${goalId}`);
    await before.controller.load();
    expect(before.api.board).not.toHaveBeenCalled();
    expect(before.controller.getSnapshot().blocked).toBe(true);
    const after = fixture();
    after.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${goalId}`);
    await after.controller.load();
    expect(after.api.board).toHaveBeenCalledTimes(1);
    expect(after.controller.getSnapshot()).toMatchObject({
      board: null,
      blocked: true,
    });
  });
  it("rechecks account ownership after an unknown write failure before retaining it", async () => {
    const rig = fixture();
    await rig.controller.load();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${goalId}`);
    rig.api.create.mockRejectedValueOnce(unknownWrite());
    await rig.controller.create(definition);
    expect(rig.controller.getSnapshot()).toMatchObject({
      board: null,
      report: null,
      pending: null,
      blocked: true,
    });
    await rig.controller.checkPending();
    expect(rig.api.create).toHaveBeenCalledTimes(1);
  });
  it("retries an unknown create with the original command and request ID", async () => {
    const rig = fixture(boardFixture({ generation: 7 }));
    await rig.controller.load();
    rig.api.create.mockRejectedValueOnce(unknownWrite());
    await rig.controller.create(definition);
    expect(rig.controller.getSnapshot().pending?.kind).toBe("create");
    await rig.controller.create({ ...definition, title: "A different goal" });
    await rig.controller.reset(true);
    expect(rig.api.create).toHaveBeenCalledTimes(1);
    expect(rig.api.reset).not.toHaveBeenCalled();
    rig.server.generation = 8;
    await rig.controller.checkPending();
    expect(rig.api.create.mock.calls[1][0]).toEqual(
      rig.api.create.mock.calls[0][0],
    );
    expect(rig.api.create.mock.calls[1][0].generation).toBe(7);
    expect(rig.requestId).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot().pending).toBeNull();
  });
  it("uses the exact saved goal revision for update retries", async () => {
    const rig = fixture();
    await rig.controller.load();
    rig.api.update.mockRejectedValueOnce(unknownWrite());
    await rig.controller.updateGoal(
      goalId,
      { ...definition, title: "My revised goal" },
      "paused",
    );
    await rig.controller.checkPending();
    expect(rig.api.update.mock.calls[1].slice(0, 2)).toEqual(
      rig.api.update.mock.calls[0].slice(0, 2),
    );
    expect(rig.api.update.mock.calls[0][1]).toMatchObject({
      revision: 2,
      status: "paused",
    });
    expect(rig.requestId).toHaveBeenCalledTimes(1);
  });
  it("reloads a known successful mutation without replaying it when the summary read fails", async () => {
    const rig = fixture();
    await rig.controller.load();
    rig.api.board.mockRejectedValueOnce(new Error("summary offline"));
    await rig.controller.create(definition);
    expect(rig.controller.getSnapshot()).toMatchObject({
      board: null,
      report: null,
      pending: null,
      board_revision: null,
    });
    expect(rig.controller.getSnapshot().notice).toMatch(/change was saved/);
    expect(rig.controller.getSnapshot().handles).toContainEqual({
      id: otherGoalId,
      revision: 1,
    });
    await rig.controller.checkPending();
    await rig.controller.create(definition);
    expect(rig.api.create).toHaveBeenCalledTimes(1);
    await rig.controller.load();
    expect(rig.controller.getSnapshot().board).not.toBeNull();
    expect(rig.api.create).toHaveBeenCalledTimes(1);
  });
  it("honors the three-active-goal limit before creating another", async () => {
    const rig = fixture(
      boardFixture({
        goals: [
          goalFixture(),
          goalFixture({ id: otherGoalId }),
          goalFixture({ id: userId }),
        ],
      }),
    );
    await rig.controller.load();
    await rig.controller.create(definition);
    expect(rig.api.create).not.toHaveBeenCalled();
    expect(rig.controller.getSnapshot().error).toMatch(/three active/);
  });
  it("looks up the selected day directly instead of assuming recent reports are complete", async () => {
    const rig = fixture();
    rig.api.day.mockResolvedValueOnce(
      checkinFixture({ on_date: "2026-10-01", revision: 8 }),
    );
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-01");
    expect(rig.api.day.mock.calls[0].slice(0, 2)).toEqual([
      goalId,
      "2026-10-01",
    ]);
    expect(rig.controller.getSnapshot().report).toMatchObject({
      on_date: "2026-10-01",
      goal_revision: 2,
      timezone: "America/New_York",
      entry: { revision: 8 },
    });
  });
  it("replaces one local-day report using its revision and expected saved timezone", async () => {
    const rig = fixture();
    rig.api.day.mockResolvedValueOnce(checkinFixture());
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    await rig.controller.checkin({
      count: null,
      completed: true,
      note: "My corrected report.",
    });
    expect(rig.api.checkin.mock.calls[0][0]).toBe(goalId);
    expect(rig.api.checkin.mock.calls[0][1]).toMatchObject({
      goal_revision: 2,
      revision: 3,
      expected_timezone: "America/New_York",
      on_date: "2026-10-19",
      count: null,
      completed: true,
      note: "My corrected report.",
    });
  });
  it("preserves the exact report body and ID after an unknown check-in response", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    rig.api.checkin.mockRejectedValueOnce(unknownWrite());
    await rig.controller.checkin({
      count: null,
      completed: false,
      note: "My words 🧪",
    });
    expect(rig.controller.getSnapshot().pending?.kind).toBe("checkin");
    await rig.controller.checkin({
      count: null,
      completed: true,
      note: "Changed words",
    });
    await rig.controller.checkPending();
    expect(rig.api.checkin.mock.calls[1].slice(0, 2)).toEqual(
      rig.api.checkin.mock.calls[0].slice(0, 2),
    );
    expect(rig.requestId).toHaveBeenCalledTimes(1);
  });
  it("requires explicit typed check-ins and never turns missing data into zero", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    expect(rig.controller.getSnapshot().report?.entry).toBeNull();
    await expect(
      rig.controller.checkin({ count: null, completed: null, note: "" }),
    ).rejects.toThrow(/explicit/);
    await expect(
      rig.controller.checkin({ count: 0, completed: null, note: "" }),
    ).rejects.toThrow(/explicit/);
    expect(rig.api.checkin).not.toHaveBeenCalled();
  });
  it("fails closed if the saved timezone changes before reporting", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    rig.server.timezone = "UTC";
    await rig.controller.checkin({ count: null, completed: true, note: "" });
    expect(rig.api.checkin).not.toHaveBeenCalled();
    expect(rig.controller.getSnapshot()).toMatchObject({
      board: null,
      report: null,
      pending: null,
    });
    expect(rig.controller.getSnapshot().error).toMatch(/time zone changed/);
  });
  it("rejects a day lookup with a mismatched goal or date", async () => {
    const rig = fixture();
    rig.api.day.mockResolvedValueOnce(
      checkinFixture({ on_date: "2026-10-18" }),
    );
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    expect(rig.controller.getSnapshot()).toMatchObject({
      board: null,
      report: null,
    });
    expect(rig.api.checkin).not.toHaveBeenCalled();
  });
  it("fences a late board response after clear and aborts its signal", async () => {
    const rig = fixture();
    const pending = deferred<StudentGoalsBoard>();
    rig.api.board.mockReturnValueOnce(pending.promise);
    const operation = rig.controller.load();
    await vi.waitFor(() => expect(rig.api.board).toHaveBeenCalledTimes(1));
    rig.controller.clear("Page hidden.");
    expect(rig.api.board.mock.calls[0][0].aborted).toBe(true);
    pending.resolve(boardFixture());
    await operation;
    expect(rig.controller.getSnapshot()).toMatchObject({
      board: null,
      report: null,
      handles: [],
      busy: false,
    });
  });
  it("keeps only content-free erasure references and board revision after clearing", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    rig.controller.clear();
    const state = rig.controller.getSnapshot();
    expect(state).toMatchObject({
      board: null,
      report: null,
      pending: null,
      handles: [{ id: goalId, revision: 2 }],
      board_revision: 4,
    });
    expect(JSON.stringify(state)).not.toMatch(
      /Read after class|few pages|self_report|America/,
    );
  });
  it("preserves the same mounted draft epoch on brief suspension after a fresh same-owner proof", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    const prior = rig.controller.getSnapshot();
    const reads = rig.api.board.mock.calls.length;
    rig.controller.suspend();
    expect(rig.controller.getSnapshot()).toMatchObject({
      masked: true,
      busy: false,
    });
    expect(rig.controller.getSnapshot().board).toBe(prior.board);
    expect(rig.controller.getSnapshot().report).toBe(prior.report);
    await rig.controller.create(definition);
    expect(rig.api.create).not.toHaveBeenCalled();
    const proofs = rig.api.account.mock.calls.length;
    await rig.controller.resume();
    expect(rig.api.account).toHaveBeenCalledTimes(proofs + 1);
    expect(rig.api.board).toHaveBeenCalledTimes(reads);
    expect(rig.controller.getSnapshot()).toMatchObject({
      masked: false,
      epoch: prior.epoch,
    });
    expect(rig.controller.getSnapshot().board).toBe(prior.board);
    expect(rig.controller.getSnapshot().report).toBe(prior.report);
  });
  it("drops suspended drafts if the refocus owner proof changes or fails", async () => {
    for (const failure of [false, true]) {
      const rig = fixture();
      await rig.controller.load();
      await rig.controller.loadDay(goalId, "2026-10-19");
      const epoch = rig.controller.getSnapshot().epoch;
      rig.controller.suspend();
      if (failure)
        rig.api.account.mockRejectedValueOnce(new Error("auth offline"));
      else rig.api.account.mockResolvedValueOnce(`${workspaceId}:${goalId}`);
      await rig.controller.resume();
      expect(rig.controller.getSnapshot()).toMatchObject({
        board: null,
        report: null,
        pending: null,
        blocked: true,
        epoch: epoch + 1,
        handles: [{ id: goalId, revision: 2 }],
      });
      expect(JSON.stringify(rig.controller.getSnapshot())).not.toMatch(
        /Read after class|few pages/,
      );
    }
  });
  it("retains the same command through suspension while fencing its late write response", async () => {
    const rig = fixture();
    await rig.controller.load();
    const pending = deferred<StudentGoal>();
    rig.api.create.mockReturnValueOnce(pending.promise);
    const work = rig.controller.create(definition);
    await vi.waitFor(() => expect(rig.api.create).toHaveBeenCalledTimes(1));
    const original = structuredClone(rig.api.create.mock.calls[0][0]);
    const board = rig.controller.getSnapshot().board;
    const epoch = rig.controller.getSnapshot().epoch;
    rig.controller.suspend();
    expect(rig.api.create.mock.calls[0][1].aborted).toBe(true);
    expect(rig.controller.getSnapshot().pending).toEqual({
      kind: "create",
      input: original,
    });
    pending.resolve(goalFixture({ id: otherGoalId, revision: 1 }));
    await work;
    expect(rig.controller.getSnapshot()).toMatchObject({ masked: true, epoch });
    expect(rig.controller.getSnapshot().board).toBe(board);
    await rig.controller.resume();
    expect(rig.controller.getSnapshot().epoch).toBe(epoch);
    await rig.controller.checkPending();
    expect(rig.api.create.mock.calls[1][0]).toEqual(original);
    expect(rig.requestId).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot().pending).toBeNull();
  });
  it("keeps suspended private content within the original server freshness lifetime", async () => {
    vi.useFakeTimers();
    const rig = fixture(boardFixture({ server_now: "2026-10-20T03:59:30Z" }));
    await rig.controller.load();
    rig.controller.suspend();
    await vi.advanceTimersByTimeAsync(30_000);
    await rig.controller.resume();
    expect(rig.controller.getSnapshot().board).toBeNull();
    expect(rig.controller.getSnapshot().report).toBeNull();
    expect(rig.api.board).toHaveBeenCalledTimes(1);
  });
  it("expires the local-day summary at server-zone midnight without background reads", async () => {
    vi.useFakeTimers();
    const rig = fixture(boardFixture({ server_now: "2026-10-20T03:59:30Z" }));
    await rig.controller.load();
    await vi.advanceTimersByTimeAsync(30_000);
    expect(rig.controller.getSnapshot().board).toBeNull();
    expect(rig.controller.getSnapshot().handles).toEqual([
      { id: goalId, revision: 2 },
    ]);
    expect(rig.api.board).toHaveBeenCalledTimes(1);
  });
  it("requires explicit erasure and retries only the same opaque goal command", async () => {
    const rig = fixture();
    await rig.controller.load();
    await rig.controller.eraseGoal(goalId, false);
    await rig.controller.eraseGoal(otherGoalId, true);
    expect(rig.api.eraseGoal).not.toHaveBeenCalled();
    rig.controller.clear();
    rig.api.eraseGoal.mockRejectedValueOnce(unknownWrite());
    await rig.controller.eraseGoal(goalId, true);
    expect(rig.controller.getSnapshot().pending?.kind).toBe("erase_goal");
    await rig.controller.checkPending();
    expect(rig.api.eraseGoal.mock.calls[1].slice(0, 2)).toEqual(
      rig.api.eraseGoal.mock.calls[0].slice(0, 2),
    );
    expect(Object.keys(rig.api.eraseGoal.mock.calls[0][1]).sort()).toEqual([
      "request_id",
      "revision",
    ]);
  });
  it("erases only the loaded exact-day check-in revision after confirmation", async () => {
    const rig = fixture();
    rig.api.day.mockResolvedValueOnce(checkinFixture());
    await rig.controller.load();
    await rig.controller.loadDay(goalId, "2026-10-19");
    await rig.controller.eraseCheckin(false);
    expect(rig.api.eraseCheckin).not.toHaveBeenCalled();
    await rig.controller.eraseCheckin(true);
    expect(rig.api.eraseCheckin.mock.calls[0].slice(0, 3)).toEqual([
      goalId,
      checkinId,
      { request_id: rig.requestId.mock.results[0].value, revision: 3 },
    ]);
  });
  it("requires a known board revision and exact retry for reset", async () => {
    const rig = fixture();
    await rig.controller.reset(true);
    expect(rig.api.reset).not.toHaveBeenCalled();
    await rig.controller.load();
    rig.controller.clear();
    rig.api.reset.mockRejectedValueOnce(unknownWrite());
    await rig.controller.reset(true);
    await rig.controller.checkPending();
    expect(rig.api.reset.mock.calls[1][0]).toEqual(
      rig.api.reset.mock.calls[0][0],
    );
    expect(rig.api.reset.mock.calls[0][0].revision).toBe(4);
    expect(rig.requestId).toHaveBeenCalledTimes(1);
  });
  it("clears on privacy events and never reloads or reports on becoming visible", async () => {
    const rig = fixture();
    await rig.controller.load();
    const windowSurface = new EventTarget();
    const documentSurface = Object.assign(new EventTarget(), {
      visibilityState: "visible" as DocumentVisibilityState,
    });
    const channel = Object.assign(new EventTarget(), { close: vi.fn() });
    const clear = vi.fn(() => rig.controller.clear());
    const detach = observeGoalsPrivacy(clear, {
      window: windowSurface,
      document: documentSurface,
      channel,
    });
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(clear).not.toHaveBeenCalled();
    for (const event of ["blur", "pagehide", "storage"])
      windowSurface.dispatchEvent(new Event(event));
    documentSurface.visibilityState = "hidden";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(5);
    documentSurface.visibilityState = "visible";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(rig.api.board).toHaveBeenCalledTimes(1);
    expect(rig.api.checkin).not.toHaveBeenCalled();
    detach();
    expect(clear).toHaveBeenCalledTimes(6);
    expect(channel.close).toHaveBeenCalledTimes(1);
    windowSurface.dispatchEvent(new Event("blur"));
    channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(6);
  });
  it("uses mask/refocus transitions only for blur or hide and clears on session exits", () => {
    const windowSurface = new EventTarget();
    const documentSurface = Object.assign(new EventTarget(), {
      visibilityState: "visible" as DocumentVisibilityState,
      hasFocus: () => true,
    });
    const channel = Object.assign(new EventTarget(), { close: vi.fn() });
    const clear = vi.fn(),
      suspend = vi.fn(),
      resume = vi.fn();
    const detach = observeGoalsPrivacy(
      clear,
      {
        window: windowSurface,
        document: documentSurface,
        channel,
      },
      { suspend, resume },
    );
    windowSurface.dispatchEvent(new Event("blur"));
    documentSurface.visibilityState = "hidden";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(suspend).toHaveBeenCalledTimes(2);
    expect(clear).not.toHaveBeenCalled();
    documentSurface.visibilityState = "visible";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(resume).not.toHaveBeenCalled();
    windowSurface.dispatchEvent(new Event("focus"));
    expect(resume).toHaveBeenCalledTimes(1);
    for (const event of ["pagehide", "storage"])
      windowSurface.dispatchEvent(new Event(event));
    channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(3);
    detach();
    expect(clear).toHaveBeenCalledTimes(4);
    windowSurface.dispatchEvent(new Event("focus"));
    windowSurface.dispatchEvent(new Event("blur"));
    expect(resume).toHaveBeenCalledTimes(1);
    expect(suspend).toHaveBeenCalledTimes(2);
  });
});
