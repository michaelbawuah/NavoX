import { afterEach, describe, expect, it, vi } from "vitest";
import {
  blankWorkExportDraft,
  downloadOwnWork,
  observeWorkExportPrivacy,
  ownWorkExportInput,
  readWorkExportContext,
  readWorkExportPdf,
  StudentWorkExportController,
  sameWorkExportContext,
  type WorkExportApi,
  type WorkExportContext,
  type WorkExportDownloadSurface,
  workExportApi,
  workExportError,
  workExportLifetime,
} from "./student-work-export";

const user = "11111111-1111-4111-8111-111111111111",
  workspace = "22222222-2222-4222-8222-222222222222",
  course = "33333333-3333-4333-8333-333333333333",
  owner = `${workspace}:${user}`;
const context: WorkExportContext = {
  course_revision: 3,
  generation: 2,
  course_label: "ECE2300 - My course",
  max_text_chars: 20000,
  max_pages_including_title: 20,
  max_pdf_bytes: 2097152,
  saved: false,
};
const blob = new Blob(["%PDF-1.4\n%%EOF"], { type: "application/pdf" });
const controllers: StudentWorkExportController[] = [];
function fixture() {
  let time = 0;
  const release = vi.fn(),
    save = vi.fn(() => release);
  const api = {
    account: vi.fn<WorkExportApi["account"]>(async () => owner),
    context: vi.fn<WorkExportApi["context"]>(async () => ({ ...context })),
    pdf: vi.fn<WorkExportApi["pdf"]>(async () => blob),
  };
  const controller = new StudentWorkExportController(
    owner,
    course,
    api,
    save,
    () => time,
  );
  controllers.push(controller);
  return {
    controller,
    api,
    save,
    release,
    clock: (value: number) => {
      time = value;
    },
  };
}
function ready(controller: StudentWorkExportController) {
  controller.edit("title", "My own title");
  controller.edit("text", "  My own work.\nSecond line.  ");
  controller.confirm("confirmed_own_work", true);
  controller.confirm("confirmed_metadata", true);
}
afterEach(() => {
  for (const controller of controllers) controller.dispose();
  controllers.length = 0;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe("own-work export boundaries", () => {
  it("accepts only bounded unsaved context and strips unrelated source fields", () => {
    const value = readWorkExportContext({
      ...context,
      tutor_text: "Never import",
      source_notes: "Never import",
    });
    expect(value).toEqual(context);
    expect(value).not.toHaveProperty("tutor_text");
    expect(() => readWorkExportContext({ ...context, saved: true })).toThrow();
    expect(() =>
      readWorkExportContext({ ...context, max_pages_including_title: 21 }),
    ).toThrow();
  });
  it("requires both independent confirmations and starts with no attribution or source text", () => {
    const draft = blankWorkExportDraft();
    expect(draft).toEqual({
      title: "",
      text: "",
      author: "",
      include_course: false,
      confirmed_own_work: false,
      confirmed_metadata: false,
    });
    expect(() =>
      ownWorkExportInput(context, {
        ...draft,
        title: "Title",
        text: "Work",
        confirmed_own_work: true,
      }),
    ).toThrow(/Confirm/);
    expect(() =>
      ownWorkExportInput(context, {
        ...draft,
        title: "Title",
        text: "Work",
        confirmed_metadata: true,
      }),
    ).toThrow(/Confirm/);
  });
  it("preserves exactly entered text and whitelists only approved metadata", () => {
    const draft = {
      ...blankWorkExportDraft(),
      title: "My title",
      text: "  A line\r\nSecond line.  ",
      author: "",
      confirmed_own_work: true,
      confirmed_metadata: true,
    };
    const value = ownWorkExportInput(context, {
      ...draft,
      source_text: "Do not import",
    } as typeof draft);
    expect(value.text).toBe(draft.text);
    expect(value.author).toBeNull();
    expect(value.course_label).toBeNull();
    expect(value).not.toHaveProperty("source_text");
    expect(
      ownWorkExportInput(context, {
        ...draft,
        author: "My chosen name",
        include_course: true,
      }),
    ).toMatchObject({
      author: "My chosen name",
      course_label: context.course_label,
      course_revision: 3,
      generation: 2,
    });
  });
  it("rejects blank, control-bearing or overlong input without silently editing it", () => {
    const draft = {
      ...blankWorkExportDraft(),
      title: "T",
      text: "Work",
      confirmed_own_work: true,
      confirmed_metadata: true,
    };
    for (const text of [" ", "A\tB", "A\u200bB", "x".repeat(20001)])
      expect(() => ownWorkExportInput(context, { ...draft, text })).toThrow();
    expect(() =>
      ownWorkExportInput(context, { ...draft, title: "A\nB" }),
    ).toThrow();
    expect(
      ownWorkExportInput(context, { ...draft, text: "😀".repeat(20000) }).text,
    ).toHaveLength(40000);
  });
  it("treats revision, reset generation and saved label as independent freshness fences", () => {
    expect(sameWorkExportContext(context, { ...context })).toBe(true);
    for (const change of [
      { course_revision: 4 },
      { generation: 3 },
      { course_label: "Changed" },
    ])
      expect(sameWorkExportContext(context, { ...context, ...change })).toBe(
        false,
      );
  });
  it("maps supported errors without echoing arbitrary server or user text", () => {
    expect(
      workExportError(
        422,
        "Some characters or writing systems are not supported by this export font",
      ).message,
    ).toContain("writing systems");
    expect(
      workExportError(422, "PRIVATE SECRET paragraph").message,
    ).not.toContain("SECRET");
    expect(workExportError(503, "__proto__").message).toContain("unavailable");
  });
});
describe("private editor lifecycle", () => {
  it("opens blank and never formats on mount or reads course materials", async () => {
    const { controller, api } = fixture();
    await controller.open();
    expect(controller.getSnapshot().draft).toEqual(blankWorkExportDraft());
    expect(api.pdf).not.toHaveBeenCalled();
    expect(api.account).toHaveBeenCalledTimes(2);
    expect(api.context).toHaveBeenCalledWith(course, expect.any(AbortSignal));
  });
  it("resets both approvals after any text or metadata edit", async () => {
    const { controller } = fixture();
    await controller.open();
    ready(controller);
    controller.edit("author", "Me");
    expect(controller.getSnapshot().draft).toMatchObject({
      confirmed_own_work: false,
      confirmed_metadata: false,
    });
    controller.confirm("confirmed_own_work", true);
    controller.confirm("confirmed_metadata", true);
    controller.includeCourse(true);
    expect(controller.getSnapshot().draft.confirmed_metadata).toBe(false);
  });
  it("rejects oversized edits without truncating or replacing an existing draft", async () => {
    const { controller } = fixture();
    await controller.open();
    controller.edit("text", "My intact text");
    controller.edit("text", "x".repeat(20001));
    expect(controller.getSnapshot().draft.text).toBe("My intact text");
    expect(controller.getSnapshot().error).toContain("not applied");
  });
  it("sends only an explicitly reviewed own-work snapshot and then resets consent", async () => {
    const { controller, api, save } = fixture();
    await controller.open();
    ready(controller);
    await controller.download();
    expect(api.pdf).toHaveBeenCalledTimes(1);
    expect(api.pdf.mock.calls[0][1]).toEqual({
      course_revision: 3,
      generation: 2,
      title: "My own title",
      text: "  My own work.\nSecond line.  ",
      author: null,
      course_label: null,
      confirmed_own_work: true,
      confirmed_metadata: true,
    });
    expect(save).toHaveBeenCalledWith(blob);
    expect(controller.getSnapshot().draft.confirmed_own_work).toBe(false);
  });
  it("preserves same-owner drafts while masked, proving owner and course before unmask", async () => {
    const { controller, api } = fixture();
    await controller.open();
    ready(controller);
    const before = controller.getSnapshot();
    controller.suspend();
    expect(controller.getSnapshot().masked).toBe(true);
    await controller.resume();
    expect(controller.getSnapshot().masked).toBe(false);
    expect(controller.getSnapshot().draft).toBe(before.draft);
    expect(controller.getSnapshot().epoch).toBe(before.epoch);
    expect(api.context).toHaveBeenCalledTimes(2);
  });
  it.each(["course_revision", "generation", "course_label"] as const)(
    "erases drafts when %s changes while hidden",
    async (field) => {
      const { controller, api } = fixture();
      await controller.open();
      ready(controller);
      controller.suspend();
      api.context.mockResolvedValueOnce({
        ...context,
        [field]: field === "course_label" ? "Changed label" : 8,
      });
      await controller.resume();
      expect(controller.getSnapshot().draft.text).toBe("");
      expect(controller.getSnapshot().context).toBeNull();
    },
  );
  it("clears and blocks when the owner changes during refocus", async () => {
    const { controller, api } = fixture();
    await controller.open();
    ready(controller);
    controller.suspend();
    api.account.mockResolvedValueOnce("other-owner");
    await controller.resume();
    expect(controller.getSnapshot().draft.text).toBe("");
    expect(controller.getSnapshot().blocked).toBe(true);
  });
  it("does not download a PDF if the owner changes after formatting", async () => {
    const { controller, api, save } = fixture();
    await controller.open();
    ready(controller);
    api.account
      .mockResolvedValueOnce(owner)
      .mockResolvedValueOnce("other-owner");
    await controller.download();
    expect(api.pdf).toHaveBeenCalledTimes(1);
    expect(save).not.toHaveBeenCalled();
    expect(controller.getSnapshot().draft.text).toBe("");
  });
  it("rechecks ownership after a rejected operation before retaining a private draft", async () => {
    const { controller, api } = fixture();
    await controller.open();
    ready(controller);
    api.pdf.mockRejectedValueOnce(new Error("network"));
    api.account
      .mockResolvedValueOnce(owner)
      .mockResolvedValueOnce("other-owner");
    await controller.download();
    expect(controller.getSnapshot().blocked).toBe(true);
    expect(controller.getSnapshot().draft.text).toBe("");
  });
  it("fences a late PDF after hide without destroying an unsaved same-owner draft", async () => {
    const { controller, api, save } = fixture();
    await controller.open();
    ready(controller);
    let finish: (value: Blob) => void = () => {};
    api.pdf.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const request = controller.download();
    await vi.waitFor(() => expect(api.pdf).toHaveBeenCalledTimes(1));
    controller.suspend();
    await controller.resume();
    finish(blob);
    await request;
    expect(save).not.toHaveBeenCalled();
    expect(controller.getSnapshot().draft.text).toContain("My own work");
  });
  it("rejects fresh-context changes before or after rendering without downloading", async () => {
    const first = fixture();
    await first.controller.open();
    ready(first.controller);
    first.api.context.mockResolvedValueOnce({ ...context, generation: 3 });
    await first.controller.download();
    expect(first.api.pdf).not.toHaveBeenCalled();
    expect(first.controller.getSnapshot().draft.text).toBe("");
    const last = fixture();
    await last.controller.open();
    ready(last.controller);
    last.api.context
      .mockResolvedValueOnce(context)
      .mockResolvedValueOnce({ ...context, course_revision: 4 });
    await last.controller.download();
    expect(last.save).not.toHaveBeenCalled();
    expect(last.controller.getSnapshot().draft.text).toBe("");
  });
  it("keeps editable text after glyph failures and retries only on an explicit new click", async () => {
    const { controller, api, save } = fixture();
    await controller.open();
    ready(controller);
    api.pdf.mockRejectedValueOnce(
      workExportError(
        422,
        "Some characters or writing systems are not supported by this export font",
      ),
    );
    await controller.download();
    expect(controller.getSnapshot().draft.text).toContain("My own work");
    expect(controller.getSnapshot().error).toContain("writing systems");
    expect(api.pdf).toHaveBeenCalledTimes(1);
    expect(save).not.toHaveBeenCalled();
    await controller.download();
    expect(api.pdf).toHaveBeenCalledTimes(2);
    expect(save).toHaveBeenCalledTimes(1);
  });
  it("keeps the original ten-minute deadline across edits, opening and refocus", async () => {
    vi.useFakeTimers();
    const { controller, clock } = fixture();
    await controller.open();
    ready(controller);
    clock(300000);
    controller.edit("author", "Name");
    await controller.open();
    controller.suspend();
    await controller.resume();
    await vi.advanceTimersByTimeAsync(workExportLifetime);
    expect(controller.getSnapshot().draft.text).toBe("");
    expect(controller.getSnapshot().context).toBeNull();
    expect(controller.getSnapshot().notice).toContain("expired");
  });
  it("checks monotonic elapsed time before refocus even if timers were frozen", async () => {
    const { controller, clock, api } = fixture();
    await controller.open();
    ready(controller);
    controller.suspend();
    const calls = api.account.mock.calls.length;
    clock(workExportLifetime + 1);
    await controller.resume();
    expect(controller.getSnapshot().draft.text).toBe("");
    expect(api.account).toHaveBeenCalledTimes(calls);
  });
  it("releases download resources on clear, hide and unmount", async () => {
    for (const action of ["clear", "suspend", "dispose"] as const) {
      const { controller, release } = fixture();
      await controller.open();
      ready(controller);
      await controller.download();
      controller[action]();
      expect(release).toHaveBeenCalledTimes(1);
    }
  });
});
describe("bounded no-store PDF transport", () => {
  it("uses scoped credentialed no-store paths and only the reviewed payload", async () => {
    const input = ownWorkExportInput(context, {
      ...blankWorkExportDraft(),
      title: "T",
      text: "Own text",
      confirmed_own_work: true,
      confirmed_metadata: true,
    });
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(context), {
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response("%PDF-1.4\n%%EOF", {
          headers: {
            "Content-Type": "application/pdf",
            "Content-Disposition": "attachment; filename=untrusted.pdf",
          },
        }),
      );
    const signal = new AbortController().signal;
    await workExportApi.context(course, signal);
    await workExportApi.pdf(course, input, signal);
    expect(fetch.mock.calls[0][0]).toContain(
      `/student/courses/${course}/work-export/context`,
    );
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      body: JSON.stringify(input),
    });
  });
  it("checks PDF type and magic rather than downloading an HTML error page", async () => {
    await expect(
      readWorkExportPdf(
        new Response("<html>private</html>", {
          headers: { "Content-Type": "text/html" },
        }),
      ),
    ).rejects.toThrow(/checked PDF/);
    await expect(
      readWorkExportPdf(
        new Response("not PDF", {
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    ).rejects.toThrow(/checked PDF/);
    expect(
      (
        await readWorkExportPdf(
          new Response(blob, {
            headers: { "Content-Type": "application/pdf" },
          }),
        )
      ).size,
    ).toBe(blob.size);
  });
  it("rejects oversized declared or streaming bodies and cancels the stream", async () => {
    await expect(
      readWorkExportPdf(
        new Response("%PDF-", {
          headers: {
            "Content-Type": "application/pdf",
            "Content-Length": "2097153",
          },
        }),
      ),
    ).rejects.toThrow(/limit/);
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(2097153));
      },
      cancel,
    });
    await expect(
      readWorkExportPdf(
        new Response(stream, {
          headers: { "Content-Type": "application/pdf" },
        }),
      ),
    ).rejects.toThrow(/limit/);
    expect(cancel).toHaveBeenCalledTimes(1);
  });
  it("uses a safe constant filename and revokes URLs promptly and idempotently", () => {
    let deferred = () => {};
    const surface: WorkExportDownloadSurface = {
      createUrl: vi.fn(() => "blob:private"),
      revokeUrl: vi.fn(),
      click: vi.fn(),
      defer: vi.fn((callback) => {
        deferred = callback;
        return vi.fn();
      }),
    };
    const release = downloadOwnWork(blob, surface);
    expect(surface.click).toHaveBeenCalledWith(
      "blob:private",
      "navox-own-work.pdf",
    );
    expect(surface.defer).toHaveBeenCalledWith(expect.any(Function), 1000);
    deferred();
    release();
    expect(surface.revokeUrl).toHaveBeenCalledTimes(1);
  });
  it("revokes a URL when browser download initiation fails", () => {
    const revoke = vi.fn();
    expect(() =>
      downloadOwnWork(blob, {
        createUrl: () => "blob:private",
        revokeUrl: revoke,
        click: () => {
          throw new Error("blocked");
        },
        defer: () => () => {},
      }),
    ).toThrow(/browser/);
    expect(revoke).toHaveBeenCalledWith("blob:private");
  });
  it("masks on blur, proves on refocus and clears on session/pagehide/unmount", () => {
    const window = new EventTarget(),
      document = Object.assign(new EventTarget(), {
        visibilityState: "visible" as DocumentVisibilityState,
      }),
      channel = Object.assign(new EventTarget(), { close: vi.fn() }),
      clear = vi.fn(),
      suspend = vi.fn(),
      resume = vi.fn();
    const stop = observeWorkExportPrivacy(clear, suspend, resume, {
      window,
      document,
      channel,
    });
    window.dispatchEvent(new Event("blur"));
    expect(suspend).toHaveBeenCalledTimes(1);
    expect(clear).not.toHaveBeenCalled();
    window.dispatchEvent(new Event("focus"));
    expect(resume).toHaveBeenCalledTimes(1);
    document.visibilityState = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(suspend).toHaveBeenCalledTimes(2);
    channel.dispatchEvent(new Event("message"));
    window.dispatchEvent(new Event("pagehide"));
    expect(clear).toHaveBeenCalledTimes(2);
    stop();
    expect(clear).toHaveBeenCalledTimes(3);
    expect(channel.close).toHaveBeenCalled();
  });
});
