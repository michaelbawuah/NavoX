import { describe, expect, it } from "vitest";
import { platform } from "./platform";

describe("platform identity", () => {
  it("exposes the NavoX engineering foundation version", () => {
    expect(platform).toEqual({ name: "NavoX", version: "0.1.0" });
  });
});
