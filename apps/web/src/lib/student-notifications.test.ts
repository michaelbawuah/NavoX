import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import manifest from "../app/manifest";
import { readAlertStatus, vapidKey } from "./student-notifications";

function status() {
  return {
    configured: false,
    public_key: null,
    settings: {
      enabled: false,
      next_class: true,
      leave_now: false,
      lead_minutes: 15,
      quiet_start: "23:00",
      quiet_end: "07:00",
      daily_cap: 12,
    },
    devices: [],
    timezone: "America/New_York",
    notice: "Unavailable",
  };
}

describe("class reminders", () => {
  it("preserves truthful unconfigured status and rejects malformed responses", () => {
    expect(readAlertStatus(status()).configured).toBe(false);
    expect(() =>
      readAlertStatus({ ...status(), devices: [{ id: "someone" }] }),
    ).toThrow();
    expect(() => readAlertStatus({ ...status(), public_key: 5 })).toThrow();
    expect(() => readAlertStatus(null)).toThrow();
  });
  it("requires a complete uncompressed VAPID public key", () => {
    const key = new Uint8Array(65);
    key[0] = 4;
    expect(vapidKey(Buffer.from(key).toString("base64url"))).toEqual(key);
    expect(() => vapidKey("aGVsbG8")).toThrow();
  });
  it("ships standalone install metadata with real 192 and 512 pixel PNGs", () => {
    const value = manifest();
    expect(value.display).toBe("standalone");
    expect(value.start_url).toBe("/");
    expect(value.scope).toBe("/");
    for (const size of [192, 512]) {
      const icon = readFileSync(resolve("public/icons", `navox-${size}.png`));
      expect(icon.subarray(1, 4).toString()).toBe("PNG");
      expect(icon.readUInt32BE(16)).toBe(size);
      expect(icon.readUInt32BE(20)).toBe(size);
    }
  });
  it("keeps private pages on the network and only opens the same-origin classes screen", async () => {
    const listeners = new Map<string, (event: unknown) => void>();
    const notifications: { title: string; options: Record<string, unknown> }[] =
      [];
    const opened: string[] = [];
    const self = {
      addEventListener: (name: string, callback: (event: unknown) => void) =>
        listeners.set(name, callback),
      skipWaiting: () => Promise.resolve(),
      location: { origin: "https://navox.net" },
      registration: {
        showNotification: (title: string, options: Record<string, unknown>) => {
          notifications.push({ title, options });
          return Promise.resolve();
        },
      },
      clients: {
        claim: () => Promise.resolve(),
        matchAll: () => Promise.resolve([]),
        openWindow: (url: string) => {
          opened.push(url);
          return Promise.resolve();
        },
      },
    };
    runInNewContext(readFileSync(resolve("public/sw.js"), "utf8"), {
      self,
      URL,
    });
    expect(listeners.has("fetch")).toBe(false);
    const promises: Promise<unknown>[] = [];
    listeners.get("push")?.({
      data: {
        json: () => ({
          tag: `class-${"a".repeat(26)}`,
          title: "Private course",
          body: "Private room",
          url: "https://evil.test",
        }),
      },
      waitUntil: (promise: Promise<unknown>) => promises.push(promise),
    });
    await Promise.all(promises);
    expect(notifications).toHaveLength(1);
    expect(JSON.stringify(notifications)).not.toContain("Private");
    expect(notifications[0].options.tag).toBe(`class-${"a".repeat(26)}`);
    listeners.get("notificationclick")?.({
      notification: { close: () => {}, data: { url: "https://evil.test" } },
      waitUntil: (promise: Promise<unknown>) => promises.push(promise),
    });
    await Promise.all(promises);
    expect(opened).toEqual(["https://navox.net/classes"]);
  });
});
