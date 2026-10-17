import { createHash } from "node:crypto";
import {
  AssistantError,
  answerStudentLookup,
  isUuid,
  parseStudentLookup,
} from "@navox/assistant-runtime";
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
  studentVoiceBody,
  studentVoiceContext,
} from "../../../../../../../lib/student-voice-route";
import { studentVoiceReceipts } from "../../../../../../../lib/student-voice-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ answerId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const cookie = sessionCookie(request);
    const body = await studentVoiceBody(request);
    const { answerId } = await context.params;
    if (
      !isUuid(answerId) ||
      Object.keys(body).some((key) => key !== "session_id")
    )
      throw new AssistantError(
        "invalid_request",
        "Choose an answer from this quick ask.",
      );
    const { scope, upstream, sessionId } = await studentVoiceContext(
      cookie,
      body.session_id,
    );
    const store = studentVoiceReceipts();
    const receipt = store.read(answerId, scope, sessionId);
    // A removed, moved or cancelled class invalidates the old spoken answer.
    const current = answerStudentLookup(
      parseStudentLookup(
        await upstream.getStudentLookup(cookie, receipt.lookup),
        receipt.lookup,
      ),
    );
    if (
      createHash("sha256").update(current.text).digest("hex") !==
      receipt.answerDigest
    ) {
      store.forget(answerId, scope, sessionId);
      throw new AssistantError(
        "not_found",
        "Your timetable changed. Ask again for the current class details.",
      );
    }
    if (request.signal.aborted)
      throw new AssistantError("unavailable", "Playback was canceled.");
    const live = store.read(answerId, scope, sessionId, true);
    return audioResponse(
      await synthesizeUpstream(cookie, live.text, request.signal),
    );
  });
}
