import type { IntentPlan } from "@navox/contracts";
import { parseIntentPlan } from "./validate";

export type NewsRegion = "world" | "us";

function normalized(text: string): string {
  return text
    .toLocaleLowerCase("en-US")
    .replaceAll("’", "'")
    .replace(/^what's\b/, "what is")
    .replace(/[?!.]+$/, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Whole, informational requests only. These reads never confer action authority. */
export function upcomingSubscriptionsRequest(text: string): boolean {
  const question = normalized(text);
  return (
    /^(?:(?:do i have|are there|any|what|which|show(?: me)?|list(?: my)?|tell me about|check(?: my)?) )?(?:any |my |the )?subscriptions? (?:are |is )?(?:renewing|renew|due|coming up|upcoming)(?: soon| next| in the next 30 days| this month)?$/.test(
      question,
    ) ||
    /^(?:show(?: me)?|list|what are|do i have|any) (?:my |any )?(?:upcoming|next|soon-to-renew) subscriptions?$/.test(
      question,
    )
  );
}

export function broadNewsRequest(text: string): { region?: NewsRegion } | null {
  const question = normalized(text);
  const broad =
    /^(?:what is (?:happening|going on)(?: (?:around|in|across) (?:the )?(?:world|us|usa|united states))?|(?:show(?: me)?|give me|tell me|what (?:is|are)) (?:the )?(?:latest |current |top )?(?:world |global |international |us |u\.s\. |american )?(?:news|headlines)|what is trending)(?: today| right now| this morning)?$/.test(
      question,
    );
  if (!broad) return null;
  if (/\b(?:world|global|international)\b/.test(question))
    return { region: "world" };
  if (/\b(?:us|usa|united states|american)\b|u\.s\./.test(question))
    return { region: "us" };
  return {};
}

/** A conservative read shortcut avoids making a merchant/topic mandatory.
 * Compound requests, named topics, follow-ups and writes stay with the planner. */
export function broadReadPlan(text: string): IntentPlan | null {
  const kind = upcomingSubscriptionsRequest(text)
    ? "subscription.search"
    : broadNewsRequest(text) !== null
      ? "news.read"
      : null;
  if (kind === null) return null;
  return parseIntentPlan({
    version: 1,
    intents: [
      {
        kind,
        capability_id: kind,
        question: text,
        confidence: 1,
        entity: { kind: "NONE", value: null, confidence: 1 },
        requires_clarification: false,
        clarification: null,
      },
    ],
  });
}
