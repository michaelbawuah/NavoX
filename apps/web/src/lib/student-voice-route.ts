import {
  AssistantError,
  createNavoxUpstream,
  isRecord,
  isUuid,
} from "@navox/assistant-runtime";
import { assistantApiBaseUrl, getAssistantRuntime } from "./assistant-server";

/** A quick ask never accepts a provider, account, clock or executable action. */
export async function studentVoiceBody(
  request: Request,
): Promise<Record<string, unknown>> {
  if (!request.body)
    throw new AssistantError("invalid_request", "A question is required.");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (!value) continue;
    length += value.length;
    if (length > 4096) {
      await reader.cancel();
      throw new AssistantError(
        "invalid_request",
        "That question is too large.",
      );
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  let body: unknown;
  try {
    body = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new AssistantError("invalid_request", "A JSON question is required.");
  }
  if (!isRecord(body))
    throw new AssistantError("invalid_request", "A JSON question is required.");
  return body;
}

export async function studentVoiceContext(cookie: string, sessionId: unknown) {
  if (!isUuid(sessionId))
    throw new AssistantError("invalid_request", "Start a new quick ask.");
  const upstream = createNavoxUpstream({ baseUrl: assistantApiBaseUrl() });
  const scope = await upstream.fetchAccount(cookie);
  await getAssistantRuntime().readSession({ cookie, session_id: sessionId });
  return { upstream, scope, sessionId };
}
