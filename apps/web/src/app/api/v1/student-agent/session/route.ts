import { AssistantError } from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import {
  createStudentAgentTransport,
  requireStudentAgentEnabled,
} from "../../../../../lib/student-agent";
import { studentVoiceBody } from "../../../../../lib/student-voice-route";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  return assistantResponse(async () => {
    requireStudentAgentEnabled();
    guardAssistantMutation(request);
    const body = await studentVoiceBody(request);
    if (Object.keys(body).length)
      throw new AssistantError(
        "invalid_request",
        "Open a private session without client account or provider settings.",
      );
    const signal = AbortSignal.any([
      request.signal,
      AbortSignal.timeout(10_000),
    ]);
    return Response.json(
      await createStudentAgentTransport(
        sessionCookie(request),
        request.headers.get("origin") ?? new URL(request.url).origin,
      ).openSession(signal),
      { status: 201 },
    );
  });
}
