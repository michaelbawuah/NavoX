import { describe, expect, it } from "vitest";
import {
  eligibleReplyCommitment,
  emailSendOutcome,
  manualEmailApprovalPayload,
  manualEmailReviewMatches,
  mayStartAnotherEmail,
  replyPreparationPayload,
  replyRecipient,
  replySubject,
  selectReplyConnection,
  selectReplySource,
} from "./email-workflow";

const sendScope = "https://www.googleapis.com/auth/gmail.send";
const source = {
  id: "source-owned",
  external_id: "message-1",
  provider: "Gmail",
  connection_id: "mailbox-owned",
};
const account = {
  id: "mailbox-owned",
  status: "active",
  granted_scopes: [sendScope],
};

describe("source-linked email selection", () => {
  it("opens only a supplied eligible request", () => {
    const commitments = [{ id: "owned", title: "Reply to the request" }];
    expect(eligibleReplyCommitment(commitments, "owned")).toBe(commitments[0]);
    expect(eligibleReplyCommitment(commitments, "foreign")).toBeUndefined();
    expect(eligibleReplyCommitment(commitments, undefined)).toBeUndefined();
  });

  it("selects one source and requires a choice when several remain", () => {
    const other = { ...source, id: "source-2" };
    expect(selectReplySource([source], "")).toBe(source);
    expect(selectReplySource([source, other], "")).toBeUndefined();
    expect(selectReplySource([source, other], other.id)).toBe(other);
    expect(selectReplySource([source], "foreign")).toBeUndefined();
  });

  it("uses the source account rather than the first connected mailbox", () => {
    const other = { ...account, id: "other" };
    expect(selectReplyConnection([other, account], source, "")).toBe(account);
    expect(selectReplyConnection([other], source, "")).toBeUndefined();
    expect(
      selectReplyConnection([other, account], undefined, ""),
    ).toBeUndefined();
    expect(selectReplyConnection([account], undefined, "")).toBe(account);
    expect(selectReplyConnection([account], source, "foreign")).toBeUndefined();
    expect(
      selectReplyConnection([other, account], source, other.id),
    ).toBeUndefined();
  });

  it("does not choose revoked or read-only accounts", () => {
    for (const unavailable of [
      { ...account, status: "revoked" },
      {
        ...account,
        granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
      },
    ]) {
      expect(selectReplyConnection([unavailable], source, "")).toBeUndefined();
      expect(
        selectReplyConnection([unavailable], source, account.id),
      ).toBeUndefined();
    }
  });

  it("never invents a recipient from IDs or ambiguous headers", () => {
    expect(replyRecipient(source)).toBe("");
    expect(replyRecipient({ ...source, sender: "person@example.com" })).toBe(
      "person@example.com",
    );
    for (const sender of [
      "Maya <maya@example.com>",
      "a@example.com,b@example.com",
      "a@example.com\nBcc: x@example.com",
    ])
      expect(replyRecipient({ ...source, sender })).toBe("");
    expect(replySubject({ ...source, subject: "Project meeting" })).toBe(
      "Re: Project meeting",
    );
    expect(replySubject({ ...source, subject: "Re: Project meeting" })).toBe(
      "Re: Project meeting",
    );
    expect(
      replySubject({ ...source, subject: "Hello\nBcc: x@example.com" }),
    ).toBe("");
  });

  it("prepares a real source reply bound to the reviewed version and request", () => {
    expect(
      replyPreparationPayload({ current_version: 3 }, account.id, "request-1"),
    ).toEqual({
      expected_version: 3,
      connection_id: account.id,
      request_id: "request-1",
      reply_to_source: true,
    });
    expect(() =>
      replyPreparationPayload({ current_version: 0 }, account.id, "request-1"),
    ).toThrow();
    expect(() =>
      replyPreparationPayload({ current_version: 3 }, "", "request-1"),
    ).toThrow();
  });
});

describe("reviewed manual email", () => {
  const action = {
    commitment_id: "owned",
    payload_hash: "a".repeat(64),
    payload: {
      to: "person@example.com",
      subject: "Meeting",
      body_text: "Friday works.",
    },
  };

  it("requires the same owned request and exact reviewed text", () => {
    expect(
      manualEmailReviewMatches(
        action,
        "owned",
        "person@example.com",
        "Meeting",
        "Friday works.",
      ),
    ).toBe(true);
    expect(
      manualEmailReviewMatches(
        action,
        "foreign",
        "person@example.com",
        "Meeting",
        "Friday works.",
      ),
    ).toBe(false);
    expect(
      manualEmailReviewMatches(
        action,
        "owned",
        "person@example.com",
        "Meeting",
        "An unapproved change",
      ),
    ).toBe(false);
    expect(
      manualEmailReviewMatches(
        { ...action, payload: { ...action.payload, draft_id: "ai-draft" } },
        "owned",
        "person@example.com",
        "Meeting",
        "Friday works.",
      ),
    ).toBe(false);
  });

  it("binds manual approval to its server hash without a fake draft version", () => {
    const payload = manualEmailApprovalPayload(action);
    expect(payload.expected_payload_hash).toBe(action.payload_hash);
    expect(payload.request_id).toBeTruthy();
    expect(payload).not.toHaveProperty("draft_version");
    expect(() =>
      manualEmailApprovalPayload({ payload_hash: "invalid" }),
    ).toThrow();
  });
});

describe("honest send outcomes", () => {
  it("requires completion and a verification timestamp before saying sent", () => {
    expect(
      emailSendOutcome({
        status: "completed",
        verified_at: "2026-10-17T05:00:00Z",
      }).label,
    ).toBe("Sent and verified");
    expect(emailSendOutcome({ status: "completed" }).label).toBe(
      "Needs review",
    );
    expect(
      emailSendOutcome({
        status: "executing",
        verified_at: "2026-10-17T05:00:00Z",
      }).label,
    ).toBe("Sending");
    expect(emailSendOutcome({ status: "uncertain" }).detail).toContain(
      "Do not send another copy",
    );
    expect(emailSendOutcome({ status: "failed" }).detail).toContain(
      "Check the account connection",
    );
    expect(emailSendOutcome({ status: "awaiting_approval" }).detail).toContain(
      "Nothing has been sent",
    );
  });

  it.each([
    "awaiting_approval",
    "approved",
    "executing",
    "uncertain",
    "completed",
  ])("blocks another email while %s has no confirmed outcome", (status) => {
    expect(mayStartAnotherEmail({ status })).toBe(false);
  });

  it("allows another request after verified completion or an explicit terminal failure", () => {
    expect(
      mayStartAnotherEmail({
        status: "completed",
        verified_at: "2026-10-17T05:00:00Z",
      }),
    ).toBe(true);
    expect(mayStartAnotherEmail({ status: "failed" })).toBe(true);
    expect(mayStartAnotherEmail(null)).toBe(true);
  });
});
