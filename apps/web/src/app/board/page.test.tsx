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

import BoardPage, { metadata } from "./page";

afterEach(() => {
  flags.studentWhiteboard = false;
});

describe("NavoX Board entry", () => {
  it("keeps the canonical entry off until the deployment explicitly enables it", () => {
    expect(() => BoardPage()).toThrow("NOT_FOUND");
  });
  it("opens an authenticated Board view with its own product name", () => {
    flags.studentWhiteboard = true;
    expect(metadata.title).toBe("NavoX Board");
    expect(renderToStaticMarkup(createElement(BoardPage))).toBe(
      "<main>board</main>",
    );
  });
});
