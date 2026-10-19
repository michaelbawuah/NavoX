import { afterEach, describe, expect, it, vi } from "vitest";
import {
  authenticate,
  authenticateAccountAccess,
  loadGoogleConnections,
  restoreAccountAccess,
} from "./account-access";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

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
      const payload =
        mode === "register"
          ? { ...credentials, display_name: "Demo" }
          : credentials;
      vi.stubGlobal("fetch", request);
      await expect(
        authenticate("https://navox.example/api/v1", mode, payload),
      ).resolves.toEqual(account);
      expect(request).toHaveBeenCalledWith(
        `https://navox.example/api/v1/auth/${mode}`,
        {
          method: "POST",
          credentials: "include",
          signal: expect.any(AbortSignal),
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
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

  it("turns FastAPI field validation into useful signup guidance", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          {
            detail: [
              {
                loc: ["body", "email"],
                type: "value_error",
                msg: "private input",
              },
              { loc: ["body", "password"], type: "string_too_long" },
              { loc: ["body", "display_name"], type: "string_too_short" },
            ],
          },
          { status: 422 },
        ),
      ),
    );
    await expect(
      authenticate("https://navox.example/api/v1", "register", {
        ...credentials,
        display_name: "Demo",
      }),
    ).rejects.toThrow(
      "Enter a valid email address. Use a password with 12 to 128 characters. Enter your name.",
    );
  });

  it.each([
    {
      display_name: "   ",
      password: credentials.password,
      error: "Enter your name.",
    },
    { display_name: "Demo", password: "short", error: "12 to 128 characters" },
    {
      display_name: "Demo",
      password: "p".repeat(129),
      error: "12 to 128 characters",
    },
    {
      display_name: "D".repeat(257),
      password: credentials.password,
      error: "up to 256 characters",
    },
  ])(
    "rejects invalid registration input before making a request: $error",
    async ({ error, ...input }) => {
      const request = vi.fn();
      vi.stubGlobal("fetch", request);
      await expect(
        authenticate("https://navox.example/api/v1", "register", {
          email: credentials.email,
          ...input,
        }),
      ).rejects.toThrow(error);
      expect(request).not.toHaveBeenCalled();
    },
  );

  it("normalizes name and email without changing password characters", async () => {
    const request = vi.fn().mockResolvedValue(Response.json({ id: "owner" }));
    vi.stubGlobal("fetch", request);
    await authenticate("https://navox.example/api/v1", "register", {
      email: " demo@example.com ",
      display_name: "  Demo  ",
      password: " example-test-password ",
    });
    expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({
      email: "demo@example.com",
      display_name: "Demo",
      password: " example-test-password ",
    });
  });

  it.each(["", "p".repeat(129)])(
    "rejects an out-of-range login password",
    async (password) => {
      const request = vi.fn();
      vi.stubGlobal("fetch", request);
      await expect(
        authenticate("https://navox.example/api/v1", "login", {
          ...credentials,
          password,
        }),
      ).rejects.toThrow("Enter your password (up to 128 characters).");
      expect(request).not.toHaveBeenCalled();
    },
  );
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
      {
        credentials: "include",
        cache: "no-store",
        signal: expect.any(AbortSignal),
      },
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

describe("returning sign-in connections", () => {
  const apiBase = "https://navox.example/api/v1";
  const credentials = {
    email: "owner@example.test",
    password: "owner-password",
  };
  const account = {
    id: "owner",
    email: credentials.email,
    display_name: "Owner",
    workspace: { id: "personal", name: "Personal", workspace_type: "personal" },
  };
  const connection = {
    id: "google-1",
    provider: "google",
    external_email: credentials.email,
    status: "active",
    granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    last_checked_at: null,
    last_error: null,
  };

  it("restores saved Google permissions immediately after password sign-in", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(Response.json(account))
      .mockResolvedValueOnce(Response.json([connection]));
    vi.stubGlobal("fetch", request);
    await expect(
      authenticateAccountAccess(apiBase, "login", credentials),
    ).resolves.toEqual({
      account,
      connections: [connection],
      connectionsError: "",
    });
    expect(request).toHaveBeenNthCalledWith(
      2,
      `${apiBase}/connections/google`,
      {
        credentials: "include",
        cache: "no-store",
        signal: expect.any(AbortSignal),
      },
    );
  });

  it.each(["network", "server", "invalid JSON", "invalid shape"])(
    "keeps successful login usable when connections fail with %s, then supports retry",
    async (failure) => {
      const request = vi.fn().mockResolvedValueOnce(Response.json(account));
      if (failure === "network")
        request.mockRejectedValueOnce(new TypeError("offline"));
      else if (failure === "server")
        request.mockResolvedValueOnce(new Response(null, { status: 503 }));
      else if (failure === "invalid JSON")
        request.mockResolvedValueOnce(new Response("invalid"));
      else
        request.mockResolvedValueOnce(Response.json({ detail: "not a list" }));
      request.mockResolvedValueOnce(Response.json([connection]));
      vi.stubGlobal("fetch", request);
      const access = await authenticateAccountAccess(
        apiBase,
        "login",
        credentials,
      );
      expect(access.account).toEqual(account);
      expect(access.connections).toEqual([]);
      expect(access.connectionsError).toContain("Retry");
      await expect(loadGoogleConnections(apiBase)).resolves.toEqual([
        connection,
      ]);
      expect(request).toHaveBeenCalledTimes(3);
    },
  );

  it("bounds a stalled connection request without undoing successful sign-in", async () => {
    const controller = new AbortController();
    const timeout = vi
      .spyOn(AbortSignal, "timeout")
      .mockReturnValue(controller.signal);
    const request = vi
      .fn()
      .mockResolvedValueOnce(Response.json(account))
      .mockImplementationOnce(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener(
              "abort",
              () => reject(options.signal?.reason),
              { once: true },
            );
          }),
      );
    vi.stubGlobal("fetch", request);
    const access = authenticateAccountAccess(apiBase, "login", credentials);
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(timeout).toHaveBeenCalledWith(10_000);
    controller.abort(new DOMException("Connection timed out", "TimeoutError"));
    await expect(access).resolves.toEqual({
      account,
      connections: [],
      connectionsError: expect.stringContaining("Retry"),
    });
  });

  it("does not fetch Google accounts after failed authentication", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ detail: "Invalid email or password" }, { status: 401 }),
      );
    vi.stubGlobal("fetch", request);
    await expect(
      authenticateAccountAccess(apiBase, "login", credentials),
    ).rejects.toThrow("Invalid email or password");
    expect(request).toHaveBeenCalledTimes(1);
  });
});
