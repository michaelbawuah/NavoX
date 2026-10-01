import {
  AssistantError,
  assertSameOriginMutation,
  toAssistantError,
} from "@navox/assistant-runtime";
import type { AssistantErrorResponse } from "@navox/contracts";
import { assistantOrigin } from "./assistant-server";

const NO_STORE = { "cache-control": "no-store" } as const;

/** Same-origin plus JSON enforcement for every mutating assistant route. */
export function guardAssistantMutation(request: Request): void {
  assertSameOriginMutation({
    method: request.method,
    origin: request.headers.get("origin"),
    requestUrl: request.url,
    secFetchSite: request.headers.get("sec-fetch-site"),
    contentType: request.headers.get("content-type"),
    configuredOrigin: assistantOrigin(),
  });
}

export function sessionCookie(request: Request): string {
  const cookie = request.headers.get("cookie") ?? "";
  if (!cookie)
    throw new AssistantError("unauthorized", "Sign in to use the assistant.");
  return cookie;
}

export async function assistantJsonBody(request: Request): Promise<unknown> {
  try {
    return await request.json();
  } catch {
    throw new AssistantError(
      "invalid_request",
      "A JSON request body is required.",
    );
  }
}

/** One error shape for every assistant route; nothing leaks upstream detail. */
export async function assistantResponse(
  run: () => Promise<Response>,
): Promise<Response> {
  try {
    const response = await run();
    for (const [key, value] of Object.entries(NO_STORE)) {
      response.headers.set(key, value);
    }
    return response;
  } catch (error) {
    const failure = toAssistantError(error);
    const body: AssistantErrorResponse = {
      error: {
        code: failure.code,
        message: failure.message,
        retryable: failure.retryable,
      },
    };
    return Response.json(body, { status: failure.status, headers: NO_STORE });
  }
}
