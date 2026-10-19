import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});
describe("authored whiteboard release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps the whiteboard off without exact true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentWhiteboard).toBe(false);
    },
  );
  it("enables practice lessons independently of models and coursework", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_WHITEBOARD_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_AGENT_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_COURSEWORK_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentWhiteboard).toBe(true);
    expect(productFeatures.studentAgent).toBe(false);
    expect(productFeatures.studentCoursework).toBe(false);
  });
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

describe("self-reported goals release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps goals off without exact true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_GOALS_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentGoals).toBe(false);
    },
  );
  it("enables goals independently of planning, recovery and email review", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_GOALS_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_RECOVERY_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_EMAIL_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentGoals).toBe(true);
    expect(productFeatures.studentPlanning).toBe(false);
    expect(productFeatures.studentRecovery).toBe(false);
    expect(productFeatures.studentEmail).toBe(false);
  });
});

describe("own-work export release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps own-work export off without exact true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_WORK_EXPORT_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentWorkExport).toBe(false);
    },
  );
  it("does not enable coursework, goals or model features implicitly", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_WORK_EXPORT_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_COURSEWORK_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_GOALS_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_AGENT_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentWorkExport).toBe(true);
    expect(productFeatures.studentCoursework).toBe(false);
    expect(productFeatures.studentGoals).toBe(false);
    expect(productFeatures.studentAgent).toBe(false);
  });
});

describe("manual day-review release gate", () => {
  it.each([undefined, "false", "1", "TRUE"])(
    "keeps day reviews off without explicit true (%s)",
    async (value) => {
      vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_COACH_ENABLED", value);
      vi.resetModules();
      const { productFeatures } = await import("./product-features");
      expect(productFeatures.studentCoach).toBe(false);
    },
  );
  it("enables only manual reviews without enabling planning, goals or models", async () => {
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_COACH_ENABLED", "true");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_PLANNING_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_GOALS_ENABLED", "false");
    vi.stubEnv("NEXT_PUBLIC_NAVOX_STUDENT_AGENT_ENABLED", "false");
    vi.resetModules();
    const { productFeatures } = await import("./product-features");
    expect(productFeatures.studentCoach).toBe(true);
    expect(productFeatures.studentPlanning).toBe(false);
    expect(productFeatures.studentGoals).toBe(false);
    expect(productFeatures.studentAgent).toBe(false);
  });
});
