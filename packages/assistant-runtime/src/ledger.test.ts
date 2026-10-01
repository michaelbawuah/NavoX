import { describe, expect, it } from "vitest";
import {
  assertTurnBudget,
  fingerprintTurn,
  resolveReplay,
  retentionWindow,
} from "./ledger";
import { LIMITS } from "./limits";

const turn = {
  text: "What am I missing today?",
  modality: "TEXT" as const,
  timezone: "America/New_York",
  referents: ["task-a"],
};

describe("turn ledger", () => {
  it("fingerprints the client payload deterministically", () => {
    expect(fingerprintTurn(turn)).toHaveLength(64);
    expect(fingerprintTurn(turn)).toBe(fingerprintTurn({ ...turn }));
  });

  it("separates an exact retry from a changed payload", () => {
    const original = fingerprintTurn(turn);
    expect(resolveReplay(original, fingerprintTurn({ ...turn }))).toBe(
      "replay",
    );
    expect(
      resolveReplay(
        original,
        fingerprintTurn({ ...turn, text: "Something else" }),
      ),
    ).toBe("conflict");
    expect(
      resolveReplay(original, fingerprintTurn({ ...turn, modality: "VOICE" })),
    ).toBe("conflict");
    expect(
      resolveReplay(original, fingerprintTurn({ ...turn, timezone: null })),
    ).toBe("conflict");
    expect(
      resolveReplay(original, fingerprintTurn({ ...turn, referents: [] })),
    ).toBe("conflict");
  });

  it("bounds retention to 30 days", () => {
    const now = new Date("2026-09-30T12:00:00.000Z");
    const window = retentionWindow(now);
    expect(window.createdAt).toBe("2026-09-30T12:00:00.000Z");
    expect(window.expiresAt).toBe("2026-10-30T12:00:00.000Z");
    expect((Date.parse(window.expiresAt) - now.getTime()) / 86_400_000).toBe(
      LIMITS.retentionDays,
    );
  });

  it("refuses to grow a session past its turn budget", () => {
    expect(() => assertTurnBudget(1)).not.toThrow();
    expect(() => assertTurnBudget(LIMITS.maxTurnsPerSession)).not.toThrow();
    expect(() => assertTurnBudget(LIMITS.maxTurnsPerSession + 1)).toThrow(
      /turn limit/i,
    );
  });
});
