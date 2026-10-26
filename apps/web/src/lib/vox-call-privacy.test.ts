import { describe, expect, it } from "vitest";
import { readVoxCallPrivacy, voxPrivacyLabel } from "./vox-call-privacy";

const expected = {
  userId: "10000000-0000-4000-8000-000000000001",
  workspaceId: "20000000-0000-4000-8000-000000000001",
  callId: "30000000-0000-4000-8000-000000000001",
};
const now = Date.parse("2026-10-26T18:00:00Z");
const descriptor = {
  user_id: expected.userId,
  workspace_id: expected.workspaceId,
  client_call_id: expected.callId,
  generation: 4,
  privacy_generation: 7,
  mode: "off-record",
  retention_days: null,
  expires_at: new Date(now + 60000).toISOString(),
};
describe("verified Vox privacy descriptor", () => {
  it.each([
    ["off-record", null, "Off the record"],
    ["history-off", null, "History off"],
    ["saved-history", 30, "History on · 30 days"],
    ["saved-history", 90, "History on · 90 days"],
    ["saved-history", null, "History on · no auto-delete"],
  ])("labels %s only from a bound response", (mode, retention_days, label) => {
    expect(
      voxPrivacyLabel(
        readVoxCallPrivacy(
          { ...descriptor, mode, retention_days },
          expected,
          now,
        ),
      ),
    ).toBe(label);
  });
  it.each([
    { user_id: "10000000-0000-4000-8000-000000000002" },
    { workspace_id: "20000000-0000-4000-8000-000000000002" },
    { client_call_id: "30000000-0000-4000-8000-000000000002" },
    { mode: "off-record", retention_days: 30 },
    { mode: "unverified" },
    { generation: -1 },
    { privacy_generation: 2147483648 },
    { expires_at: new Date(now).toISOString() },
    { expires_at: new Date(now + 120001).toISOString() },
    { extra: "not part of this descriptor" },
  ])("rejects wrong scope, stale or malformed proof: %j", (changes) => {
    expect(() =>
      readVoxCallPrivacy({ ...descriptor, ...changes }, expected, now),
    ).toThrow();
  });
});
