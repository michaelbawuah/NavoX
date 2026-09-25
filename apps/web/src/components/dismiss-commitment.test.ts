import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DismissCommitment } from "./dismiss-commitment";

describe("not-a-task control", () => {
  it("offers explicit dismissal for an already active AI-created task", () => {
    const onDismiss = vi.fn();
    const markup = renderToStaticMarkup(
      createElement(DismissCommitment, {
        createdBy: "ai",
        status: "confirmed",
        busy: false,
        onDismiss,
      }),
    );
    expect(markup).toContain("Not a task");
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it.each([
    ["user", "confirmed"],
    ["ai", "completed"],
    ["ai", "rejected"],
    ["ai", "superseded"],
    ["ai", "candidate"],
  ])(
    "preserves manual and terminal controls for %s/%s",
    (createdBy, status) => {
      expect(
        renderToStaticMarkup(
          createElement(DismissCommitment, {
            createdBy,
            status,
            busy: false,
            onDismiss: vi.fn(),
          }),
        ),
      ).toBe("");
    },
  );
});
