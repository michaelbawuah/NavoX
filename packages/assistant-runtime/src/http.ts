import { AssistantError } from "./errors";

export interface MutationGuardInput {
  method: string;
  origin: string | null;
  /**
   * Server-derived request URL. The expected origin comes from here (or from
   * configuration) and never from a client-supplied host header.
   */
  requestUrl: string;
  secFetchSite: string | null;
  contentType: string | null;
  configuredOrigin: string | null;
  /**
   * The exact request content type this route accepts. Mutating assistant
   * routes take JSON; the raw speech route takes a WAV container. Defaults to
   * `application/json`; a media-type parameter is ignored on both sides.
   */
  expectedContentType?: string;
  /** Bounded refusal copy for a wrong content type. Never echoes the body. */
  contentTypeMessage?: string;
}

const FORBIDDEN = "This request must come from the NavoX app.";

/**
 * Normalised `scheme://host[:port]` for http(s) URLs, or null for anything
 * malformed or non-http. `URL.origin` folds default ports, so a scheme, host or
 * port difference is always visible as a string difference.
 */
export function httpOriginOf(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value.length === 0) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname) return null;
  return url.origin;
}

/**
 * Mutating assistant routes require the exact same origin (scheme, host and
 * port) and a JSON body. The expected origin is configuration when present,
 * otherwise the server-derived request URL; a client-supplied host header is
 * never treated as authority.
 */
export function assertSameOriginMutation(input: MutationGuardInput): void {
  const method = input.method.toUpperCase();
  if (method === "GET" || method === "HEAD" || method === "OPTIONS") return;

  const contentType =
    (input.contentType ?? "").split(";")[0]?.trim().toLowerCase() ?? "";
  const expectedContentType = (input.expectedContentType ?? "application/json")
    .split(";")[0]
    ?.trim()
    .toLowerCase();
  if (contentType !== expectedContentType) {
    throw new AssistantError(
      "invalid_request",
      input.contentTypeMessage ?? "A JSON request body is required.",
    );
  }

  const expected = input.configuredOrigin
    ? httpOriginOf(input.configuredOrigin)
    : httpOriginOf(input.requestUrl);
  if (!expected) {
    throw new AssistantError(
      "misconfigured",
      "The assistant origin is not configured as an http(s) origin.",
    );
  }
  const actual = httpOriginOf(input.origin);
  if (!actual || actual !== expected) {
    throw new AssistantError("forbidden", FORBIDDEN);
  }
  if (
    input.secFetchSite !== null &&
    input.secFetchSite !== "same-origin" &&
    input.secFetchSite !== "none"
  ) {
    throw new AssistantError("forbidden", FORBIDDEN);
  }
}
