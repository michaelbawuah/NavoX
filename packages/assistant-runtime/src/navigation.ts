import type { AssistantBlock, AssistantCitation } from "@navox/contracts";
import { AssistantError } from "./errors";
import { isRecord, isUuid } from "./validate";

/**
 * The exact source types this runtime may turn into a guarded navigation link.
 * Canvas is deferred from this release, so it is deliberately absent.
 */
export const NAVIGATION_SOURCE_TYPES = [
  "EMAIL",
  "EMAIL_THREAD",
  "CLASS_MEETING",
] as const;

export type NavigationSourceType = (typeof NAVIGATION_SOURCE_TYPES)[number];

/** One item an earlier saved turn already cited and may offer to open. */
export interface NavigationTarget {
  item_id: string;
  source_type: NavigationSourceType;
  label: string;
  /** Required by the class-navigation authority; null for a knowledge item. */
  connection_id: string | null;
  /** Selectors the saved item already carried; re-checked at click time. */
  citations: AssistantCitation[];
}

function unverifiable(): never {
  throw new AssistantError(
    "unavailable",
    "The navigation target could not be verified.",
  );
}

/**
 * The same-origin guarded path one navigation block links to. The browser
 * never builds this and never navigates an external URL directly; the route it
 * names re-checks session ownership and the current source authority.
 */
export function navigationHref(
  sessionId: string,
  turnId: string,
  itemId: string,
): string {
  const query = new URLSearchParams({
    session_id: sessionId,
    turn_id: turnId,
    item_id: itemId,
  });
  return `/api/v1/assistant/navigation?${query.toString()}`;
}

/**
 * The eligible targets of one already-saved presentation. Only an exact cited
 * item or an exact class-navigation block counts; anything else is not a
 * navigation target, so an ambiguous turn asks the operator instead of
 * guessing.
 */
export function navigationTargets(
  blocks: readonly AssistantBlock[],
): NavigationTarget[] {
  const targets: NavigationTarget[] = [];
  for (const block of blocks) {
    if (block.kind === "ITEM") {
      const item = block.item;
      const cited = item.sources.find(
        (source) =>
          source.evidence_id === item.id && source.source_type === item.type,
      );
      if (
        (item.type === "EMAIL" || item.type === "EMAIL_THREAD") &&
        isUuid(item.id) &&
        cited
      ) {
        targets.push({
          item_id: item.id,
          source_type: item.type,
          label: item.type === "EMAIL" ? "Open that email" : "Open that thread",
          connection_id: cited.connection_id,
          citations: item.sources,
        });
      }
    } else if (
      block.kind === "CLASS_NAVIGATION" &&
      // Canvas is deferred from this release: only the Google Calendar class
      // target may become an openable link. A Canvas block stays unactionable.
      block.label === "Open Calendar"
    ) {
      targets.push({
        item_id: block.resource_id,
        source_type: "CLASS_MEETING",
        label: block.label,
        connection_id: block.connection_id,
        citations: [],
      });
    }
  }
  return targets;
}

/**
 * The expected Google hosts for each openable source. This release opens only
 * Google mail and Google Calendar targets; a foreign host fails closed even
 * when the owning service returned it, so a redirect can never become an
 * unexpected destination.
 */
function hostAllowed(url: URL, sourceType: NavigationSourceType): boolean {
  if (sourceType === "CLASS_MEETING") {
    return (
      url.hostname === "calendar.google.com" ||
      (url.hostname === "www.google.com" &&
        url.pathname.startsWith("/calendar/"))
    );
  }
  return url.hostname === "mail.google.com";
}

/**
 * One verified external URL. Only https, never with embedded credentials,
 * never longer than the SPEC-007 bound, and only on the expected Google host
 * for the exact source type.
 */
export function parseSafeNavigationUrl(
  value: unknown,
  sourceType: NavigationSourceType,
): string {
  if (typeof value !== "string" || value.length === 0 || value.length > 2048) {
    unverifiable();
  }
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    unverifiable();
  }
  if (url.protocol !== "https:" || url.username || url.password) unverifiable();
  if (!hostAllowed(url, sourceType)) unverifiable();
  return url.href;
}

/** Validates one SPEC-007 resource detail's exact verified location. */
export function parseResourceNavigation(
  payload: unknown,
  expected: { resource_id: string; source_type: NavigationSourceType },
): { url: string } {
  if (!isRecord(payload)) unverifiable();
  if (
    payload.resource_id !== expected.resource_id ||
    payload.source_type !== expected.source_type
  ) {
    unverifiable();
  }
  return {
    url: parseSafeNavigationUrl(payload.canonical_url, expected.source_type),
  };
}

/** Validates the exact `{ url }` envelope the class-navigation route returns. */
export function parseClassNavigationTarget(payload: unknown): { url: string } {
  if (!isRecord(payload) || !("url" in payload)) unverifiable();
  return { url: parseSafeNavigationUrl(payload.url, "CLASS_MEETING") };
}
