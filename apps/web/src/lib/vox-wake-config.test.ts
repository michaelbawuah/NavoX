import { describe, expect, it, vi } from "vitest";
import { VoxReservationClient } from "./vox-reservations";

const owner = {
  userId: "11111111-1111-4111-8111-111111111111",
  workspaceId: "22222222-2222-4222-8222-222222222222",
};
const callId = "33333333-3333-4333-8333-333333333333";
const connectionId = "44444444-4444-4444-8444-444444444444";

describe("private wake configuration cookie transport", () => {
  it("uses the same bounded exact-owner POST and treats disabled configuration as unavailable", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(new Response(null, { status: 404 }));
    const client = new VoxReservationClient({
      apiBaseUrl: "https://api.example/api/v1",
      fetch,
    });
    const signal = new AbortController().signal;
    await expect(
      client.mediaRequest(
        owner,
        callId,
        7,
        { action: "wake-config", connectionId },
        signal,
      ),
    ).resolves.toBeNull();
    expect(fetch).toHaveBeenCalledWith(
      `https://api.example/api/v1/vox/calls/${callId}/media/${connectionId}/wake-config`,
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: {
          "Content-Type": "application/json",
          "X-NavoX-Account": `${owner.workspaceId}:${owner.userId}`,
        },
        body: JSON.stringify({ generation: 7 }),
        signal: expect.any(AbortSignal),
      }),
    );
  });
  it.each([401, 403, 409, 500])(
    "never treats HTTP%s as disabled licensed configuration",
    async (status) => {
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValue(new Response(null, { status }));
      const client = new VoxReservationClient({ fetch });
      await expect(
        client.mediaRequest(owner, callId, 7, {
          action: "wake-config",
          connectionId,
        }),
      ).rejects.toThrow();
    },
  );
  it("cancels the private configuration read on owner teardown even if fetch ignores abort", async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(
      () =>
        new Promise((yes) => {
          resolve = yes;
        }),
    );
    const client = new VoxReservationClient({ fetch });
    const abort = new AbortController();
    const pending = client.mediaRequest(
      owner,
      callId,
      7,
      { action: "wake-config", connectionId },
      abort.signal,
    );
    const rejected = expect(pending).rejects.toThrow();
    abort.abort();
    await rejected;
    const cancel = vi.fn();
    const body = new ReadableStream({ cancel });
    resolve(
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledTimes(1));
  });
});
