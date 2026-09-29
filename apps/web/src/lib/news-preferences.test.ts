import { describe, expect, it } from "vitest";
import { parseFollowedLabels } from "./news-preferences";

describe("explicit News interests", () => {
  it("normalizes comma-separated topics and entities without inferring anything", () => {
    expect(parseFollowedLabels(" NASA, Ghana,  Nvidia , ")).toEqual([
      "NASA",
      "Ghana",
      "Nvidia",
    ]);
    expect(parseFollowedLabels(null)).toEqual([]);
  });

  it("keeps user wording rather than assigning political or sensitive labels", () => {
    expect(parseFollowedLabels("climate policy, Cornell University")).toEqual([
      "climate policy",
      "Cornell University",
    ]);
  });
});
