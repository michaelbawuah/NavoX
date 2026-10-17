import { afterEach, describe, expect, it, vi } from "vitest";
import {
  courseEmailUrl,
  courseMaterialUrl,
  courseOverviewDraft,
  coursePercentHundredths,
  coursePolicyQuote,
  courseRequest,
  readCourseAssignments,
  readCourseEmailCandidates,
  readCourseEmails,
  readCourseMaterialDetail,
  readCourseOverview,
  readCourseReceipt,
  readCourseSaveReceipt,
  readCourseSearch,
  uploadCourseMaterial,
} from "./student-coursework";

const courseId = "11111111-1111-4111-8111-111111111111",
  materialId = "22222222-2222-4222-8222-222222222222",
  requestId = "33333333-3333-4333-8333-333333333333";
const material = {
  id: materialId,
  title: "Course syllabus",
  kind: "syllabus" as const,
  revision: 2,
  media_type: "application/pdf",
  byte_size: 1000,
  text_size: 100,
  page_count: 2,
  created_at: "2026-10-19T12:00:00Z",
  updated_at: "2026-10-19T13:00:00Z",
  pages: [
    "Learning outcomes.",
    "AI use:\nAsk your instructor\tbefore assessed work.",
  ],
};
const overview = {
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
  policy_status: "unknown" as const,
  default_help_mode: "tutor_outline_review" as const,
  ai_permission_granted: false as const,
  notice: "Private course workspace",
};
const receipt = {
  request_id: requestId,
  entity_id: materialId,
  revision: 2,
  reused: false,
  available: true,
};
afterEach(() => vi.restoreAllMocks());
describe("private course contracts", () => {
  it("accepts truthful empty course collections without inferring a policy", () => {
    expect(readCourseOverview(overview).ai_policy).toBeNull();
    expect(
      readCourseAssignments({ tasks: [], feed_items: [], next_offset: null }),
    ).toEqual({ tasks: [], feed_items: [], next_offset: null });
    expect(readCourseEmails({ links: [], unavailable_count: 0 }).links).toEqual(
      [],
    );
    expect(
      readCourseSearch({
        course_id: courseId,
        results: [],
        limit_reached: false,
      }).results,
    ).toEqual([]);
  });
  it("refuses a claimed AI permission even alongside a quoted syllabus", () => {
    expect(() =>
      readCourseOverview({ ...overview, ai_permission_granted: true }),
    ).toThrow();
    expect(() =>
      readCourseOverview({
        ...overview,
        default_help_mode: "write_my_assignment",
      }),
    ).toThrow();
  });
  it("preserves verbatim multi-line policy sources with exact version and page", () => {
    const quote = "Ask your instructor\tbefore assessed work.";
    expect(coursePolicyQuote(material, 2, quote)).toEqual({
      material_id: materialId,
      revision: 2,
      page: 2,
      quote,
    });
    expect(
      coursePolicyQuote(material, 2, "AI use:\nAsk your instructor").quote,
    ).toContain("\n");
    expect(() =>
      coursePolicyQuote(
        material,
        2,
        "Ask your instructor before assessed work.",
      ),
    ).toThrow("exact passage");
    expect(() =>
      coursePolicyQuote({ ...material, kind: "material" }, 2, quote),
    ).toThrow();
    expect(() => coursePolicyQuote(material, 3, quote)).toThrow();
  });
  it("checks page completeness and extraction limits", () => {
    expect(readCourseMaterialDetail(material).pages).toHaveLength(2);
    expect(() =>
      readCourseMaterialDetail({ ...material, pages: ["Only one"] }),
    ).toThrow();
    expect(() =>
      readCourseMaterialDetail({ ...material, page_count: 101, pages: [] }),
    ).toThrow();
    expect(() =>
      readCourseMaterialDetail({
        ...material,
        pages: ["x".repeat(200000), "x"],
      }),
    ).toThrow();
  });
  it("copies editable overview fields without course identity or mutable source aliases", () => {
    const quote = coursePolicyQuote(material, 2, "AI use:");
    const parsed = readCourseOverview({
      ...overview,
      ai_policy: quote,
      policy_status: "quoted_policy",
      staff: [
        {
          name: "Instructor",
          role: "Professor",
          email: null,
          office_hours: null,
          source: quote,
        },
      ],
    });
    const draft = courseOverviewDraft(parsed);
    expect(draft).not.toHaveProperty("course");
    expect(draft).not.toHaveProperty("ai_permission_granted");
    if (draft.ai_policy) draft.ai_policy.quote = "Changed locally";
    expect(parsed.ai_policy?.quote).toBe("AI use:");
    if (draft.staff[0].source) draft.staff[0].source.page = 1;
    expect(parsed.staff[0].source?.page).toBe(2);
  });
  it("keeps grading arithmetic exact to two decimal places", () => {
    expect(coursePercentHundredths("33.33")).toBe(3333);
    expect(coursePercentHundredths("33.34")).toBe(3334);
    expect(coursePercentHundredths("100.00")).toBe(10000);
    for (const value of ["100.01", "2.345", "1e2", "-1", "NaN"])
      expect(coursePercentHundredths(value)).toBeNull();
  });
  it("only opens verified Gmail HTTPS origins", () => {
    expect(
      courseEmailUrl("https://mail.google.com/mail/u/0/#inbox/123"),
    ).toContain("mail.google.com");
    for (const value of [
      "javascript:alert(1)",
      "https://mail.google.com.evil.test/",
      "https://user:password@mail.google.com/",
      "http://mail.google.com/",
      null,
    ])
      expect(courseEmailUrl(value)).toBeNull();
    expect(courseMaterialUrl(courseId, materialId, 2)).toContain(
      `/courses/${courseId}/workspace/materials/${materialId}/file#page=2`,
    );
  });
  it("requires readonly recovery for a replay whose private data was forgotten", () => {
    expect(() =>
      readCourseSaveReceipt({ ...receipt, reused: true, available: false }),
    ).toThrow("deleted");
    expect(readCourseReceipt({ ...receipt, available: false }).available).toBe(
      false,
    );
  });
  it("keeps the selected email hash paired with fresh nullable labels", () => {
    const candidate = {
      source_id: materialId,
      source_hash: "a".repeat(64),
      title: null,
      sender_label: "Course staff",
      display_label: null,
    };
    expect(
      readCourseEmailCandidates({
        candidates: [candidate],
        limit_reached: false,
        notice: "Saved labels only.",
      }).candidates[0],
    ).toEqual(candidate);
    expect(() =>
      readCourseEmailCandidates({
        candidates: [{ ...candidate, source_hash: "bad" }],
        limit_reached: false,
        notice: "",
      }),
    ).toThrow();
    expect(
      readCourseEmails({
        links: [
          {
            ...candidate,
            id: requestId,
            revision: 1,
            source_type: "communication.message",
            source_open_url: null,
          },
        ],
        unavailable_count: 0,
      }).links[0].title,
    ).toBeNull();
  });
  it("keeps locator-only saved Inbox labels distinct from actual email subjects", () => {
    const candidate = {
      source_id: materialId,
      source_hash: "b".repeat(64),
      title: null,
      sender_label: null,
      display_label: "Saved item: Review the lab deadline",
    };
    const value = readCourseEmailCandidates({
      candidates: [candidate],
      limit_reached: false,
      notice: "Saved item labels are not email subjects.",
    });
    expect(value.candidates[0]).toEqual(candidate);
    const links = readCourseEmails({
      links: [
        {
          ...candidate,
          id: requestId,
          revision: 1,
          source_type: "communication.message",
          source_open_url: null,
        },
      ],
      unavailable_count: 0,
    });
    expect(links.links[0].title).toBeNull();
    expect(links.links[0].display_label).toBe(candidate.display_label);
  });
});
describe("explicit course requests", () => {
  it("reads only the requested course, with account cookies and no cache", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(overview));
    await courseRequest(courseId, "/overview", readCourseOverview);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        `/student/courses/${courseId}/workspace/overview`,
      ),
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        cache: "no-store",
      }),
    );
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("saves a selected file as raw bytes only on explicit upload", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(receipt));
    const body = new Blob(["My note"], { type: "text/plain" });
    await uploadCourseMaterial(courseId, {
      title: "Week 1 · notes",
      kind: "note",
      revision: 0,
      request_id: requestId,
      content: body,
      media_type: "text/plain",
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/materials?kind=note&revision=0"),
      expect.objectContaining({
        method: "POST",
        body,
        credentials: "include",
        headers: {
          "Content-Type": "text/plain",
          "X-Material-Title": encodeURIComponent("Week 1 · notes"),
          "X-Request-ID": requestId,
        },
      }),
    );
  });
  it("keeps replacements revision-fenced", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(receipt));
    await uploadCourseMaterial(courseId, {
      title: "Syllabus",
      kind: "syllabus",
      revision: 2,
      material_id: materialId,
      request_id: requestId,
      content: new Blob(["PDF content"]),
      media_type: "application/pdf",
    });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining(
        `/materials/${materialId}?kind=syllabus&revision=2`,
      ),
      expect.objectContaining({ method: "PUT" }),
    );
  });
  it("rejects oversize uploads before any network request", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    await expect(
      uploadCourseMaterial(courseId, {
        title: "Large file",
        kind: "material",
        revision: 0,
        request_id: requestId,
        content: new Blob([new Uint8Array(5 * 1024 * 1024 + 1)]),
        media_type: "application/pdf",
      }),
    ).rejects.toThrow("5 MiB");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("blocks repeated writes after a lost or unreadable save response", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(
      new TypeError("Connection ended"),
    );
    await expect(
      uploadCourseMaterial(courseId, {
        title: "Private note",
        kind: "note",
        revision: 0,
        request_id: requestId,
        content: new Blob(["Private text"]),
        media_type: "text/plain",
      }),
    ).rejects.toMatchObject({ uncertain: true });
  });
  it("shows image-only PDF errors as a known failed write", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        { detail: "This PDF has no extractable text. Upload a text version." },
        { status: 422 },
      ),
    );
    await expect(
      uploadCourseMaterial(courseId, {
        title: "Scan",
        kind: "syllabus",
        revision: 0,
        request_id: requestId,
        content: new Blob(["PDF"]),
        media_type: "application/pdf",
      }),
    ).rejects.toMatchObject({
      uncertain: false,
      message: expect.stringContaining("text version"),
    });
  });
});
