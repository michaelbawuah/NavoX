// @vitest-environment jsdom

import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  type CourseProof,
  coursePrivacyApi,
} from "../lib/student-course-privacy";
import type { CourseMaterialDetail } from "../lib/student-coursework";
import { StudentCourseWorkspace } from "./student-course-workspace";

const course = "11111111-1111-4111-8111-111111111111";
const materialId = "22222222-2222-4222-8222-222222222222";
const account =
  "33333333-3333-4333-8333-333333333333:44444444-4444-4444-8444-444444444444";
const material: CourseMaterialDetail = {
  id: materialId,
  title: "Lecture source",
  kind: "material",
  revision: 2,
  media_type: "application/pdf",
  byte_size: 200,
  text_size: 40,
  page_count: 2,
  created_at: "2026-10-22T12:00:00Z",
  updated_at: "2026-10-22T12:00:00Z",
  pages: ["First source page", "Second source page"],
};
const proof: CourseProof = {
  overview: {
    course: {
      id: course,
      code: "ECE 2300",
      name: "Digital Logic",
      professor: null,
      archived: false,
      revision: 1,
      term_start: "2026-08-20",
      term_end: "2026-12-20",
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
  },
  materials: [material],
  assignments: { tasks: [], feed_items: [], next_offset: null },
  emails: { links: [], unavailable_count: 0 },
};
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(coursePrivacyApi, "account").mockResolvedValue(account);
  vi.spyOn(coursePrivacyApi, "proof").mockImplementation(async () =>
    structuredClone(proof),
  );
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
async function mount(revision = 2, page = 2) {
  await act(async () => {
    root.render(
      createElement(StudentCourseWorkspace, {
        accountId: account,
        courseId: course,
        enabled: true,
        materialReference: { materialId, page, revision },
      }),
    );
  });
}
describe("assistant citation to saved course reader", () => {
  it("checks course ownership and opens the exact saved page, rather than the first page", async () => {
    const fetch = vi.fn(async (_input: RequestInfo | URL) =>
      Response.json(material),
    );
    vi.stubGlobal("fetch", fetch);
    await mount();
    expect(host.textContent).toContain("Second source page");
    expect(host.textContent).not.toContain("First source page");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[0]).toContain(
      `/courses/${course}/workspace/materials/${materialId}`,
    );
    expect(coursePrivacyApi.account).toHaveBeenCalledTimes(4);
  });
  it("does not fetch a removed, replaced or out-of-range source from an old answer", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await mount(1);
    expect(host.textContent).toContain("This source changed or was removed");
    expect(fetch).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    root = createRoot(host);
    await mount(2, 3);
    expect(host.textContent).toContain("This source changed or was removed");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("suppresses the linked page when the account changes during its read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        vi.mocked(coursePrivacyApi.account).mockResolvedValue("other-owner");
        return Response.json(material);
      }),
    );
    await mount();
    expect(host.textContent).not.toContain("Second source page");
    expect(host.textContent).toContain("Your account could not be checked");
  });
  it("clears an open page when same-course navigation selects a stale source", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(material)),
    );
    await mount();
    expect(host.textContent).toContain("Second source page");
    await mount(1);
    expect(host.textContent).toContain("This source changed or was removed");
    expect(host.textContent).not.toContain("Second source page");
  });
  it("fences a pending previous citation after navigation to an unavailable citation", async () => {
    let finish!: (response: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    await mount();
    expect(fetch).toHaveBeenCalledTimes(1);
    await mount(1);
    await act(async () => {
      finish(Response.json(material));
    });
    expect(host.textContent).toContain("This source changed or was removed");
    expect(host.textContent).not.toContain("Second source page");
    expect(host.textContent).not.toContain("First source page");
  });
});
