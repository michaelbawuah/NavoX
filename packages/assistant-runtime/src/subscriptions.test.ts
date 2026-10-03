import { describe, expect, it } from "vitest";
import { LIMITS } from "./limits";
import { buildPresentationPlan } from "./presentation";
import {
  answerSubscription,
  answerUpcomingSubscriptions,
  parseSubscriptionCancellation,
  parseSubscriptionSearch,
  subscriptionSelector,
} from "./subscriptions";

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const DATE = "2026-10-15T12:00:00Z";
const row = {
  id: ID,
  revision: 1,
  name: "Netflix",
  plan_name: "Premium",
  status: "ACTIVE",
  next_renewal_at: DATE,
  last_verified_at: null,
  updated_at: "2026-09-30T12:00:00Z",
};
const scope = {
  user_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
};

function attempt(status: string, verification_status: string) {
  return {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    obligation_id: ID,
    status,
    verification_status,
    preview: {
      target: { obligation_id: ID, revision: "1", ...scope },
      access_ends_at: DATE,
    },
  };
}

describe("read-only subscription evidence", () => {
  it("speaks the next three saved renewal names and recorded dates", () => {
    const now = new Date("2026-10-01T12:00:00Z");
    const subscriptions = [
      {
        ...row,
        id: "1aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name: "Netflix",
        next_renewal_at: "2026-10-15T12:00:00Z",
      },
      {
        ...row,
        id: "2aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name: "Adobe",
        next_renewal_at: "2026-10-04T12:00:00Z",
      },
      {
        ...row,
        id: "3aaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name: "Spotify",
        next_renewal_at: "2026-10-20T12:00:00Z",
      },
    ];
    const answer = answerUpcomingSubscriptions(
      parseSubscriptionSearch({ intent: "UPCOMING", subscriptions }, null, now),
    );
    const presentation = buildPresentationPlan({
      modality: "VOICE",
      delivery: "AUTOMATIC",
      decision: answer.decision,
      blocks: answer.blocks,
    });
    expect(presentation.speak).toBe(true);
    for (const subscription of subscriptions) {
      expect(presentation.speech_text).toContain(subscription.name);
      expect(presentation.speech_text).toContain(subscription.next_renewal_at);
    }
    expect(presentation.speech_text!.length).toBeLessThanOrEqual(
      LIMITS.maxSpeechLength,
    );
    expect(presentation.speech_text!.indexOf("Adobe")).toBeLessThan(
      presentation.speech_text!.indexOf("Netflix"),
    );
    expect(answer.blocks.filter((block) => block.kind === "ITEM")).toHaveLength(
      3,
    );
    expect(answer.decision.action_state).toBe("NONE");
  });
  it("answers a next-30-days aggregate without implying complete account coverage", () => {
    const now = new Date("2026-10-01T12:00:00Z");
    const rows = parseSubscriptionSearch(
      { intent: "UPCOMING", subscriptions: [row] },
      null,
      now,
    );
    const answer = answerUpcomingSubscriptions(rows);
    expect(answer.state).toBe("READY");
    expect(answer.blocks[0]).toMatchObject({
      text: expect.stringContaining("next 30 days"),
    });
    expect(answer.decision.action_state).toBe("NONE");
    expect(answerUpcomingSubscriptions([]).blocks[0]).toMatchObject({
      text: expect.stringContaining("you’ve added or NavoX has found"),
    });
    for (const next_renewal_at of [
      null,
      "2026-09-30T12:00:00Z",
      "2026-12-01T12:00:00Z",
    ]) {
      expect(() =>
        parseSubscriptionSearch(
          { intent: "UPCOMING", subscriptions: [{ ...row, next_renewal_at }] },
          null,
          now,
        ),
      ).toThrow();
    }
    expect(() =>
      parseSubscriptionSearch(
        {
          intent: "UPCOMING",
          subscriptions: [{ ...row, status: "CANCELLED" }],
        },
        null,
        now,
      ),
    ).toThrow();
  });
  it("only searches an entity copied from the user question", () => {
    const intent = {
      kind: "subscription.search",
      question: "When does Netflix renew?",
      entity: { kind: "ORGANIZATION", value: "Netflix", confidence: 0.9 },
    } as Parameters<typeof subscriptionSelector>[0];
    expect(subscriptionSelector(intent)).toBe("Netflix");
    expect(
      subscriptionSelector({
        ...intent,
        entity: { ...intent.entity, value: "Spotify" },
      }),
    ).toBeNull();
  });

  it("rejects changed, duplicate or malformed source records", () => {
    expect(
      parseSubscriptionSearch(
        { intent: "SEARCH", subscriptions: [row] },
        "Netflix",
      ),
    ).toHaveLength(1);
    expect(() =>
      parseSubscriptionSearch(
        { intent: "SEARCH", subscriptions: [row, row] },
        "Netflix",
      ),
    ).toThrow();
    expect(() =>
      parseSubscriptionSearch(
        { intent: "SEARCH", subscriptions: [{ ...row, name: "Other" }] },
        "Netflix",
      ),
    ).toThrow();
    expect(() =>
      parseSubscriptionSearch(
        {
          intent: "SEARCH",
          subscriptions: [{ ...row, next_renewal_at: "tomorrow" }],
        },
        "Netflix",
      ),
    ).toThrow();
  });

  it("separates verified cancellation, recorded renewal and preview access end", () => {
    const [subscription] = parseSubscriptionSearch(
      { intent: "SEARCH", subscriptions: [row] },
      "Netflix",
    );
    expect(subscription).toBeDefined();
    if (!subscription) return;
    const pending = parseSubscriptionCancellation(
      attempt("VERIFICATION_PENDING", "VERIFICATION_PENDING"),
      subscription,
      scope,
    );
    const pendingAnswer = answerSubscription(subscription, pending);
    expect(pendingAnswer.blocks[0]).toMatchObject({
      text: expect.stringContaining("Cancellation is not verified"),
    });
    expect(JSON.stringify(pendingAnswer.blocks)).toContain(
      "Provider preview estimated access end",
    );
    expect(JSON.stringify(pendingAnswer.blocks)).toContain(
      "Access end date: unknown",
    );
    const verified = parseSubscriptionCancellation(
      attempt("VERIFIED_CANCELLED", "VERIFIED_CANCELLED"),
      subscription,
      scope,
    );
    expect(answerSubscription(subscription, verified).blocks[0]).toMatchObject({
      text: expect.stringContaining("cancellation is verified"),
    });
    const noRenewal = answerSubscription(
      { ...subscription, next_renewal_at: null },
      null,
    );
    expect(noRenewal.blocks[0]).toMatchObject({
      text: expect.stringContaining("No renewal date is recorded"),
    });
    expect(() =>
      parseSubscriptionCancellation(
        {
          ...attempt("VERIFIED_CANCELLED", "VERIFIED_CANCELLED"),
          preview: {
            target: {
              obligation_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              revision: "1",
              ...scope,
            },
            access_ends_at: DATE,
          },
        },
        subscription,
        scope,
      ),
    ).toThrow();
    expect(() =>
      parseSubscriptionCancellation(
        attempt("VERIFIED_CANCELLED", "VERIFIED_CANCELLED"),
        { ...subscription, revision: 2 },
        scope,
      ),
    ).toThrow();
  });
});
