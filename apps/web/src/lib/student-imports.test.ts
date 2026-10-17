import { afterEach, describe, expect, it, vi } from "vitest";
import {
  readStudentScreenshot,
  readStudentScreenshotPreview,
  studentClassPrefill,
} from "./student-imports";

const candidate = {
  course_name: "Digital logic",
  course_code: "ECE 200",
  kind: "other",
  weekdays: [1, 3],
  start_time: null,
  end_time: null,
  timezone: null,
  building: null,
  room: null,
  instructor: null,
  uncertainties: ["Check AM or PM."],
};
const preview = {
  extracted_text: "ECE 200 Tue Thu",
  candidates: [candidate],
  warnings: ["Review all details."],
  requires_review: true,
};
afterEach(() => vi.restoreAllMocks());
describe("screenshot review boundary", () => {
  it("keeps unknown times and locations unknown", () => {
    expect(readStudentScreenshotPreview(preview)).toEqual(preview);
  });
  it.each([
    { ...preview, requires_review: false },
    { ...preview, candidates: [{ ...candidate, weekdays: [7] }] },
    { ...preview, candidates: [{ ...candidate, start_time: "4:30 pm" }] },
    {
      ...preview,
      candidates: [{ ...candidate, timezone: "America/New_York" }],
    },
  ])("rejects an unreviewable preview", (value) => {
    expect(() => readStudentScreenshotPreview(value)).toThrow(
      "preview could not be read",
    );
  });
  it("sends a selected image only on explicit request as bounded raw bytes", async () => {
    const file = new File(["image bytes"], "schedule.png", {
      type: "image/png",
    });
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify(preview)));
    expect(await readStudentScreenshot(file)).toEqual(preview);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/student/imports/screenshot/preview"),
      expect.objectContaining({
        method: "POST",
        body: file,
        credentials: "include",
        headers: { "Content-Type": "image/png" },
      }),
    );
  });
  it("rejects unsupported or oversized images before making any request", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    await expect(
      readStudentScreenshot(
        new File(["x"], "not-image.svg", { type: "image/svg+xml" }),
      ),
    ).rejects.toThrow("PNG");
    await expect(
      readStudentScreenshot(
        new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", {
          type: "image/png",
        }),
      ),
    ).rejects.toThrow("5 MB");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("offers a manual fallback if local reading is unavailable", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("{}", { status: 503 }),
    );
    await expect(
      readStudentScreenshot(
        new File(["x"], "schedule.png", { type: "image/png" }),
      ),
    ).rejects.toThrow("by hand");
  });
});

describe("explicit voice class prefill", () => {
  it("prefills explicit code, type, days, AM/PM times and room while leaving term and zone to review", () => {
    const draft = studentClassPrefill(
      "Add CS2110 lecture Monday 9:05 AM to 9:55 AM in Hollister 110",
    );
    expect(draft).toMatchObject({
      course_code: "CS 2110",
      course_name: "CS 2110",
      kind: "lecture",
      weekdays: [0],
      start_time: "09:05",
      end_time: "09:55",
      timezone: null,
      building: "Hollister",
      room: "110",
    });
    expect(draft?.uncertainties.join(" ")).toContain(
      "term dates and time zone",
    );
  });
  it("does not guess AM or PM in the product-plan example", () => {
    const draft = studentClassPrefill(
      "Add ECE2300 lecture, Tue/Thu12:30–1:45, EngineeringHall101",
    );
    expect(draft).toMatchObject({
      course_code: "ECE 2300",
      kind: "lecture",
      weekdays: [1, 3],
      start_time: null,
      end_time: null,
      building: "EngineeringHall",
      room: "101",
    });
    expect(draft?.uncertainties.join(" ")).toContain("did not guess");
  });
  it("accepts explicit 24-hour afternoon times", () => {
    expect(
      studentClassPrefill(
        "Create a class CS 100 lab Wednesdays 14:00 to 16:00 in Science Hall room G01",
      ),
    ).toMatchObject({
      course_code: "CS 100",
      kind: "lab",
      weekdays: [2],
      start_time: "14:00",
      end_time: "16:00",
      building: "Science Hall",
      room: "G01",
    });
  });
  it("does not infer a missing AM/PM from the other endpoint", () => {
    expect(
      studentClassPrefill("Add CS100 lecture Monday 9 to 10 AM in Hall 100"),
    ).toMatchObject({ start_time: null, end_time: "10:00" });
  });
  it("keeps a named course without inventing its course code", () => {
    expect(
      studentClassPrefill(
        "Add my chem lab, Wednesdays 2:30 PM to 4:25 PM in Science Center 210",
      ),
    ).toMatchObject({
      course_code: null,
      course_name: "chem",
      kind: "lab",
      weekdays: [2],
      start_time: "14:30",
      end_time: "16:25",
      building: "Science Center",
      room: "210",
    });
  });
  it("does not fill an external-action clause as a building", () => {
    const value = studentClassPrefill(
      "Add CS100 Monday 9 AM to 10 AM and email my professor",
    );
    expect(value?.building).toBeNull();
    expect(value?.instructor).toBeNull();
  });
  it("requires explicit days and end time rather than manufacturing a one-hour class", () => {
    const value = studentClassPrefill("Add CS100 lecture");
    expect(value).toMatchObject({
      weekdays: [],
      start_time: null,
      end_time: null,
      timezone: null,
    });
  });
  it.each([
    "Tell me about CS100",
    `Add ${"x".repeat(501)}`,
    "Add CS100\nMonday",
  ])("falls back safely for unsupported notes", (text) => {
    expect(studentClassPrefill(text)).toBeNull();
  });
});
