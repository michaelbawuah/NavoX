import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  RecoveryExtension,
  RecoveryView,
  RecoveryWork,
} from "../lib/student-recovery";
import {
  RecoveryDeadlineCard,
  RecoveryEvidenceList,
  RecoveryExtensionEditor,
  RecoveryExtensionForm,
  RecoveryPolicyPicker,
  RecoveryProposal,
  RecoveryTargetField,
  StudentRecovery,
  StudentRecoveryCard,
} from "./student-recovery";

const courseId = "11111111-1111-4111-8111-111111111111",
  taskId = "22222222-2222-4222-8222-222222222222",
  workId = "33333333-3333-5333-8333-333333333333",
  previewId = "44444444-4444-4444-8444-444444444444",
  extensionId = "55555555-5555-4555-8555-555555555555";
const zone = "America/New_York";
const work: RecoveryWork = {
  id: workId,
  task_id: taskId,
  material_id: null,
  course_id: courseId,
  title: "Finish my lab",
  remaining_minutes: 90,
  priority: 4,
  official_due_at: "2026-10-18T23:59:00Z",
  deadline_source: "school_assignment",
  official_deadline_status: "overdue",
  personal_catchup_target: "2026-10-21T20:00:00Z",
  source_revision: 2,
  deadline_notice:
    "A personal catch-up target does not change the official deadline or grant an extension.",
};
const preview: RecoveryView = {
  preview_id: previewId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T12:20:00Z",
  server_now: "2026-10-19T12:00:00Z",
  timezone: zone,
  trigger: {
    kind: "reported_absence",
    course_ids: [courseId],
    from_day: "2026-10-15",
    through_day: "2026-10-16",
    task_id: null,
    task_revision: null,
  },
  evidence: [],
  available_materials: [],
  policies: [],
  work: [work],
  result: {
    start_at: "2026-10-19T12:05:00Z",
    end_at: "2026-10-23T04:00:00Z",
    blocks: [],
    unfitted_work: [
      {
        task_id: workId,
        title: work.title,
        minutes: 90,
        due_at: work.personal_catchup_target,
        reason: "cannot_meet_deadline",
      },
    ],
    capacities: [],
    warnings: [
      "Existing planned study blocks were kept busy; this proposal does not replace them.",
    ],
    planning_only: true,
  },
  unknowns: [
    "Attendance is student-reported.",
    "Saved material titles do not establish which lecture topics you missed.",
  ],
  proposal_only: true,
  requires_student_review: true,
  official_deadlines_changed: false,
  result_deadline_basis: "personal_catchup_target",
};
const draft: RecoveryExtension = {
  preview_id: extensionId,
  preview_hash: "b".repeat(64),
  expires_at: preview.expires_at,
  timezone: zone,
  recipient: "prof@example.edu",
  recipient_name: "Professor",
  recipient_source: null,
  subject: "Extension request: Finish my lab",
  body: "Hello Professor,\n\nMy reason: I missed class.\nNo documentation is invented.",
  student_reported_reason: "I missed class.",
  official_due_at: work.official_due_at ?? "",
  deadline_source: "school_assignment",
  requested_extension_until: work.personal_catchup_target,
  policy: null,
  notices: ["No message or external draft has been created."],
  editable_preview_only: true,
  sent: false,
  external_draft_created: false,
};
const render = (value: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(value);
describe("truthful catch-up recovery screens", () => {
  it("offers an explicit catch-up entry without predicting an absence or claiming work was recovered", () => {
    const html = render(createElement(StudentRecoveryCard));
    expect(html).toContain('href="/recovery"');
    expect(html).toContain("Review a catch-up proposal");
    expect(html).not.toContain("You missed class");
    expect(html).not.toContain("Recovery completed");
  });
  it("starts with readonly loading and does not fetch or create a recovery preview during render", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentRecovery, { accountId: `${courseId}:${taskId}` }),
      );
      expect(html).toContain("Opening your saved sources");
      expect(html).toContain("One step at a time");
      expect(html).not.toContain("We verified your absence");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("keeps school deadlines and personal targets separate with overdue work still overdue", () => {
    const html = render(
      createElement(RecoveryDeadlineCard, { work, timezone: zone }),
    );
    expect(html).toContain("School deadline");
    expect(html).toContain("Your personal catch-up target");
    expect(html).toContain("Still overdue");
    expect(html).toContain(
      "does not change the official deadline or grant an extension",
    );
  });
  it("does not label a manually recorded due date as a school-verified deadline", () => {
    const html = render(
      createElement(RecoveryDeadlineCard, {
        work: { ...work, deadline_source: "student_recorded" },
        timezone: zone,
      }),
    );
    expect(html).toContain("Deadline you recorded");
    expect(html).toContain("Verify this date with your school");
    expect(html).not.toContain("School deadline");
  });
  it("leaves material review deadlines unknown instead of inventing a due date", () => {
    const html = render(
      createElement(RecoveryDeadlineCard, {
        work: {
          ...work,
          task_id: null,
          material_id: taskId,
          official_due_at: null,
          deadline_source: "not_recorded",
          official_deadline_status: "not_recorded",
        },
        timezone: zone,
      }),
    );
    expect(html).toContain("Official deadline unknown");
    expect(html).toContain("No deadline source saved");
  });
  it("shows unfitted minutes, respects existing study time, and makes no fake acceptance or send available", () => {
    const html = render(
      createElement(RecoveryProposal, {
        preview,
        timezone: zone,
        courses: [],
        tasks: [],
        disabled: false,
        onExtension: vi.fn(),
        onCheck: vi.fn(),
        onForget: vi.fn(),
      }),
    );
    expect(html).toContain("1h 30m not placed");
    expect(html).toContain("personal catch-up target");
    expect(html).toContain("Existing planned study blocks were kept busy");
    expect(html).toContain("This hasn’t changed your deadlines");
    expect(html).toContain("No policy passage selected");
    expect(html).not.toMatch(/<button[^>]*>Accept/);
    expect(html).not.toMatch(/<button[^>]*>Send/);
    expect(html).not.toContain("The missed quiz will be dropped");
  });
  it("labels reported missed classes as unverified attendance evidence", () => {
    const html = render(
      createElement(RecoveryEvidenceList, {
        evidence: [
          {
            kind: "student_reported_absence",
            course_id: courseId,
            scheduled_start_at: "2026-10-16T14:00:00Z",
            scheduled_end_at: "2026-10-16T15:00:00Z",
            attendance_verified: false,
          },
        ],
        courses: [],
        tasks: [],
        timezone: zone,
      }),
    );
    expect(html).toContain("You reported an absence");
    expect(html).toContain("Attendance has not been verified");
  });
  it("offers both DST occurrences for a personal target without picking one silently", () => {
    const html = render(
      createElement(RecoveryTargetField, {
        label: "My target",
        value: "2026-11-01T01:30",
        choice: "",
        timezone: zone,
        disabled: false,
        onChange: vi.fn(),
        onChoice: vi.fn(),
      }),
    );
    expect(html).toContain("This clock time occurs twice");
    expect(html).toContain("Earlier");
    expect(html).toContain("Later");
    expect(html).toContain("Choose which occurrence");
  });
  it("requires exact saved syllabus material before adding a policy passage", () => {
    const html = render(
      createElement(RecoveryPolicyPicker, {
        courses: [],
        disabled: false,
        onMaterials: vi.fn(),
        onMaterial: vi.fn(),
        onAdd: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Choose a recovery course");
    expect(html).toContain("Choose a syllabus");
    expect(html).toContain("does not infer an extension");
    expect(html).toMatch(
      /<button[^>]*type="submit"[^>]*disabled=""[^>]*>Add reviewed policy passage<\/button>/,
    );
  });
});
describe("reviewable extension requests", () => {
  it("starts with an empty personal reason and requires an unchecked recipient confirmation", () => {
    const html = render(
      createElement(RecoveryExtensionForm, {
        preview,
        work,
        timezone: zone,
        disabled: false,
        contacts: [],
        onSubmit: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Your reason, in your own words");
    expect(html).toContain(
      "No illness, documentation, or instructor approval is invented",
    );
    expect(html).not.toContain('checked=""');
    expect(html).toMatch(/<textarea[^>]*><\/textarea>/);
    expect(html).toMatch(
      /<button[^>]*type="submit"[^>]*disabled=""[^>]*>Prepare editable extension draft<\/button>/,
    );
  });
  it("keeps recipient and body editable with explicit copy and no outbound action", () => {
    const html = render(
      createElement(RecoveryExtensionEditor, {
        value: draft,
        timezone: zone,
        disabled: false,
        onCopy: vi.fn(),
        onForget: vi.fn(),
      }),
    );
    expect(html).toContain('value="prof@example.edu"');
    expect(html).toContain("My reason: I missed class.");
    expect(html).toContain("Check sources &amp; copy edited draft");
    expect(html).toContain("The exact reason you supplied");
    expect(html).not.toMatch(/<button[^>]*>Send/);
    expect(html).not.toContain("mailto:");
  });
  it("escapes draft text and maintains the provenance of student-recorded deadlines", () => {
    const html = render(
      createElement(RecoveryExtensionEditor, {
        value: {
          ...draft,
          body: "<script>private reason</script>",
          deadline_source: "student_recorded",
        },
        timezone: zone,
        disabled: true,
        onCopy: vi.fn(),
        onForget: vi.fn(),
      }),
    );
    expect(html).toContain("&lt;script&gt;private reason&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("Deadline recorded by you");
    expect(html).toContain('<fieldset disabled="">');
  });
});
