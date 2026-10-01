import { parseAssistantMessageRequest } from "@navox/assistant-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AssistantClientError,
  AssistantRequestIdError,
  AssistantTurnLedger,
  assistantSessionPath,
  createAssistantSession,
  deleteAssistantSession,
  loadAssistantSession,
  MAX_SPEECH_AUDIO_BYTES,
  newAssistantRequestId,
  resumeOrCreateAssistantSession,
  submitAssistantTurn,
  synthesizeAssistantSpeech,
  transcribeAssistantSpeech,
} from "./assistant-client";

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const session = {
  id: "66666666-6666-4666-8666-666666666666",
  created_at: "2026-09-30T12:00:00.000Z",
  updated_at: "2026-09-30T12:00:00.000Z",
  expires_at: "2026-10-30T12:00:00.000Z",
  status: "active" as const,
  turns: [],
};

const turn = {
  id: "88888888-8888-4888-8888-888888888888",
  sequence: 1,
  modality: "TEXT" as const,
  state: "READY" as const,
  question: "What am I missing today?",
  plan: null,
  decision: null,
  presentation: null,
  action_refs: [],
  created_at: "2026-09-30T12:00:00.000Z",
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

afterEach(() => vi.unstubAllGlobals());

describe("assistant client", () => {
  it("resumes an authorized tab session and replaces an expired pointer", async () => {
    const entries = new Map<string, string>();
    const storage = {
      getItem: (key: string) => entries.get(key) ?? null,
      setItem: (key: string, value: string) => {
        entries.set(key, value);
      },
      removeItem: (key: string) => {
        entries.delete(key);
      },
    };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(json({ session }, 201))
      .mockResolvedValueOnce(json({ session }))
      .mockResolvedValueOnce(json({ error: { code: "expired" } }, 410))
      .mockResolvedValueOnce(json({ session }, 201));
    vi.stubGlobal("fetch", fetcher);
    expect(await resumeOrCreateAssistantSession(storage)).toEqual(session);
    expect(await resumeOrCreateAssistantSession(storage)).toEqual(session);
    expect(fetcher.mock.calls[1][1].body).toBeUndefined();
    expect(await resumeOrCreateAssistantSession(storage)).toEqual(session);
    expect(fetcher.mock.calls[3][1].method).toBe("POST");
  });
  it("posts same-origin JSON with credentials and never a client scope", async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ session }, 201));
    vi.stubGlobal("fetch", fetcher);
    expect(await createAssistantSession()).toEqual(session);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("/api/v1/assistant/sessions");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("sends only the request id, text, modality, timezone and referents", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(json({ session_id: session.id, turn, replay: false }));
    vi.stubGlobal("fetch", fetcher);
    await submitAssistantTurn(session.id, {
      requestId: "77777777-7777-4777-8777-777777777777",
      text: "What am I missing today?",
      modality: "VOICE",
      timezone: "America/New_York",
    });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`${assistantSessionPath(session.id)}/messages`);
    expect(JSON.parse(init.body)).toEqual({
      request_id: "77777777-7777-4777-8777-777777777777",
      text: "What am I missing today?",
      modality: "VOICE",
      timezone: "America/New_York",
      referents: [],
    });
  });

  it("forwards an explicit email selection as pointers, without client authority", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(json({ session_id: session.id, turn, replay: false }));
    vi.stubGlobal("fetch", fetcher);
    await submitAssistantTurn(session.id, {
      requestId: "77777777-7777-4777-8777-777777777777",
      text: "Select an email",
      modality: "TEXT",
      referents: [turn.id, "cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
    });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
      text: "Select an email",
      referents: [turn.id, "cccccccc-cccc-4ccc-8ccc-cccccccccccc"],
    });
  });

  it("loads and deletes through the same session path", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(json({ session }))
      .mockResolvedValueOnce(json({ session_id: session.id, deleted: true }));
    vi.stubGlobal("fetch", fetcher);
    expect(await loadAssistantSession(session.id)).toEqual(session);
    await deleteAssistantSession(session.id);
    expect(fetcher.mock.calls[0][1].method).toBe("GET");
    expect(fetcher.mock.calls[1][1].method).toBe("DELETE");
    expect(fetcher.mock.calls[0][0]).toBe(
      `/api/v1/assistant/sessions/${session.id}`,
    );
  });

  it("surfaces the server error code and message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            error: {
              code: "conflict",
              message: "Already sent.",
              retryable: false,
            },
          },
          409,
        ),
      ),
    );
    const failure = await submitAssistantTurn(session.id, {
      requestId: "77777777-7777-4777-8777-777777777777",
      text: "What am I missing today?",
      modality: "TEXT",
    }).catch((error) => error);
    expect(failure).toBeInstanceOf(AssistantClientError);
    expect(failure.code).toBe("conflict");
    expect(failure.status).toBe(409);
    expect(failure.message).toBe("Already sent.");
  });

  it("falls back to a qualified failure for an unreadable error body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("nope", { status: 500 })),
    );
    const failure = await createAssistantSession().catch((error) => error);
    expect(failure.code).toBe("unavailable");
  });
});

describe("assistant speech transcription", () => {
  const wav = Uint8Array.from([0x52, 0x49, 0x46, 0x46, 0x00, 0x00]);

  it("uploads the raw WAV container to the session-scoped route only", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(json({ text: "  What am I missing today?  " }));
    vi.stubGlobal("fetch", fetcher);

    expect(await transcribeAssistantSpeech(session.id, wav)).toBe(
      "What am I missing today?",
    );
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`${assistantSessionPath(session.id)}/speech/transcribe`);
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.cache).toBe("no-store");
    expect(init.headers).toEqual({ "content-type": "audio/wav" });
    // The same bytes leave the encoder; nothing is re-encoded or relabelled.
    expect(init.body).toBe(wav);
    // No provider, model, credential, URL or client scope field exists.
    const wire = JSON.stringify({ url, headers: init.headers, body: "wav" });
    expect(wire).not.toMatch(/model|provider|credential|api_key|base_url/i);
  });

  it("carries the abort signal so Stop cancels an in-flight upload", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn((_url: string, init: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      });
    });
    vi.stubGlobal("fetch", fetcher);

    const pending = transcribeAssistantSpeech(
      session.id,
      wav,
      controller.signal,
    );
    expect(fetcher.mock.calls[0][1].signal).toBe(controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("refuses a blank, overlong or unreadable transcript", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ text: "   " })));
    await expect(transcribeAssistantSpeech(session.id, wav)).rejects.toThrow(
      /no speech was heard/i,
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(json({ text: "x".repeat(501) })),
    );
    await expect(transcribeAssistantSpeech(session.id, wav)).rejects.toThrow(
      /too long/i,
    );

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{", { status: 200 })),
    );
    await expect(transcribeAssistantSpeech(session.id, wav)).rejects.toThrow(
      /could not be transcribed/i,
    );
  });

  it("surfaces the route's typed refusal for a typed-input fallback", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            error: {
              code: "unsupported",
              message:
                "Speech transcription is not available right now. Type your question instead.",
              retryable: false,
            },
          },
          422,
        ),
      ),
    );
    const failure = await transcribeAssistantSpeech(session.id, wav).catch(
      (error) => error,
    );
    expect(failure).toBeInstanceOf(AssistantClientError);
    expect(failure.code).toBe("unsupported");
    expect(failure.status).toBe(422);
    expect(failure.message).toMatch(/type your question instead/i);
  });
});

describe("saved-turn speech client", () => {
  const mp3 = (bytes = [0x49, 0x44, 0x33]) =>
    new Response(Uint8Array.from(bytes), {
      status: 200,
      headers: { "content-type": "audio/mpeg" },
    });

  it("sends only the session and turn selectors and returns the bounded audio", async () => {
    const fetcher = vi.fn().mockResolvedValue(mp3());
    vi.stubGlobal("fetch", fetcher);

    const audio = await synthesizeAssistantSpeech(session.id, turn.id);

    expect(Array.from(audio)).toEqual([0x49, 0x44, 0x33]);
    const [url, init] = fetcher.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(
      `/api/v1/assistant/sessions/${session.id}/turns/${turn.id}/speech`,
    );
    expect(init.method).toBe("POST");
    expect(init.cache).toBe("no-store");
    expect(init.credentials).toBe("include");
    expect(init.headers).toEqual({ "content-type": "application/json" });
    // No answer text, provider, model or voice can travel from the browser.
    expect(init.body).toBe("{}");
  });

  it("carries the abort signal and surfaces the route's typed refusal", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn(
      (_url: unknown, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () =>
            reject(new Error("aborted")),
          );
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const pending = synthesizeAssistantSpeech(
      session.id,
      turn.id,
      controller.signal,
    );
    controller.abort();
    await expect(pending).rejects.toThrow(/aborted/i);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json(
          {
            error: {
              code: "unsupported",
              message: "Spoken answers are not available right now.",
              retryable: false,
            },
          },
          503,
        ),
      ),
    );
    const failure = await synthesizeAssistantSpeech(session.id, turn.id).catch(
      (error) => error,
    );
    expect(failure).toBeInstanceOf(AssistantClientError);
    expect(failure.code).toBe("unsupported");
  });

  it("refuses a wrong media type, an empty body and an oversize answer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(Uint8Array.from([1, 2, 3]), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    await expect(
      synthesizeAssistantSpeech(session.id, turn.id),
    ).rejects.toThrow(/could not be played/i);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(new Uint8Array(0), {
          status: 200,
          headers: { "content-type": "audio/mpeg" },
        }),
      ),
    );
    await expect(
      synthesizeAssistantSpeech(session.id, turn.id),
    ).rejects.toThrow(/could not be played/i);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(new Uint8Array(MAX_SPEECH_AUDIO_BYTES + 1), {
          status: 200,
          headers: {
            "content-type": "audio/mpeg",
            "content-length": String(MAX_SPEECH_AUDIO_BYTES + 1),
          },
        }),
      ),
    );
    await expect(
      synthesizeAssistantSpeech(session.id, turn.id),
    ).rejects.toThrow(/could not be played/i);
  });
});

describe("request identifiers", () => {
  it("prefers crypto.randomUUID", () => {
    const source = {
      randomUUID: () => "11111111-1111-4111-8111-111111111111",
    };
    expect(newAssistantRequestId(source)).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
  });

  it("derives a server-valid v4 uuid when randomUUID is unavailable", () => {
    const source = {
      getRandomValues: (array: Uint8Array) => {
        for (let index = 0; index < array.length; index += 1) {
          array[index] = (index * 16 + 1) % 256;
        }
        return array;
      },
    };
    const id = newAssistantRequestId(source);
    expect(id).toMatch(UUID_V4);
    // The runtime's validator is the authority for what the API accepts.
    expect(() =>
      parseAssistantMessageRequest({
        request_id: id,
        text: "What am I missing today?",
        modality: "TEXT",
      }),
    ).not.toThrow();
  });

  it("falls back to getRandomValues when randomUUID throws", () => {
    const source = {
      randomUUID: () => {
        throw new Error("blocked by policy");
      },
      getRandomValues: (array: Uint8Array) => {
        array.fill(7);
        return array;
      },
    };
    expect(newAssistantRequestId(source)).toMatch(UUID_V4);
  });

  it("fails clearly instead of sending an unacceptable identifier", () => {
    expect(() => newAssistantRequestId({})).toThrow(AssistantRequestIdError);
    expect(() => newAssistantRequestId({})).toThrow(
      /secure request identifier/i,
    );
  });
});

describe("request ledger", () => {
  it("reuses one identifier for a pending submission", () => {
    const ledger = new AssistantTurnLedger();
    const first = ledger.requestIdFor("TEXT:hello");
    expect(ledger.requestIdFor("TEXT:hello")).toBe(first);
    expect(ledger.requestIdFor("TEXT:different")).not.toBe(first);
  });

  it("forgets the identifier after a resolved or abandoned submission", () => {
    const ledger = new AssistantTurnLedger();
    const first = ledger.requestIdFor("TEXT:hello");
    ledger.resolve();
    expect(ledger.requestIdFor("TEXT:hello")).not.toBe(first);
    const second = ledger.requestIdFor("TEXT:hello");
    ledger.forget();
    expect(ledger.requestIdFor("TEXT:hello")).not.toBe(second);
  });
});
