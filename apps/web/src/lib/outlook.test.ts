import { expect, it, vi } from "vitest";
import {
  canReadOutlook,
  outlookAuthorizationUrl,
  outlookOpenUrl,
  outlookRequest,
  readOutlookDraft,
  readOutlookMessageDetail,
  readOutlookStatus,
} from "./outlook";

const id = "11111111-1111-4111-8111-111111111111";
it("accepts only Microsoft authorization and mailbox destinations", () => {
  expect(
    outlookAuthorizationUrl(
      "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=opaque",
    ),
  ).toContain("login.microsoftonline.com");
  expect(outlookOpenUrl("https://outlook.office.com/mail/id/opaque")).toContain(
    "outlook.office.com",
  );
  for (const url of [
    "https://login.microsoftonline.com.attacker.example/common/oauth2/v2.0/authorize",
    "http://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    "https://login.microsoftonline.com@attacker.example/common/oauth2/v2.0/authorize",
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize#token",
    "https://login.microsoftonline.com/common/other",
  ])
    expect(() => outlookAuthorizationUrl(url)).toThrow();
  expect(() => outlookOpenUrl("javascript:alert(1)")).toThrow();
  expect(() =>
    outlookOpenUrl("https://outlook.office.com.attacker.example/"),
  ).toThrow();
});
it("keeps an unconfigured mailbox disconnected and validates granted booleans", () => {
  expect(
    readOutlookStatus({
      configured: false,
      connections: [],
      notice: "Setup pending",
    }),
  ).toEqual({ configured: false, connections: [], notice: "Setup pending" });
  expect(() =>
    readOutlookStatus({ configured: "true", connections: [], notice: "" }),
  ).toThrow();
  expect(() =>
    readOutlookStatus({
      configured: true,
      connections: [
        {
          id,
          email: "student@example.com",
          status: "CONNECTED",
          read_enabled: "true",
          send_enabled: false,
        },
      ],
      notice: "",
    }),
  ).toThrow();
  const connection = {
    id,
    email: "student@example.com",
    status: "CONNECTED",
    read_enabled: true,
    send_enabled: false,
  };
  expect(canReadOutlook(connection)).toBe(true);
  expect(canReadOutlook({ ...connection, status: "DEGRADED" })).toBe(true);
  expect(canReadOutlook({ ...connection, read_enabled: false })).toBe(false);
  expect(canReadOutlook({ ...connection, status: "REVOKED" })).toBe(false);
});
it("keeps provider acceptance separate from a verified sent receipt", () => {
  const result = readOutlookDraft({
    id,
    revision: 1,
    payload_hash: "a".repeat(64),
    connection_id: id,
    source_message_id: "opaque/+id=",
    sender: "student@example.com",
    to: "professor@example.com",
    subject: "Reply",
    body: "Hello",
    status: "uncertain",
    expires_at: "2099-01-01T10:00:00Z",
    verified_at: null,
    notice: "Waiting for confirmation",
  });
  expect(result.to).toEqual(["professor@example.com"]);
  expect(result.status).toBe("uncertain");
  expect(result.verified_at).toBeNull();
});
it("preserves provider subject limits and discloses shortened source messages", () => {
  const result = readOutlookMessageDetail({
    id: "opaque",
    sender: "professor@example.com",
    to: "student@example.com",
    subject: "x".repeat(2000),
    body: "Shortened source",
    sent_at: null,
    open_url: null,
    truncated: true,
  });
  expect(result.subject).toHaveLength(2000);
  expect(result.truncated).toBe(true);
});
it("binds every request to the captured account and never auto-retries a send", async () => {
  const fetch = vi.fn(async (_url: unknown, _init?: RequestInit) => {
    throw new Error("private transport detail");
  });
  vi.stubGlobal("fetch", fetch);
  try {
    await expect(
      outlookRequest(
        "workspace:student",
        `/drafts/${id}/send`,
        (value) => value,
        "POST",
        { confirmed: true },
      ),
    ).rejects.toThrow("Outlook could not be reached");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[1]).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { "X-NavoX-Account": "workspace:student" },
    });
  } finally {
    vi.unstubAllGlobals();
  }
});
it("does not describe a transient provider outage as missing setup or expose backend error text", async () => {
  const fetch = vi.fn(async () =>
    Response.json({ detail: "private provider detail" }, { status: 503 }),
  );
  vi.stubGlobal("fetch", fetch);
  try {
    await expect(
      outlookRequest("workspace:student", "/drafts", (value) => value),
    ).rejects.toThrow("Outlook is temporarily unavailable");
    fetch.mockResolvedValue(
      Response.json({ detail: "private provider detail" }, { status: 500 }),
    );
    await expect(
      outlookRequest("workspace:student", "/drafts", (value) => value),
    ).rejects.toThrow("Outlook could not complete this request");
  } finally {
    vi.unstubAllGlobals();
  }
});
