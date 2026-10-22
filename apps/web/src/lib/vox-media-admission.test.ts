import { describe, expect, it, vi } from "vitest";
import type { VoxTransportRequest } from "./vox-call";
import { createVoxMediaAdmission } from "./vox-media-admission";
import { VoxReservationClient } from "./vox-reservations";

const owner = {
  userId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "22222222-2222-4222-8222-222222222222",
};
const callId = "33333333-3333-4333-8333-333333333333";
const connectionId = "44444444-4444-4444-8444-444444444444";
const requestId = "55555555-5555-4555-8555-555555555555";
const now = Date.parse("2026-10-22T12:00:00Z");
function setup(qualified = true) {
  const reservations = new VoxReservationClient();
  const authority = vi
    .spyOn(reservations, "mediaAuthority")
    .mockReturnValue({ generation: 7, expiresAt: now + 120_000 });
  const binding = {
    connection_id: connectionId,
    client_call_id: callId,
    generation: 7,
    room_name: `vx-${"a".repeat(32)}`,
    participant_identity: `vc-${"b".repeat(32)}`,
    agent_identity: `va-${"c".repeat(32)}`,
    expires_at: new Date(now + 120_000).toISOString(),
  };
  const grant = {
    ...binding,
    server_url: "wss://project.livekit.cloud",
    token: "e30.e30.c2ln",
  };
  const media = vi.spyOn(reservations, "mediaRequest").mockResolvedValue(grant);
  const abort = new AbortController();
  const request: VoxTransportRequest = {
    owner,
    callId,
    signal: abort.signal,
    mayTransmitAudio: () => true,
    mayPlayAudio: () => true,
    mayListenForWake: () => false,
    onWake: () => {},
    onEvent: () => {},
  };
  const admission = createVoxMediaAdmission({
    reservations,
    qualified,
    now: () => now,
    requestId: () => requestId,
  });
  return { authority, binding, grant, media, abort, request, admission };
}
describe("native Vox media admission", () => {
  it("does no work at construction and requires independent qualification", async () => {
    const env = setup(false);
    expect(env.admission.available).toBe(false);
    expect(env.authority).not.toHaveBeenCalled();
    await expect(env.admission.join(env.request)).rejects.toThrow();
    expect(env.media).not.toHaveBeenCalled();
  });
  it("captures the live reservation generation and exact immutable epoch", async () => {
    const env = setup();
    const lease = await env.admission.join(env.request);
    expect(env.authority).toHaveBeenCalledWith(owner, callId);
    expect(env.media).toHaveBeenCalledWith(
      owner,
      callId,
      7,
      { action: "join", requestId },
      env.abort.signal,
    );
    expect(lease.connectionId).toBe(connectionId);
    expect(lease.generation).toBe(7);
    expect(lease.expiresAt).toBe(now + 120_000);
    env.media.mockResolvedValue(env.binding);
    await expect(lease.renew(new AbortController().signal)).resolves.toEqual({
      connectionId,
      generation: 7,
      expiresAt: now + 120_000,
    });
  });
  it.each([
    { connection_id: "bad" },
    { client_call_id: requestId },
    { generation: 8 },
    { room_name: "user@example.com" },
    { agent_identity: "va-other" },
    { expires_at: new Date(now + 121_000).toISOString() },
    { expires_at: new Date(now).toISOString() },
    { server_url: "https://project.livekit.cloud" },
    { server_url: "wss://project.livekit.cloud?secret=x" },
    { token: "raw-secret" },
    { unexpected: true },
  ])("rejects malformed or mismatched admission %j", async (change) => {
    const env = setup();
    env.media.mockResolvedValue({ ...env.grant, ...change });
    await expect(env.admission.join(env.request)).rejects.toThrow();
  });
  it.each([
    { connection_id: requestId },
    { generation: 8 },
    { room_name: `vx-${"d".repeat(32)}` },
    { participant_identity: `vc-${"e".repeat(32)}` },
    { agent_identity: `va-${"f".repeat(32)}` },
    { expires_at: new Date(now + 119_000).toISOString() },
    { token: "e30.e30.c2ln" },
  ])("rejects replacement or changed renewal authority %j", async (change) => {
    const env = setup();
    const lease = await env.admission.join(env.request);
    env.media.mockResolvedValue({ ...env.binding, ...change });
    await expect(lease.renew(new AbortController().signal)).rejects.toThrow();
  });
  it("closes an epoch once after caller abort independently of reservation removal", async () => {
    const env = setup();
    const lease = await env.admission.join(env.request);
    env.abort.abort();
    env.media.mockResolvedValue(null);
    const first = lease.close();
    expect(lease.close()).toBe(first);
    await first;
    expect(env.media).toHaveBeenLastCalledWith(owner, callId, 7, {
      action: "close",
      connectionId,
    });
    expect(env.media).toHaveBeenCalledTimes(2);
    await expect(lease.renew(new AbortController().signal)).rejects.toThrow();
  });
  it("keeps an uncertain cleanup rejection shared without silently retrying", async () => {
    const env = setup();
    const lease = await env.admission.join(env.request);
    env.media.mockRejectedValue(new Error("unavailable"));
    const close = lease.close();
    await expect(close).rejects.toThrow();
    expect(lease.close()).toBe(close);
    expect(env.media).toHaveBeenCalledTimes(2);
  });
});
