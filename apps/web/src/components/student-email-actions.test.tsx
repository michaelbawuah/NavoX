import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { StudentEmailReview } from "../lib/student-email";
import {
  StudentEmailActions,
  StudentEmailCard,
  StudentEmailCorrectionEditor,
  StudentEmailEvidence,
  StudentEmailTaskActions,
} from "./student-email-actions";

const id = "11111111-1111-4111-8111-111111111111",
  sourceId = "22222222-2222-4222-8222-222222222222",
  taskId = "33333333-3333-4333-8333-333333333333";
const review: StudentEmailReview = {
  review_id: id,
  revision: 1,
  source_id: sourceId,
  source_hash: "a".repeat(64),
  expires_at: "2026-10-17T12:20:00Z",
  classification: {
    category: "has_deadline",
    reasons: ["An explicit timestamp appears in the message."],
    evidence: [
      {
        source: "content",
        quote: "Submit <script>alert('x')</script>",
        start: 0,
        end: 34,
      },
    ],
    proposed_deadline: null,
    ambiguities: ["Friday has no exact date or time zone."],
  },
  correction: {
    category: "has_deadline",
    not_important: false,
    course_id: null,
    title: null,
    due_at: null,
    estimated_minutes: null,
    confirmed_course: false,
    confirmed_deadline: false,
  },
  content_truncated: false,
  reply_handoff: null,
  actions_executed: false,
  task_id: null,
  existing_task: null,
  notice: "Review required.",
};
const render = (value: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(value);
describe("explicit selected-email review", () => {
  it("renders a useful entry and starts with no source or provider read", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      expect(render(createElement(StudentEmailCard))).toContain(
        'href="/email-actions"',
      );
      const html = render(
        createElement(StudentEmailActions, { accountId: `${id}:${sourceId}` }),
      );
      expect(html).toContain("Search saved email labels");
      expect(html).toContain("Clear private text");
      expect(html).not.toContain("Analyze selected message");
      expect(html).not.toContain('checked=""');
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("escapes exact evidence and leaves ambiguous deadlines unresolved", () => {
    const html = render(
      createElement(StudentEmailEvidence, { review, timezone: "UTC" }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("Friday has no exact date or time zone");
    expect(html).toContain("No unambiguous deadline was extracted");
    expect(html).toContain("does not verify the sender");
    expect(html).toContain("characters 1–34");
  });
  it("does not imply a truncated email established its deadline", () => {
    const html = render(
      createElement(StudentEmailEvidence, {
        review: { ...review, content_truncated: true },
        timezone: "UTC",
      }),
    );
    expect(html).toContain("Only part of this message was reviewed");
    expect(html).toContain("deadline has not been inferred");
  });
  it("requires an explicit course and deadline review rather than defaulting to a fake course or due date", () => {
    const html = render(
      createElement(StudentEmailCorrectionEditor, {
        review,
        courses: [],
        timezone: "America/New_York",
        disabled: false,
        onSave: vi.fn(),
        onNotImportant: vi.fn(),
      }),
    );
    expect(html).toContain("Choose after reviewing the email");
    expect(html).toContain("Personal / no course");
    expect(html).toContain("Review the deadline");
    expect(html).toContain("Save without a deadline");
    expect(html).toContain("Corrections affect this review only");
    expect(html).toContain("does not train a mail filter");
    expect(html).not.toContain('checked=""');
    expect(html).not.toContain("CS101");
  });
  it("preserves an exact saved deadline including seconds until the student edits it", () => {
    const html = render(
      createElement(StudentEmailCorrectionEditor, {
        review: {
          ...review,
          correction: { ...review.correction, due_at: "2026-10-17T15:12:37Z" },
        },
        courses: [],
        timezone: "UTC",
        disabled: false,
        onSave: vi.fn(),
        onNotImportant: vi.fn(),
      }),
    );
    expect(html).toContain("2026-10-17T15:12:37Z");
    expect(html).toContain("preserved unless you edit");
  });
  it("does not offer invisible task creation when Planner is disabled", () => {
    const html = render(
      createElement(StudentEmailTaskActions, {
        review,
        timezone: "UTC",
        disabled: false,
        planningEnabled: false,
        accepted: false,
        onAccept: vi.fn(),
        onCheck: vi.fn(),
      }),
    );
    expect(html).toContain("Saving a study task is not enabled");
    expect(html).not.toContain("Create reviewed task");
    expect(html).not.toContain('href="/planner"');
  });
  it("refreshes the exact linked active task while explaining preserved recorded work", () => {
    const html = render(
      createElement(StudentEmailTaskActions, {
        review: {
          ...review,
          existing_task: {
            id: taskId,
            revision: 2,
            title: "My existing lab task",
            course_id: null,
            status: "active",
            remaining_minutes: 60,
            actual_minutes: 35,
          },
        },
        timezone: "UTC",
        disabled: false,
        planningEnabled: true,
        accepted: false,
        onAccept: vi.fn(),
        onCheck: vi.fn(),
      }),
    );
    expect(html).toContain("My existing lab task");
    expect(html).toContain("35 minutes already recorded");
    expect(html).toContain("Refresh linked task");
    expect(html).not.toContain("Create reviewed task");
    expect(html).toContain("keeps its recorded work");
  });
  it.each(["completed", "archived"] as const)(
    "does not create duplicates for a %s linked task",
    (status) => {
      const html = render(
        createElement(StudentEmailTaskActions, {
          review: {
            ...review,
            existing_task: {
              id: taskId,
              revision: 2,
              title: "Linked task",
              course_id: null,
              status,
              remaining_minutes: 0,
              actual_minutes: 60,
            },
          },
          timezone: "UTC",
          disabled: false,
          planningEnabled: true,
          accepted: false,
          onAccept: vi.fn(),
          onCheck: vi.fn(),
        }),
      );
      expect(html).toContain(`This linked task is ${status}`);
      expect(html).not.toContain("Create reviewed task");
      expect(html).not.toContain(">Refresh linked task</button>");
    },
  );
  it("hands off only to the existing Inbox flow, with no send button", () => {
    const html = render(
      createElement(StudentEmailTaskActions, {
        review: {
          ...review,
          reply_handoff: {
            commitment_id: taskId,
            path: `/inbox?reply=${taskId}#drafts`,
            notice: "Review your existing draft in Inbox.",
          },
        },
        timezone: "UTC",
        disabled: false,
        planningEnabled: true,
        accepted: false,
        onAccept: vi.fn(),
        onCheck: vi.fn(),
      }),
    );
    expect(html).toContain(`href="/inbox?reply=${taskId}#drafts"`);
    expect(html).toContain("Review the recipient and exact draft there");
    expect(html).not.toMatch(/<button[^>]*>Send/);
    expect(html).not.toContain("mailto:");
  });
  it("does not offer another task write after an unavailable acceptance receipt", () => {
    const html = render(
      createElement(StudentEmailTaskActions, {
        review,
        timezone: "UTC",
        disabled: false,
        planningEnabled: true,
        accepted: false,
        taskUnavailable: true,
        onAccept: vi.fn(),
        onCheck: vi.fn(),
      }),
    );
    expect(html).toContain("That accepted task is no longer available");
    expect(html).not.toContain("Create reviewed task");
    expect(html).toContain("Check saved review");
  });
  it("does not assert that a task from an older acceptance still exists", () => {
    const html = render(
      createElement(StudentEmailTaskActions, {
        review: { ...review, task_id: taskId },
        timezone: "UTC",
        disabled: false,
        planningEnabled: true,
        accepted: false,
        onAccept: vi.fn(),
        onCheck: vi.fn(),
      }),
    );
    expect(html).toContain("Task previously accepted");
    expect(html).toContain("check its current availability");
    expect(html).not.toContain("The accepted task is in Planner");
  });
});
