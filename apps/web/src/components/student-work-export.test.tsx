import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  blankWorkExportDraft,
  type WorkExportContext,
} from "../lib/student-work-export";
import {
  StudentWorkExport,
  StudentWorkExportEntry,
  WorkExportFields,
  WorkExportPreview,
  WorkExportPrivateSurface,
} from "./student-work-export";

const owner =
    "11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222",
  course = "33333333-3333-4333-8333-333333333333";
const context: WorkExportContext = {
  course_revision: 2,
  generation: 1,
  course_label: "My saved course",
  max_text_chars: 20000,
  max_pages_including_title: 20,
  max_pdf_bytes: 2097152,
  saved: false,
};
const render = (element: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(element);
describe("reviewed own-work PDF screens", () => {
  it("keeps the course entry absent when disabled or archived, with no reads", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      expect(
        render(
          createElement(StudentWorkExportEntry, {
            accountId: owner,
            courseId: course,
          }),
        ),
      ).toBe("");
      expect(
        render(
          createElement(StudentWorkExportEntry, {
            accountId: owner,
            courseId: course,
            enabled: true,
            archived: true,
          }),
        ),
      ).toBe("");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("shows an explicit course action without mounting an editor or fetching context", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentWorkExportEntry, {
          accountId: owner,
          courseId: course,
          enabled: true,
        }),
      );
      expect(html).toContain("Format my own work");
      expect(html).not.toContain("<textarea");
      expect(html).not.toContain("Download PDF");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("starts blank with clear ten-minute draft-loss copy and no automatic PDF call", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentWorkExport, {
          accountId: owner,
          courseId: course,
        }),
      );
      expect(html).toContain("Drafts aren’t saved. Keep a copy elsewhere");
      expect(html).toContain("10 minutes after opening");
      expect(html).toContain("Clear editor");
      expect(html).not.toContain("Tutor response");
      expect(html).not.toContain("Saved course notes");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it("does not include course metadata or authorship in a blank preview", () => {
    const html = render(
      createElement(WorkExportPreview, {
        draft: blankWorkExportDraft(),
        context,
      }),
    );
    expect(html).toContain("Preview your document");
    expect(html).toContain("Omitted");
    expect(html).not.toContain(context.course_label);
    expect(html).toContain("Your own text will appear here");
    expect(html).toContain("title page counts toward");
  });
  it("escapes and preserves the exact entered text rather than rendering markup", () => {
    const html = render(
      createElement(WorkExportPreview, {
        draft: {
          ...blankWorkExportDraft(),
          title: "Own title",
          text: "<script>alert('x')</script>\n**plain text**",
          author: "My chosen name",
          include_course: true,
        },
        context,
      }),
    );
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain("**plain text**");
    expect(html).toContain("My chosen name");
    expect(html).toContain(context.course_label);
    expect(html).not.toContain("<strong>plain text</strong>");
  });
  it("requires both independent approvals and leaves optional metadata unchecked", () => {
    const html = render(
      createElement(WorkExportFields, {
        draft: blankWorkExportDraft(),
        context,
        disabled: false,
        onEdit: vi.fn(),
        onInclude: vi.fn(),
        onConfirm: vi.fn(),
        onDownload: vi.fn(),
      }),
    );
    expect(html.match(/type="checkbox"/g)).toHaveLength(3);
    expect(html).not.toContain('checked=""');
    expect(html).toContain("This text is my own work");
    expect(html).toContain(
      "I approve the title and any author or course label",
    );
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Download PDF/);
    expect(html).toContain('autoComplete="off"');
    expect(html).toContain('spellCheck="false"');
  });
  it("offers only a download after a reviewed own-work draft, with no submission/send/import controls", () => {
    const html = render(
      createElement(WorkExportFields, {
        draft: {
          ...blankWorkExportDraft(),
          title: "Title",
          text: "Own work",
          confirmed_own_work: true,
          confirmed_metadata: true,
        },
        context,
        disabled: false,
        onEdit: vi.fn(),
        onInclude: vi.fn(),
        onConfirm: vi.fn(),
        onDownload: vi.fn(),
      }),
    );
    expect(html).toContain("Download PDF");
    expect(html).not.toMatch(/<button[^>]*>Send/);
    expect(html).not.toContain("Submit assignment");
    expect(html).not.toContain("Import tutor");
    expect(html).not.toContain("Upload to school");
  });
  it("masks the same existing editor tree instead of deleting a brief same-owner draft", () => {
    const child = createElement("textarea", {
      defaultValue: "An unsaved paragraph",
    });
    const hidden = render(
        createElement(WorkExportPrivateSurface, { masked: true }, child),
      ),
      shown = render(
        createElement(WorkExportPrivateSurface, { masked: false }, child),
      );
    expect(hidden).toContain('hidden=""');
    expect(hidden).toContain('inert=""');
    expect(hidden).toContain("An unsaved paragraph");
    expect(shown).toContain("An unsaved paragraph");
    expect(shown).not.toContain('hidden=""');
  });
});
