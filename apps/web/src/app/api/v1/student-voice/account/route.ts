import { createNavoxUpstream } from "@navox/assistant-runtime";
import {
  assistantResponse,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import { assistantApiBaseUrl } from "../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    const scope = await createNavoxUpstream({
      baseUrl: assistantApiBaseUrl(),
    }).fetchAccount(sessionCookie(request));
    return Response.json({
      account_key: `${scope.workspace_id}:${scope.user_id}`,
    });
  });
}
