import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentCoach: false }));
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

import DayReviewPage from "./page";

afterEach(() => {
  flags.studentCoach = false;
});
describe("optional day-review route", () => {
  it("does not expose the day-review page when the deployment flag is off", () => {
    expect(() => DayReviewPage()).toThrow("NOT_FOUND");
  });
  it("uses the account-scoped day-review view only when explicitly enabled", () => {
    flags.studentCoach = true;
    expect(renderToStaticMarkup(createElement(DayReviewPage))).toContain(
      "<main>day-review</main>",
    );
  });
});
