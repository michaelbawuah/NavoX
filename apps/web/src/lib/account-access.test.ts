import { afterEach, describe, expect, it, vi } from "vitest";
import { authenticate, restoreAccountAccess } from "./account-access";

afterEach(() => vi.unstubAllGlobals());

describe("account access", () => {
  const credentials = {
    email: "demo@example.com",
    password: "example-test-password",
  };
  it.each(["register", "login"] as const)(
    "preserves the %s endpoint and cookie contract",
    async (mode) => {
      const account = { id: "example-account", email: credentials.email };
      const request = vi.fn().mockResolvedValue(Response.json(account));
      vi.stubGlobal("fetch", request);
      await expect(
        authenticate("https://navox.example/api/v1", mode, credentials),
      ).resolves.toEqual(account);
      expect(request).toHaveBeenCalledWith(
        `https://navox.example/api/v1/auth/${mode}`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(credentials),
        },
      );
    },
  );
  it("turns an offline API into a retryable message without leaking request details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("private transport details")),
    );
    await expect(
      authenticate("https://navox.example/api/v1", "login", credentials),
    ).rejects.toThrow("Could not reach NavoX. Please try again.");
  });
  it("preserves an actionable server rejection", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json(
            { detail: "Invalid email or password." },
            { status: 401 },
          ),
        ),
    );
    await expect(
      authenticate("https://navox.example/api/v1", "login", credentials),
    ).rejects.toThrow("Invalid email or password.");
  });
  it("handles a non-JSON server failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })),
    );
    await expect(
      authenticate("https://navox.example/api/v1", "login", credentials),
    ).rejects.toThrow("Something went wrong. Please try again.");
  });
});

describe("session restoration", () => {
  const apiBase = "https://navox.example/api/v1";
  const account = {
    id: "owner",
    email: "owner@example.test",
    display_name: "Owner",
    workspace: { id: "personal", name: "Personal", workspace_type: "personal" },
  };

  it.each(["network", "server", "invalid JSON"])(
    "preserves verified sign-in when connected apps fail with %s",
    async (failure) => {
      const request = vi.fn().mockResolvedValueOnce(Response.json(account));
      if (failure === "network")
        request.mockRejectedValueOnce(new TypeError("offline"));
      else if (failure === "server")
        request.mockResolvedValueOnce(new Response(null, { status: 503 }));
      else request.mockResolvedValueOnce(new Response("invalid"));
      vi.stubGlobal("fetch", request);
      const restored = await restoreAccountAccess(apiBase);
      expect(restored.account).toEqual(account);
      expect(restored.connections).toEqual([]);
      expect(restored.connectionsError).toContain(
        "Connected apps could not be loaded",
      );
    },
  );

  it("recognizes a verified empty connection list separately from an unavailable list", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(Response.json(account))
      .mockResolvedValueOnce(Response.json([]));
    vi.stubGlobal("fetch", request);
    await expect(restoreAccountAccess(apiBase)).resolves.toEqual({
      account,
      connections: [],
      connectionsError: "",
    });
    expect(request).toHaveBeenNthCalledWith(
      2,
      `${apiBase}/connections/google`,
      { credentials: "include" },
    );
  });

  it("does not load account connections after a rejected session", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", request);
    await expect(restoreAccountAccess(apiBase)).resolves.toEqual({
      account: null,
      connections: [],
      connectionsError: "",
    });
    expect(request).toHaveBeenCalledTimes(1);
  });
});
