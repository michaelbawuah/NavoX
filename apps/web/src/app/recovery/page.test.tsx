import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentRecovery: false }));
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

import RecoveryPage from "./page";

afterEach(() => {
  flags.studentRecovery = false;
});
describe("optional recovery route", () => {
  it("does not expose the recovery page when the deployment flag is off", () => {
    expect(() => RecoveryPage()).toThrow("NOT_FOUND");
  });
  it("uses the account-scoped recovery view only when explicitly enabled", () => {
    flags.studentRecovery = true;
    expect(renderToStaticMarkup(createElement(RecoveryPage))).toContain(
      "<main>recovery</main>",
    );
  });
});
