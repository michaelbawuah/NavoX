import { AssistantError } from "@navox/assistant-runtime";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * The click-time half of a guarded navigation link.
 *
 * The browser supplies only the session, turn and item selectors this runtime
 * generated. Ownership is re-derived from the cookie, the cited item is
 * re-found in that saved turn, and the current source authority is re-checked
 * before any redirect. A missing, revoked, stale or mismatched target fails
 * closed and never produces a Location header.
 */
export async function GET(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    const cookie = sessionCookie(request);
    const query = new URL(request.url).searchParams;
    const sessionId = query.get("session_id");
    const turnId = query.get("turn_id");
    const itemId = query.get("item_id");
    if (
      typeof sessionId !== "string" ||
      typeof turnId !== "string" ||
      typeof itemId !== "string"
    ) {
      throw new AssistantError(
        "invalid_request",
        "A navigation target is required.",
      );
    }
    const target = await getAssistantRuntime().resolveNavigationTarget({
      cookie,
      session_id: sessionId,
      turn_id: turnId,
      item_id: itemId,
    });
    return new Response(null, {
      status: 303,
      headers: {
        Location: target.url,
        "referrer-policy": "no-referrer",
      },
    });
  });
}
