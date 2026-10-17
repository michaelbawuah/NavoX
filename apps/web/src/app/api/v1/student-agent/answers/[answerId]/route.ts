import { AssistantError, isUuid } from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../lib/assistant-route";
import {
  createStudentAgentTransport,
  privateStudentAgentAnswers,
  requireStudentAgentEnabled,
} from "../../../../../../lib/student-agent";
import { studentVoiceBody } from "../../../../../../lib/student-voice-route";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ answerId: string }> },
) {
  return assistantResponse(async () => {
    requireStudentAgentEnabled();
    guardAssistantMutation(request);
    const body = await studentVoiceBody(request);
    const { answerId } = await context.params;
    if (
      !isUuid(answerId) ||
      !isUuid(body.session_id) ||
      Object.keys(body).some((key) => key !== "session_id")
    )
      throw new AssistantError(
        "invalid_request",
        "Choose a private answer from this session.",
      );
    const signal = AbortSignal.any([
      request.signal,
      AbortSignal.timeout(10_000),
    ]);
    const transport = createStudentAgentTransport(
      sessionCookie(request),
      request.headers.get("origin") ?? new URL(request.url).origin,
    );
    privateStudentAgentAnswers().forget(
      answerId,
      await transport.upstream.authorize(signal),
      body.session_id,
    );
    return new Response(null, { status: 204 });
  });
}
