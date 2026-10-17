import {
  AssistantError,
  createNavoxUpstream,
  isUuid,
} from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../../lib/assistant-route";
import { assistantApiBaseUrl } from "../../../../../../lib/assistant-server";
import { studentVoiceBody } from "../../../../../../lib/student-voice-route";
import { studentVoiceReceipts } from "../../../../../../lib/student-voice-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ answerId: string }> },
): Promise<Response> {
  return assistantResponse(async () => {
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
        "Choose an answer from this quick ask.",
      );
    const scope = await createNavoxUpstream({
      baseUrl: assistantApiBaseUrl(),
    }).fetchAccount(sessionCookie(request));
    studentVoiceReceipts().forget(answerId, scope, body.session_id);
    return new Response(null, { status: 204 });
  });
}
