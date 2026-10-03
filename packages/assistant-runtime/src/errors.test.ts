import { describe, expect, it } from "vitest";
import { AssistantError, toAssistantError } from "./errors";

describe("assistant error boundary", () => {
  it("preserves intentional public errors", () => {
    const error = new AssistantError("forbidden", "Access changed.");
    expect(toAssistantError(error)).toBe(error);
  });

  it("does not disclose unexpected database or connection details", () => {
    const failure = toAssistantError(
      new Error("password=secret host=internal-db query=SELECT * FROM users"),
    );
    expect(failure.code).toBe("unavailable");
    expect(failure.status).toBe(503);
    expect(failure.message).toBe("The assistant is unavailable right now.");
    expect(failure.message).not.toMatch(/secret|internal-db|SELECT/i);
  });
});
