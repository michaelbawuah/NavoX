import { AssistantError } from "@navox/assistant-runtime";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Re-reads the current content of one saved evidence selector.
 *
 * The browser supplies only the exact selectors the runtime already recorded.
 * Ownership and the SPEC-007 source authority are re-checked on every read, so
 * a revoked or changed source removes its content instead of replaying a
 * stored copy. Durable assistant rows never hold the excerpt text.
 */
export async function GET(
  request: Request,
  context: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
    const { sessionId } = await context.params;
    const cookie = sessionCookie(request);
    const query = new URL(request.url).searchParams;
    const turnId = query.get("turn_id");
    const itemId = query.get("item_id");
    if (typeof turnId !== "string" || typeof itemId !== "string") {
      throw new AssistantError(
        "invalid_request",
        "A source selector is required.",
      );
    }
    const evidence = await getAssistantRuntime().readEmailEvidence({
      cookie,
      session_id: sessionId,
      turn_id: turnId,
      item_id: itemId,
    });
    return Response.json(evidence);
  });
}
