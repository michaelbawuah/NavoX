import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  CommunicationDrafts,
  draftApprovalPayload,
  draftReviewMatches,
} from "./communication-drafts";

const draft = {
  id: "draft-1",
  commitment_id: "commitment-1",
  source_id: "source-1",
  current_version: 3,
  status: "awaiting_approval",
  action_id: "action-1",
  versions: [],
};
const action = {
  id: "action-1",
  status: "awaiting_approval",
  payload_hash: "a".repeat(64),
  result: {},
  payload: {
    sender: "owner@example.com",
    to: "maya@example.com",
    subject: "Friday",
    body_text: "Would Friday work?",
    draft_id: "draft-1",
    draft_version: 3,
  },
};

describe("versioned draft approval", () => {
  it("binds the exact reviewed hash and version", () => {
    const payload = draftApprovalPayload(action);
    expect(payload.expected_payload_hash).toBe(action.payload_hash);
    expect(payload.draft_version).toBe(3);
    expect(payload.request_id).toBeTruthy();
    expect(
      draftReviewMatches(
        draft,
        action,
        "maya@example.com",
        "Friday",
        "Would Friday work?",
      ),
    ).toBe(true);
  });
  it.each([
    ["other@example.com", "Friday", "Would Friday work?"],
    ["maya@example.com", "Different subject", "Would Friday work?"],
    ["maya@example.com", "Friday", "An unapproved edit"],
  ])("blocks changed recipient or text", (to, subject, body) => {
    expect(draftReviewMatches(draft, action, to, subject, body)).toBe(false);
  });
  it("rejects a different draft or an earlier approval", () => {
    for (const changed of [
      { ...draft, current_version: 4 },
      { ...draft, id: "other" },
    ]) {
      expect(
        draftReviewMatches(
          changed,
          action,
          "maya@example.com",
          "Friday",
          "Would Friday work?",
        ),
      ).toBe(false);
    }
    expect(() =>
      draftApprovalPayload({ ...action, payload_hash: "" }),
    ).toThrow();
  });
  it("cannot generate or send when no source is available", () => {
    const markup = renderToStaticMarkup(
      createElement(CommunicationDrafts, {
        commitments: [{ id: "commitment-1", title: "Reply to Maya" }],
        connections: [],
        paused: false,
        onStateChanged: vi.fn(),
      }),
    );
    expect(markup).toContain("no readable Gmail message");
    expect(markup).toMatch(/disabled=""[^>]*>Generate draft/);
    expect(markup).not.toContain("Approve and send now");
  });
});
