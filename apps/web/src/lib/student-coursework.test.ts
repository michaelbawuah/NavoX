import { afterEach, describe, expect, it, vi } from "vitest";
import {
  COURSE_JSON_LIMIT_BYTES,
  CourseRequestError,
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
import { StudentRequestError, studentWriteUncertain } from "./student-schedule";

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
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
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
        redirect: "error",
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
        cache: "no-store",
        redirect: "error",
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
  it("keeps rejected PDFs known without echoing server extraction text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        { detail: "This PDF has no extractable text. Upload a text version." },
        { status: 422 },
      ),
    );
    const error = await uploadCourseMaterial(courseId, {
      title: "Scan",
      kind: "syllabus",
      revision: 0,
      request_id: requestId,
      content: new Blob(["PDF"]),
      media_type: "application/pdf",
    }).catch((error: unknown) => error);
    expect(error).toMatchObject({
      status: 422,
      uncertain: false,
    });
    expect(String(error)).not.toContain("This PDF has no extractable text");
  });
});

const privateText = "Private syllabus or email text: never echo this";
const materialUpload = () => ({
  title: "Private note",
  kind: "note" as const,
  revision: 0,
  request_id: requestId,
  content: new Blob(["Private text"]),
  media_type: "text/plain",
});

function abortableFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_url, options) =>
      new Promise<Response>((_resolve, reject) => {
        const signal = options?.signal;
        const abort = () => reject(new DOMException(privateText, "AbortError"));
        if (!signal || signal.aborted) abort();
        else signal.addEventListener("abort", abort, { once: true });
      }),
  );
}

describe("status-bearing private course transport", () => {
  it("preserves a safe 404 status without exposing body text or retrying", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        Response.json(
          { detail: privateText, course: "Another owner's course" },
          { status: 404 },
        ),
      );
    const error = await courseRequest(
      courseId,
      "/overview",
      readCourseOverview,
    ).catch((error: unknown) => error);
    expect(error).toBeInstanceOf(CourseRequestError);
    expect(error).toBeInstanceOf(StudentRequestError);
    expect(error).toMatchObject({ status: 404, uncertain: false });
    expect(String(error)).not.toContain(privateText);
    expect(String(error)).not.toContain("Another owner's course");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("preserves a known 404 even when its unread response stream already failed", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.error(new TypeError(privateText));
      },
    });
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(body, { status: 404 }));
    const error = await courseRequest(
      courseId,
      "/overview",
      readCourseOverview,
    ).catch((error: unknown) => error);
    expect(error).toMatchObject({ status: 404, uncertain: false });
    expect(String(error)).not.toContain(privateText);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([401, 403])(
    "retains authorization status %s with fixed private copy",
    async (status) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ detail: privateText }, { status }));
      const error = await courseRequest(
        courseId,
        "/materials",
        readCourseMaterialDetail,
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({ status, uncertain: false });
      expect(error).toBeInstanceOf(CourseRequestError);
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it.each([401, 403, 404, 409, 422])(
    "keeps rejected status %s writes known and never echoes validation details",
    async (status) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ detail: privateText }, { status }));
      const error = await courseRequest(
        courseId,
        "/overview",
        readCourseSaveReceipt,
        "PUT",
        { revision: 1, request_id: requestId },
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({ status, uncertain: false });
      expect(studentWriteUncertain(error)).toBe(false);
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("writes the exact explicit JSON once with uncached credentialed redirect-blocking transport", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(receipt));
    const body = { revision: 1, request_id: requestId, staff: [] };
    expect(
      await courseRequest(
        courseId,
        "/overview",
        readCourseSaveReceipt,
        "PUT",
        body,
      ),
    ).toEqual(receipt);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toContain(
      `/student/courses/${courseId}/workspace/overview`,
    );
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "PUT",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    expect(fetch.mock.calls[0][1]?.signal).toBeInstanceOf(AbortSignal);
  });

  it.each(["GET", "PUT"])(
    "supports an external abort for %s and keeps uncertainty appropriate to the method",
    async (method) => {
      const fetch = abortableFetch();
      const controller = new AbortController();
      const operation = courseRequest(
        courseId,
        "/overview",
        readCourseOverview,
        method,
        method === "PUT" ? { request_id: requestId } : undefined,
        controller.signal,
      ).catch((error: unknown) => error);
      expect(fetch).toHaveBeenCalledTimes(1);
      const requestSignal = fetch.mock.calls[0][1]?.signal;
      expect(requestSignal?.aborted).toBe(false);
      controller.abort();
      const error = await operation;
      expect(requestSignal?.aborted).toBe(true);
      expect(error).toMatchObject({
        status: null,
        uncertain: method !== "GET",
      });
      expect(error).toBeInstanceOf(CourseRequestError);
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("keeps a lost write response uncertain without retrying or leaking exception text", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError(privateText));
    const error = await courseRequest(
      courseId,
      "/overview",
      readCourseSaveReceipt,
      "PUT",
      { revision: 1, request_id: requestId },
    ).catch((error: unknown) => error);
    expect(error).toMatchObject({ status: null, uncertain: true });
    expect(studentWriteUncertain(error)).toBe(true);
    expect(String(error)).not.toContain(privateText);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([500, 503])(
    "treats status %s writes as uncertain but does not expose their body",
    async (status) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ detail: privateText }, { status }));
      const error = await courseRequest(
        courseId,
        "/materials",
        readCourseSaveReceipt,
        "POST",
        { request_id: requestId },
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({ status, uncertain: true });
      expect(studentWriteUncertain(error)).toBe(true);
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["GET", "PUT"])(
    "sanitizes successful %s parser failures and never presents unchecked content",
    async (method) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ raw_private_body: privateText }));
      const parser = vi.fn(() => {
        throw new Error(privateText);
      });
      const error = await courseRequest(
        courseId,
        "/overview",
        parser,
        method,
        method === "PUT" ? { request_id: requestId } : undefined,
      ).catch((error: unknown) => error);
      expect(parser).toHaveBeenCalledTimes(1);
      expect(error).toMatchObject({
        status: null,
        uncertain: method !== "GET",
      });
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["GET", "PUT"])(
    "rejects unreadable successful %s JSON without echoing raw text",
    async (method) => {
      const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(privateText, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
      const error = await courseRequest(
        courseId,
        "/overview",
        readCourseOverview,
        method,
        method === "PUT" ? { request_id: requestId } : undefined,
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({
        status: null,
        uncertain: method !== "GET",
      });
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("treats a read-side network failure as known without reflecting the exception", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError(privateText));
    const error = await courseRequest(
      courseId,
      "/overview",
      readCourseOverview,
    ).catch((error: unknown) => error);
    expect(error).toMatchObject({ status: null, uncertain: false });
    expect(String(error)).not.toContain(privateText);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["declared", "streamed"])(
    "bounds %s JSON before any private parser runs",
    async (kind) => {
      const cancel = vi.fn();
      const response =
        kind === "declared"
          ? new Response("{}", {
              headers: {
                "Content-Length": String(COURSE_JSON_LIMIT_BYTES + 1),
              },
            })
          : new Response(
              new ReadableStream<Uint8Array>({
                start(controller) {
                  controller.enqueue(new Uint8Array(COURSE_JSON_LIMIT_BYTES));
                  controller.enqueue(new Uint8Array([1]));
                },
                cancel,
              }),
            );
      const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
      const parser = vi.fn();
      const error = await courseRequest(courseId, "/overview", parser).catch(
        (error: unknown) => error,
      );
      expect(error).toMatchObject({ status: null, uncertain: false });
      expect(parser).not.toHaveBeenCalled();
      if (kind === "streamed") expect(cancel).toHaveBeenCalledTimes(1);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("rejects invalid UTF-8 JSON as an unchecked write rather than replacing private bytes", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(new Uint8Array([0xc3, 0x28])));
    const parser = vi.fn();
    const error = await courseRequest(courseId, "/overview", parser, "PUT", {
      request_id: requestId,
    }).catch((error: unknown) => error);
    expect(error).toMatchObject({ status: null, uncertain: true });
    expect(parser).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["GET", "PUT"])(
    "bounds an unanswered %s request to 12 seconds without retrying",
    async (method) => {
      vi.useFakeTimers();
      const fetch = abortableFetch();
      const operation = courseRequest(
        courseId,
        "/overview",
        readCourseOverview,
        method,
      ).catch((error: unknown) => error);
      const signal = fetch.mock.calls[0][1]?.signal;
      await vi.advanceTimersByTimeAsync(11_999);
      expect(signal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      expect(signal?.aborted).toBe(true);
      expect(await operation).toMatchObject({
        status: null,
        uncertain: method !== "GET",
      });
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );
});

describe("status-bearing private course uploads", () => {
  it.each([401, 403, 404, 409, 422])(
    "retains known status %s and uses safe upload failure copy",
    async (status) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ detail: privateText }, { status }));
      const error = await uploadCourseMaterial(
        courseId,
        materialUpload(),
      ).catch((error: unknown) => error);
      expect(error).toBeInstanceOf(CourseRequestError);
      expect(error).toBeInstanceOf(StudentRequestError);
      expect(error).toMatchObject({ status, uncertain: false });
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("aborts an in-flight upload using the caller's signal and does not retry it", async () => {
    const fetch = abortableFetch();
    const controller = new AbortController();
    const operation = uploadCourseMaterial(courseId, {
      ...materialUpload(),
      signal: controller.signal,
    }).catch((error: unknown) => error);
    expect(fetch).toHaveBeenCalledTimes(1);
    const requestSignal = fetch.mock.calls[0][1]?.signal;
    controller.abort();
    const error = await operation;
    expect(requestSignal?.aborted).toBe(true);
    expect(error).toMatchObject({ status: null, uncertain: true });
    expect(String(error)).not.toContain(privateText);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([500, 503])(
    "does not retry an uncertain status %s upload",
    async (status) => {
      const fetch = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(Response.json({ detail: privateText }, { status }));
      const error = await uploadCourseMaterial(
        courseId,
        materialUpload(),
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({ status, uncertain: true });
      expect(studentWriteUncertain(error)).toBe(true);
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("does not retry an uncertain network upload or expose its exception", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError(privateText));
    const error = await uploadCourseMaterial(courseId, materialUpload()).catch(
      (error: unknown) => error,
    );
    expect(error).toMatchObject({ status: null, uncertain: true });
    expect(String(error)).not.toContain(privateText);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["malformed", "forgotten", "unparseable"])(
    "keeps a successful upload with a %s receipt uncertain",
    async (kind) => {
      const response =
        kind === "unparseable"
          ? new Response(privateText, { status: 200 })
          : Response.json(
              kind === "forgotten"
                ? { ...receipt, reused: true, available: false }
                : { raw_body: privateText },
            );
      const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
      const error = await uploadCourseMaterial(
        courseId,
        materialUpload(),
      ).catch((error: unknown) => error);
      expect(error).toMatchObject({ status: null, uncertain: true });
      expect(String(error)).not.toContain(privateText);
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("bounds an unanswered upload to 40 seconds and retains its uncertain result", async () => {
    vi.useFakeTimers();
    const fetch = abortableFetch();
    const operation = uploadCourseMaterial(courseId, materialUpload()).catch(
      (error: unknown) => error,
    );
    const signal = fetch.mock.calls[0][1]?.signal;
    await vi.advanceTimersByTimeAsync(39_999);
    expect(signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(signal?.aborted).toBe(true);
    expect(await operation).toMatchObject({ status: null, uncertain: true });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
