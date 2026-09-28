import { afterEach, describe, expect, it, vi } from "vitest";
import { authenticate } from "./account-access";

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
