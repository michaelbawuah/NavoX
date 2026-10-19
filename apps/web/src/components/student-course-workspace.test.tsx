import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  CourseMaterialDetail,
  CourseOverview,
} from "../lib/student-coursework";
import {
  CourseAccessView,
  CourseAssignmentList,
  CourseEmailPicker,
  CourseMaterialForm,
  CourseMaterialReader,
  CourseOverviewForm,
  CoursePolicyForm,
  CoursePolicySummary,
  CoursePrivateSurface,
  CourseSource,
  StudentCourseWorkspace,
} from "./student-course-workspace";

const courseId = "11111111-1111-4111-8111-111111111111",
  materialId = "22222222-2222-4222-8222-222222222222";
const overview: CourseOverview = {
  course: {
    id: courseId,
    code: "",
    name: "My course",
    term_start: "2026-09-01",
    term_end: "2026-12-31",
    professor: null,
    archived: false,
    revision: 1,
    meetings: [],
  },
  archived: false,
  revision: 0,
  staff: [],
  grade_breakdown: [],
  ai_policy: null,
  grade_weight_total: "0",
  policy_status: "unknown",
  default_help_mode: "tutor_outline_review",
  ai_permission_granted: false,
  notice: "Private course workspace",
};
const material: CourseMaterialDetail = {
  id: materialId,
  title: "Syllabus",
  kind: "syllabus",
  revision: 2,
  media_type: "application/pdf",
  byte_size: 2000,
  text_size: 100,
  page_count: 2,
  created_at: "2026-10-19T12:00:00Z",
  updated_at: "2026-10-19T13:00:00Z",
  pages: ["Outcomes", "Ask your instructor before using AI."],
};
const render = (value: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(value);
describe("private course workspace", () => {
  it("requires an explicit saved-email search before any association is offered", () => {
    const search = vi.fn(),
      link = vi.fn();
    const html = render(
      createElement(CourseEmailPicker, {
        courseName: "My course",
        disabled: false,
        onSearch: search,
        onLink: link,
      }),
    );
    expect(html).toContain("Find saved emails");
    expect(html).toContain("Messages without saved labels cannot appear");
    expect(html).not.toContain("Link reviewed email");
    expect(html).not.toContain("source_hash");
    expect(search).not.toHaveBeenCalled();
    expect(link).not.toHaveBeenCalled();
  });
  it("starts with truthful loading and does not fetch, upload, or invent a chat during render", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentCourseWorkspace, {
          accountId: "workspace:user",
          courseId,
          enabled: true,
        }),
      );
      expect(html).toContain("Reload saved course");
      expect(html).not.toContain("Private material");
      expect(html).not.toContain("Ask AI");
      expect(html).not.toContain("Sample");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("leaves unknown course policy unknown and defaults to tutoring", () => {
    const html = render(
      createElement(CoursePolicySummary, {
        overview,
        materials: [],
        onOpen: vi.fn(),
      }),
    );
    expect(html).toContain("No policy passage saved");
    expect(html).toContain("tutoring, outlining, and reviewing your own work");
    expect(html).not.toContain("AI use is allowed");
  });
  it("presents only the selected literal policy with source page and version", () => {
    const quote = {
      material_id: materialId,
      revision: 2,
      page: 2,
      quote: material.pages[1],
    };
    const html = render(
      createElement(CoursePolicySummary, {
        overview: {
          ...overview,
          ai_policy: quote,
          policy_status: "quoted_policy",
        },
        materials: [material],
        onOpen: vi.fn(),
      }),
    );
    expect(html).toContain(quote.quote);
    expect(html).toContain("page 2 · version 2");
    expect(html).toContain("does not automatically authorize AI use");
  });
  it("does not present an old citation as verified by a replacement file", () => {
    const html = render(
      createElement(CourseSource, {
        quote: {
          material_id: materialId,
          revision: 1,
          page: 2,
          quote: "Old policy",
        },
        materials: [material],
        onOpen: vi.fn(),
      }),
    );
    expect(html).toContain("source changed or is no longer available");
    expect(html).not.toContain("<button");
  });
  it("keeps extracted source text escaped, page-specific and linked to the original", () => {
    const html = render(
      createElement(CourseMaterialReader, {
        courseId,
        material: {
          ...material,
          pages: [material.pages[0], "<script>not executable</script>"],
        },
        page: 2,
        onPage: vi.fn(),
      }),
    );
    expect(html).toContain("&lt;script&gt;not executable&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("version 2, page 2");
    expect(html).toContain(`/workspace/materials/${materialId}/file`);
    expect(html).toContain(`id="course-source-${materialId}-page-2"`);
    expect(html).not.toContain(material.pages[0]);
  });
  it("requires a saved syllabus choice before policy review can be saved", () => {
    const html = render(
      createElement(CoursePolicyForm, {
        overview,
        materials: [material],
        disabled: false,
        loadMaterial: vi.fn(),
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Choose a syllabus");
    expect(html).toMatch(
      /<button[^>]*type="submit"[^>]*disabled=""[^>]*>Save reviewed policy passage<\/button>/,
    );
  });
  it("keeps file selection separate from the explicit save", () => {
    const html = render(
      createElement(CourseMaterialForm, {
        kind: "syllabus",
        disabled: false,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Selecting a file does not save it");
    expect(html).toContain("Image-only PDFs need a text version");
    expect(html).toContain("5 MiB and 100 pages");
    expect(html).toContain("Save to this course");
    expect(html).not.toContain("Automatically uploaded");
  });
  it("preserves note content for explicit editing and disables archived edits", () => {
    const html = render(
      createElement(CourseMaterialForm, {
        kind: "note",
        material: {
          ...material,
          kind: "note",
          page_count: 1,
          pages: ["Private note\nMore detail"],
        },
        disabled: true,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(html).toContain("Private note\nMore detail");
    expect(html).toContain('<fieldset disabled="">');
    expect(html).toContain("Save replacement");
    const facts = render(
      createElement(CourseOverviewForm, {
        overview,
        disabled: true,
        onSave: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(facts).toMatch(/<button[^>]*type="submit"[^>]*disabled=""/);
  });
  it("keeps school calendar entries separate from actual study task copies", () => {
    const html = render(
      createElement(CourseAssignmentList, {
        value: {
          tasks: [],
          feed_items: [
            {
              id: materialId,
              revision: 2,
              title: "Exam",
              kind: "exam",
              due_at: null,
              cancelled: true,
              conflicted: true,
              source: "confirmed_calendar_feed",
            },
          ],
          next_offset: null,
        },
      }),
    );
    expect(html).toContain("No study tasks saved");
    expect(html).toContain("Cancelled at source");
    expect(html).toContain("Conflicting sources");
    expect(html).toContain("No deadline supplied");
    expect(html).not.toContain("Task created");
  });
});

describe("course source access boundary", () => {
  const proof = {
    overview,
    materials: [material],
    assignments: { tasks: [], feed_items: [], next_offset: null },
    emails: { links: [], unavailable_count: 0 },
  };
  const state = {
    proof,
    masked: false,
    checking: false,
    blocked: false,
    uncertain: false,
    writePending: false,
    error: "",
    notice: "",
    epoch: 0,
    readEpoch: 0,
  };
  it("removes previously rendered course text and snippets when source proof is cleared", () => {
    const child = createElement(
      "div",
      null,
      "Private extracted body and old search snippet",
    );
    const before = render(
      createElement(
        CourseAccessView,
        { state, onClear: vi.fn(), onReload: vi.fn() },
        child,
      ),
    );
    expect(before).toContain("Private extracted body");
    const after = render(
      createElement(
        CourseAccessView,
        {
          state: {
            ...state,
            proof: null,
            error: "This saved source is no longer available.",
          },
          onClear: vi.fn(),
          onReload: vi.fn(),
        },
        child,
      ),
    );
    expect(after).not.toContain("Private extracted body");
    expect(after).not.toContain("old search snippet");
    expect(after).toContain("Reload saved course");
  });
  it("masks and inerts the same editable subtree for a file-picker-like blur", () => {
    const child = createElement(CourseMaterialForm, {
      kind: "material",
      disabled: false,
      onSave: vi.fn(),
      onClose: vi.fn(),
    });
    const html = render(
      createElement(
        CourseAccessView,
        {
          state: { ...state, masked: true },
          onClear: vi.fn(),
          onReload: vi.fn(),
        },
        child,
      ),
    );
    expect(html).toMatch(/<div[^>]*hidden=""[^>]*inert=""/);
    expect(html).toContain('type="file"');
    expect(html).toContain("Check current course");
    const active = render(
      createElement(CoursePrivateSurface, { masked: false }, child),
    );
    expect(active).not.toContain('hidden=""');
    expect(active).toContain('type="file"');
  });
  it("cannot load private sources when the coursework feature is disabled", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentCourseWorkspace, {
          accountId: "workspace:user",
          courseId,
          enabled: false,
        }),
      );
      expect(html).toContain("not available here yet");
      expect(html).not.toContain('type="file"');
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("keeps uncertain writes explicit and disables reopening while a write is pending", () => {
    const html = render(
      createElement(CourseAccessView, {
        state: { ...state, proof: null, uncertain: true, writePending: true },
        onClear: vi.fn(),
        onReload: vi.fn(),
      }),
    );
    expect(html).toContain("may have saved");
    expect(html).toMatch(/<button[^>]*disabled=""/);
  });
});
