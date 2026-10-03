import type {
  AssistantBlock,
  AssistantResponseState,
  CapabilityDecision,
  IntentPlan,
  PlannedIntent,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import { LIMITS } from "./limits";
import {
  isRecord,
  isUuid,
  parseCapabilityDecision,
  parseIntentPlan,
} from "./validate";

const VERIFICATION = new Set([
  "VERIFIED",
  "CORROBORATED",
  "ATTRIBUTED",
  "DEVELOPING",
  "UNCONFIRMED",
  "DISPUTED",
  "CONTRADICTED",
  "RETRACTED",
]);

export interface NewsStoryRecord {
  id: string;
  version: number;
  headline: string;
  description: string | null;
  verification_status: string;
  published_at: string;
  last_updated_at: string;
  retrieved_at: string;
  source_count: number;
  evidence_pending: boolean;
  sources: {
    id: string;
    canonical_url: string;
    source_name: string;
    expires_at: string;
  }[];
}

export interface NewsSummaryRecord {
  headline: string;
  headline_attribution: string;
  as_of: string;
  facts: {
    claim_id: string;
    text: string;
    status: string;
    source_name: string;
    source_url: string;
  }[];
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "News returned information this runtime could not verify.",
  );
}

function bounded(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    unreadable();
  return value;
}

function optional(value: unknown, max: number): string | null {
  if (value === null) return null;
  return bounded(value, max);
}

function timestamp(value: unknown, now: Date, current = false): string {
  const text = bounded(value, 64);
  const parsed = Date.parse(text);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text) || !Number.isFinite(parsed))
    unreadable();
  if (current ? parsed <= now.getTime() : parsed > now.getTime() + 60_000)
    unreadable();
  return text;
}

function httpsUrl(value: unknown): string {
  const text = bounded(value, 2048);
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return unreadable();
  }
  if (
    url.protocol !== "https:" ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.hash
  )
    unreadable();
  return text;
}

function positiveInt(value: unknown, max: number): number {
  if (
    typeof value !== "number" ||
    !Number.isSafeInteger(value) ||
    value < 1 ||
    value > max
  )
    unreadable();
  return value;
}

/** Exact general-headline spans do not need a named topic. This runs only
 * after the qualified planner envelope has passed all scope/authority checks. */
export function normalizeGenericNewsPlan(plan: IntentPlan): IntentPlan {
  const genericSlots = new Set([
    "news",
    "trending",
    "today",
    "headlines",
    "latest news",
    "current news",
    "trending today",
  ]);
  return parseIntentPlan(
    {
      ...plan,
      intents: plan.intents.map((intent) => {
        const question = intent.question
          .toLocaleLowerCase("en-US")
          .replaceAll("’", "'")
          .replace(/^what's\b/, "what is")
          .replace(/[?!.]+$/, "")
          .replace(/\s+/g, " ")
          .trim();
        const generic =
          /^(?:what is trending(?: today| right now)?|what is (?:on|in) the news(?: today)?|what (?:is|are) the (?:latest )?headlines(?: today)?|what is the latest news)$/.test(
            question,
          );
        if (
          !generic ||
          !["news.read", "assistant.clarify"].includes(intent.kind) ||
          intent.reference.kind !== "NONE" ||
          (intent.entity.kind !== "NONE" &&
            (intent.entity.kind !== "TOPIC" ||
              !genericSlots.has(
                intent.entity.value?.toLocaleLowerCase("en-US") ?? "",
              )))
        )
          return intent;
        return {
          ...intent,
          kind: "news.read",
          capability_id: "news.read",
          entity: { kind: "NONE", value: null, confidence: 1 },
          requires_clarification: false,
          clarification: null,
        };
      }),
    },
    { allowBoundTurn: true },
  );
}

/** `undefined` means an ungrounded proposal; `null` means a generic trends request. */
export function newsSelector(intent: PlannedIntent): string | null | undefined {
  if (intent.kind !== "news.read") return undefined;
  if (intent.entity.kind === "NONE") return null;
  const value = intent.entity.value?.trim() ?? "";
  if (
    value.length < 2 ||
    value.length > 200 ||
    !intent.question
      .toLocaleLowerCase("en-US")
      .includes(value.toLocaleLowerCase("en-US"))
  )
    return undefined;
  // The planner may name the medium itself for a generic headlines request.
  // Preserve named people and topics such as News Corp as exact selectors.
  if (
    intent.entity.kind === "TOPIC" &&
    value.toLocaleLowerCase("en-US") === "news"
  )
    return null;
  return value;
}

export function parseNewsStory(payload: unknown, now: Date): NewsStoryRecord {
  if (
    !isRecord(payload) ||
    !isUuid(payload.id) ||
    !Array.isArray(payload.sources)
  )
    unreadable();
  if (payload.sources.length < 1 || payload.sources.length > 100) unreadable();
  const sources = payload.sources.map((source) => {
    if (!isRecord(source) || !isUuid(source.id)) unreadable();
    return {
      id: source.id,
      canonical_url: httpsUrl(source.canonical_url),
      source_name: bounded(source.source_name, 200),
      expires_at: timestamp(source.expires_at, now, true),
    };
  });
  const status = bounded(payload.verification_status, 32);
  if (
    !VERIFICATION.has(status) ||
    typeof payload.evidence_pending !== "boolean"
  )
    unreadable();
  const sourceCount = positiveInt(payload.source_count, 100);
  if (sourceCount > sources.length) unreadable();
  return {
    id: payload.id,
    version: positiveInt(payload.version, 1_000_000),
    headline: bounded(payload.headline, 500),
    description: optional(payload.description, 4000),
    verification_status: status,
    published_at: timestamp(payload.published_at, now),
    last_updated_at: timestamp(payload.last_updated_at, now),
    retrieved_at: timestamp(payload.retrieved_at, now),
    source_count: sourceCount,
    evidence_pending: payload.evidence_pending,
    sources,
  };
}

export function parseNewsFeed(payload: unknown, now: Date): NewsStoryRecord[] {
  if (!Array.isArray(payload) || payload.length > 50) unreadable();
  const seen = new Set<string>();
  return payload.map((item) => {
    const story = parseNewsStory(item, now);
    if (seen.has(story.id)) unreadable();
    seen.add(story.id);
    return story;
  });
}

export function namedNewsMatches(
  stories: NewsStoryRecord[],
  selector: string,
): NewsStoryRecord[] {
  const term = selector.toLocaleLowerCase("en-US");
  return stories.filter((story) =>
    `${story.headline} ${story.description ?? ""}`
      .toLocaleLowerCase("en-US")
      .includes(term),
  );
}

export function parseNewsSummary(
  payload: unknown,
  story: NewsStoryRecord,
  now: Date,
): NewsSummaryRecord {
  if (
    !isRecord(payload) ||
    payload.status !== "READY" ||
    payload.actions_executed !== false
  )
    unreadable();
  if (
    payload.headline_status !== "ATTRIBUTED" ||
    !Array.isArray(payload.sections)
  )
    unreadable();
  if (payload.sections.length < 1 || payload.sections.length > 4) unreadable();
  const sourceNames = new Map(
    story.sources.map((source) => [source.canonical_url, source.source_name]),
  );
  const headlineUrl = httpsUrl(payload.headline_source_url);
  const headlineAttribution = bounded(payload.headline_attribution, 200);
  if (sourceNames.get(headlineUrl) !== headlineAttribution) unreadable();
  const facts: NewsSummaryRecord["facts"] = [];
  for (const section of payload.sections) {
    if (
      !isRecord(section) ||
      !Array.isArray(section.facts) ||
      section.facts.length > 20
    )
      unreadable();
    if (
      typeof section.heading !== "string" ||
      ![
        "what_happened",
        "why_it_matters",
        "what_is_unclear",
        "latest_development",
      ].includes(section.heading)
    )
      unreadable();
    for (const fact of section.facts) {
      if (!isRecord(fact) || !isUuid(fact.claim_id)) unreadable();
      const status = bounded(fact.status, 32);
      const sourceUrl = httpsUrl(fact.source_url);
      const sourceName = bounded(fact.source_name, 200);
      if (
        !VERIFICATION.has(status) ||
        sourceNames.get(sourceUrl) !== sourceName
      )
        unreadable();
      facts.push({
        claim_id: fact.claim_id,
        text: bounded(fact.text, 2000),
        status,
        source_name: sourceName,
        source_url: sourceUrl,
      });
    }
  }
  if (facts.length < 1 || facts.length > 40) unreadable();
  const asOf = timestamp(payload.as_of, now);
  if (Date.parse(asOf) < Date.parse(story.last_updated_at)) unreadable();
  return {
    headline: bounded(payload.headline, 500),
    headline_attribution: headlineAttribution,
    as_of: asOf,
    facts,
  };
}

function decision(
  state: AssistantResponseState,
  reason: string,
): CapabilityDecision {
  return parseCapabilityDecision({
    kind: state === "READY" ? "DELEGATE" : "CLARIFY",
    capability_id: "news.read",
    target: "news.trending",
    reason,
    requires_approval: false,
    action_state: "NONE",
    action_id: null,
    response_state: state,
  });
}

function storyItem(story: NewsStoryRecord): AssistantBlock {
  const title =
    story.headline.length > 300
      ? `${story.headline.slice(0, 299)}…`
      : story.headline;
  return {
    kind: "ITEM",
    item: {
      id: story.id,
      type: "NEWS_STORY",
      title,
      description:
        story.description === null
          ? null
          : story.description.length > 1000
            ? `${story.description.slice(0, 999)}…`
            : story.description,
      status: story.verification_status,
      due_at: null,
      band: "TRENDING",
      sources: [
        {
          provider: "navox",
          source_type: "NEWS_STORY",
          external_resource_id: null,
          evidence_id: story.id,
          connection_id: null,
          observed_at: story.retrieved_at,
        },
      ],
    },
  };
}

export function newsTrends(stories: NewsStoryRecord[]): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  if (stories.length === 0) {
    return {
      state: "CLARIFY",
      decision: decision("CLARIFY", "news.no_current_trends"),
      blocks: [
        {
          kind: "ANSWER",
          text: "No current stories are available in your News feed. Try again later.",
        },
      ],
    };
  }
  return {
    state: "READY",
    decision: decision("READY", "news.trending"),
    blocks: [
      {
        kind: "ANSWER",
        text: "Here are a few headlines drawing attention in your news sources. Trending doesn’t mean a report is confirmed.",
      },
      ...stories.slice(0, 3).map(storyItem),
    ],
  };
}

export function clarifyNews(matches: NewsStoryRecord[]): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  return {
    state: "CLARIFY",
    decision: decision(
      "CLARIFY",
      matches.length ? "news.ambiguous" : "news.no_match",
    ),
    blocks: [
      {
        kind: "ANSWER",
        text: matches.length
          ? `I found ${matches.length} current stories about that name. Which story did you mean?`
          : "I could not find a current trending story for that name. Can you be more specific?",
      },
      ...matches.slice(0, 5).map(storyItem),
    ],
  };
}

export function answerNews(
  story: NewsStoryRecord,
  summary: NewsSummaryRecord,
): {
  state: AssistantResponseState;
  decision: CapabilityDecision;
  blocks: AssistantBlock[];
} {
  const shown = summary.facts.slice(0, LIMITS.maxDetails);
  const detailLines = shown.map((fact) => {
    const prefix = `${fact.status}: `;
    const suffix = ` — ${fact.source_name}`;
    const available = 400 - prefix.length - suffix.length;
    const excerpt =
      fact.text.length > available
        ? `${fact.text.slice(0, available - 1)}…`
        : fact.text;
    return `${prefix}${excerpt}${suffix}`;
  });
  const more =
    summary.facts.length > shown.length
      ? ` Showing ${shown.length} of ${summary.facts.length} source-backed facts.`
      : "";
  return {
    state: "READY",
    decision: decision("READY", "news.story_summary"),
    blocks: [
      {
        kind: "ANSWER",
        text: `${summary.headline} — attributed to ${summary.headline_attribution}. Story verification: ${story.verification_status}. Trending activity does not verify the headline.${more}`,
      },
      storyItem(story),
      {
        kind: "DETAILS",
        lines: detailLines,
      },
      {
        kind: "CITATIONS",
        citations: shown.map((fact) => ({
          provider: "navox",
          source_type: "NEWS_CLAIM",
          external_resource_id:
            fact.source_url.length <= 512 ? fact.source_url : null,
          evidence_id: fact.claim_id,
          connection_id: null,
          observed_at: summary.as_of,
        })),
      },
    ],
  };
}
