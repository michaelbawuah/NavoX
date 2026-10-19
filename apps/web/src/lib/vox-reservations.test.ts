import { afterEach, describe, expect, it, vi } from "vitest";
import {
  VoxCallController,
  type VoxConnection,
  type VoxOwner,
  type VoxTransport,
  type VoxTransportRequest,
} from "./vox-call";
import {
  VOX_RESERVATION_LIMIT,
  VOX_RESERVATION_RESPONSE_BYTES,
  VOX_RESERVATION_TIMEOUT_MS,
  VoxReservationClient,
  wrapVoxReservations,
} from "./vox-reservations";

const owner: VoxOwner = {
  userId: "10000000-0000-4000-8000-000000000001",
  workspaceId: "20000000-0000-4000-8000-000000000001",
};
const otherOwner = { ...owner, userId: "10000000-0000-4000-8000-000000000002" };
const callId = "30000000-0000-4000-8000-000000000001";
const anotherCall = "30000000-0000-4000-8000-000000000002";
const now = Date.parse("2026-10-19T12:00:00Z");
const apiBaseUrl = "https://api.navox.example/api/v1";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function reserved(id = callId, generation = 7) {
  return {
    id: "40000000-0000-4000-8000-000000000001",
    client_call_id: id,
    generation,
    status: "reserved",
    mode: "off-record",
    created_at: "2026-10-19T12:00:00Z",
    expires_at: "2026-10-19T12:02:00Z",
    ended_at: null,
    end_reason: null,
    media_enabled: false,
  };
}

function json(value: unknown, status = 200) {
  return Response.json(value, { status });
}

function requests(fetcher: ReturnType<typeof vi.fn<typeof fetch>>) {
  return fetcher.mock.calls.map(([url, init]) => ({
    url: String(url),
    init,
    body: init?.body ? JSON.parse(String(init.body)) : null,
  }));
}

function http() {
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).endsWith("/context"))
      return json({ generation: 7, call: null, media_enabled: false });
    if (String(url).endsWith("/end"))
      return new Response(null, { status: 204 });
    const body = JSON.parse(String(init?.body));
    return json(reserved(body.client_call_id, body.generation), 201);
  });
  const client = new VoxReservationClient({
    apiBaseUrl,
    fetch: fetcher,
    now: () => now,
  });
  return { fetcher, client };
}

function connection(request: VoxTransportRequest): VoxConnection {
  return {
    owner: request.owner,
    callId: request.callId,
    setMicrophoneEnabled: vi.fn(),
    setSpeakerEnabled: vi.fn(),
    setWakeEnabled: vi.fn(),
    stopPlayback: vi.fn(),
    close: vi.fn(),
  };
}

function controller(client: VoxReservationClient) {
  const joins: VoxTransportRequest[] = [];
  const joined: VoxConnection[] = [];
  const inner: VoxTransport = {
    available: true,
    localWakeAvailable: false,
    connect: vi.fn(async (request) => {
      joins.push(request);
      const current = connection(request);
      joined.push(current);
      return current;
    }),
  };
  let serial = 0;
  const call = new VoxCallController({
    owner,
    transport: wrapVoxReservations(inner, client),
    newCallId: () =>
      `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
  });
  return { call, inner, joins, joined };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
});

describe("owned native Vox reservations", () => {
  it("does no I/O on construction or wrapping an unavailable transport", async () => {
    const { client, fetcher } = http();
    const inner: VoxTransport = {
      available: false,
      localWakeAvailable: false,
      connect: vi.fn(),
    };
    const transport = wrapVoxReservations(inner, client);
    expect(fetcher).not.toHaveBeenCalled();
    expect(transport.available).toBe(false);
    await expect(
      transport.connect({
        owner,
        callId,
        signal: new AbortController().signal,
      } as VoxTransportRequest),
    ).rejects.toThrow("unavailable");
    await client.end(owner, callId);
    expect(fetcher).not.toHaveBeenCalled();
    expect(inner.connect).not.toHaveBeenCalled();
  });

  it("uses the configured API, exact owner header, credentialled no-store requests and strict off-record metadata", async () => {
    const { client, fetcher } = http();
    const mutableOwner = { ...owner };
    const pending = client.reserve(
      mutableOwner,
      callId,
      new AbortController().signal,
    );
    mutableOwner.userId = otherOwner.userId;
    const result = await pending;
    expect(result.media_enabled).toBe(false);
    const sent = requests(fetcher);
    expect(sent.map((row) => [row.url, row.init?.method])).toEqual([
      [`${apiBaseUrl}/vox/calls/context`, "GET"],
      [`${apiBaseUrl}/vox/calls`, "POST"],
    ]);
    for (const row of sent) {
      expect(row.init).toMatchObject({
        credentials: "include",
        cache: "no-store",
        redirect: "error",
      });
      expect(new Headers(row.init?.headers).get("X-NavoX-Account")).toBe(
        `${owner.workspaceId}:${owner.userId}`,
      );
    }
    expect(sent[1].body).toEqual({
      client_call_id: callId,
      generation: 7,
      mode: "off-record",
    });
    result.generation = 90;
    await client.end(owner, callId);
    expect(requests(fetcher)[2].body).toEqual({ generation: 7 });
  });

  it("uses the public API environment default only at request time", async () => {
    const { fetcher } = http();
    const client = new VoxReservationClient({ fetch: fetcher, now: () => now });
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", `${apiBaseUrl}/`);
    await client.reserve(owner, callId, new AbortController().signal);
    expect(String(fetcher.mock.calls[0][0])).toBe(
      `${apiBaseUrl}/vox/calls/context`,
    );
  });

  it("retries an uncertain create with its original generation instead of fetching newer authority", async () => {
    const { client, fetcher } = http();
    const successful = fetcher.getMockImplementation();
    let createAttempts = 0;
    fetcher.mockImplementation(async (url, init) => {
      if (
        init?.method === "POST" &&
        !String(url).endsWith("/end") &&
        ++createAttempts === 1
      )
        throw new Error("private upstream body");
      return successful?.(url, init) as Promise<Response>;
    });
    await expect(
      client.reserve(owner, callId, new AbortController().signal),
    ).rejects.toThrow("unavailable");
    await client.reserve(owner, callId, new AbortController().signal);
    expect(
      requests(fetcher).filter((row) => row.init?.method === "GET"),
    ).toHaveLength(1);
    expect(
      requests(fetcher)
        .filter((row) => row.init?.method === "POST")
        .map((row) => row.body.generation),
    ).toEqual([7, 7]);
  });

  it("coalesces concurrent retries without sharing mutable result views", async () => {
    const { client, fetcher } = http();
    const [first, second] = await Promise.all([
      client.reserve(owner, callId, new AbortController().signal),
      client.reserve(owner, callId, new AbortController().signal),
    ]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    first.generation = 99;
    expect(second.generation).toBe(7);
  });

  it("does not create or join after an end while context proof is pending", async () => {
    const { client, fetcher } = http();
    const proof = deferred<Response>();
    fetcher.mockImplementationOnce(() => proof.promise);
    const abort = new AbortController();
    const pending = client.reserve(owner, callId, abort.signal);
    const rejected = expect(pending).rejects.toMatchObject({
      code: "cancelled",
    });
    const ended = client.end(owner, callId);
    abort.abort();
    expect(fetcher).toHaveBeenCalledTimes(1);
    proof.resolve(json({ generation: 19, call: null, media_enabled: false }));
    await Promise.all([rejected, ended]);
    expect(requests(fetcher).map((row) => [row.url, row.body])).toEqual([
      [`${apiBaseUrl}/vox/calls/context`, null],
      [`${apiBaseUrl}/vox/calls/${callId}/end`, { generation: 19 }],
    ]);
  });

  it("sends the server end fence before an in-flight create returns", async () => {
    const { client, fetcher } = http();
    const create = deferred<Response>();
    const successful = fetcher.getMockImplementation();
    fetcher.mockImplementation((url, init) =>
      String(url) === `${apiBaseUrl}/vox/calls`
        ? create.promise
        : (successful?.(url, init) as Promise<Response>),
    );
    const pending = client.reserve(owner, callId, new AbortController().signal);
    const rejected = expect(pending).rejects.toMatchObject({
      code: "cancelled",
    });
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await client.end(owner, callId);
    expect(requests(fetcher)[2]).toMatchObject({
      url: `${apiBaseUrl}/vox/calls/${callId}/end`,
      body: { generation: 7 },
    });
    create.resolve(json(reserved(), 201));
    await rejected;
  });

  it("never creates a requested call from another call's context", async () => {
    const { client, fetcher } = http();
    fetcher.mockResolvedValueOnce(
      json({
        generation: 7,
        call: reserved(anotherCall),
        media_enabled: false,
      }),
    );
    await expect(
      client.reserve(owner, callId, new AbortController().signal),
    ).rejects.toMatchObject({ code: "scope-changed" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([
    { room: "foreign-room" },
    { media_enabled: true },
    { client_call_id: anotherCall },
    { generation: 8 },
    { generation: true },
    { status: "active" },
    { ended_at: "2026-10-19T12:01:00Z" },
    { expires_at: "2026-10-19T12:00:00Z" },
    { mode: "remember-everything" },
    { id: "not-an-id" },
  ])("rejects untrusted reservation response %j", async (changes) => {
    const { client, fetcher } = http();
    fetcher.mockResolvedValueOnce(
      json({ generation: 7, call: null, media_enabled: false }),
    );
    fetcher.mockResolvedValueOnce(json({ ...reserved(), ...changes }, 201));
    await expect(
      client.reserve(owner, callId, new AbortController().signal),
    ).rejects.toMatchObject({ code: "invalid-response" });
  });

  it.each([401, 409])(
    "fails closed on status %i without echoing server data",
    async (status) => {
      const { client, fetcher } = http();
      fetcher.mockResolvedValueOnce(
        json({ detail: "private token=secret" }, status),
      );
      const error = await client
        .reserve(owner, callId, new AbortController().signal)
        .catch((value: unknown) => value);
      expect(error).toMatchObject({ code: "scope-changed" });
      expect(String(error)).not.toContain("secret");
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );

  it("bounds declared and streamed JSON and cancels oversized bodies", async () => {
    for (const declared of [true, false]) {
      const { client, fetcher } = http();
      const cancel = vi.fn();
      fetcher.mockResolvedValueOnce(
        new Response(
          new ReadableStream({
            start(stream) {
              stream.enqueue(
                new Uint8Array(VOX_RESERVATION_RESPONSE_BYTES + 1),
              );
            },
            cancel,
          }),
          {
            headers: {
              "content-type": "application/json",
              ...(declared
                ? {
                    "content-length": String(
                      VOX_RESERVATION_RESPONSE_BYTES + 1,
                    ),
                  }
                : {}),
            },
          },
        ),
      );
      await expect(
        client.reserve(owner, callId, new AbortController().signal),
      ).rejects.toMatchObject({ code: "invalid-response" });
      expect(cancel).toHaveBeenCalledTimes(1);
    }
  });

  it.each(["headers", "body"] as const)(
    "times out a hung %s even when the injected transport ignores abort",
    async (stage) => {
      vi.useFakeTimers();
      const fetcher = vi.fn<typeof fetch>(
        stage === "headers"
          ? () => new Promise(() => {})
          : async () =>
              new Response(new ReadableStream({ start() {} }), {
                headers: { "content-type": "application/json" },
              }),
      );
      const client = new VoxReservationClient({ apiBaseUrl, fetch: fetcher });
      const pending = expect(
        client.reserve(owner, callId, new AbortController().signal),
      ).rejects.toMatchObject({ code: "cancelled" });
      await vi.advanceTimersByTimeAsync(VOX_RESERVATION_TIMEOUT_MS);
      await pending;
    },
  );

  it("cancels a late body from a fetch that resolves after timeout", async () => {
    vi.useFakeTimers();
    const late = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => late.promise);
    const client = new VoxReservationClient({ apiBaseUrl, fetch: fetcher });
    const pending = expect(
      client.reserve(owner, callId, new AbortController().signal),
    ).rejects.toMatchObject({ code: "cancelled" });
    await vi.advanceTimersByTimeAsync(VOX_RESERVATION_TIMEOUT_MS);
    await pending;
    const cancel = vi.fn();
    late.resolve(
      new Response(new ReadableStream({ start() {}, cancel }), {
        headers: { "content-type": "application/json" },
      }),
    );
    await Promise.resolve();
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed owner/call and pre-aborted intents before I/O", async () => {
    const { client, fetcher } = http();
    await expect(
      client.reserve(
        { ...owner, userId: "bad" },
        callId,
        new AbortController().signal,
      ),
    ).rejects.toThrow();
    await expect(
      client.reserve(owner, "bad", new AbortController().signal),
    ).rejects.toThrow();
    await expect(
      client.reserve(owner, callId, AbortSignal.abort()),
    ).rejects.toMatchObject({ code: "cancelled" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("bounds in-memory intents and releases terminal bindings", async () => {
    const { client, fetcher } = http();
    for (let i = 1; i <= VOX_RESERVATION_LIMIT; i++) {
      await client.reserve(
        owner,
        `30000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
        new AbortController().signal,
      );
    }
    const extra = "30000000-0000-4000-8000-000000000201";
    const count = fetcher.mock.calls.length;
    await expect(
      client.reserve(owner, extra, new AbortController().signal),
    ).rejects.toThrow("unavailable");
    expect(fetcher).toHaveBeenCalledTimes(count);
    await client.end(owner, callId);
    await client.reserve(owner, extra, new AbortController().signal);
  });
});

describe("reservation wrapper with the actual Vox controller", () => {
  it("allows a legitimate context-plus-end cleanup taking over ten seconds", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>(async (url) => {
      await new Promise<void>((resolve) => setTimeout(resolve, 9_000));
      return String(url).endsWith("/context")
        ? json({ generation: 7, call: null, media_enabled: false })
        : new Response(null, { status: 204 });
    });
    const client = new VoxReservationClient({
      apiBaseUrl,
      fetch: fetcher,
      now: () => now,
    });
    const current = controller(client);
    const starting = current.call.start();
    await vi.advanceTimersByTimeAsync(0);
    const hangingUp = current.call.hangUp();
    let finished = false;
    void hangingUp.then(() => {
      finished = true;
    });
    await vi.advanceTimersByTimeAsync(10_001);
    expect(finished).toBe(false);
    expect(current.call.current().endReason).toBe("hang-up");
    await vi.advanceTimersByTimeAsync(7_999);
    await Promise.all([starting, hangingUp]);
    expect(finished).toBe(true);
    expect(current.call.current().endReason).toBe("hang-up");
    expect(current.inner.connect).not.toHaveBeenCalled();
    expect(requests(fetcher).map((row) => row.url)).toEqual([
      `${apiBaseUrl}/vox/calls/context`,
      `${apiBaseUrl}/vox/calls/${callId}/end`,
    ]);
  });

  it("keeps reconnect attempt cleanup separate from terminal reservation cleanup", async () => {
    const { client, fetcher } = http();
    const current = controller(client);
    await current.call.start();
    await current.call.reconnect();
    expect(current.inner.connect).toHaveBeenCalledTimes(2);
    expect(current.joined[0].close).toHaveBeenCalledTimes(1);
    expect(
      requests(fetcher).filter((row) => row.url.endsWith("/end")),
    ).toHaveLength(0);
    await current.call.hangUp();
    await current.call.hangUp();
    await current.call.dispose();
    expect(
      requests(fetcher).filter((row) => row.url.endsWith("/end")),
    ).toHaveLength(1);
  });

  it("fences old-owner late proof before allowing the new owner's call", async () => {
    const { client, fetcher } = http();
    const oldProof = deferred<Response>();
    fetcher.mockImplementationOnce(() => oldProof.promise);
    const current = controller(client);
    const starting = current.call.start();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const changing = current.call.bindOwner(otherOwner);
    const freshStart = current.call.start();
    expect(current.inner.connect).not.toHaveBeenCalled();
    oldProof.resolve(json({ generation: 7, call: null, media_enabled: false }));
    await Promise.all([starting, changing, freshStart]);
    expect(current.inner.connect).toHaveBeenCalledTimes(1);
    expect(current.joins[0].owner).toEqual(otherOwner);
    expect(
      requests(fetcher)
        .filter((row) => row.url === `${apiBaseUrl}/vox/calls`)
        .map((row) => new Headers(row.init?.headers).get("X-NavoX-Account")),
    ).toEqual([`${otherOwner.workspaceId}:${otherOwner.userId}`]);
    await current.call.dispose();
  });

  it("ends an in-flight create immediately and never joins from its late response", async () => {
    const { client, fetcher } = http();
    const create = deferred<Response>();
    const successful = fetcher.getMockImplementation();
    fetcher.mockImplementation((url, init) =>
      String(url) === `${apiBaseUrl}/vox/calls`
        ? create.promise
        : (successful?.(url, init) as Promise<Response>),
    );
    const current = controller(client);
    const starting = current.call.start();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    const hangingUp = current.call.hangUp();
    await vi.waitFor(() =>
      expect(
        requests(fetcher).filter((row) => row.url.endsWith("/end")),
      ).toHaveLength(1),
    );
    expect(current.inner.connect).not.toHaveBeenCalled();
    create.resolve(json(reserved(), 201));
    await Promise.all([starting, hangingUp]);
    expect(current.call.current().phase).toBe("ended");
    expect(current.inner.connect).not.toHaveBeenCalled();
  });
});
