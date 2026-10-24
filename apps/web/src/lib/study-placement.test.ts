import { expect, it } from "vitest";
import { consumeStudyWindow, studyWindowKey } from "./study-placement";

const owner = "personal:student",
  now = Date.parse("2026-10-24T13:00:00Z");
const intent = {
  accountKey: owner,
  start_at: "2026-10-24T14:00:00Z",
  end_at: "2026-10-24T16:00:00Z",
  timezone: "America/New_York",
  task_id: "11111111-1111-4111-8111-111111111111",
  created_at: new Date(now).toISOString(),
};
function storage(value: unknown) {
  const values = new Map([[studyWindowKey(owner), JSON.stringify(value)]]);
  return {
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => {
      values.delete(key);
    },
  };
}
it("carries the exact interval and task once, only to its owner", () => {
  const saved = storage(intent);
  expect(consumeStudyWindow(saved, "personal:other", now)).toBeNull();
  expect(consumeStudyWindow(saved, owner, now)).toEqual(intent);
  expect(consumeStudyWindow(saved, owner, now)).toBeNull();
});
it.each([
  { accountKey: "personal:other" },
  { created_at: "2026-10-24T12:49:59Z" },
  { end_at: "2026-10-24T12:00:00Z" },
  { timezone: "not-a-zone" },
  { task_id: "another account's task" },
])("rejects stale, invalid or mismatched handoffs: %j", (change) => {
  expect(
    consumeStudyWindow(storage({ ...intent, ...change }), owner, now),
  ).toBeNull();
});
