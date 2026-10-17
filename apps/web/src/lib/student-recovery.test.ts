import { afterEach, describe, expect, it, vi } from "vitest";
import {
  RecoveryClipboardError,
  type RecoveryView,
  readRecoveryExtension,
  readRecoveryView,
  recoveryAccount,
  recoveryCopyExtension,
  recoveryErasedHandles,
  recoveryExtensionText,
  recoveryReceipt,
  recoveryRecipientSource,
  recoveryRequest,
  recoveryReviewMilliseconds,
  recoveryTarget,
  recoveryUnfittedReason,
} from "./student-recovery";

const courseId = "11111111-1111-4111-8111-111111111111",
  taskId = "22222222-2222-4222-8222-222222222222",
  workId = "33333333-3333-5333-8333-333333333333",
  previewId = "44444444-4444-4444-8444-444444444444",
  extensionId = "55555555-5555-4555-8555-555555555555",
  otherId = "66666666-6666-4666-8666-666666666666";
const work = {
  id: workId,
  task_id: taskId,
  material_id: null,
  course_id: courseId,
  title: "Finish my lab",
  remaining_minutes: 60,
  priority: 4,
  official_due_at: "2026-10-18T23:59:00Z",
  deadline_source: "school_assignment" as const,
  official_deadline_status: "overdue" as const,
  personal_catchup_target: "2026-10-21T20:00:00Z",
  source_revision: 2,
  deadline_notice:
    "A personal target does not change the official deadline or grant an extension.",
};
const block = {
  task_id: workId,
  title: work.title,
  start_at: "2026-10-19T12:05:00Z",
  end_at: "2026-10-19T13:05:00Z",
  minutes: 60,
  focus_window: true,
};
const preview: RecoveryView = {
  preview_id: previewId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T12:20:00Z",
  server_now: "2026-10-19T12:00:00Z",
  timezone: "America/New_York",
  trigger: {
    kind: "overdue_task",
    course_ids: [],
    from_day: null,
    through_day: null,
    task_id: taskId,
    task_revision: 2,
  },
  evidence: [
    {
      kind: "saved_overdue_task",
      task_id: taskId,
      revision: 2,
      saved_due_at: work.official_due_at,
      remaining_minutes: 60,
    },
  ],
  available_materials: [],
  policies: [],
  work: [work],
  result: {
    start_at: "2026-10-19T12:05:00Z",
    end_at: "2026-10-23T04:00:00Z",
    blocks: [block],
    unfitted_work: [],
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
  },
  unknowns: ["No extension policy was selected."],
  proposal_only: true,
  requires_student_review: true,
  official_deadlines_changed: false,
  result_deadline_basis: "personal_catchup_target",
};
const extension = {
  preview_id: extensionId,
  preview_hash: "b".repeat(64),
  expires_at: preview.expires_at,
  timezone: preview.timezone,
  recipient: "prof@example.edu",
  recipient_name: "Professor",
  recipient_source: null,
  subject: "Extension request: Finish my lab",
  body: "My own words stay here.\nNo documentation is invented.",
  student_reported_reason: "  I missed class.\n\tHere are my own words.  ",
  official_due_at: work.official_due_at,
  deadline_source: "school_assignment",
  requested_extension_until: work.personal_catchup_target,
  policy: null,
  notices: ["No message or external draft was created."],
  editable_preview_only: true,
  sent: false,
  external_draft_created: false,
};
afterEach(() => vi.restoreAllMocks());
describe("source-grounded recovery previews", () => {
  it("keeps a passed school deadline overdue beside a later personal target", () => {
    const value = readRecoveryView(preview);
    expect(value.work[0].official_deadline_status).toBe("overdue");
    expect(value.work[0].official_due_at).toBe(work.official_due_at);
    expect(value.work[0].personal_catchup_target).toBe(
      work.personal_catchup_target,
    );
    expect(value.result_deadline_basis).toBe("personal_catchup_target");
  });
  it("distinguishes student-recorded deadlines from school sources", () => {
    expect(
      readRecoveryView({
        ...preview,
        work: [{ ...work, deadline_source: "student_recorded" }],
      }).work[0].deadline_source,
    ).toBe("student_recorded");
    expect(() =>
      readRecoveryView({
        ...preview,
        work: [{ ...work, official_deadline_status: "upcoming" }],
      }),
    ).toThrow();
  });
  it("accepts unknown material deadlines without fabricating official dates", () => {
    const value = readRecoveryView({
      ...preview,
      work: [
        {
          ...work,
          task_id: null,
          material_id: taskId,
          official_due_at: null,
          deadline_source: "not_recorded",
          official_deadline_status: "not_recorded",
        },
      ],
    });
    expect(value.work[0].official_due_at).toBeNull();
    expect(() =>
      readRecoveryView({
        ...preview,
        work: [{ ...work, official_due_at: null }],
      }),
    ).toThrow();
  });
  it("does not accept a proposal claiming to have changed deadlines or skipped review", () => {
    for (const change of [
      { official_deadlines_changed: true },
      { requires_student_review: false },
      { proposal_only: false },
      { result_deadline_basis: "official_due_at" },
    ])
      expect(() => readRecoveryView({ ...preview, ...change })).toThrow();
  });
  it("requires an authoritative valid saved time zone", () => {
    expect(readRecoveryView(preview).timezone).toBe("America/New_York");
    expect(() =>
      readRecoveryView({ ...preview, timezone: "Made/Up" }),
    ).toThrow();
  });
  it("rejects blocks or unfitted work disconnected from the reviewed source set", () => {
    expect(() =>
      readRecoveryView({
        ...preview,
        result: { ...preview.result, blocks: [{ ...block, task_id: otherId }] },
      }),
    ).toThrow();
    expect(() =>
      readRecoveryView({
        ...preview,
        result: {
          ...preview.result,
          unfitted_work: [
            {
              task_id: otherId,
              title: "Unknown",
              minutes: 60,
              due_at: work.personal_catchup_target,
              reason: "insufficient_time",
            },
          ],
        },
      }),
    ).toThrow();
  });
  it("refuses a claimed verified absence or an invented missed-material classification", () => {
    expect(() =>
      readRecoveryView({
        ...preview,
        evidence: [
          {
            kind: "student_reported_absence",
            course_id: courseId,
            scheduled_start_at: block.start_at,
            scheduled_end_at: block.end_at,
            attendance_verified: true,
          },
        ],
      }),
    ).toThrow();
    expect(() =>
      readRecoveryView({
        ...preview,
        available_materials: [
          {
            id: taskId,
            course_id: courseId,
            revision: 1,
            title: "Slides",
            kind: "material",
            claim: "verified_missed_lecture",
          },
        ],
      }),
    ).toThrow();
  });
  it("expires local presentation at the next server-derived review boundary", () => {
    expect(recoveryReviewMilliseconds(preview)).toBe(5 * 60 * 1000);
    expect(
      recoveryReviewMilliseconds({
        ...preview,
        result: { ...preview.result, blocks: [] },
      }),
    ).toBe(20 * 60 * 1000);
    expect(
      recoveryReviewMilliseconds({
        ...preview,
        work: [
          {
            ...work,
            official_due_at: "2026-10-19T12:02:00Z",
            official_deadline_status: "upcoming",
          },
        ],
      }),
    ).toBe(2 * 60 * 1000);
  });
  it("rejects expired previews relative to the response clock, independent of the device clock", () => {
    expect(() =>
      readRecoveryView({ ...preview, expires_at: preview.server_now }),
    ).toThrow();
    expect(() =>
      readRecoveryView({
        ...preview,
        work: [{ ...work, personal_catchup_target: preview.server_now }],
      }),
    ).toThrow();
  });
  it("labels a fitting failure against the personal target rather than implying a changed school deadline", () => {
    expect(recoveryUnfittedReason("cannot_meet_deadline")).toContain(
      "personal catch-up target",
    );
  });
});
describe("exact private extension drafts", () => {
  it("attaches recipient provenance only for the complete matching mailbox", () => {
    const source = {
      material_id: taskId,
      revision: 1,
      page: 1,
      quote: "Course contact: <prof@EXAMPLE.EDU>.",
    };
    expect(recoveryRecipientSource("prof@example.edu", source)).toEqual(source);
    for (const quote of [
      "assistant-prof@example.edu",
      "prof@example.edu.evil",
      "Prof@example.edu",
      "other@example.edu",
    ]) {
      expect(
        recoveryRecipientSource("prof@example.edu", { ...source, quote }),
      ).toBeNull();
    }
    expect(recoveryRecipientSource("prof@example.edu", null)).toBeNull();
  });
  it("separates clipboard refusal from stale-source errors without changing the edited text", async () => {
    const edited = {
      recipient: "ta@example.edu",
      subject: "Edited request",
      body: "My exact edited reason.\nMy own closing.",
    };
    const write = vi
      .fn()
      .mockRejectedValue(new DOMException("Not allowed", "NotAllowedError"));
    await expect(recoveryCopyExtension(edited, write)).rejects.toBeInstanceOf(
      RecoveryClipboardError,
    );
    expect(write).toHaveBeenCalledWith(recoveryExtensionText(edited));
    expect(edited.body).toBe("My exact edited reason.\nMy own closing.");
    await expect(
      recoveryCopyExtension(edited, async () => {
        throw new TypeError("Clipboard not supported");
      }),
    ).rejects.toThrow("Your edits are still here");
  });
  it("preserves the exact student reason and remains a draft", () => {
    const value = readRecoveryExtension(extension);
    expect(value.student_reported_reason).toBe(
      extension.student_reported_reason,
    );
    expect(value.sent).toBe(false);
    expect(value.external_draft_created).toBe(false);
    expect(() => readRecoveryExtension({ ...extension, sent: true })).toThrow();
    expect(() =>
      readRecoveryExtension({ ...extension, external_draft_created: true }),
    ).toThrow();
  });
  it("copies only the user-edited recipient, subject, and body", () => {
    expect(
      recoveryExtensionText({
        recipient: "ta@example.edu",
        subject: "My subject",
        body: "My edited reason.\nMy own closing.",
      }),
    ).toBe(
      "To: ta@example.edu\nSubject: My subject\n\nMy edited reason.\nMy own closing.",
    );
  });
  it("retains only opaque erase metadata and erases a parent's child handles without losing unrelated ones", () => {
    const metadata = recoveryReceipt(preview);
    expect(Object.keys(metadata).sort()).toEqual([
      "preview_hash",
      "preview_id",
    ]);
    const parent = { ...metadata, kind: "recovery" as const, parent_id: null },
      child = {
        ...recoveryReceipt(extension),
        kind: "extension" as const,
        parent_id: previewId,
      },
      other = {
        preview_id: otherId,
        preview_hash: "c".repeat(64),
        kind: "recovery" as const,
        parent_id: null,
      };
    expect(recoveryErasedHandles([parent, child, other], previewId)).toEqual([
      other,
    ]);
    expect(recoveryErasedHandles([parent, child, other], extensionId)).toEqual([
      parent,
      other,
    ]);
  });
});
describe("personal targets in the saved zone", () => {
  it("does not guess through skipped or repeated DST clock times", () => {
    expect(() =>
      recoveryTarget("2027-03-14T02:30", "America/New_York", ""),
    ).toThrow("skipped");
    expect(() =>
      recoveryTarget("2026-11-01T01:30", "America/New_York", ""),
    ).toThrow("twice");
    expect(
      recoveryTarget(
        "2026-11-01T01:30",
        "America/New_York",
        "2026-11-01T06:30:00.000Z",
      ),
    ).toBe("2026-11-01T06:30:00.000Z");
  });
});
describe("bounded explicit recovery requests", () => {
  it("reads the active account scope without browser storage", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        Response.json({ id: taskId, workspace: { id: courseId } }),
      );
    expect(await recoveryAccount()).toBe(`${courseId}:${taskId}`);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/me"),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      }),
    );
  });
  it("keeps malformed or unsigned accounts from authorizing a private preview", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ id: "missing", workspace: { id: courseId } }),
    );
    await expect(recoveryAccount()).rejects.toThrow(
      "account could not be checked",
    );
  });
  it("only reads a preview when asked and keeps stale/erased responses distinguishable", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        Response.json(
          { detail: "Your recovery sources changed." },
          { status: 409 },
        ),
      );
    await expect(
      recoveryRequest(`/previews/${previewId}`, readRecoveryView),
    ).rejects.toMatchObject({ status: 409, uncertain: false });
    expect(fetch).toHaveBeenCalledTimes(1);
    fetch.mockResolvedValue(
      Response.json({ detail: "This preview was erased." }, { status: 410 }),
    );
    await expect(
      recoveryRequest(`/previews/${previewId}`, readRecoveryView),
    ).rejects.toMatchObject({ status: 410, uncertain: false });
  });
  it("marks lost creation responses unknown without retrying automatically", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("Lost response"));
    const body = { request_id: previewId, student_reason: "Private reason" };
    await expect(
      recoveryRequest("/preview", readRecoveryView, "POST", body),
    ).rejects.toMatchObject({ uncertain: true, status: null });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/recovery/preview"),
      expect.objectContaining({
        body: JSON.stringify(body),
        credentials: "include",
        redirect: "error",
      }),
    );
    expect(String(fetch.mock.calls[0][0])).not.toContain("Private reason");
  });
  it("treats an unreadable saved draft response as unknown rather than claiming a valid draft", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ ...extension, sent: true }),
    );
    await expect(
      recoveryRequest(
        `/previews/${previewId}/extension-draft`,
        readRecoveryExtension,
        "POST",
        {},
      ),
    ).rejects.toMatchObject({ uncertain: true });
  });
  it("can erase with only the saved hash, never replaying private reasons", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(null, { status: 204 }));
    await recoveryRequest(
      `/previews/${previewId}`,
      (value) => value,
      "DELETE",
      { preview_hash: preview.preview_hash },
    );
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(`/previews/${previewId}`),
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ preview_hash: preview.preview_hash }),
      }),
    );
  });
});
