import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});
describe("recovery release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps recovery off without explicit true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentRecovery).toBe(false);
    },
  );
  it("enables only the recovery surface when its own public flag is true", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentRecovery).toBe(true);
    expect(productFeatures.studentPlanning).toBe(false);
  });
});

describe("selected email release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps email review off without exact true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentEmail).toBe(false);
    },
  );
  it("does not turn on task planning or recovery with the email flag", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentEmail).toBe(true);
    expect(productFeatures.studentPlanning).toBe(false);
    expect(productFeatures.studentRecovery).toBe(false);
  });
});
