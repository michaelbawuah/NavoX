import { AssistantError, isUuid } from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../../lib/assistant-route";
import {
  audioResponse,
  synthesizeUpstream,
} from "../../../../../../../lib/assistant-synthesis";
import {
  createStudentAgentTransport,
  privateStudentAgentAnswers,
  requireStudentAgentEnabled,
} from "../../../../../../../lib/student-agent";
import { studentVoiceBody } from "../../../../../../../lib/student-voice-route";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
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
    const cookie = sessionCookie(request);
    const transport = createStudentAgentTransport(
      cookie,
      request.headers.get("origin") ?? new URL(request.url).origin,
    );
    await transport.requireSession(body.session_id, signal);
    const scope = await transport.upstream.authorize(signal);
    const text = privateStudentAgentAnswers().read(
      answerId,
      scope,
      body.session_id,
      true,
    );
    return audioResponse(await synthesizeUpstream(cookie, text, signal));
  });
}
