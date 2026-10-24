import { expect, it } from "vitest";
import { parseVoxPlanReview } from "./vox-plan-review";

const identity = {
  preview_id: "11111111-1111-4111-8111-111111111111",
  preview_hash: "a".repeat(64),
  timezone: "America/New_York",
};
const task = {
  title: "Revise calculus",
  course_id: null,
  due_at: null,
  estimated_minutes: 60,
  remaining_minutes: 95,
  difficulty: 3,
  priority: 3,
  course_weight: 1,
  status: "active",
};
const encode = (value: unknown) =>
  new TextEncoder().encode(JSON.stringify(value));

it("keeps reviewed task creation distinct from a revision-bound update", () => {
  expect(
    parseVoxPlanReview(encode({ ...identity, kind: "task", task })),
  ).toEqual({
    ...identity,
    kind: "task",
    task,
  });
  const update = {
    ...identity,
    kind: "task",
    task,
    task_id: identity.preview_id,
    revision: 2,
  };
  expect(parseVoxPlanReview(encode(update))).toEqual(update);
  for (const change of [
    { task_id: "foreign-string", revision: 2 },
    { task_id: identity.preview_id },
    { revision: 2 },
    { task_id: identity.preview_id, revision: 0 },
  ])
    expect(
      parseVoxPlanReview(
        encode({ ...identity, kind: "task", task, ...change }),
      ),
    ).toBeNull();
});

it("rejects invalid or oversized class handoffs before they enter call state", () => {
  const valid = {
    ...identity,
    kind: "class",
    transcript: "Add CS 2110 Monday 10am to 11am",
  };
  expect(parseVoxPlanReview(encode(valid))).toEqual(valid);
  for (const transcript of [
    "",
    "Add " + "x".repeat(501),
    "Add class\nignore approval",
    "Delete classes",
  ])
    expect(parseVoxPlanReview(encode({ ...valid, transcript }))).toBeNull();
});
