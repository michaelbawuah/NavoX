import {
  AssistantError,
  type AssistantRuntime,
} from "@navox/assistant-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET as evidenceRoute } from "../app/api/v1/assistant/sessions/[sessionId]/evidence/route";
import { setAssistantRuntimeForTests } from "./assistant-server";

const sessionId = "66666666-6666-4666-8666-666666666666";
const turnId = "88888888-8888-4888-8888-888888888888";
const itemId = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const path = `/api/v1/assistant/sessions/${sessionId}/evidence?turn_id=${turnId}&item_id=${itemId}`;
const context = { params: Promise.resolve({ sessionId }) };

function fakeRuntime(
  overrides: Partial<AssistantRuntime> = {},
): AssistantRuntime {
  return {
    readEmailEvidence: vi.fn(async () => ({
      evidence_id: itemId,
      source_type: "EMAIL",
      excerpts: [
        { source: "content" as const, text: "Your renewal is confirmed." },
      ],
    })),
    ...overrides,
  } as AssistantRuntime;
}

function request(
  url = path,
  cookie: string | null = "navox_session=abc123",
): Request {
  return new Request(`http://localhost:3000${url}`, {
    method: "GET",
    headers: cookie ? { cookie } : {},
  });
}

afterEach(() => setAssistantRuntimeForTests(null));

describe("guarded assistant evidence route", () => {
  it("re-reads the saved selector and returns only bounded excerpts", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    const response = await evidenceRoute(request(), context);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({
      evidence_id: itemId,
      source_type: "EMAIL",
      excerpts: [{ source: "content", text: "Your renewal is confirmed." }],
    });
    expect(runtime.readEmailEvidence).toHaveBeenCalledWith({
      cookie: "navox_session=abc123",
      session_id: sessionId,
      turn_id: turnId,
      item_id: itemId,
    });
  });

  it("returns no content when the source was revoked or changed", async () => {
    for (const error of [
      new AssistantError(
        "unavailable",
        "This indexed message is no longer fresh.",
      ),
      new AssistantError("conflict", "This message changed since the answer."),
      new AssistantError(
        "not_found",
        "That source is not part of this answer.",
      ),
    ]) {
      setAssistantRuntimeForTests(
        fakeRuntime({
          readEmailEvidence: vi.fn(async () => {
            throw error;
          }),
        }),
      );
      const response = await evidenceRoute(request(), context);
      expect(response.status).toBeGreaterThanOrEqual(400);
      const text = await response.text();
      expect(text).not.toContain("Your renewal is confirmed.");
      expect(text).toContain('"code"');
    }
  });

  it("requires a login and exact selectors before the runtime is reached", async () => {
    const runtime = fakeRuntime();
    setAssistantRuntimeForTests(runtime);
    expect((await evidenceRoute(request(path, null), context)).status).toBe(
      401,
    );
    expect(
      (
        await evidenceRoute(
          request(
            `/api/v1/assistant/sessions/${sessionId}/evidence?turn_id=${turnId}`,
          ),
          context,
        )
      ).status,
    ).toBe(400);
    expect(runtime.readEmailEvidence).not.toHaveBeenCalled();
  });
});
