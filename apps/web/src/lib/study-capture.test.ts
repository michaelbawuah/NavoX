import { describe, expect, it } from "vitest";
import { parseStudyCapture } from "./study-capture";

const courses = [
  { id: "ece", code: "ECE 2100", name: "Circuits" },
  { id: "cs", code: "CS 2110", name: "Object Oriented Programming" },
];
const now = Date.parse("2026-10-23T02:00:00Z");
describe("reviewable task capture", () => {
  it("extracts effort, a unique course and the student's local deadline", () => {
    const task = parseStudyCapture(
      "ECE lab report, Friday, about 4 hours",
      courses,
      "America/New_York",
      now,
    );
    expect(task.course_id).toBe("ece");
    expect(task.estimated_minutes).toBe(240);
    expect(task.deadlineLocal).toBe("2026-10-23T23:59");
    expect(task.title).toBe("ECE lab report");
    expect(task.notes.join(" ")).toContain("suggested");
  });
  it("uses the saved timezone across midnight and reads explicit clock and combined effort", () => {
    const task = parseStudyCapture(
      "CS2110 tomorrow at 5pm, 1 hour 30 minutes",
      courses,
      "America/New_York",
      now,
    );
    expect(task.course_id).toBe("cs");
    expect(task.deadlineLocal).toBe("2026-10-23T17:00");
    expect(task.estimated_minutes).toBe(90);
  });
  it("does not associate a different course number or ambiguous department", () => {
    expect(
      parseStudyCapture("CS21101 assignment", courses, "UTC", now).course_id,
    ).toBeNull();
    expect(
      parseStudyCapture(
        "CS assignment",
        [...courses, { id: "cs2", code: "CS 2120", name: "Algorithms" }],
        "UTC",
        now,
      ).course_id,
    ).toBeNull();
  });
  it("leaves missing deadlines for the student and labels default effort", () => {
    const task = parseStudyCapture("Essay", courses, "UTC", now);
    expect(task.deadlineLocal).toBe("");
    expect(task.notes.join(" ")).toContain("starting estimate");
  });
});
