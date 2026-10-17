export const workExportFilename = "navox-own-work.pdf";
export const workExportLifetime = 10 * 60_000;
export interface WorkExportContext {
  course_revision: number;
  generation: number;
  course_label: string;
  max_text_chars: 20000;
  max_pages_including_title: 20;
  max_pdf_bytes: 2097152;
  saved: false;
}
export interface WorkExportDraft {
  title: string;
  text: string;
  author: string;
  include_course: boolean;
  confirmed_own_work: boolean;
  confirmed_metadata: boolean;
}
export interface WorkExportInput {
  course_revision: number;
  generation: number;
  title: string;
  text: string;
  author: string | null;
  course_label: string | null;
  confirmed_own_work: true;
  confirmed_metadata: true;
}
export interface WorkExportApi {
  account(signal: AbortSignal): Promise<string>;
  context(courseId: string, signal: AbortSignal): Promise<WorkExportContext>;
  pdf(
    courseId: string,
    input: WorkExportInput,
    signal: AbortSignal,
  ): Promise<Blob>;
}
export interface WorkExportState {
  context: WorkExportContext | null;
  draft: WorkExportDraft;
  masked: boolean;
  busy: boolean;
  phase: "idle" | "opening" | "checking" | "formatting";
  blocked: boolean;
  error: string;
  notice: string;
  epoch: number;
}
export interface WorkExportDownloadSurface {
  createUrl: (blob: Blob) => string;
  revokeUrl: (url: string) => void;
  click: (url: string, filename: string) => void;
  defer: (callback: () => void, ms: number) => () => void;
}
export class WorkExportError extends Error {
  constructor(
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "WorkExportError";
  }
}
function invalid(): never {
  throw new WorkExportError(
    "The export context could not be checked. Open a fresh blank editor.",
  );
}
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return invalid();
  return value as Record<string, unknown>;
}
function number(value: unknown, min = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min)
    return invalid();
  return value;
}
function uuid(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    )
  )
    return invalid();
  return value;
}
function line(value: unknown, max: number): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    [...value].length > max ||
    /\p{C}/u.test(value)
  )
    throw new WorkExportError(
      "Use a nonempty single line within the title or attribution limit.",
    );
  return value;
}
export function readWorkExportContext(value: unknown): WorkExportContext {
  const v = record(value);
  if (
    v.max_text_chars !== 20000 ||
    v.max_pages_including_title !== 20 ||
    v.max_pdf_bytes !== 2097152 ||
    v.saved !== false
  )
    return invalid();
  return {
    course_revision: number(v.course_revision, 1),
    generation: number(v.generation),
    course_label: line(v.course_label, 244),
    max_text_chars: 20000,
    max_pages_including_title: 20,
    max_pdf_bytes: 2097152,
    saved: false,
  };
}
export function sameWorkExportContext(
  left: WorkExportContext,
  right: WorkExportContext,
) {
  return (
    left.course_revision === right.course_revision &&
    left.generation === right.generation &&
    left.course_label === right.course_label
  );
}
export function blankWorkExportDraft(): WorkExportDraft {
  return {
    title: "",
    text: "",
    author: "",
    include_course: false,
    confirmed_own_work: false,
    confirmed_metadata: false,
  };
}
export function ownWorkExportInput(
  context: WorkExportContext,
  draft: WorkExportDraft,
): WorkExportInput {
  if (draft.confirmed_own_work !== true || draft.confirmed_metadata !== true)
    throw new WorkExportError(
      "Confirm your own work and the displayed metadata before downloading.",
    );
  const title = line(draft.title, 160);
  if (!draft.text.trim() || [...draft.text].length > context.max_text_chars)
    throw new WorkExportError("Add 1–20,000 characters of your own work.");
  if (
    [...draft.text].some(
      (char) => char !== "\n" && char !== "\r" && /\p{C}/u.test(char),
    )
  )
    throw new WorkExportError(
      "Use plain text, spaces and line breaks. Tabs and hidden control characters are unsupported.",
    );
  return {
    course_revision: context.course_revision,
    generation: context.generation,
    title,
    text: draft.text,
    author: draft.author === "" ? null : line(draft.author, 120),
    course_label: draft.include_course ? context.course_label : null,
    confirmed_own_work: true,
    confirmed_metadata: true,
  };
}
const publicErrors: Record<string, string> = {
  "Some characters or writing systems are not supported by this export font":
    "Some characters or writing systems are not supported by this PDF font. Your draft stays here; use another document tool if this writing system is needed.",
  "This text needs more than 20 pages including its title page":
    "This text needs more than 20 pages including its title page. Shorten your own text before trying again.",
  "The title or metadata needs too much page space. Use shorter lines":
    "The title or attribution needs too much page space. Use shorter metadata.",
  "This document exceeds the 2 MiB export limit":
    "This PDF exceeds the 2 MiB download limit. Try a shorter document.",
  "Restore this course before formatting your work":
    "Restore this course in Classes before opening an export editor.",
};
export function workExportError(
  status: number,
  detail: unknown,
): WorkExportError {
  if (typeof detail === "string" && Object.hasOwn(publicErrors, detail))
    return new WorkExportError(publicErrors[detail], status);
  return new WorkExportError(
    status === 401 || status === 403
      ? "Your account access could not be checked. Private export text was cleared."
      : status === 404
        ? "This course or export feature is unavailable."
        : status === 409
          ? "Your course or student data changed. Open a fresh blank editor."
          : status === 429
            ? "Two documents are already being formatted. Try again shortly; your draft stays here."
            : status === 408
              ? "Formatting took too long. You can try again with the same text or a shorter document."
              : status === 413
                ? "The document exceeds this export’s size limit. Shorten your text before trying again."
                : status === 422
                  ? "This text or metadata cannot be formatted. Check the plain text and character limits."
                  : "PDF formatting is unavailable. Your draft stays here until it expires.",
    status,
  );
}
const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
async function responseError(response: Response): Promise<never> {
  const value: unknown = await response.json().catch(() => null);
  const detail =
    value && typeof value === "object"
      ? (value as Record<string, unknown>).detail
      : null;
  throw workExportError(response.status, detail);
}
export async function readWorkExportPdf(response: Response): Promise<Blob> {
  if (!response.ok) return responseError(response);
  if (
    response.headers.get("Content-Type")?.split(";", 1)[0].trim() !==
    "application/pdf"
  )
    throw new WorkExportError(
      "The download was not a checked PDF. Your draft stays here.",
    );
  const declared = response.headers.get("Content-Length");
  if (
    declared !== null &&
    (!/^\d+$/.test(declared) || Number(declared) > 2097152)
  )
    throw new WorkExportError("The PDF exceeds the download limit.");
  if (!response.body)
    throw new WorkExportError(
      "The PDF download was empty. Try again when ready.",
    );
  const reader = response.body.getReader(),
    chunks: Uint8Array<ArrayBuffer>[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2097152)
        throw new WorkExportError("The PDF exceeds the download limit.");
      chunks.push(new Uint8Array(value));
    }
  } catch (cause) {
    await reader.cancel().catch(() => {});
    throw cause;
  } finally {
    reader.releaseLock();
  }
  const blob = new Blob(chunks, { type: "application/pdf" });
  if ((await blob.slice(0, 5).text()) !== "%PDF-")
    throw new WorkExportError(
      "The download was not a checked PDF. Your draft stays here.",
    );
  return blob;
}
export const workExportApi: WorkExportApi = {
  async account(signal) {
    const response = await fetch(`${apiBaseUrl}/auth/me`, {
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    });
    if (!response.ok) throw workExportError(response.status, null);
    const v = record(await response.json());
    return `${uuid(record(v.workspace).id)}:${uuid(v.id)}`;
  },
  async context(courseId, signal) {
    uuid(courseId);
    const response = await fetch(
      `${apiBaseUrl}/student/courses/${courseId}/work-export/context`,
      {
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
      },
    );
    if (!response.ok) return responseError(response);
    return readWorkExportContext(await response.json());
  },
  async pdf(courseId, input, signal) {
    uuid(courseId);
    const response = await fetch(
      `${apiBaseUrl}/student/courses/${courseId}/work-export/pdf`,
      {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        redirect: "error",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
        signal: AbortSignal.any([signal, AbortSignal.timeout(25_000)]),
      },
    );
    return readWorkExportPdf(response);
  },
};
const browserDownloadSurface: WorkExportDownloadSurface = {
  createUrl: (blob) => URL.createObjectURL(blob),
  revokeUrl: (url) => URL.revokeObjectURL(url),
  click(url, filename) {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.hidden = true;
    document.body.append(anchor);
    try {
      anchor.click();
    } finally {
      anchor.remove();
    }
  },
  defer(callback, ms) {
    const timer = setTimeout(callback, ms);
    return () => clearTimeout(timer);
  },
};
/** Remote filenames are ignored; a private URL is released after download starts. */
export function downloadOwnWork(
  blob: Blob,
  surface: WorkExportDownloadSurface = browserDownloadSurface,
): () => void {
  const url = surface.createUrl(blob);
  let revoked = false;
  let cancel = () => {};
  const release = () => {
    if (revoked) return;
    revoked = true;
    cancel();
    surface.revokeUrl(url);
  };
  try {
    surface.click(url, workExportFilename);
    cancel = surface.defer(release, 1000);
  } catch {
    release();
    throw new WorkExportError(
      "The browser could not start this download. Your draft stays here.",
    );
  }
  return release;
}
export function initialWorkExportState(): WorkExportState {
  return {
    context: null,
    draft: blankWorkExportDraft(),
    masked: false,
    busy: false,
    phase: "idle",
    blocked: false,
    error: "",
    notice: "Opening a blank editor for your own work…",
    epoch: 0,
  };
}
export class StudentWorkExportController {
  private state = initialWorkExportState();
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private deadline: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private releaseDownload: (() => void) | null = null;
  private disposed = false;
  constructor(
    readonly accountId: string,
    readonly courseId: string,
    private api: WorkExportApi = workExportApi,
    private save: (blob: Blob) => () => void = downloadOwnWork,
    private now: () => number = () => performance.now(),
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private update(change: Partial<WorkExportState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }
  clear = (
    notice = "The editor was cleared. Open a new blank editor when ready.",
  ) => {
    ++this.generation;
    this.request?.abort();
    this.request = null;
    this.releaseDownload?.();
    this.releaseDownload = null;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.deadline = null;
    this.update({
      ...initialWorkExportState(),
      notice,
      epoch: this.state.epoch + 1,
    });
  };
  dispose = () => {
    this.clear();
    this.disposed = true;
    this.listeners.clear();
  };
  private expired() {
    if (this.deadline !== null && this.now() >= this.deadline) {
      this.clear(
        "This editor’s 10 minutes expired. Its text and metadata were cleared.",
      );
      return true;
    }
    return false;
  }
  private current(token: number, signal: AbortSignal) {
    return !this.disposed && token === this.generation && !signal.aborted;
  }
  private async run<T>(
    operation: (signal: AbortSignal) => Promise<T>,
    apply: (value: T) => void,
    allowMasked = false,
    phase: WorkExportState["phase"] = "opening",
  ) {
    if (
      this.disposed ||
      this.state.busy ||
      this.state.blocked ||
      (this.state.masked && !allowMasked) ||
      this.expired()
    )
      return;
    const token = ++this.generation,
      request = new AbortController();
    this.request = request;
    this.update({ busy: true, phase, error: "" });
    let checked = false;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      let result: T;
      try {
        result = await operation(request.signal);
      } catch (cause) {
        checked = false;
        if (!this.current(token, request.signal)) return;
        if ((await this.api.account(request.signal)) !== this.accountId)
          throw new Error("Account changed");
        checked = true;
        throw cause;
      }
      checked = false;
      if (!this.current(token, request.signal)) return;
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new Error("Account changed");
      checked = true;
      if (this.current(token, request.signal) && !this.expired()) apply(result);
    } catch (cause) {
      if (!this.current(token, request.signal)) return;
      if (!checked) {
        this.clear("Your account access changed. Export text was cleared.");
        this.update({
          blocked: true,
          error: "Reload this page after signing in to the intended account.",
        });
        return;
      }
      if (
        cause instanceof WorkExportError &&
        [401, 403, 404, 409].includes(cause.status ?? 0)
      ) {
        this.clear("The course or account changed. Export text was cleared.");
        this.update({ error: cause.message });
      } else
        this.update({
          error:
            cause instanceof WorkExportError
              ? cause.message
              : "PDF formatting could not be checked. Your draft stays here; try again only when ready.",
        });
    } finally {
      if (this.current(token, request.signal)) {
        this.request = null;
        this.update({ busy: false, phase: "idle" });
      }
    }
  }
  open = async () => {
    if (this.state.context) return;
    await this.run(
      (signal) => this.api.context(this.courseId, signal),
      (context) => {
        this.deadline = this.now() + workExportLifetime;
        this.timer = setTimeout(
          () =>
            this.clear(
              "This editor’s 10 minutes expired. Its text and metadata were cleared.",
            ),
          workExportLifetime,
        );
        this.update({
          context,
          notice:
            "Enter your own text, then review the document before downloading.",
        });
      },
    );
  };
  edit = (field: "title" | "text" | "author", value: string) => {
    if (
      this.expired() ||
      !this.state.context ||
      this.state.masked ||
      this.state.busy ||
      this.state.blocked
    )
      return;
    const limit = field === "text" ? 20000 : field === "title" ? 160 : 120;
    if ([...value].length > limit) {
      this.update({
        error: `This edit exceeds ${limit.toLocaleString("en-US")} characters and was not applied. Shorten it before entering it.`,
      });
      return;
    }
    this.update({
      draft: {
        ...this.state.draft,
        [field]: value,
        confirmed_own_work: false,
        confirmed_metadata: false,
      },
      error: "",
    });
  };
  includeCourse = (value: boolean) => {
    if (
      this.expired() ||
      !this.state.context ||
      this.state.masked ||
      this.state.busy ||
      this.state.blocked
    )
      return;
    this.update({
      draft: {
        ...this.state.draft,
        include_course: value,
        confirmed_own_work: false,
        confirmed_metadata: false,
      },
    });
  };
  confirm = (
    field: "confirmed_own_work" | "confirmed_metadata",
    value: boolean,
  ) => {
    if (
      this.expired() ||
      !this.state.context ||
      this.state.masked ||
      this.state.busy ||
      this.state.blocked
    )
      return;
    this.update({ draft: { ...this.state.draft, [field]: value } });
  };
  suspend = () => {
    if (this.disposed || this.expired()) return;
    ++this.generation;
    this.request?.abort();
    this.request = null;
    this.releaseDownload?.();
    this.releaseDownload = null;
    this.update({ masked: true, busy: false, phase: "idle" });
  };
  resume = async () => {
    if (!this.state.masked || this.expired()) return;
    const context = this.state.context;
    await this.run(
      async (signal) => {
        const fresh = await this.api.context(this.courseId, signal);
        if (context && !sameWorkExportContext(context, fresh))
          throw new WorkExportError(
            "Your course or student data changed. Open a fresh blank editor.",
            409,
          );
        return fresh;
      },
      () => this.update({ masked: false }),
      true,
      "checking",
    );
  };
  download = async () => {
    if (this.expired() || !this.state.context) return;
    const context = this.state.context;
    let input: WorkExportInput;
    try {
      input = ownWorkExportInput(context, this.state.draft);
    } catch (cause) {
      this.update({
        error:
          cause instanceof Error ? cause.message : "Review your work first.",
      });
      return;
    }
    await this.run(
      async (signal) => {
        const fresh = await this.api.context(this.courseId, signal);
        if (!sameWorkExportContext(context, fresh))
          throw new WorkExportError(
            "Your course or student data changed. Open a fresh blank editor.",
            409,
          );
        const blob = await this.api.pdf(this.courseId, input, signal);
        const after = await this.api.context(this.courseId, signal);
        if (!sameWorkExportContext(context, after))
          throw new WorkExportError(
            "Your course or student data changed while formatting. Open a fresh blank editor.",
            409,
          );
        return blob;
      },
      (blob) => {
        this.releaseDownload?.();
        this.releaseDownload = this.save(blob);
        this.update({
          draft: {
            ...this.state.draft,
            confirmed_own_work: false,
            confirmed_metadata: false,
          },
          notice:
            "Your PDF download was requested. Check your browser’s downloads.",
        });
      },
      false,
      "formatting",
    );
  };
}
export function observeWorkExportPrivacy(
  clear: () => void,
  suspend: () => void,
  resume: () => void,
  surface: {
    window: Pick<Window, "addEventListener" | "removeEventListener">;
    document: Pick<
      Document,
      "addEventListener" | "removeEventListener" | "visibilityState"
    > &
      Partial<Pick<Document, "hasFocus">>;
    channel: Pick<
      BroadcastChannel,
      "addEventListener" | "removeEventListener" | "close"
    > | null;
  },
) {
  let focused = surface.document.hasFocus?.() ?? true;
  const refocus = () => {
    if (focused && surface.document.visibilityState === "visible") resume();
  };
  const blur = () => {
      focused = false;
      suspend();
    },
    focus = () => {
      focused = true;
      refocus();
    };
  const visibility = () => {
    if (surface.document.visibilityState !== "visible") suspend();
    else refocus();
  };
  surface.window.addEventListener("blur", blur);
  surface.window.addEventListener("focus", focus);
  for (const event of ["pagehide", "storage"])
    surface.window.addEventListener(event, clear);
  surface.document.addEventListener("visibilitychange", visibility);
  surface.channel?.addEventListener("message", clear);
  if (!focused || surface.document.visibilityState !== "visible") suspend();
  return () => {
    surface.window.removeEventListener("blur", blur);
    surface.window.removeEventListener("focus", focus);
    for (const event of ["pagehide", "storage"])
      surface.window.removeEventListener(event, clear);
    surface.document.removeEventListener("visibilitychange", visibility);
    surface.channel?.removeEventListener("message", clear);
    surface.channel?.close();
    clear();
  };
}
