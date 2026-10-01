import { describe, expect, it } from "vitest";
import {
  assertRegistryIntegrity,
  CAPABILITIES,
  capabilityForIntentKind,
  listCapabilities,
  resolveCapability,
} from "./capabilities";

describe("capability registry", () => {
  it("holds its invariants", () => {
    expect(() => assertRegistryIntegrity()).not.toThrow();
  });

  it("authorizes exactly the read-only routes this phase owns", () => {
    const ids = CAPABILITIES.map((capability) => capability.id);
    expect(ids).toEqual([
      "today.read",
      "email.search",
      "subscription.search",
      "news.read",
      "weather.read",
      "class.next",
      "time.now",
    ]);
    expect(
      CAPABILITIES.every((capability) => capability.mode === "read_only"),
    ).toBe(true);
    expect(
      CAPABILITIES.every(
        (capability) => capability.requires_approval === false,
      ),
    ).toBe(true);
  });

  it("resolves known capabilities and refuses unknown ones", () => {
    expect(resolveCapability("today.read").delegate_target).toBe("today.query");
    expect(resolveCapability("email.search").delegate_target).toBe(
      "knowledge.search",
    );
    expect(resolveCapability("subscription.search").delegate_target).toBe(
      "subscriptions.query",
    );
    expect(resolveCapability("news.read").delegate_target).toBe(
      "news.trending",
    );
    expect(resolveCapability("weather.read").delegate_target).toBe(
      "workspace.weather",
    );
    expect(resolveCapability("class.next").delegate_target).toBe(
      "knowledge.class-sources",
    );
    expect(resolveCapability("time.now").delegate_target).toBe("runtime.clock");
    expect(resolveCapability("time.now").mode).toBe("read_only");
    expect(resolveCapability("time.now").requires_approval).toBe(false);
    expect(() => resolveCapability("email.send")).toThrow(/not available/i);
    expect(() => resolveCapability(null)).toThrow(/not available/i);
    expect(listCapabilities()).toHaveLength(7);
  });

  it("maps every planned route to an owned capability or a clarification", () => {
    expect(capabilityForIntentKind("today.read")?.id).toBe("today.read");
    expect(capabilityForIntentKind("email.search")?.id).toBe("email.search");
    expect(capabilityForIntentKind("subscription.search")?.id).toBe(
      "subscription.search",
    );
    expect(capabilityForIntentKind("news.read")?.id).toBe("news.read");
    expect(capabilityForIntentKind("weather.read")?.id).toBe("weather.read");
    expect(capabilityForIntentKind("class.next")?.id).toBe("class.next");
    expect(capabilityForIntentKind("time.now")?.id).toBe("time.now");
    expect(capabilityForIntentKind("assistant.clarify")).toBeNull();
    expect(() => capabilityForIntentKind("files.delete" as never)).toThrow(
      /no capability registry entry/i,
    );
  });
});
