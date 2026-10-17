import { afterEach, describe, expect, it, vi } from "vitest";
import {
  observeStudentEmailPrivacy,
  readStudentEmailCorrection,
  readStudentEmailReview,
  readStudentEmailSources,
  readStudentEmailTaskReceipt,
  type StudentEmailApi,
  type StudentEmailContext,
  StudentEmailController,
  type StudentEmailCorrection,
  StudentEmailRequestError,
  type StudentEmailReview,
  type StudentEmailSource,
  studentEmailApi,
  studentEmailDeadline,
  studentEmailHandle,
  studentEmailReadyForTask,
  studentEmailRequest,
} from "./student-email";

const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const sourceId = "33333333-3333-4333-8333-333333333333";
const reviewId = "44444444-4444-4444-8444-444444444444";
const taskId = "55555555-5555-4555-8555-555555555555";
const courseId = "66666666-6666-4666-8666-666666666666";
const commitmentId = "77777777-7777-4777-8777-777777777777";
const otherSourceId = "88888888-8888-4888-8888-888888888888";
const otherReviewId = "99999999-9999-4999-8999-999999999999";
const accountId = `${workspaceId}:${userId}`;
const source: StudentEmailSource = {
  source_id: sourceId,
  source_hash: "a".repeat(64),
  display_label: "Professor · lab deadline",
  title: "Lab deadline",
  sender_label: "Professor",
};
const otherSource: StudentEmailSource = {
  ...source,
  source_id: otherSourceId,
  source_hash: "b".repeat(64),
};
const context: StudentEmailContext = {
  timezone: "America/New_York",
  server_now: "2026-10-19T12:00:00Z",
  courses: [
    {
      id: courseId,
      code: "CHEM 101",
      name: "Chemistry",
      professor: null,
      term_start: "2026-08-20",
      term_end: "2026-12-20",
      archived: false,
      revision: 1,
      meetings: [],
    },
  ],
};
function reviewFixture(
  changes: Partial<StudentEmailReview> = {},
): StudentEmailReview {
  return {
    review_id: reviewId,
    revision: 1,
    source_id: sourceId,
    source_hash: source.source_hash,
    expires_at: "2026-10-19T12:20:00Z",
    classification: {
      category: "has_deadline",
      reasons: ["The selected message mentions a deadline."],
      evidence: [{ source: "content", quote: "lab due", start: 0, end: 7 }],
      proposed_deadline: null,
      ambiguities: ["Confirm the intended date and course."],
    },
    correction: {
      category: "has_deadline",
      not_important: false,
      course_id: null,
      title: "Finish my lab",
      due_at: null,
      estimated_minutes: 45,
      confirmed_course: false,
      confirmed_deadline: false,
    },
    content_truncated: false,
    reply_handoff: null,
    actions_executed: false,
    task_id: null,
    existing_task: null,
    notice: "Review the proposal before saving a task. No message was sent.",
    ...changes,
  };
}
function readyReview(changes: Partial<StudentEmailReview> = {}) {
  const review = reviewFixture(changes);
  return {
    ...review,
    correction: {
      ...review.correction,
      confirmed_course: true,
      confirmed_deadline: true,
    },
  };
}
const controllers: StudentEmailController[] = [];
function fixture(review = reviewFixture()) {
  const api = {
    account: vi.fn(async (_signal: AbortSignal) => accountId),
    context: vi.fn(async (_signal: AbortSignal) => structuredClone(context)),
    search: vi.fn(async (_query: string, _signal: AbortSignal) => ({
      sources: [structuredClone(source), structuredClone(otherSource)],
      limit_reached: false,
      notice: "Only saved email labels were searched.",
    })),
    analyze: vi.fn<StudentEmailApi["analyze"]>(async () =>
      structuredClone(review),
    ),
    review: vi.fn<StudentEmailApi["review"]>(async () =>
      structuredClone(review),
    ),
    correct: vi.fn<StudentEmailApi["correct"]>(async (_id, input) => ({
      ...structuredClone(review),
      revision: input.revision + 1,
      correction: readStudentEmailCorrection(input),
    })),
    accept: vi.fn<StudentEmailApi["accept"]>(async () => ({
      task_id: taskId,
      reused: false,
      available: true,
      actions_executed: false,
      notice: "Saved after review. No message was sent.",
    })),
    erase: vi.fn<StudentEmailApi["erase"]>(async (_id, revision) => ({
      erased: true,
      revision: revision + 1,
    })),
    forget: vi.fn<StudentEmailApi["forget"]>(async () => ({
      erased: true,
      reviews_erased: 1,
      derived_tasks_erased: 1,
    })),
  } satisfies StudentEmailApi;
  let sequence = 0;
  const requestId = vi.fn(
    () => `aaaaaaaa-aaaa-4aaa-8aaa-${String(++sequence).padStart(12, "0")}`,
  );
  const controller = new StudentEmailController(accountId, api, requestId);
  controllers.push(controller);
  return { api, controller, requestId };
}
async function select(
  controller: StudentEmailController,
  item: StudentEmailSource = source,
) {
  controller.setQuery("lab deadline");
  await controller.search();
  controller.select(item);
}
async function open(rig: ReturnType<typeof fixture>) {
  await select(rig.controller);
  rig.controller.consent(true);
  await rig.controller.analyze();
}
function unknownWrite() {
  return new StudentEmailRequestError("The response was lost.", null, true);
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

describe("email review response boundaries", () => {
  it("projects saved labels and review handles without retaining hidden payloads", () => {
    const labels = readStudentEmailSources({
      sources: [{ ...source, body: "private message", token: "secret" }],
      limit_reached: false,
      notice: "Saved labels only.",
    });
    expect(labels.sources).toEqual([source]);
    const handle = studentEmailHandle(reviewFixture());
    expect(handle).toEqual({
      review_id: reviewId,
      revision: 1,
      source_id: sourceId,
    });
    expect(Object.keys(handle).sort()).toEqual([
      "review_id",
      "revision",
      "source_id",
    ]);
  });
  it("uses Unicode code points for bounded evidence offsets", () => {
    const review = reviewFixture();
    review.classification.evidence = [
      { source: "content", quote: "🧪due", start: 3, end: 7 },
    ];
    expect(
      readStudentEmailReview(review).classification.evidence[0].quote,
    ).toBe("🧪due");
    review.classification.evidence[0].end = 8;
    expect(() => readStudentEmailReview(review)).toThrow();
  });
  it("bounds sources and quotes by actual code points", () => {
    expect(
      readStudentEmailSources({
        sources: [{ ...source, title: "🧪".repeat(160) }],
        limit_reached: false,
        notice: "Saved labels.",
      }).sources[0].title,
    ).toHaveLength(320);
    expect(() =>
      readStudentEmailSources({
        sources: Array.from({ length: 21 }, () => source),
        limit_reached: false,
        notice: "Saved labels.",
      }),
    ).toThrow();
    const review = reviewFixture();
    review.classification.evidence = [
      { source: "content", quote: "a".repeat(241), start: 0, end: 241 },
    ];
    expect(() => readStudentEmailReview(review)).toThrow();
  });
  it.each([
    { actions_executed: true },
    { classification: { ...reviewFixture().classification, category: "send" } },
    {
      content_truncated: true,
      classification: {
        ...reviewFixture().classification,
        proposed_deadline: "2026-10-20T20:00:00Z",
      },
    },
    { expires_at: "2026-10-19T12:20:00" },
  ])(
    "rejects unverified action, category, deadline or clock claims: %j",
    (change) => {
      expect(() =>
        readStudentEmailReview({ ...reviewFixture(), ...change }),
      ).toThrow();
    },
  );
  it("allows only the fixed matching Inbox reply handoff", () => {
    const handoff = {
      commitment_id: commitmentId,
      source_id: sourceId,
      path: `/inbox?reply=${commitmentId}#drafts`,
      notice: "Open the existing draft review flow.",
    };
    expect(
      readStudentEmailReview({ ...reviewFixture(), reply_handoff: handoff })
        .reply_handoff,
    ).toEqual({
      commitment_id: commitmentId,
      path: handoff.path,
      notice: handoff.notice,
    });
    for (const path of [
      "https://example.com/send",
      `/inbox?reply=${taskId}#drafts`,
      `/inbox?reply=${commitmentId}&send=true#drafts`,
      `/inbox?reply=${commitmentId}#send`,
    ]) {
      expect(() =>
        readStudentEmailReview({
          ...reviewFixture(),
          reply_handoff: { ...handoff, path },
        }),
      ).toThrow();
    }
  });
  it("requires explicit course and deadline confirmation even for personal work", () => {
    expect(studentEmailReadyForTask(reviewFixture())).toBe(false);
    expect(studentEmailReadyForTask(readyReview())).toBe(true);
    const review = readyReview();
    review.correction.confirmed_deadline = false;
    expect(studentEmailReadyForTask(review)).toBe(false);
    review.correction.confirmed_deadline = true;
    review.correction.not_important = true;
    expect(studentEmailReadyForTask(review)).toBe(false);
    expect(studentEmailReadyForTask(readyReview({ task_id: taskId }))).toBe(
      false,
    );
  });
  it("requires a deliberate DST occurrence and rejects a skipped local deadline", () => {
    expect(() =>
      studentEmailDeadline("2026-03-08T02:30", "America/New_York", ""),
    ).toThrow(/skipped/);
    expect(() =>
      studentEmailDeadline("2026-11-01T01:30", "America/New_York", ""),
    ).toThrow(/twice/);
    expect(
      studentEmailDeadline(
        "2026-11-01T01:30",
        "America/New_York",
        "2026-11-01T06:30:00.000Z",
      ),
    ).toBe("2026-11-01T06:30:00.000Z");
  });
});

describe("bounded email transport", () => {
  it("makes one credentialed uncached request and never retries an unknown write", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetcher);
    await expect(
      studentEmailRequest("/analyze", readStudentEmailReview, "POST", {
        request_id: reviewId,
        source_id: sourceId,
        source_hash: source.source_hash,
      }),
    ).rejects.toMatchObject({ status: null, uncertain: true });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toMatch(/\/student\/email\/analyze$/);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
    });
  });
  it("treats malformed successful writes as unknown, but ordinary read failure as a read", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ actions_executed: true }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    await expect(
      studentEmailRequest("/analyze", readStudentEmailReview, "POST", {}),
    ).rejects.toMatchObject({ status: 200, uncertain: true });
    fetcher.mockRejectedValueOnce(new TypeError("offline"));
    await expect(
      studentEmailRequest("/sources?q=lab", readStudentEmailSources),
    ).rejects.toMatchObject({ status: null, uncertain: false });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("uses content-free erasure bodies and no body for source forgetting", async () => {
    const fetcher = vi.fn<typeof fetch>();
    fetcher.mockResolvedValueOnce(
      new Response(JSON.stringify({ erased: true, revision: 2 }), {
        status: 200,
      }),
    );
    fetcher.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          erased: true,
          reviews_erased: 1,
          derived_tasks_erased: 1,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetcher);
    await studentEmailApi.erase(reviewId, 1, new AbortController().signal);
    await studentEmailApi.forget(sourceId, new AbortController().signal);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      method: "DELETE",
      body: JSON.stringify({ revision: 1 }),
    });
    expect(fetcher.mock.calls[1][0]).toMatch(
      new RegExp(`/sources/${sourceId}$`),
    );
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
    expect(fetcher.mock.calls[1][1]).not.toHaveProperty("body");
  });
  it("rejects receipts claiming an executed external action", () => {
    expect(() =>
      readStudentEmailTaskReceipt({
        task_id: taskId,
        reused: false,
        available: true,
        actions_executed: true,
      }),
    ).toThrow();
  });
  it("keeps timeout and server error writes unknown while rejecting known conflicts", async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetcher);
    for (const status of [408, 503]) {
      fetcher.mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Request unavailable." }), {
          status,
        }),
      );
      await expect(
        studentEmailRequest("/analyze", readStudentEmailReview, "POST", {}),
      ).rejects.toMatchObject({ status, uncertain: true });
    }
    fetcher.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "The source changed." }), {
        status: 409,
      }),
    );
    await expect(
      studentEmailRequest("/analyze", readStudentEmailReview, "POST", {}),
    ).rejects.toMatchObject({ status: 409, uncertain: false });
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

describe("private email review controller", () => {
  it("reads no email before selecting a current saved source and consenting", async () => {
    const rig = fixture();
    expect(rig.api.account).not.toHaveBeenCalled();
    await rig.controller.analyze();
    expect(rig.api.analyze).not.toHaveBeenCalled();
    await select(rig.controller);
    await rig.controller.analyze();
    expect(rig.api.analyze).not.toHaveBeenCalled();
    rig.controller.consent(true);
    await rig.controller.analyze();
    expect(rig.api.analyze).toHaveBeenCalledTimes(1);
    expect(rig.api.analyze.mock.calls[0][0]).toEqual({
      request_id: rig.requestId.mock.results[0].value,
      source_id: sourceId,
      source_hash: source.source_hash,
    });
    expect(rig.controller.getSnapshot().readConsent).toBe(false);
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("rejects an arbitrary source or an outdated saved-source hash", async () => {
    const { controller, api } = fixture();
    controller.select(source);
    expect(controller.getSnapshot().selected).toBeNull();
    controller.setQuery("lab");
    await controller.search();
    controller.select({ ...source, source_hash: "c".repeat(64) });
    expect(controller.getSnapshot().selected).toBeNull();
    expect(api.analyze).not.toHaveBeenCalled();
  });
  it("retries an unknown analysis with the exact same ID and body", async () => {
    const rig = fixture();
    rig.api.analyze.mockRejectedValueOnce(unknownWrite());
    await open(rig);
    const pending = rig.controller.getSnapshot().pending;
    expect(pending?.kind).toBe("analyze");
    rig.controller.consent(true);
    await rig.controller.analyze();
    await rig.controller.search();
    expect(rig.api.analyze).toHaveBeenCalledTimes(1);
    await rig.controller.checkPending();
    expect(rig.api.analyze).toHaveBeenCalledTimes(2);
    expect(rig.api.analyze.mock.calls[1][0]).toEqual(
      rig.api.analyze.mock.calls[0][0],
    );
    expect(rig.requestId).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot().pending).toBeNull();
    expect(rig.controller.getSnapshot().review?.review_id).toBe(reviewId);
  });
  it("keeps a completed analysis pending when its following context read fails", async () => {
    const rig = fixture();
    rig.api.context.mockRejectedValueOnce(new Error("context is offline"));
    await open(rig);
    expect(rig.controller.getSnapshot().pending?.kind).toBe("analyze");
    expect(rig.controller.getSnapshot().review).toBeNull();
    await rig.controller.checkPending();
    expect(rig.api.analyze).toHaveBeenCalledTimes(2);
    expect(rig.api.analyze.mock.calls[1][0]).toEqual(
      rig.api.analyze.mock.calls[0][0],
    );
    expect(rig.requestId).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot().review?.review_id).toBe(reviewId);
  });
  it("does not show an analysis for a different source or source revision", async () => {
    const rig = fixture(reviewFixture({ source_hash: "c".repeat(64) }));
    await open(rig);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().pending?.kind).toBe("analyze");
    expect(rig.api.context).not.toHaveBeenCalled();
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("retries a correction with the exact original revision, ID and reviewed fields", async () => {
    const rig = fixture();
    await open(rig);
    rig.api.correct.mockRejectedValueOnce(unknownWrite());
    const correction: StudentEmailCorrection = {
      ...reviewFixture().correction,
      course_id: courseId,
      due_at: "2026-10-21T20:00:00Z",
      title: "My reviewed task title",
      confirmed_course: true,
      confirmed_deadline: true,
    };
    await rig.controller.correct(correction);
    expect(rig.controller.getSnapshot().pending?.kind).toBe("correct");
    await rig.controller.notImportant();
    expect(rig.api.correct).toHaveBeenCalledTimes(1);
    await rig.controller.checkPending();
    expect(rig.api.correct.mock.calls[1].slice(0, 2)).toEqual(
      rig.api.correct.mock.calls[0].slice(0, 2),
    );
    expect(rig.api.correct.mock.calls[1][1]).toMatchObject({
      ...correction,
      revision: 1,
    });
    expect(rig.requestId).toHaveBeenCalledTimes(2);
    expect(rig.controller.getSnapshot().review?.revision).toBe(2);
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("retries an unknown task acceptance without allocating a second task request", async () => {
    const rig = fixture(readyReview());
    await open(rig);
    rig.api.accept.mockRejectedValueOnce(unknownWrite());
    await rig.controller.accept(true, false);
    expect(rig.controller.getSnapshot().pending?.kind).toBe("accept");
    await rig.controller.accept(true, false);
    await rig.controller.checkPending();
    expect(rig.api.accept).toHaveBeenCalledTimes(2);
    expect(rig.api.accept.mock.calls[1].slice(0, 2)).toEqual(
      rig.api.accept.mock.calls[0].slice(0, 2),
    );
    expect(rig.requestId).toHaveBeenCalledTimes(2);
    expect(rig.controller.getSnapshot().receipt?.task_id).toBe(taskId);
  });
  it("checks ownership before I/O and blocks a different account", async () => {
    const rig = fixture();
    rig.api.account.mockResolvedValueOnce(`${workspaceId}:${taskId}`);
    rig.controller.setQuery("lab");
    await rig.controller.search();
    expect(rig.api.search).not.toHaveBeenCalled();
    expect(rig.controller.getSnapshot()).toMatchObject({
      blocked: true,
      query: "",
      sources: null,
      review: null,
    });
  });
  it("does not show a successful response after ownership changes during I/O", async () => {
    const rig = fixture();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${taskId}`);
    rig.controller.setQuery("lab");
    await rig.controller.search();
    expect(rig.api.search).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot()).toMatchObject({
      blocked: true,
      query: "",
      sources: null,
    });
  });
  it("rechecks ownership after an unknown write failure before retaining private state", async () => {
    const rig = fixture();
    await open(rig);
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${taskId}`);
    rig.api.correct.mockRejectedValueOnce(unknownWrite());
    await rig.controller.notImportant();
    expect(rig.controller.getSnapshot()).toMatchObject({
      blocked: true,
      pending: null,
      review: null,
      selected: null,
      context: null,
    });
    expect(rig.controller.getSnapshot().handles).toEqual([
      { review_id: reviewId, revision: 1, source_id: sourceId },
    ]);
    await rig.controller.checkPending();
    expect(rig.api.correct).toHaveBeenCalledTimes(1);
  });
  it("aborts and fences a late response when the private view is cleared", async () => {
    const rig = fixture();
    await select(rig.controller);
    const pending = deferred<StudentEmailReview>();
    rig.api.analyze.mockReturnValueOnce(pending.promise);
    rig.controller.consent(true);
    const operation = rig.controller.analyze();
    await vi.waitFor(() => expect(rig.api.analyze).toHaveBeenCalledTimes(1));
    const signal = rig.api.analyze.mock.calls[0][1];
    rig.controller.clear("The private page was hidden.");
    expect(signal.aborted).toBe(true);
    pending.resolve(reviewFixture());
    await operation;
    expect(rig.controller.getSnapshot()).toMatchObject({
      query: "",
      review: null,
      context: null,
      selected: null,
      pending: null,
      busy: false,
      handles: [],
    });
  });
  it("clears visible content and retains only opaque erasure handles", async () => {
    const rig = fixture();
    await open(rig);
    rig.controller.clear();
    const state = rig.controller.getSnapshot();
    expect(state).toMatchObject({
      query: "",
      sources: null,
      selected: null,
      readConsent: false,
      review: null,
      context: null,
      receipt: null,
      pending: null,
    });
    expect(state.handles).toEqual([studentEmailHandle(reviewFixture())]);
    expect(JSON.stringify(state)).not.toMatch(
      /lab due|Professor|Finish my lab/,
    );
    await rig.controller.reopen(taskId);
    expect(rig.api.review).not.toHaveBeenCalled();
    await rig.controller.reopen(reviewId);
    expect(rig.api.review).toHaveBeenCalledTimes(1);
    expect(rig.controller.getSnapshot().review?.review_id).toBe(reviewId);
  });
  it("expires private text using the server receipt lifetime without a new email read", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await open(rig);
    await vi.advanceTimersByTimeAsync(20 * 60_000);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().handles).toEqual([
      studentEmailHandle(reviewFixture()),
    ]);
    expect(rig.api.analyze).toHaveBeenCalledTimes(1);
    expect(rig.api.review).not.toHaveBeenCalled();
  });
  it("rejects an already-expired receipt instead of showing stale evidence", async () => {
    const rig = fixture(reviewFixture({ expires_at: context.server_now }));
    await open(rig);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.controller.getSnapshot().error).toMatch(/expired/);
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("saves Not important as a reviewed correction and creates no task", async () => {
    const rig = fixture(readyReview());
    await open(rig);
    await rig.controller.notImportant();
    expect(rig.api.correct.mock.calls[0][1]).toMatchObject({
      category: "ignore",
      not_important: true,
      revision: 1,
    });
    await rig.controller.accept(true, false);
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("requires explicit refresh of only the exact active linked task and revision", async () => {
    const rig = fixture(
      readyReview({
        existing_task: {
          id: taskId,
          revision: 7,
          title: "Existing lab",
          course_id: courseId,
          status: "active",
          remaining_minutes: 30,
          actual_minutes: 15,
        },
      }),
    );
    await open(rig);
    await rig.controller.accept(false, true);
    await rig.controller.accept(true, false);
    expect(rig.api.accept).not.toHaveBeenCalled();
    await rig.controller.accept(true, true);
    expect(rig.api.accept.mock.calls[0][0]).toBe(reviewId);
    expect(rig.api.accept.mock.calls[0][1]).toMatchObject({
      task_id: taskId,
      task_revision: 7,
      revision: 1,
      source_hash: source.source_hash,
      confirmed: true,
    });
  });
  it.each(["completed", "archived"] as const)(
    "never refreshes a linked %s task",
    async (status) => {
      const rig = fixture(
        readyReview({
          existing_task: {
            id: taskId,
            revision: 2,
            title: "Old work",
            course_id: null,
            status,
            remaining_minutes: 0,
            actual_minutes: 30,
          },
        }),
      );
      await open(rig);
      await rig.controller.accept(true, true);
      expect(rig.api.accept).not.toHaveBeenCalled();
    },
  );
  it("does not refresh a task whose saved course is no longer active", async () => {
    const rig = fixture(
      readyReview({
        existing_task: {
          id: taskId,
          revision: 2,
          title: "Old lab",
          course_id: courseId,
          status: "active",
          remaining_minutes: 30,
          actual_minutes: 0,
        },
      }),
    );
    rig.api.context.mockResolvedValue({ ...context, courses: [] });
    await open(rig);
    await rig.controller.accept(true, true);
    expect(rig.api.accept).not.toHaveBeenCalled();
  });
  it("erases one review copy while keeping a different source's handle", async () => {
    const rig = fixture();
    await open(rig);
    rig.api.analyze.mockResolvedValueOnce(
      reviewFixture({
        review_id: otherReviewId,
        source_id: otherSourceId,
        source_hash: otherSource.source_hash,
      }),
    );
    await select(rig.controller, otherSource);
    rig.controller.consent(true);
    await rig.controller.analyze();
    await rig.controller.erase(studentEmailHandle(reviewFixture()));
    expect(rig.api.erase.mock.calls[0].slice(0, 2)).toEqual([reviewId, 1]);
    expect(rig.controller.getSnapshot().handles).toEqual([
      { review_id: otherReviewId, revision: 1, source_id: otherSourceId },
    ]);
    expect(rig.controller.getSnapshot().review).toBeNull();
    expect(rig.api.forget).not.toHaveBeenCalled();
  });
  it("requires confirmation and a known source handle before forgetting derived data", async () => {
    const rig = fixture();
    await open(rig);
    await rig.controller.forget(otherSourceId, true);
    await rig.controller.forget(sourceId, false);
    expect(rig.api.forget).not.toHaveBeenCalled();
    await rig.controller.forget(sourceId, true);
    expect(rig.api.forget.mock.calls[0][0]).toBe(sourceId);
    expect(rig.controller.getSnapshot().handles).toEqual([]);
    expect(rig.controller.getSnapshot().notice).toMatch(
      /original email.*remain/,
    );
    expect(rig.api.erase).not.toHaveBeenCalled();
  });
  it("retries an unknown erase with the retained opaque handle and original revision", async () => {
    const rig = fixture();
    await open(rig);
    rig.api.erase.mockRejectedValueOnce(unknownWrite());
    await rig.controller.erase({
      ...studentEmailHandle(reviewFixture()),
      revision: 987,
      source_id: otherSourceId,
    });
    expect(rig.controller.getSnapshot().pending).toEqual({
      kind: "erase",
      handle: studentEmailHandle(reviewFixture()),
    });
    await rig.controller.notImportant();
    await rig.controller.forget(sourceId, true);
    expect(rig.api.correct).not.toHaveBeenCalled();
    expect(rig.api.forget).not.toHaveBeenCalled();
    await rig.controller.checkPending();
    expect(rig.api.erase.mock.calls.map((call) => call.slice(0, 2))).toEqual([
      [reviewId, 1],
      [reviewId, 1],
    ]);
    expect(rig.controller.getSnapshot().handles).toEqual([]);
    expect(rig.controller.getSnapshot().pending).toBeNull();
    expect(rig.requestId).toHaveBeenCalledTimes(1);
  });
  it("retries unknown forgetting only for the same owned source", async () => {
    const rig = fixture();
    await open(rig);
    rig.api.forget.mockRejectedValueOnce(unknownWrite());
    await rig.controller.forget(sourceId, true);
    expect(rig.controller.getSnapshot().pending).toEqual({
      kind: "forget",
      source_id: sourceId,
    });
    await rig.controller.accept(true, false);
    await rig.controller.erase(studentEmailHandle(reviewFixture()));
    expect(rig.api.accept).not.toHaveBeenCalled();
    expect(rig.api.erase).not.toHaveBeenCalled();
    await rig.controller.checkPending();
    expect(rig.api.forget.mock.calls.map((call) => call[0])).toEqual([
      sourceId,
      sourceId,
    ]);
    expect(rig.controller.getSnapshot().handles).toEqual([]);
    expect(rig.controller.getSnapshot().pending).toBeNull();
    expect(rig.requestId).toHaveBeenCalledTimes(1);
  });
  it("clears on browser and account events without reading again on visibility", async () => {
    const rig = fixture();
    await open(rig);
    const windowSurface = new EventTarget();
    const documentSurface = Object.assign(new EventTarget(), {
      visibilityState: "visible" as DocumentVisibilityState,
    });
    const channel = Object.assign(new EventTarget(), { close: vi.fn() });
    const clear = vi.fn(() => rig.controller.clear());
    const detach = observeStudentEmailPrivacy(clear, {
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
    expect(rig.controller.getSnapshot().review).toBeNull();
    documentSurface.visibilityState = "visible";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(rig.api.analyze).toHaveBeenCalledTimes(1);
    expect(rig.api.review).not.toHaveBeenCalled();
    detach();
    expect(clear).toHaveBeenCalledTimes(6);
    expect(channel.close).toHaveBeenCalledTimes(1);
    windowSurface.dispatchEvent(new Event("storage"));
    channel.dispatchEvent(new Event("message"));
    documentSurface.visibilityState = "hidden";
    documentSurface.dispatchEvent(new Event("visibilitychange"));
    expect(clear).toHaveBeenCalledTimes(6);
  });
});
