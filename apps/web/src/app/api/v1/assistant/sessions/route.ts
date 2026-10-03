import type { CreateAssistantSessionResponse } from "@navox/contracts";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../lib/assistant-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const cookie = sessionCookie(request);
    const session = await getAssistantRuntime().createSession({ cookie });
    const body: CreateAssistantSessionResponse = { session };
    return Response.json(body, { status: 201 });
  });
}
