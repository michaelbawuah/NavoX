import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  VoxCallController,
  type VoxConnection,
  type VoxOwner,
  type VoxTransport,
  type VoxTransportRequest,
} from "./vox-call";
import {
  VOX_RESERVATION_LEASE_MS,
  VoxReservationClient,
  wrapVoxReservations,
} from "./vox-reservations";

const owner: VoxOwner = {
  userId: "10000000-0000-4000-8000-000000000001",
  workspaceId: "20000000-0000-4000-8000-000000000001",
};
const otherOwner = { ...owner, userId: "10000000-0000-4000-8000-000000000002" };
const callId = "30000000-0000-4000-8000-000000000001";
const base = Date.parse("2026-10-19T12:00:00Z");
const calls: VoxCallController[] = [];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function reservation(id = callId, expires = base + VOX_RESERVATION_LEASE_MS) {
  return {
    id: "40000000-0000-4000-8000-000000000001",
    client_call_id: id,
    generation: 7,
    status: "reserved",
    mode: "off-record",
    created_at: new Date(base).toISOString(),
    expires_at: new Date(expires).toISOString(),
    ended_at: null,
    end_reason: null,
    media_enabled: false,
  };
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

function implementation(fetcher: ReturnType<typeof vi.fn<typeof fetch>>) {
  const value = fetcher.getMockImplementation();
  if (!value) throw new Error("Missing HTTP fixture");
  return value;
}

function rig() {
  let offset = 0;
  const saved = new Map<string, ReturnType<typeof reservation>>();
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/context"))
      return Response.json({ generation: 7, call: null, media_enabled: false });
    if (url.endsWith("/end")) return new Response(null, { status: 204 });
    if (url.endsWith("/renew")) {
      const id = url.split("/").at(-2) as string;
      const value = reservation(id, Date.now() + VOX_RESERVATION_LEASE_MS);
      saved.set(id, value);
      return Response.json(value);
    }
    const body = JSON.parse(String(init?.body));
    const value =
      saved.get(body.client_call_id) ??
      reservation(body.client_call_id, Date.now() + VOX_RESERVATION_LEASE_MS);
    saved.set(body.client_call_id, value);
    return Response.json(value, { status: 201 });
  });
  const client = new VoxReservationClient({
    apiBaseUrl: "https://navox.example/api/v1",
    fetch: fetcher,
    monotonicNow: () => performance.now() + offset,
  });
  const joined: VoxConnection[] = [];
  const requests: VoxTransportRequest[] = [];
  const inner: VoxTransport = {
    available: true,
    localWakeAvailable: true,
    connect: vi.fn(async (request) => {
      requests.push(request);
      const value = connection(request);
      joined.push(value);
      return value;
    }),
    endCall: vi.fn(),
  };
  const transport = wrapVoxReservations(inner, client);
  let serial = 0;
  const call = new VoxCallController({
    owner,
    transport,
    newCallId: () =>
      `30000000-0000-4000-8000-${String(++serial).padStart(12, "0")}`,
  });
  calls.push(call);
  return {
    call,
    client,
    inner,
    transport,
    fetcher,
    requests,
    joined,
    jumpMonotonic(milliseconds: number) {
      offset += milliseconds;
    },
    renewals: () =>
      fetcher.mock.calls.filter(([input]) => String(input).endsWith("/renew")),
    ends: () =>
      fetcher.mock.calls.filter(([input]) => String(input).endsWith("/end")),
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(base);
});
afterEach(async () => {
  await Promise.all(calls.splice(0).map((call) => call.dispose()));
  vi.useRealTimers();
});

describe("bounded native Vox lease renewal", () => {
  it("never renews without the synchronous ownership observer and disposes a registered observer on close", async () => {
    const current = rig();
    const events = vi.fn();
    const request: VoxTransportRequest = {
      owner,
      callId,
      signal: new AbortController().signal,
      mayTransmitAudio: () => true,
      mayPlayAudio: () => true,
      mayListenForWake: () => false,
      mayRenewReservation: () => true,
      onWake: () => {},
      onEvent: events,
    };
    const joined = await current.transport.connect(request);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(current.renewals()).toHaveLength(0);
    expect(events).toHaveBeenCalledWith({ type: "ended" });
    await joined.close();
    await current.transport.endCall?.(owner, callId);

    const next = rig();
    const listeners = new Set<() => void>();
    const remove = vi.fn((listener: () => void) => listeners.delete(listener));
    const second = await next.transport.connect({
      ...request,
      observeReservationAuthority: (listener) => {
        listeners.add(listener);
        return () => {
          remove(listener);
        };
      },
    });
    expect(listeners.size).toBe(1);
    await second.close();
    expect(listeners.size).toBe(0);
    expect(remove).toHaveBeenCalledTimes(1);
    await next.transport.endCall?.(owner, callId);
  });

  it("uses the actual short lease margin and fails closed when renewal cannot extend it", async () => {
    const current = rig();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) => {
      const url = String(input);
      if (!url.endsWith("/context") && !url.endsWith("/end")) {
        return Promise.resolve(
          Response.json(reservation(callId, base + 40_000), {
            status: url.endsWith("/renew") ? 200 : 201,
          }),
        );
      }
      return normal(input, init);
    });
    await current.call.start();
    await vi.advanceTimersByTimeAsync(9_999);
    expect(current.renewals()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(current.renewals()).toHaveLength(1);
    expect(current.call.current().phase).toBe("ended");
  });

  it("does not add response latency to the request-start lease bound", async () => {
    const current = rig();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) => {
      if (String(input).endsWith("/renew")) {
        const expires = Date.now() + VOX_RESERVATION_LEASE_MS;
        return new Promise((resolve) =>
          setTimeout(
            () => resolve(Response.json(reservation(callId, expires))),
            9_000,
          ),
        );
      }
      return normal(input, init);
    });
    await current.call.start();
    await vi.advanceTimersByTimeAsync(99_000);
    expect(current.client.leaseRemaining(owner, callId)).toBe(111_000);
    await vi.advanceTimersByTimeAsync(80_999);
    expect(current.renewals()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(current.renewals()).toHaveLength(2);
  });

  it("renews the same reservation at its deadline margin without reconnecting or unmuting", async () => {
    const current = rig();
    await current.call.start();
    current.call.setMicrophoneMuted(true);
    current.call.setSpeakerEnabled(false);
    current.call.setMinimized(true);
    await vi.advanceTimersByTimeAsync(89_999);
    expect(current.renewals()).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(current.renewals()).toHaveLength(1);
    const [, init] = current.renewals()[0];
    expect(JSON.parse(String(init?.body))).toEqual({ generation: 7 });
    expect(new Headers(init?.headers).get("X-NavoX-Account")).toBe(
      `${owner.workspaceId}:${owner.userId}`,
    );
    expect(init).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
    });
    expect(current.inner.connect).toHaveBeenCalledTimes(1);
    expect(current.call.current()).toMatchObject({
      callId,
      microphoneMuted: true,
      speakerEnabled: false,
      minimized: true,
    });
    expect(current.client.leaseRemaining(owner, callId)).toBe(120_000);
    await vi.advanceTimersByTimeAsync(90_000);
    expect(current.renewals()).toHaveLength(2);
  });

  it("renews in standby without giving audio transmission authority", async () => {
    const current = rig();
    await current.call.start();
    await vi.advanceTimersByTimeAsync(120_000);
    current.call.tick();
    expect(current.call.current().phase).toBe("standby");
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(current.renewals()).toHaveLength(2);
    expect(current.call.current().phase).toBe("standby");
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
  });

  it("suspends renewal, then resumes only while the old lease is still live", async () => {
    const current = rig();
    await current.call.start();
    await vi.advanceTimersByTimeAsync(80_000);
    current.call.suspendForSessionCheck();
    expect(current.requests[0].mayRenewReservation?.()).toBe(false);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(current.renewals()).toHaveLength(0);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    await current.call.bindOwner(owner);
    await vi.advanceTimersByTimeAsync(0);
    expect(current.renewals()).toHaveLength(1);
    expect(current.call.current().phase).toBe("active");
    expect(current.client.leaseRemaining(owner, callId)).toBe(120_000);
  });

  it("aborts an in-flight renewal synchronously on privacy suspension and ignores its late proof", async () => {
    const current = rig();
    const late = deferred<Response>();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith("/renew") ? late.promise : normal(input, init),
    );
    await current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    const signal = current.renewals()[0][1]?.signal;
    current.call.suspendForSessionCheck();
    expect(signal?.aborted).toBe(true);
    late.resolve(Response.json(reservation(callId, base + 210_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(current.client.leaseRemaining(owner, callId)).toBe(30_000);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(current.call.current().phase).toBe("ended");
    await current.call.bindOwner(owner);
    expect(current.renewals()).toHaveLength(1);
    expect(current.call.current().phase).toBe("ended");
  });

  it("waits for cancelled renewal settlement when same-owner verification returns in the same turn", async () => {
    const current = rig();
    const late = deferred<Response>();
    const normal = implementation(current.fetcher);
    let renewing = 0;
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith("/renew") && ++renewing === 1
        ? late.promise
        : normal(input, init),
    );
    await current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    current.call.suspendForSessionCheck();
    const verified = current.call.bindOwner(owner);
    await verified;
    await vi.advanceTimersByTimeAsync(0);
    expect(current.renewals()).toHaveLength(2);
    expect(current.call.current().phase).toBe("active");
    expect(current.client.leaseRemaining(owner, callId)).toBe(120_000);
    late.resolve(Response.json(reservation(callId, base + 210_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(current.call.current().phase).toBe("active");
  });

  it.each([
    { id: "40000000-0000-4000-8000-000000000002" },
    { client_call_id: "30000000-0000-4000-8000-000000000002" },
    { generation: 8 },
    { created_at: "2026-10-19T12:00:01.000Z" },
    { expires_at: new Date(base + 120_000).toISOString() },
    { expires_at: new Date(base + 119_000).toISOString() },
    { expires_at: new Date(base + 500_000).toISOString() },
    { media_enabled: true },
  ])(
    "ends on untrusted renewal %j without retry or a new reservation",
    async (changes) => {
      const current = rig();
      const normal = implementation(current.fetcher);
      current.fetcher.mockImplementation((input, init) =>
        String(input).endsWith("/renew")
          ? Promise.resolve(
              Response.json({
                ...reservation(callId, base + 210_000),
                ...changes,
              }),
            )
          : normal(input, init),
      );
      await current.call.start();
      await vi.advanceTimersByTimeAsync(90_000);
      expect(current.call.current().phase).toBe("ended");
      expect(current.requests[0].mayTransmitAudio()).toBe(false);
      expect(current.joined[0].close).toHaveBeenCalledTimes(1);
      expect(current.ends()).toHaveLength(1);
      expect(JSON.parse(String(current.ends()[0][1]?.body))).toEqual({
        generation: 7,
      });
      await vi.advanceTimersByTimeAsync(300_000);
      expect(current.renewals()).toHaveLength(1);
      expect(current.inner.connect).toHaveBeenCalledTimes(1);
    },
  );

  it.each([401, 404, 409, 410, 503])(
    "fails closed on renewal status %i",
    async (status) => {
      const current = rig();
      const normal = implementation(current.fetcher);
      current.fetcher.mockImplementation((input, init) =>
        String(input).endsWith("/renew")
          ? Promise.resolve(
              Response.json({ private: "never shown" }, { status }),
            )
          : normal(input, init),
      );
      await current.call.start();
      await vi.advanceTimersByTimeAsync(90_000);
      expect(current.call.current()).toMatchObject({
        phase: "ended",
        endReason: "transport-ended",
      });
      expect(JSON.stringify(current.call.current())).not.toContain(
        "never shown",
      );
      expect(current.ends()).toHaveLength(1);
    },
  );

  it("rejects a future renewal arriving after the old absolute expiry despite throttled timers", async () => {
    const current = rig();
    const late = deferred<Response>();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith("/renew") ? late.promise : normal(input, init),
    );
    await current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    vi.setSystemTime(base + 121_000); // No timers are advanced by a wall-clock jump.
    late.resolve(Response.json(reservation(callId, base + 241_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(current.call.current().phase).toBe("ended");
    expect(current.client.leaseRemaining(owner, callId)).toBe(0);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
  });

  it("checks the monotonic expiry in audio gates even when wall time moves backwards and timers have not run", async () => {
    const current = rig();
    await current.call.start();
    vi.setSystemTime(base - 60_000);
    current.jumpMonotonic(121_000);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.requests[0].mayPlayAudio()).toBe(false);
    expect(current.requests[0].mayListenForWake()).toBe(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(current.call.current().phase).toBe("ended");
    expect(current.renewals()).toHaveLength(0);
  });

  it("does not renew a replacement account from a late old-account response", async () => {
    const current = rig();
    const late = deferred<Response>();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith(`/${callId}/renew`)
        ? late.promise
        : normal(input, init),
    );
    await current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    await current.call.bindOwner(otherOwner);
    await current.call.start();
    late.resolve(Response.json(reservation(callId, base + 210_000)));
    await vi.advanceTimersByTimeAsync(0);
    expect(current.call.current()).toMatchObject({
      owner: otherOwner,
      phase: "active",
      callId: "30000000-0000-4000-8000-000000000002",
    });
    expect(current.requests[0].mayRenewReservation?.()).toBe(false);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.joined[0].close).toHaveBeenCalledTimes(1);
    expect(current.renewals()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(90_000);
    expect(
      new Headers(current.renewals()[1][1]?.headers).get("X-NavoX-Account"),
    ).toBe(`${otherOwner.workspaceId}:${otherOwner.userId}`);
  });

  it("hangs up during renewal once and cannot be reopened by the late response", async () => {
    const current = rig();
    const late = deferred<Response>();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith("/renew") ? late.promise : normal(input, init),
    );
    await current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    await current.call.hangUp();
    expect(current.renewals()[0][1]?.signal?.aborted).toBe(true);
    late.resolve(Response.json(reservation(callId, base + 210_000)));
    await vi.advanceTimersByTimeAsync(300_000);
    expect(current.call.current()).toMatchObject({
      phase: "ended",
      endReason: "hang-up",
    });
    expect(current.ends()).toHaveLength(1);
    expect(current.renewals()).toHaveLength(1);
    expect(current.inner.connect).toHaveBeenCalledTimes(1);
  });

  it("ends a suspended pending join at expiry and releases its late resource before a replacement", async () => {
    const current = rig();
    const late = deferred<VoxConnection>();
    const joining = vi.fn(async (request: VoxTransportRequest) => {
      current.requests.push(request);
      return late.promise;
    });
    current.inner.connect = joining;
    const starting = current.call.start();
    await vi.advanceTimersByTimeAsync(0);
    current.call.suspendForSessionCheck();
    await vi.advanceTimersByTimeAsync(120_000);
    expect(current.call.current().phase).toBe("ended");
    expect(current.renewals()).toHaveLength(0);
    expect(current.requests[0].signal.aborted).toBe(true);
    const resource = connection(current.requests[0]);
    late.resolve(resource);
    await starting;
    expect(resource.close).toHaveBeenCalledTimes(1);
    expect(current.call.current().phase).toBe("ended");
  });

  it("synchronously aborts renewal during a pending SDK join when ownership is suspended", async () => {
    const current = rig();
    const lateJoin = deferred<VoxConnection>();
    const lateRenew = deferred<Response>();
    const normal = implementation(current.fetcher);
    current.fetcher.mockImplementation((input, init) =>
      String(input).endsWith("/renew")
        ? lateRenew.promise
        : normal(input, init),
    );
    current.inner.connect = vi.fn(async (request) => {
      current.requests.push(request);
      return lateJoin.promise;
    });
    const starting = current.call.start();
    await vi.advanceTimersByTimeAsync(90_000);
    expect(current.call.current().phase).toBe("connecting");
    current.call.suspendForSessionCheck();
    expect(current.renewals()[0][1]?.signal?.aborted).toBe(true);
    lateRenew.resolve(Response.json(reservation(callId, base + 210_000)));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(current.call.current().phase).toBe("ended");
    const resource = connection(current.requests[0]);
    lateJoin.resolve(resource);
    await starting;
    expect(resource.close).toHaveBeenCalledTimes(1);
    expect(current.renewals()).toHaveLength(1);
    expect(current.client.leaseRemaining(owner, callId)).toBe(0);
  });

  it("blocks replacement after an aborted late join cannot be physically released", async () => {
    const current = rig();
    const late = deferred<VoxConnection>();
    current.inner.connect = vi.fn(async (request) => {
      current.requests.push(request);
      return late.promise;
    });
    const starting = current.call.start();
    await vi.advanceTimersByTimeAsync(0);
    const hangingUp = current.call.hangUp();
    const resource = connection(current.requests[0]);
    resource.close = vi.fn(async () => {
      throw new Error("cleanup failed");
    });
    late.resolve(resource);
    await Promise.all([starting, hangingUp]);
    expect(current.transport.available).toBe(false);
    await current.call.start();
    expect(current.inner.connect).toHaveBeenCalledTimes(1);
    expect(current.call.current().phase).not.toBe("active");
  });

  it("preserves the same reservation across reconnect and retires only the previous attempt timer", async () => {
    const current = rig();
    await current.call.start();
    await vi.advanceTimersByTimeAsync(45_000);
    await current.call.reconnect();
    expect(current.ends()).toHaveLength(0);
    expect(current.joined[0].close).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(45_000);
    expect(current.renewals()).toHaveLength(1);
    expect(current.call.current().callId).toBe(callId);
    expect(current.requests[0].mayTransmitAudio()).toBe(false);
    expect(current.requests[1].mayTransmitAudio()).toBe(true);
    current.requests[0].onEvent({ type: "ended" });
    expect(current.call.current().phase).toBe("active");
  });
});
