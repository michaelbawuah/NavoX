import { describe, expect, it } from "vitest";
import {
  approvedConnectBody,
  approvedServiceChoices,
  approvedServicePaths,
  canConnectApprovedService,
} from "./approved-connector-setup";

const approved = {
  id: "approved-service",
  name: "Approved service",
  authentication: "api_token" as const,
  read_capabilities: ["tasks.items.read", "calendar.events.read"],
};

describe("approved REST and MCP setup", () => {
  it("maps only reviewed identities and read grants into each setup request", () => {
    expect(approvedServicePaths("rest")).toEqual({
      options: "/connectors/generic-rest-api/configurations",
      connect: "/connectors/generic-rest-api/connect",
    });
    expect(approvedServicePaths("mcp")).toEqual({
      options: "/connectors/mcp/servers",
      connect: "/connectors/mcp/connect",
    });
    expect(
      approvedConnectBody(
        "mcp",
        approved,
        ["tasks.items.read"],
        "secret",
        "uuid",
      ),
    ).toEqual({
      server_id: "approved-service",
      capabilities: ["tasks.items.read"],
      token: "secret",
      confirmed: true,
      request_id: "uuid",
    });
    expect(
      approvedConnectBody(
        "rest",
        approved,
        ["tasks.items.read"],
        undefined,
        "uuid",
      ),
    ).toEqual({
      configuration_id: "approved-service",
      capabilities: ["tasks.items.read"],
      confirmed: true,
      request_id: "uuid",
    });
  });
  it("requires explicit selected permissions, consent and API token", () => {
    expect(
      canConnectApprovedService(approved, ["tasks.items.read"], true, true),
    ).toBe(true);
    expect(
      canConnectApprovedService(approved, ["tasks.items.read"], false, true),
    ).toBe(false);
    expect(canConnectApprovedService(approved, [], true, true)).toBe(false);
    expect(
      canConnectApprovedService(approved, ["tasks.items.write"], true, true),
    ).toBe(false);
    expect(
      canConnectApprovedService(approved, ["tasks.items.read"], true, false),
    ).toBe(false);
    expect(
      canConnectApprovedService(
        { ...approved, authentication: "none" },
        ["tasks.items.read"],
        true,
        false,
      ),
    ).toBe(true);
  });

  it("rejects malformed or unreviewed public configurations", () => {
    expect(approvedServiceChoices([approved])).toEqual([approved]);
    for (const invalid of [
      { ...approved, authentication: "oauth2" },
      {
        ...approved,
        read_capabilities: ["tasks.items.write", "tasks.items.write"],
      },
      { ...approved, read_capabilities: ["raw secret"] },
      { ...approved, name: "" },
      { ...approved, read_capabilities: [] },
    ]) {
      expect(() => approvedServiceChoices([invalid])).toThrow();
    }
    expect(() => approvedServiceChoices([approved, approved])).toThrow();
    expect(() => approvedServiceChoices({ config: approved })).toThrow();
  });
});
