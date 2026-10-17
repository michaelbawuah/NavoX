import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import {
  parseStudentAgentTurn,
  privateStudentAgentAnswers,
  requireStudentAgentEnabled,
  studentAgentForRequest,
} from "../../../../../lib/student-agent";
import { studentVoiceBody } from "../../../../../lib/student-voice-route";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  return assistantResponse(async () => {
    requireStudentAgentEnabled();
    guardAssistantMutation(request);
    const input = parseStudentAgentTurn(await studentVoiceBody(request));
    const signal = AbortSignal.any([
      request.signal,
      AbortSignal.timeout(10_000),
    ]);
    const { transport, agent } = studentAgentForRequest(
      sessionCookie(request),
      request.headers.get("origin") ?? new URL(request.url).origin,
    );
    await transport.requireSession(input.session_id, signal);
    const reply = await agent.ask(input, signal);
    // Connected-data speech remains on its owning freshness-checked route. This receipt
    // is only for the no-history, question-only ordinary conversation answer.
    const receipt =
      reply.status === "answer" &&
      reply.source === "question_only_conversation" &&
      !signal.aborted
        ? privateStudentAgentAnswers().put(
            await transport.upstream.authorize(signal),
            input.session_id,
            reply.answer.length <= 600
              ? reply.answer
              : "This answer is longer than the spoken limit. Read the full answer on screen, or ask for a shorter explanation.",
          )
        : null;
    return Response.json({
      turn_id: input.turn_id,
      session_id: input.session_id,
      reply,
      receipt_id: receipt?.id ?? null,
      expires_at: receipt?.expires_at ?? null,
    });
  });
}
