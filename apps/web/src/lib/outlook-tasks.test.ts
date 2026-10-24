import { afterEach, expect, it, vi } from "vitest";
import { outlookTaskApi, prepareOutlookTask } from "./outlook-tasks";

const id = "11111111-1111-4111-8111-111111111111";
afterEach(() => vi.unstubAllGlobals());

it("uses the server's saved time zone and rejects a missing zone", async () => {
  const context = {
    server_now: "2026-10-24T10:00:00Z",
    timezone: "Pacific/Auckland",
    date: "2026-10-24",
    days: 7,
    courses: [],
    fixed_blocks: [],
    breaks: [],
    exceptions: [],
    occurrences: [],
    fixed_occurrences: [],
    conflicts: [],
    warnings: [],
  };
  const fetcher = vi.fn(async () => Response.json(context));
  vi.stubGlobal("fetch", fetcher);
  expect(
    await outlookTaskApi("workspace:owner").context(
      new AbortController().signal,
    ),
  ).toEqual({
    timezone: "Pacific/Auckland",
    server_now: context.server_now,
    courses: [],
  });
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toMatch(/\/outlook\/task-context$/);
  expect(init.headers).toMatchObject({ "X-NavoX-Account": "workspace:owner" });
  fetcher.mockImplementationOnce(async () =>
    Response.json({ ...context, timezone: undefined }),
  );
  await expect(
    outlookTaskApi("workspace:owner").context(new AbortController().signal),
  ).rejects.toThrow();
});

it("binds selected-message preparation to the current account without copying email text", async () => {
  const fetcher = vi.fn(async () =>
    Response.json({ detail: "Unavailable" }, { status: 503 }),
  );
  vi.stubGlobal("fetch", fetcher);
  await expect(
    prepareOutlookTask("workspace:owner", id, "opaque/+message=", id),
  ).rejects.toThrow();
  expect(fetcher).toHaveBeenCalledTimes(1);
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toMatch(/\/outlook\/task-reviews$/);
  expect(init.headers).toMatchObject({
    "X-NavoX-Account": "workspace:owner",
    "Content-Type": "application/json",
  });
  expect(JSON.parse(String(init.body))).toEqual({
    request_id: id,
    connection_id: id,
    source_message_id: "opaque/+message=",
    confirmed: true,
  });
  expect(init.credentials).toBe("include");
  expect(init.cache).toBe("no-store");
  expect(init.redirect).toBe("error");
});

it("sends the exact reviewed task decision through the existing private transport", async () => {
  const fetcher = vi.fn(async () =>
    Response.json({
      task_id: id,
      reused: true,
      available: true,
      actions_executed: false,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const decision = {
    request_id: id,
    revision: 3,
    source_hash: "a".repeat(64),
    confirmed: true as const,
  };
  const result = await outlookTaskApi("workspace:owner").accept(
    id,
    decision,
    new AbortController().signal,
  );
  expect(result.task_id).toBe(id);
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toMatch(new RegExp(`/outlook/task-reviews/${id}/accept-task$`));
  expect(init.headers).toMatchObject({ "X-NavoX-Account": "workspace:owner" });
  expect(JSON.parse(String(init.body))).toEqual(decision);
  expect(url).not.toContain("/send");
});

it("keeps source Forget owner-bound even when it has no body", async () => {
  const fetcher = vi.fn(async () =>
    Response.json({ erased: true, reviews_erased: 1, derived_tasks_erased: 1 }),
  );
  vi.stubGlobal("fetch", fetcher);
  await outlookTaskApi("workspace:owner").forget(
    id,
    new AbortController().signal,
  );
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toMatch(new RegExp(`/outlook/task-sources/${id}$`));
  expect(init.method).toBe("DELETE");
  expect(init.headers).toMatchObject({ "X-NavoX-Account": "workspace:owner" });
  expect(init.body).toBeUndefined();
});
