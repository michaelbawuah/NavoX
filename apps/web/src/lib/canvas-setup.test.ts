import { describe, expect, it } from "vitest";
import {
  canvasAuthorizationUrl,
  canvasOrigin,
  canvasSelection,
} from "./canvas-setup";

describe("Canvas read consent and OAuth redirect boundary", () => {
  it("accepts dependent read scopes", () =>
    expect(
      canvasSelection(["academic.courses.read", "academic.assignments.read"]),
    ).toHaveLength(2));
  it.each(
    [
      [],
      ["academic.assignments.read"],
      ["academic.courses.read", "academic.submissions.read"],
      ["academic.courses.read", "assignments.submit"],
      ["academic.courses.read", "academic.courses.read"],
    ].map((values) => ({ values })),
  )("rejects invalid grants $values", ({ values }) =>
    expect(() => canvasSelection(values)).toThrow(),
  );
  it("accepts the configured OAuth origin only", () =>
    expect(
      canvasAuthorizationUrl(
        "https://canvas.example.edu/login/oauth2/auth?state=nonce",
        "https://canvas.example.edu",
      ),
    ).toContain("state=nonce"));
  it.each([
    "javascript:alert(1)",
    "https://evil.example/login/oauth2/auth?state=n",
    "https://canvas.example.edu/other?state=n",
    "https://canvas.example.edu/login/oauth2/auth",
    "https://u:p@canvas.example.edu/login/oauth2/auth?state=n",
  ])("rejects unsafe redirect %s", (value) =>
    expect(() =>
      canvasAuthorizationUrl(value, "https://canvas.example.edu"),
    ).toThrow(),
  );
  it.each([
    "http://canvas.example.edu",
    "https://canvas.example.edu:8080",
    "https://canvas.example.edu/path",
  ])("rejects unsafe setup %s", (value) =>
    expect(() => canvasOrigin(value)).toThrow(),
  );
});
