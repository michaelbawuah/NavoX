import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentGoals: false }));
vi.mock("../../lib/product-features", () => ({ productFeatures: flags }));
vi.mock("../../components/account-workspace", () => ({
  AccountWorkspace: ({ view }: { view: string }) =>
    createElement("main", null, view),
}));
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NOT_FOUND");
  },
}));

import GoalsPage from "./page";

afterEach(() => {
  flags.studentGoals = false;
});
describe("optional goals route", () => {
  it("does not expose the goals page when the deployment flag is off", () => {
    expect(() => GoalsPage()).toThrow("NOT_FOUND");
  });
  it("uses the account-scoped goals view only when explicitly enabled", () => {
    flags.studentGoals = true;
    expect(renderToStaticMarkup(createElement(GoalsPage))).toContain(
      "<main>goals</main>",
    );
  });
});
