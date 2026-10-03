import {
  AssistantError,
  parseClassNavigationTarget,
} from "@navox/assistant-runtime";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import { assistantApiBaseUrl } from "../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function GET(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    const cookie = sessionCookie(request);
    const query = new URL(request.url).searchParams;
    const connectionId = query.get("connection_id");
    const resourceId = query.get("resource_id");
    if (!UUID.test(connectionId ?? "") || !UUID.test(resourceId ?? "")) {
      throw new AssistantError(
        "invalid_request",
        "A class source is required.",
      );
    }
    const target = new URL(
      `${assistantApiBaseUrl().replace(/\/$/, "")}/knowledge/class-navigation`,
    );
    target.searchParams.set("connection_id", connectionId ?? "");
    target.searchParams.set("resource_id", resourceId ?? "");
    let response: Response;
    try {
      response = await fetch(target, {
        method: "GET",
        headers: { cookie },
        cache: "no-store",
        redirect: "manual",
      });
    } catch {
      throw new AssistantError(
        "unavailable",
        "Class navigation is unavailable.",
      );
    }
    if (response.status === 401)
      throw new AssistantError("unauthorized", "Sign in to open this class.");
    if (response.status === 403 || response.status === 404)
      throw new AssistantError("not_found", "Class navigation is unavailable.");
    if (!response.ok)
      throw new AssistantError(
        "unavailable",
        "Class navigation is unavailable.",
      );
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new AssistantError(
        "unavailable",
        "Class navigation is unavailable.",
      );
    }
    // Class redirects are restricted to the expected Google Calendar hosts;
    // Canvas is deferred, and a foreign https host is refused here too.
    const destination = parseClassNavigationTarget(payload).url;
    return new Response(null, {
      status: 303,
      headers: { Location: destination, "referrer-policy": "no-referrer" },
    });
  });
}
