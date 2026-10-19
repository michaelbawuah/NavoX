import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

const flags = vi.hoisted(() => ({ studentWhiteboard: false }));
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

import WhiteboardPage from "./page";

afterEach(() => {
  flags.studentWhiteboard = false;
});

describe("optional whiteboard route", () => {
  it("does not expose lessons with the deployment flag off", () => {
    expect(() => WhiteboardPage()).toThrow("NOT_FOUND");
  });

  it("opens the account-scoped whiteboard view only when explicitly enabled", () => {
    flags.studentWhiteboard = true;
    expect(renderToStaticMarkup(createElement(WhiteboardPage))).toBe(
      "<main>whiteboard</main>",
    );
  });
});
