import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { OperationalAssistance } from "./operational-assistance";

it("requires a selection and disables assistance when paused", () => {
  const markup = renderToStaticMarkup(
    createElement(OperationalAssistance, {
      items: [{ id: "a", title: "Saved task", type: "task" }],
      paused: true,
    }),
  );
  expect(markup).toContain("Saved task");
  expect(markup).toMatch(/<fieldset[^>]*disabled/);
  expect(markup).toMatch(/<button[^>]*disabled[^>]*>Get suggestions/);
  expect(markup).not.toContain("Approve and send");
});
