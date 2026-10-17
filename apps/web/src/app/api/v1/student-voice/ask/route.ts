import { createHash } from "node:crypto";
import {
  AssistantError,
  answerStudentLookup,
  isUuid,
  parseStudentLookup,
  studentClassDraftText,
  studentLookupRequest,
  studentLookupSpeech,
} from "@navox/assistant-runtime";
import {
  assistantResponse,
  guardAssistantMutation,
  sessionCookie,
} from "../../../../../lib/assistant-route";
import { getAssistantRuntime } from "../../../../../lib/assistant-server";
import {
  studentVoiceBody,
  studentVoiceContext,
} from "../../../../../lib/student-voice-route";
import { studentVoiceReceipts } from "../../../../../lib/student-voice-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  return assistantResponse(async () => {
    guardAssistantMutation(request);
    const cookie = sessionCookie(request);
    const body = await studentVoiceBody(request);
    if (
      Object.keys(body).some(
        (key) =>
          !["session_id", "request_id", "text", "modality", "save"].includes(
            key,
          ),
      ) ||
      typeof body.text !== "string" ||
      !body.text.trim() ||
      body.text.trim().length > 500 ||
      !isUuid(body.request_id) ||
      (body.modality !== "VOICE" && body.modality !== "TEXT") ||
      typeof body.save !== "boolean"
    )
      throw new AssistantError(
        "invalid_request",
        "Ask a class question of up to 500 characters.",
      );
    const text = body.text.trim();
    const { scope, upstream, sessionId } = await studentVoiceContext(
      cookie,
      body.session_id,
    );
    const classDraft = studentClassDraftText(text);
    if (classDraft)
      return Response.json({
        kind: "class_draft",
        answer:
          "Review the class details in Timetable before saving. Nothing has been added.",
        saved: false,
        class_draft: classDraft,
        receipt_id: null,
      });
    const lookup = studentLookupRequest(text);
    if (!lookup)
      return Response.json({
        kind: "clarify",
        answer:
          "Ask what class is on now, what comes next, or what classes you have today or tomorrow. For another time, say ‘What class do I have at 2 pm tomorrow?’",
        saved: false,
        receipt_id: null,
      });
    if (body.save) {
      const response = await getAssistantRuntime().submitTurn({
        cookie,
        session_id: sessionId,
        body: {
          request_id: body.request_id,
          text,
          modality: body.modality,
          timezone: null,
          referents: [],
        },
      });
      const answer =
        response.turn.presentation?.blocks
          .filter((block) => block.kind === "ANSWER" || block.kind === "NOTICE")
          .map((block) => block.text)
          .join(" ") ?? "The answer is available in your saved conversation.";
      // Saving a transcript does not turn a current-class answer into timeless speech.
      // The same short receipt/fresh-lookup gate protects saved and private quick asks.
      const receipt =
        response.turn.state === "READY" &&
        response.turn.decision?.reason === "class.student_timetable" &&
        !request.signal.aborted
          ? studentVoiceReceipts().put({
              scope,
              sessionId,
              lookup,
              text:
                answer.length <= 600
                  ? answer
                  : "This timetable answer is longer than the spoken limit. Open Timetable to see every class time and room.",
              answerDigest: createHash("sha256").update(answer).digest("hex"),
            })
          : null;
      return Response.json({
        kind: "lookup",
        answer,
        saved: true,
        session_id: sessionId,
        turn_id: response.turn.id,
        receipt_id: receipt?.id ?? null,
        expires_at: receipt ? new Date(receipt.expiresAt).toISOString() : null,
      });
    }
    const snapshot = parseStudentLookup(
      await upstream.getStudentLookup(cookie, lookup),
      lookup,
    );
    const answer = answerStudentLookup(snapshot);
    if (request.signal.aborted)
      throw new AssistantError("unavailable", "That quick ask was canceled.");
    const receipt = studentVoiceReceipts().put({
      scope,
      sessionId,
      lookup,
      text: studentLookupSpeech(snapshot, answer.text),
      answerDigest: createHash("sha256").update(answer.text).digest("hex"),
    });
    return Response.json({
      kind: "lookup",
      answer: answer.text,
      saved: false,
      receipt_id: receipt.id,
      expires_at: new Date(receipt.expiresAt).toISOString(),
    });
  });
}
