import { describe, expect, it } from "vitest";
import {
  canvasAuthorizationUrl,
  canvasInstitutions,
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
  it("accepts a public multi-school catalog and binds a redirect to its selected school", () => {
    const institutions = canvasInstitutions([
      {
        id: "north",
        name: "North University",
        origin: "https://canvas.north.edu",
      },
      {
        id: "south",
        name: "South College",
        origin: "https://canvas.south.edu",
      },
    ]);
    expect(institutions).toHaveLength(2);
    expect(() =>
      canvasAuthorizationUrl(
        "https://canvas.south.edu/login/oauth2/auth?state=nonce",
        institutions[0].origin,
      ),
    ).toThrow();
    expect(
      canvasAuthorizationUrl(
        "https://canvas.south.edu/login/oauth2/auth?state=nonce",
        institutions[1].origin,
      ),
    ).toContain("state=nonce");
  });
  it.each(
    [
      [],
      [{ id: "north", name: "North", origin: "http://canvas.north.edu" }],
      [
        {
          id: "north",
          name: "North",
          origin: "https://canvas.north.edu",
          client_secret: "leak",
        },
      ],
      [
        { id: "north", name: "North", origin: "https://canvas.north.edu" },
        { id: "north", name: "Other", origin: "https://canvas.other.edu" },
      ],
    ].map((value) => ({ value })),
  )("rejects malformed or duplicate school catalogs", ({ value }) => {
    expect(() => canvasInstitutions(value)).toThrow();
  });
});
