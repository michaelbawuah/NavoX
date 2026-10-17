import { afterEach, describe, expect, it, vi } from "vitest";
import {
  COURSE_PRIVATE_TTL_MS,
  type CoursePrivacyApi,
  CoursePrivacySession,
  type CourseProof,
  courseProofCurrent,
  courseSearchCurrent,
  observeCoursePrivacy,
} from "./student-course-privacy";
import { CourseRequestError, type CourseSearch } from "./student-coursework";

const userId = "11111111-1111-4111-8111-111111111111";
const workspaceId = "22222222-2222-4222-8222-222222222222";
const courseId = "33333333-3333-4333-8333-333333333333";
const materialId = "44444444-4444-4444-8444-444444444444";
const otherId = "55555555-5555-4555-8555-555555555555";
const accountId = `${workspaceId}:${userId}`;
const privateText = "Private source detail that must not survive";

function proofFixture(): CourseProof {
  const quote = {
    material_id: materialId,
    revision: 2,
    page: 2,
    quote: "Ask the instructor before assessed work.",
  };
  return {
    overview: {
      course: {
        id: courseId,
        code: "BIO 101",
        name: "Synthetic private course",
        term_start: "2026-09-01",
        term_end: "2026-12-31",
        professor: null,
        archived: false,
        revision: 1,
        meetings: [],
      },
      archived: false,
      revision: 2,
      staff: [
        {
          name: "Synthetic instructor",
          role: "Professor",
          email: null,
          office_hours: null,
          source: { ...quote },
        },
      ],
      grade_breakdown: [
        { name: "Coursework", weight_percent: "100", source: { ...quote } },
      ],
      ai_policy: { ...quote },
      grade_weight_total: "100",
      policy_status: "quoted_policy",
      default_help_mode: "tutor_outline_review",
      ai_permission_granted: false,
      notice: "Private course workspace",
    },
    materials: [
      {
        id: materialId,
        title: "Synthetic syllabus",
        kind: "syllabus",
        revision: 2,
        media_type: "application/pdf",
        byte_size: 1000,
        text_size: 100,
        page_count: 2,
        created_at: "2026-10-19T12:00:00Z",
        updated_at: "2026-10-19T13:00:00Z",
      },
    ],
    assignments: { tasks: [], feed_items: [], next_offset: null },
    emails: {
      links: [
        {
          id: otherId,
          source_id: materialId,
          source_hash: "a".repeat(64),
          revision: 1,
          source_type: "communication.message",
          source_open_url: null,
          title: "Synthetic saved source",
          sender_label: "Course staff",
        },
      ],
      unavailable_count: 0,
    },
  };
}

const search: CourseSearch = {
  course_id: courseId,
  results: [
    {
      material_id: materialId,
      revision: 2,
      title: "Synthetic syllabus",
      page: 2,
      snippet: "Ask the instructor before assessed work.",
    },
  ],
  limit_reached: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const sessions = new Set<CoursePrivacySession>();
function fixture() {
  const server = { proof: proofFixture() };
  let now = 0;
  const api = {
    account: vi.fn<CoursePrivacyApi["account"]>().mockResolvedValue(accountId),
    proof: vi
      .fn<CoursePrivacyApi["proof"]>()
      .mockImplementation(async () => structuredClone(server.proof)),
  };
  const session = new CoursePrivacySession(accountId, courseId, api, () => now);
  sessions.add(session);
  return {
    session,
    api,
    server,
    advance: (milliseconds: number) => {
      now += milliseconds;
    },
  };
}

async function microtasks() {
  await Promise.resolve();
  await Promise.resolve();
}

afterEach(() => {
  for (const session of sessions) session.clear();
  sessions.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("current course source proofs", () => {
  it("accepts exact material versions/pages for policy and search citations", () => {
    const proof = proofFixture();
    expect(courseProofCurrent(proof, courseId)).toBe(true);
    expect(courseSearchCurrent(search, proof, courseId)).toBe(true);
  });

  it("rejects a foreign course, removed material, changed revision, or invalid citation page", () => {
    const proof = proofFixture();
    expect(courseProofCurrent(proof, otherId)).toBe(false);
    expect(courseProofCurrent({ ...proof, materials: [] }, courseId)).toBe(
      false,
    );
    expect(
      courseProofCurrent(
        { ...proof, materials: [{ ...proof.materials[0], revision: 3 }] },
        courseId,
      ),
    ).toBe(false);
    for (const page of [0, 3]) {
      const stale = structuredClone(proof);
      if (stale.overview.ai_policy) stale.overview.ai_policy.page = page;
      expect(courseProofCurrent(stale, courseId)).toBe(false);
    }
    expect(
      courseProofCurrent(
        { ...proof, overview: { ...proof.overview, archived: true } },
        courseId,
      ),
    ).toBe(false);
  });

  it("rejects stale staff/grade citations as well as stale policy quotes", () => {
    for (const type of ["staff", "grade_breakdown"] as const) {
      const proof = proofFixture();
      const source = proof.overview[type][0].source;
      if (source) source.revision = 1;
      expect(courseProofCurrent(proof, courseId)).toBe(false);
    }
  });

  it("binds search hits to this course and its complete current material list", () => {
    const proof = proofFixture();
    expect(
      courseSearchCurrent({ ...search, course_id: otherId }, proof, courseId),
    ).toBe(false);
    for (const changes of [
      { material_id: otherId },
      { revision: 1 },
      { page: 0 },
      { page: 3 },
    ]) {
      expect(
        courseSearchCurrent(
          { ...search, results: [{ ...search.results[0], ...changes }] },
          proof,
          courseId,
        ),
      ).toBe(false);
    }
    expect(
      courseSearchCurrent({ ...search, results: [] }, proof, courseId),
    ).toBe(true);
  });
});

describe("owned course snapshot lifecycle", () => {
  it("waits for an explicit refresh and proves the owner around the full course snapshot", async () => {
    const rig = fixture();
    expect(rig.api.account).not.toHaveBeenCalled();
    expect(rig.api.proof).not.toHaveBeenCalled();
    const finalProof = deferred<string>();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockReturnValueOnce(finalProof.promise);
    const operation = rig.session.refresh();
    await microtasks();
    expect(rig.api.proof).toHaveBeenCalledTimes(1);
    expect(rig.api.proof.mock.calls[0][0]).toBe(courseId);
    expect(rig.api.account).toHaveBeenCalledTimes(2);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      masked: true,
      checking: true,
    });
    finalProof.resolve(accountId);
    expect(await operation).toBe(true);
    expect(rig.session.getSnapshot()).toMatchObject({
      masked: false,
      checking: false,
      blocked: false,
    });
    expect(rig.session.getSnapshot().proof).toEqual(rig.server.proof);
  });

  it.each(["mismatch", "offline"])(
    "does not fetch private records after a failed initial owner proof (%s)",
    async (failure) => {
      const rig = fixture();
      if (failure === "mismatch")
        rig.api.account.mockResolvedValueOnce(`${workspaceId}:${otherId}`);
      else rig.api.account.mockRejectedValueOnce(new Error(privateText));
      expect(await rig.session.refresh()).toBe(false);
      expect(rig.api.proof).not.toHaveBeenCalled();
      expect(rig.session.getSnapshot().proof).toBeNull();
      expect(rig.session.getSnapshot().error).not.toContain(privateText);
    },
  );

  it("discards a loaded snapshot when the final owner proof changes", async () => {
    const rig = fixture();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${otherId}`);
    expect(await rig.session.refresh()).toBe(false);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      blocked: true,
      masked: false,
      checking: false,
      epoch: 1,
    });
    expect(JSON.stringify(rig.session.getSnapshot())).not.toContain(
      "Synthetic private course",
    );
  });

  it.each([401, 403, 404, 409])(
    "clears the old snapshot and editor epoch on a fresh status %s",
    async (status) => {
      const rig = fixture();
      await rig.session.refresh();
      const epoch = rig.session.getSnapshot().epoch;
      rig.api.proof.mockRejectedValueOnce(
        new CourseRequestError(privateText, status),
      );
      expect(await rig.session.refresh()).toBe(false);
      expect(rig.session.getSnapshot()).toMatchObject({
        proof: null,
        epoch: epoch + 1,
        blocked: status === 401 || status === 403,
        checking: false,
        masked: false,
      });
      expect(JSON.stringify(rig.session.getSnapshot())).not.toMatch(
        /Synthetic private course|Ask the instructor|Private source detail/,
      );
    },
  );

  it("clears old details and drafts after a reset removes the cited source", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.server.proof.materials = [];
    expect(await rig.session.refresh()).toBe(false);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
    expect(rig.session.getSnapshot().error).toMatch(/changed/);
  });

  it.each(["clear", "suspend"] as const)(
    "does not start a source fetch after %s interrupts account proof",
    async (action) => {
      const rig = fixture();
      const proof = deferred<string>();
      rig.api.account.mockReturnValueOnce(proof.promise);
      const operation = rig.session.refresh();
      rig.session[action]();
      expect(rig.api.account.mock.calls[0][0].aborted).toBe(true);
      proof.resolve(accountId);
      expect(await operation).toBe(false);
      expect(rig.api.proof).not.toHaveBeenCalled();
    },
  );

  it.each(["success", "error"])(
    "fences a late refresh %s after Clear",
    async (result) => {
      const rig = fixture();
      const pending = deferred<CourseProof>();
      rig.api.proof.mockReturnValueOnce(pending.promise);
      const operation = rig.session.refresh();
      await microtasks();
      rig.session.clear("Session changed.");
      const state = rig.session.getSnapshot();
      expect(rig.api.proof.mock.calls[0][1].aborted).toBe(true);
      if (result === "success") pending.resolve(proofFixture());
      else pending.reject(new CourseRequestError(privateText, 404));
      expect(await operation).toBe(false);
      expect(rig.session.getSnapshot()).toBe(state);
      expect(state.proof).toBeNull();
    },
  );

  it("does not overwrite a new successful refresh with an older late response", async () => {
    const rig = fixture();
    const old = deferred<CourseProof>();
    rig.api.proof.mockReturnValueOnce(old.promise);
    const earlier = rig.session.refresh();
    await microtasks();
    rig.session.clear();
    rig.server.proof.overview.course.name = "New course snapshot";
    await rig.session.refresh();
    old.resolve(proofFixture());
    await earlier;
    expect(rig.session.getSnapshot().proof?.overview.course.name).toBe(
      "New course snapshot",
    );
  });

  it("preserves the editor epoch through picker-like suspension only after unchanged source and owner proofs", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const before = rig.session.getSnapshot();
    rig.session.suspend();
    expect(rig.session.getSnapshot()).toMatchObject({
      masked: true,
      epoch: before.epoch,
    });
    expect(rig.session.getSnapshot().proof).toBe(before.proof);
    const proofs = rig.api.proof.mock.calls.length;
    const accounts = rig.api.account.mock.calls.length;
    expect(await rig.session.refresh()).toBe(true);
    expect(rig.api.proof).toHaveBeenCalledTimes(proofs + 1);
    expect(rig.api.account).toHaveBeenCalledTimes(accounts + 2);
    expect(rig.session.getSnapshot()).toMatchObject({
      masked: false,
      epoch: before.epoch,
    });
    expect(rig.session.getSnapshot().readEpoch).toBeGreaterThan(
      before.readEpoch,
    );
  });

  it.each(["course", "material", "email", "assignment"])(
    "remounts drafts after a changed %s proof",
    async (field) => {
      const rig = fixture();
      await rig.session.refresh();
      const before = rig.session.getSnapshot();
      rig.session.suspend();
      if (field === "course") rig.server.proof.overview.course.revision += 1;
      else if (field === "material")
        rig.server.proof.materials[0].updated_at = "2026-10-19T14:00:00Z";
      else if (field === "email")
        rig.server.proof.emails.links[0].source_hash = "b".repeat(64);
      else rig.server.proof.assignments.next_offset = 50;
      expect(await rig.session.refresh()).toBe(true);
      expect(rig.session.getSnapshot()).toMatchObject({
        masked: false,
        epoch: before.epoch + 1,
      });
      expect(rig.session.getSnapshot().readEpoch).toBeGreaterThan(
        before.readEpoch,
      );
      expect(rig.session.getSnapshot().notice).toMatch(
        /Previous edits.*cleared/,
      );
    },
  );

  it("increments the editor epoch when a current material revision invalidates saved citations", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.session.suspend();
    rig.server.proof.materials[0].revision = 3;
    expect(await rig.session.refresh()).toBe(false);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
  });

  it("notifies subscribers and stops notifying detached observers", async () => {
    const rig = fixture();
    const listener = vi.fn();
    const detach = rig.session.subscribe(listener);
    await rig.session.refresh();
    expect(listener).toHaveBeenCalled();
    detach();
    listener.mockClear();
    rig.session.clear();
    expect(listener).not.toHaveBeenCalled();
  });
});

describe("owned private material reads", () => {
  it("requires a current unmasked proof before reading source text", async () => {
    const rig = fixture();
    const read = vi.fn(async () => privateText);
    await expect(rig.session.read(read)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    await rig.session.refresh();
    rig.session.suspend();
    await expect(rig.session.read(read)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    expect(read).not.toHaveBeenCalled();
  });

  it("proves the owner before and after a private read before returning its text", async () => {
    const rig = fixture();
    await rig.session.refresh();
    rig.api.account.mockClear();
    const finalProof = deferred<string>();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockReturnValueOnce(finalProof.promise);
    const read = vi.fn(async () => privateText);
    const operation = rig.session.read(read);
    let exposed = false;
    void operation.then(() => {
      exposed = true;
    });
    await microtasks();
    expect(read).toHaveBeenCalledTimes(1);
    expect(rig.api.account).toHaveBeenCalledTimes(2);
    expect(exposed).toBe(false);
    finalProof.resolve(accountId);
    expect(await operation).toBe(privateText);
  });

  it.each(["before", "after"])(
    "drops a private read when the owner changes %s fetching",
    async (when) => {
      const rig = fixture();
      await rig.session.refresh();
      const read = vi.fn(async () => privateText);
      if (when === "after") rig.api.account.mockResolvedValueOnce(accountId);
      rig.api.account.mockResolvedValueOnce(`${workspaceId}:${otherId}`);
      await expect(rig.session.read(read)).rejects.toBeInstanceOf(
        CourseRequestError,
      );
      expect(read).toHaveBeenCalledTimes(when === "before" ? 0 : 1);
      expect(rig.session.getSnapshot()).toMatchObject({
        proof: null,
        blocked: true,
      });
      expect(JSON.stringify(rig.session.getSnapshot())).not.toContain(
        privateText,
      );
    },
  );

  it("clears the old snapshot on a private read error without echoing source text", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    const error = await rig.session
      .read(async () => {
        throw new CourseRequestError(privateText, 404);
      })
      .catch((error: unknown) => error);
    expect(String(error)).not.toContain(privateText);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
    expect(rig.session.getSnapshot().error).toMatch(/no longer available/);
  });

  it.each(["success", "error"])(
    "fences a late private read %s after suspension",
    async (result) => {
      const rig = fixture();
      await rig.session.refresh();
      const pending = deferred<string>();
      const read = vi.fn((_: AbortSignal) => pending.promise);
      const operation = rig.session.read(read).catch((error: unknown) => error);
      await microtasks();
      rig.session.suspend();
      const suspended = rig.session.getSnapshot();
      expect(read.mock.calls[0][0].aborted).toBe(true);
      if (result === "success") pending.resolve(privateText);
      else pending.reject(new CourseRequestError(privateText, 404));
      expect(await operation).toBeInstanceOf(CourseRequestError);
      expect(rig.session.getSnapshot()).toBe(suspended);
    },
  );
});

describe("background-only draft retention", () => {
  it("does not expire an active editor just because ten minutes of typing passed", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.session.refresh();
    const before = rig.session.getSnapshot();
    rig.advance(COURSE_PRIVATE_TTL_MS * 2);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS * 2);
    expect(rig.session.getSnapshot()).toBe(before);
    expect(await rig.session.read(async () => privateText)).toBe(privateText);
    expect(rig.api.proof).toHaveBeenCalledTimes(1);
  });

  it("starts retention on suspension and clears background drafts at its original deadline", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.advance(COURSE_PRIVATE_TTL_MS * 2);
    rig.session.suspend();
    rig.advance(COURSE_PRIVATE_TTL_MS - 1);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS - 1);
    expect(rig.session.getSnapshot().proof).not.toBeNull();
    rig.advance(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
    expect(rig.api.proof).toHaveBeenCalledTimes(1);
  });

  it("does not extend retention after repeated hide events", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.session.refresh();
    rig.session.suspend();
    rig.advance(COURSE_PRIVATE_TTL_MS - 10_000);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS - 10_000);
    rig.session.suspend();
    rig.advance(10_000);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rig.session.getSnapshot().proof).toBeNull();
  });

  it("keeps the first background deadline when refocus is pending and the page hides again", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.session.suspend();
    rig.advance(COURSE_PRIVATE_TTL_MS - 10_000);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS - 10_000);
    const pending = deferred<CourseProof>();
    rig.api.proof.mockReturnValueOnce(pending.promise);
    const operation = rig.session.refresh();
    await microtasks();
    rig.session.suspend();
    rig.advance(10_000);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
    pending.resolve(proofFixture());
    expect(await operation).toBe(false);
    expect(rig.session.getSnapshot().proof).toBeNull();
  });

  it("expires on refocus using monotonic time even if the timeout callback was delayed", async () => {
    const rig = fixture();
    await rig.session.refresh();
    rig.session.suspend();
    const proofs = rig.api.proof.mock.calls.length;
    rig.advance(COURSE_PRIVATE_TTL_MS);
    expect(await rig.session.refresh()).toBe(false);
    expect(rig.session.getSnapshot().proof).toBeNull();
    expect(rig.api.proof).toHaveBeenCalledTimes(proofs);
  });

  it("does not revive a retained draft when the owner/source checks finish after its deadline", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.session.suspend();
    const proof = deferred<CourseProof>();
    rig.api.proof.mockReturnValueOnce(proof.promise);
    const operation = rig.session.refresh();
    await microtasks();
    rig.advance(COURSE_PRIVATE_TTL_MS);
    proof.resolve(proofFixture());
    expect(await operation).toBe(false);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
    });
  });

  it("cancels the old background deadline after a successful unchanged refocus", async () => {
    vi.useFakeTimers();
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.session.suspend();
    rig.advance(COURSE_PRIVATE_TTL_MS - 1000);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS - 1000);
    expect(await rig.session.refresh()).toBe(true);
    rig.advance(COURSE_PRIVATE_TTL_MS * 2);
    await vi.advanceTimersByTimeAsync(COURSE_PRIVATE_TTL_MS * 2);
    expect(rig.session.getSnapshot()).toMatchObject({ epoch, masked: false });
    expect(rig.session.getSnapshot().proof).not.toBeNull();
  });
});

describe("explicit course writes and uncertain results", () => {
  it("will not write until the current course is proved and visible", async () => {
    const rig = fixture();
    const write = vi.fn(async () => ({ saved: true }));
    await expect(rig.session.write(write)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    await rig.session.refresh();
    rig.session.suspend();
    await expect(rig.session.write(write)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    expect(write).not.toHaveBeenCalled();
  });

  it("proves ownership before and after a successful explicit write", async () => {
    const rig = fixture();
    await rig.session.refresh();
    rig.api.account.mockClear();
    const write = vi.fn(async () => ({ saved: true }));
    expect(await rig.session.write(write)).toEqual({ saved: true });
    expect(write).toHaveBeenCalledTimes(1);
    expect(rig.api.account).toHaveBeenCalledTimes(2);
    expect(rig.session.getSnapshot()).toMatchObject({
      uncertain: false,
      writePending: false,
    });
  });

  it("does not send a write after a late owner proof was interrupted", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const proof = deferred<string>();
    rig.api.account.mockReturnValueOnce(proof.promise);
    const write = vi.fn(async () => ({ saved: true }));
    const operation = rig.session.write(write).catch((error: unknown) => error);
    rig.session.suspend();
    proof.resolve(accountId);
    expect(await operation).toBeInstanceOf(CourseRequestError);
    expect(write).not.toHaveBeenCalled();
  });

  it.each([413, 415, 422, 429])(
    "keeps drafts after a known %s rejection only after fresh unchanged owner/source proofs",
    async (status) => {
      const rig = fixture();
      await rig.session.refresh();
      const before = rig.session.getSnapshot();
      const accounts = rig.api.account.mock.calls.length;
      const proofs = rig.api.proof.mock.calls.length;
      const write = vi.fn(async () => {
        throw new CourseRequestError(privateText, status, false);
      });
      const error = await rig.session
        .write(write)
        .catch((error: unknown) => error);
      expect(error).toMatchObject({ status, uncertain: false });
      expect(rig.session.getSnapshot()).toMatchObject({
        proof: before.proof,
        epoch: before.epoch,
        uncertain: false,
        writePending: false,
      });
      expect(rig.session.getSnapshot().readEpoch).toBeGreaterThan(
        before.readEpoch,
      );
      expect(rig.api.account).toHaveBeenCalledTimes(accounts + 3);
      expect(rig.api.proof).toHaveBeenCalledTimes(proofs + 1);
      expect(write).toHaveBeenCalledTimes(1);
      expect(JSON.stringify(rig.session.getSnapshot())).not.toContain(
        privateText,
      );
    },
  );

  it.each([422, 404])(
    "clears retained edits when readonly recovery after a rejected file fails with %s",
    async (status) => {
      const rig = fixture();
      await rig.session.refresh();
      const epoch = rig.session.getSnapshot().epoch;
      rig.api.proof.mockRejectedValueOnce(
        new CourseRequestError(privateText, status),
      );
      const write = vi.fn(async () => {
        throw new CourseRequestError(privateText, 422, false);
      });
      await expect(rig.session.write(write)).rejects.toBeInstanceOf(
        CourseRequestError,
      );
      expect(rig.session.getSnapshot()).toMatchObject({
        proof: null,
        epoch: epoch + 1,
        uncertain: false,
        writePending: false,
      });
      expect(write).toHaveBeenCalledTimes(1);
      expect(JSON.stringify(rig.session.getSnapshot())).not.toContain(
        privateText,
      );
    },
  );

  it("clears edits when rejected-file recovery proves a changed source", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const epoch = rig.session.getSnapshot().epoch;
    rig.server.proof.overview.course.revision += 1;
    const write = vi.fn(async () => {
      throw new CourseRequestError(privateText, 422, false);
    });
    await expect(rig.session.write(write)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 1,
      uncertain: false,
    });
    expect(rig.session.getSnapshot().error).toMatch(/changed/);
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("clears and blocks when the final owner proof after a known rejection changes", async () => {
    const rig = fixture();
    await rig.session.refresh();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${otherId}`);
    const write = vi.fn(async () => {
      throw new CourseRequestError(privateText, 422, false);
    });
    await expect(rig.session.write(write)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      blocked: true,
      uncertain: false,
    });
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("retains unknown-write uncertainty until explicit checked reload and never retries the operation", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const write = vi.fn(async () => {
      throw new CourseRequestError(privateText, null, true);
    });
    await expect(rig.session.write(write)).rejects.toMatchObject({
      uncertain: true,
    });
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      uncertain: true,
      writePending: false,
    });
    await expect(rig.session.write(write)).rejects.toBeInstanceOf(
      CourseRequestError,
    );
    expect(write).toHaveBeenCalledTimes(1);
    expect(await rig.session.refresh()).toBe(true);
    expect(rig.session.getSnapshot().uncertain).toBe(false);
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("treats a write whose final owner proof changes as uncertain and blocks access", async () => {
    const rig = fixture();
    await rig.session.refresh();
    rig.api.account
      .mockResolvedValueOnce(accountId)
      .mockResolvedValueOnce(`${workspaceId}:${otherId}`);
    const write = vi.fn(async () => ({ saved: true }));
    await expect(rig.session.write(write)).rejects.toMatchObject({
      uncertain: true,
    });
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      blocked: true,
      uncertain: true,
    });
    expect(write).toHaveBeenCalledTimes(1);
  });

  it("keeps an already sent write uncertain after hide and refuses reads/refocus until it finishes", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const pending = deferred<{ saved: boolean }>();
    const write = vi.fn(() => pending.promise);
    const operation = rig.session.write(write).catch((error: unknown) => error);
    await microtasks();
    rig.session.suspend();
    const proofs = rig.api.proof.mock.calls.length;
    expect(await rig.session.refresh()).toBe(false);
    expect(rig.api.proof).toHaveBeenCalledTimes(proofs);
    expect(rig.session.getSnapshot()).toMatchObject({
      masked: true,
      uncertain: true,
      writePending: true,
    });
    pending.resolve({ saved: true });
    expect(await operation).toMatchObject({ uncertain: true });
    expect(rig.session.getSnapshot()).toMatchObject({
      masked: true,
      uncertain: true,
      writePending: false,
    });
    expect(write).toHaveBeenCalledTimes(1);
    expect(await rig.session.refresh()).toBe(true);
    expect(rig.session.getSnapshot().uncertain).toBe(false);
  });
});

describe("course privacy event boundaries", () => {
  function surfaces(
    focused = true,
    visibilityState: DocumentVisibilityState = "visible",
  ) {
    return {
      window: new EventTarget(),
      document: Object.assign(new EventTarget(), {
        visibilityState,
        hasFocus: () => focused,
      }),
      channel: Object.assign(new EventTarget(), { close: vi.fn() }),
    };
  }

  it("masks on picker-like blur and checks owner/source only after visible focus returns", () => {
    const surface = surfaces();
    const clear = vi.fn(),
      suspend = vi.fn(),
      refresh = vi.fn();
    const detach = observeCoursePrivacy(clear, suspend, refresh, surface);
    surface.window.dispatchEvent(new Event("blur"));
    surface.document.visibilityState = "hidden";
    surface.document.dispatchEvent(new Event("visibilitychange"));
    expect(suspend).toHaveBeenCalledTimes(2);
    expect(clear).not.toHaveBeenCalled();
    surface.document.visibilityState = "visible";
    surface.document.dispatchEvent(new Event("visibilitychange"));
    expect(refresh).not.toHaveBeenCalled();
    surface.window.dispatchEvent(new Event("focus"));
    expect(refresh).toHaveBeenCalledTimes(1);
    detach();
  });

  it.each([
    [false, "visible"],
    [true, "hidden"],
  ] as const)(
    "begins masked for focus=%s visibility=%s",
    (focused, visibility) => {
      const suspend = vi.fn();
      const detach = observeCoursePrivacy(
        vi.fn(),
        suspend,
        vi.fn(),
        surfaces(focused, visibility),
      );
      expect(suspend).toHaveBeenCalledTimes(1);
      detach();
    },
  );

  it("clears all drafts on pagehide, session events, and cleanup and detaches listeners", async () => {
    const rig = fixture();
    await rig.session.refresh();
    const surface = surfaces();
    const clear = vi.fn(() => rig.session.clear());
    const suspend = vi.fn(rig.session.suspend);
    const refresh = vi.fn();
    const detach = observeCoursePrivacy(clear, suspend, refresh, surface);
    const epoch = rig.session.getSnapshot().epoch;
    surface.window.dispatchEvent(new Event("pagehide"));
    surface.window.dispatchEvent(new Event("storage"));
    surface.channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(3);
    expect(rig.session.getSnapshot()).toMatchObject({
      proof: null,
      epoch: epoch + 3,
    });
    detach();
    expect(clear).toHaveBeenCalledTimes(4);
    expect(surface.channel.close).toHaveBeenCalledTimes(1);
    for (const event of ["pagehide", "storage", "blur", "focus"])
      surface.window.dispatchEvent(new Event(event));
    surface.document.dispatchEvent(new Event("visibilitychange"));
    surface.channel.dispatchEvent(new Event("message"));
    expect(clear).toHaveBeenCalledTimes(4);
    expect(suspend).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
  });
});
