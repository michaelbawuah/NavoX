import { createNavoxUpstream } from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import {
  assistantApiBaseUrl,
  getAssistantRuntime,
} from "../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const cookie = sessionCookie(request);
    const scope = await createNavoxUpstream({
      baseUrl: assistantApiBaseUrl(),
    }).fetchAccount(cookie);
    const session = await getAssistantRuntime().createSession({ cookie });
    return Response.json(
      {
        session_id: session.id,
        account_key: `${scope.workspace_id}:${scope.user_id}`,
      },
      { status: 201 },
    );
  });
}
