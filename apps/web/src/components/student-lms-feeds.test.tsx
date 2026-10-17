import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type {
  LmsCalendarItem,
  LmsFeedPlatform,
  LmsFeedPreview,
} from "../lib/student-lms-feeds";
import type { StudentCourse } from "../lib/student-schedule";
import {
  LmsCalendarItemCard,
  LmsCourseMatchFields,
  LmsFeedPreviewReview,
  LmsPlatformGuide,
  StudentLmsFeeds,
} from "./student-lms-feeds";

const feedId = "11111111-1111-4111-8111-111111111111",
  courseId = "22222222-2222-4222-8222-222222222222",
  token = "44444444-4444-4444-8444-444444444444";
const course: StudentCourse = {
  id: courseId,
  code: "CS 100",
  name: "Programming",
  term_start: "2026-08-24",
  term_end: "2026-12-10",
  professor: null,
  archived: false,
  revision: 1,
  meetings: [],
};
const item: LmsCalendarItem = {
  external_id: "event-1",
  occurrence_id: "2026-10-20",
  title: "Lab report",
  kind: "assignment",
  starts_at: null,
  due_at: "2026-10-21T03:59:00Z",
  ends_at: null,
  all_day: true,
  course_hint: "CS 100 section 1",
  details: "Review the rubric.",
  source_url: "https://school.example/assignments/1",
  location: null,
  source_updated_at: null,
  cancelled: false,
};
const preview: LmsFeedPreview = {
  preview_token: token,
  feed_id: feedId,
  preview_hash: "a".repeat(64),
  expires_at: "2026-10-19T12:30:00Z",
  domain: "school.example",
  platform: "canvas",
  count: 1,
  course_hints: [item.course_hint as string],
  all_day_count: 1,
  all_day_due_time: "23:59",
  items: [item],
  notice: "Review before connecting.",
};
const render = (element: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(element);
describe("learning-system calendar UI", () => {
  it("renders the initial saved-list check without starting a sync or OAuth flow", () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
      const html = render(
        createElement(StudentLmsFeeds, { accountId: "workspace:user" }),
      );
      expect(html).toContain("Opening your saved connections");
      expect(html).not.toContain("/oauth/");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      fetch.mockRestore();
    }
  });
  it.each([
    "canvas",
    "blackboard",
    "moodle",
    "brightspace",
    "other",
  ] as LmsFeedPlatform[])(
    "shows specific paste-link steps for %s without a token or administrator request",
    (platform) => {
      const html = render(createElement(LmsPlatformGuide, { platform }));
      expect(html).toContain("<ol>");
      expect(html).not.toContain("personal access token");
      expect(html).not.toContain("developer key");
    },
  );
  it("keeps Google Classroom on the existing Calendar connection path", () => {
    const html = render(
      createElement(LmsPlatformGuide, { platform: "google_classroom" }),
    );
    expect(html).toContain("Use your Google Calendar connection");
    expect(html).toContain('href="/connections"');
    expect(html).toContain(
      "does not enable a separate Classroom API connection",
    );
  });
  it("shows domain-only metadata, source-zone and selected all-day due time before connecting", () => {
    const html = render(
      createElement(LmsFeedPreviewReview, {
        preview,
        courses: [course],
        timezone: "America/New_York",
        disabled: false,
        name: "My deadlines",
        matches: {},
        confirmed: false,
        onName: vi.fn(),
        onMatch: vi.fn(),
        onConfirm: vi.fn(),
        onConnect: vi.fn(),
        onDiscard: vi.fn(),
      }),
    );
    expect(html).toContain("1 calendar items from school.example");
    expect(html).toContain("Nothing will sync automatically until you confirm");
    expect(html).toContain("23:59");
    expect(html).toContain("America/New York");
    expect(html).toContain("Discard preview");
    expect(html).toMatch(
      /<button[^>]*disabled=""[^>]*>Connect reviewed calendar<\/button>/,
    );
    expect(html).not.toContain("SYNTHETIC-SECRET");
  });
  it("requires a reviewed course choice as well as a confirmation checkbox", () => {
    const props = {
      preview,
      courses: [course],
      timezone: "America/New_York",
      disabled: false,
      name: "My deadlines",
      confirmed: true,
      onName: vi.fn(),
      onMatch: vi.fn(),
      onConfirm: vi.fn(),
      onConnect: vi.fn(),
      onDiscard: vi.fn(),
    };
    const missing = render(
      createElement(LmsFeedPreviewReview, { ...props, matches: {} }),
    );
    expect(missing).toMatch(
      /<button[^>]*disabled=""[^>]*>Connect reviewed calendar<\/button>/,
    );
    const ready = render(
      createElement(LmsFeedPreviewReview, {
        ...props,
        matches: { [item.course_hint as string]: courseId },
      }),
    );
    expect(ready).not.toMatch(
      /<button[^>]*disabled=""[^>]*>Connect reviewed calendar<\/button>/,
    );
  });
  it("provides explicit unassigned choices without making up course IDs", () => {
    const html = render(
      createElement(LmsCourseMatchFields, {
        hints: ["Unknown seminar"],
        matches: { "Unknown seminar": null },
        courses: [],
        disabled: false,
        onChange: vi.fn(),
      }),
    );
    expect(html).toContain("Keep unassigned");
    expect(html).toContain("No course is created automatically");
    expect(html).not.toContain(courseId);
  });
  it("renders cancelled and conflicting items distinctly and does not offer silent acceptance", () => {
    const cancelled = render(
      createElement(LmsCalendarItemCard, {
        item,
        timezone: "America/New_York",
        status: "cancelled",
      }),
    );
    expect(cancelled).toContain("Cancelled in the source calendar");
    const conflict = render(
      createElement(LmsCalendarItemCard, {
        item,
        timezone: "America/New_York",
        status: "conflicted",
      }),
    );
    expect(conflict).toContain("The sources disagree");
    expect(conflict).toContain("Tuesday, Oct 20 at 11:59 PM");
    expect(conflict).toContain("uses your selected all-day deadline time");
  });
  it("opens source items without a referrer and escapes calendar-provided HTML", () => {
    const html = render(
      createElement(LmsCalendarItemCard, {
        item: { ...item, details: "<script>secret()</script>" },
        timezone: "America/New_York",
      }),
    );
    expect(html).toContain('referrerPolicy="no-referrer"');
    expect(html).toContain('rel="noreferrer noopener"');
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    const unsafe = render(
      createElement(LmsCalendarItemCard, {
        item: { ...item, source_url: "javascript:alert(1)" },
        timezone: "America/New_York",
      }),
    );
    expect(unsafe).not.toContain("javascript:");
    expect(unsafe).not.toContain("Open source calendar item");
  });
});
