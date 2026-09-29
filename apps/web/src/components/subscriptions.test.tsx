import type {
  CancellationAttempt,
  RecurringSubscription,
  SubscriptionInput,
  SubscriptionQueryResult,
} from "@navox/contracts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  canConfirmCancellation,
  cancellationLabel,
  safeManagementUrl,
  subscriptionAttention,
  subscriptionCorrections,
  subscriptionMoney,
} from "../lib/subscriptions";
import { CancellationReview } from "./cancellation-review";
import { NavoXAskBox } from "./navox-ui";
import { SubscriptionEditor } from "./subscription-editor";
import {
  SubscriptionQueryResultView,
  subscriptionQueryFromQuestion,
} from "./subscriptions-ask";

const now = Date.parse("2026-09-25T15:00:00Z");
const item = {
  id: "subscription",
  status: "ACTIVE",
  review_state: "KEPT",
  review_after: null,
  attention_reasons: [],
  trial_ends_at: null,
  next_renewal_at: "2026-09-26T12:00:00Z",
} as unknown as RecurringSubscription;

const attempt: CancellationAttempt = {
  id: "attempt",
  obligation_id: "subscription",
  status: "AWAITING_CONFIRMATION",
  method: "CONNECTED_PROVIDER_ACTION",
  verification_status: "NOT_VERIFIED",
  payload_hash: "a".repeat(64),
  expires_at: "2026-09-25T15:05:00Z",
  failure_code: null,
  preview: {
    target: {
      obligation_id: "subscription",
      workspace_id: "workspace",
      user_id: "owner",
      connection_id: "connection",
      external_resource_id: "provider-subscription",
      merchant_id: "merchant",
      merchant_domain: "example.test",
      name: "Example service",
      plan_name: "Old plan",
      billing_amount: "10.00",
      billing_currency: "USD",
      billing_interval: "MONTH",
      billing_interval_count: 1,
      next_renewal_at: "2026-10-01T15:00:00Z",
      auto_renew: true,
      revision: "1",
    },
    economics: {
      plan_name: "Current provider plan",
      billing_amount: "24.99",
      billing_currency: "USD",
      billing_interval: "MONTH",
      billing_interval_count: 1,
      next_renewal_at: "2026-10-05T15:00:00Z",
    },
    method: "CONNECTED_PROVIDER_ACTION",
    provider_revision: "r1",
    provider_state: "active",
    payload: {},
    expected_effect: "Stop the next renewal.",
    access_ends_at: null,
    fee: null,
    refund: null,
    management_url: "https://example.test/account",
    warnings: [],
    inspected_at: "2026-09-25T15:00:00Z",
    expires_at: "2026-09-25T15:05:00Z",
  },
};

describe("subscription decisions", () => {
  it.each([
    [null, ""],
    ["USD", "USD"],
    ["GHS", "GHS"],
    ["DEM", "DEM"],
  ])(
    "preserves the saved currency %s when opening corrections",
    (saved, expected) => {
      const markup = renderToStaticMarkup(
        createElement(SubscriptionEditor, {
          item: { ...item, billing_currency: saved },
          onSave: vi.fn(),
          onClose: vi.fn(),
        }),
      );
      const currency = markup.match(
        /<select name="billing_currency"[^>]*>[\s\S]*?<\/select>/,
      )?.[0];
      expect(currency).toBeDefined();
      expect(currency).toContain(`<option value="${expected}" selected="">`);
    },
  );
  it("records only changed form fields as user corrections", () => {
    const original = {
      ...item,
      name: "Evidence name",
      billing_amount: "10.0000",
    };
    const input = {
      name: "Evidence name",
      billing_amount: "12.0000",
    } as SubscriptionInput;
    expect(subscriptionCorrections(original, input)).toEqual({
      billing_amount: "12.0000",
    });
  });
  it("keeps unchanged renewal warnings suppressed but displays material server facts", () => {
    expect(subscriptionAttention(item, now)).toEqual([]);
    expect(
      subscriptionAttention(
        { ...item, attention_reasons: ["obligation.price_changed"] },
        now,
      ),
    ).toEqual(["Price changed"]);
    expect(
      subscriptionAttention({ ...item, status: "CANCEL_PENDING" }, now),
    ).toEqual(["Cancellation not verified"]);
  });
  it("never offers confirmation without acceptance, a current preview and an executable method", () => {
    expect(canConfirmCancellation(attempt, false, now)).toBe(false);
    expect(canConfirmCancellation(attempt, true, now)).toBe(true);
    expect(canConfirmCancellation(attempt, true, now + 600_000)).toBe(false);
    expect(
      canConfirmCancellation({ ...attempt, method: "UNSUPPORTED" }, true, now),
    ).toBe(false);
    expect(
      canConfirmCancellation({ ...attempt, status: "SUBMITTED" }, true, now),
    ).toBe(false);
  });
  it("does not label submitted or contradicted cancellations as successful", () => {
    expect(cancellationLabel({ ...attempt, status: "SUBMITTED" })).toContain(
      "not yet verified",
    );
    expect(
      cancellationLabel({
        ...attempt,
        status: "VERIFIED_CANCELLED",
        verification_status: "CONTRADICTED",
      }),
    ).toContain("contradicts");
  });
  it("shows freshly inspected provider economics and leaves confirmation unchecked", () => {
    const markup = renderToStaticMarkup(
      createElement(CancellationReview, {
        attempt,
        busy: false,
        error: "",
        onConfirm: vi.fn(),
        onAbort: vi.fn(),
        onRefresh: vi.fn(),
        onClose: vi.fn(),
      }),
    );
    expect(markup).toContain("Current provider plan");
    expect(markup).toContain("24.99");
    expect(markup).not.toContain("Old plan");
    expect(markup).not.toContain("10.00");
    expect(markup).not.toContain('checked=""');
    expect(markup).toMatch(
      /disabled=""[^>]*type="button">Confirm this exact cancellation/,
    );
  });
  it("keeps unknown money distinct from a free price and rejects credential-bearing links", () => {
    expect(subscriptionMoney(null, "USD")).toBe("Unknown cost");
    expect(subscriptionMoney("0", "USD")).toContain("0.00");
    expect(
      safeManagementUrl("https://user:secret@example.test/cancel"),
    ).toBeNull();
    expect(safeManagementUrl("javascript:alert(1)")).toBeNull();
    expect(safeManagementUrl("https://example.test/account")).toBe(
      "https://example.test/account",
    );
  });

  it("maps plain subscription questions to bounded read-only query intents", () => {
    expect(
      subscriptionQueryFromQuestion("Which trials are ending?").intent,
    ).toBe("TRIALS");
    expect(
      subscriptionQueryFromQuestion("What renews next month?").intent,
    ).toBe("UPCOMING");
    expect(
      subscriptionQueryFromQuestion("What is my monthly cost?").intent,
    ).toBe("SUMMARY");
    expect(subscriptionQueryFromQuestion("  Notion AI  ")).toMatchObject({
      intent: "SEARCH",
      text: "Notion AI",
      days: 30,
      currency: null,
    });
    expect(subscriptionQueryFromQuestion("What about Notion AI?").text).toBe(
      "Notion AI",
    );
    expect(
      subscriptionQueryFromQuestion("What subscriptions do I have?").text,
    ).toBe("");
  });

  it("honors explicit renewal windows without silently broadening them", () => {
    expect(subscriptionQueryFromQuestion("What renews in 7 days?").days).toBe(
      7,
    );
    expect(subscriptionQueryFromQuestion("What renews in 2 weeks?").days).toBe(
      14,
    );
    expect(subscriptionQueryFromQuestion("What renews next week?").days).toBe(
      7,
    );
    expect(subscriptionQueryFromQuestion("What renews tomorrow?").days).toBe(1);
    for (const question of [
      "Renewals in 0 days",
      "Renewals in 367 days",
      "Renewals in 999 weeks",
    ])
      expect(() => subscriptionQueryFromQuestion(question)).toThrow("1 to 366");
  });

  it("renders a shared accessible Ask NavoX control with a disabled empty submit", () => {
    const html = renderToStaticMarkup(
      createElement(NavoXAskBox, {
        id: "subscription-test-question",
        label: "Ask about subscriptions",
        value: "",
        placeholder: "What renews soon?",
        hint: "Read-only registry question.",
        onChange: vi.fn(),
        onSubmit: vi.fn(),
      }),
    );
    expect(html).toContain('for="subscription-test-question"');
    expect(html).toContain('id="subscription-test-question"');
    expect(html).toContain("Read-only registry question.");
    expect(html).toContain(
      'aria-describedby="subscription-test-question-hint"',
    );
    expect(html).toContain('id="subscription-test-question-hint"');
    expect(html).toMatch(/<button[^>]*disabled=""/);
  });

  it("renders subscription answers without turning a question into an action", () => {
    const result: SubscriptionQueryResult = {
      intent: "UPCOMING",
      summary: null,
      subscriptions: [
        {
          ...item,
          name: "Example service",
          billing_amount: "24.99",
          billing_currency: "USD",
        } as RecurringSubscription,
      ],
    };
    const html = renderToStaticMarkup(
      createElement(SubscriptionQueryResultView, {
        result,
        onOpen: vi.fn(),
      }),
    );
    expect(html).toContain("Upcoming renewals");
    expect(html).toContain("Example service");
    expect(html).toContain("24.99");
    expect(html).toContain("Open details");
    expect(html).not.toContain("Confirm cancellation");
    expect(html).not.toContain("Cancel subscription");
  });
});
