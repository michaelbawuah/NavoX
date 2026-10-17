import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentEmail: false }));
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

import EmailActionsPage from "./page";

afterEach(() => {
  flags.studentEmail = false;
});
describe("optional email review route", () => {
  it("does not expose the email review page when the deployment flag is off", () => {
    expect(() => EmailActionsPage()).toThrow("NOT_FOUND");
  });
  it("uses the account-scoped email review view only when explicitly enabled", () => {
    flags.studentEmail = true;
    expect(renderToStaticMarkup(createElement(EmailActionsPage))).toContain(
      "<main>student-email</main>",
    );
  });
});
