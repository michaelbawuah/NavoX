import {
  type CourseAssignments,
  type CourseEmails,
  type CourseMaterial,
  type CourseOverview,
  CourseRequestError,
  type CourseSearch,
  courseRequest,
  readCourseAssignments,
  readCourseEmails,
  readCourseMaterials,
  readCourseOverview,
} from "./student-coursework";

export interface CourseProof {
  overview: CourseOverview;
  materials: CourseMaterial[];
  assignments: CourseAssignments;
  emails: CourseEmails;
}
export interface CoursePrivacyApi {
  account(signal: AbortSignal): Promise<string>;
  proof(courseId: string, signal: AbortSignal): Promise<CourseProof>;
}
export interface CoursePrivacyState {
  proof: CourseProof | null;
  masked: boolean;
  checking: boolean;
  blocked: boolean;
  uncertain: boolean;
  writePending: boolean;
  error: string;
  notice: string;
  epoch: number;
  readEpoch: number;
}
export const COURSE_PRIVATE_TTL_MS = 10 * 60_000;
const stopped = () =>
  new CourseRequestError(
    "This course view was interrupted. Reload its saved details.",
  );
const sourceChanged = () =>
  new CourseRequestError(
    "This course or saved source changed. Reload its current details before editing.",
    409,
  );
export function courseProofCurrent(
  proof: CourseProof,
  courseId: string,
): boolean {
  if (
    proof.overview.course.id !== courseId ||
    proof.overview.archived !== proof.overview.course.archived
  )
    return false;
  const quotes = [
    proof.overview.ai_policy,
    ...proof.overview.staff.map((row) => row.source),
    ...proof.overview.grade_breakdown.map((row) => row.source),
  ];
  return quotes.every(
    (quote) =>
      quote === null ||
      proof.materials.some(
        (row) =>
          row.id === quote.material_id &&
          row.revision === quote.revision &&
          quote.page >= 1 &&
          quote.page <= row.page_count,
      ),
  );
}
export function courseSearchCurrent(
  search: CourseSearch,
  proof: CourseProof,
  courseId: string,
): boolean {
  return (
    search.course_id === courseId &&
    search.results.every((hit) =>
      proof.materials.some(
        (row) =>
          row.id === hit.material_id &&
          row.revision === hit.revision &&
          hit.page >= 1 &&
          hit.page <= row.page_count,
      ),
    )
  );
}
const base =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";
export const coursePrivacyApi: CoursePrivacyApi = {
  async account(signal) {
    const response = await fetch(`${base}/auth/me`, {
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
    });
    if (!response.ok)
      throw new CourseRequestError(
        "Your access to this private course could not be checked. Sign in again.",
        response.status,
      );
    const value = await response.json();
    const uuid =
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
    if (!uuid.test(value?.id ?? "") || !uuid.test(value?.workspace?.id ?? ""))
      throw stopped();
    return `${value.workspace.id}:${value.id}`;
  },
  async proof(courseId, signal) {
    const [overview, materials, assignments, emails] = await Promise.all([
      courseRequest(
        courseId,
        "/overview",
        readCourseOverview,
        "GET",
        undefined,
        signal,
      ),
      courseRequest(
        courseId,
        "/materials",
        readCourseMaterials,
        "GET",
        undefined,
        signal,
      ),
      courseRequest(
        courseId,
        "/assignments",
        readCourseAssignments,
        "GET",
        undefined,
        signal,
      ),
      courseRequest(
        courseId,
        "/email-links",
        readCourseEmails,
        "GET",
        undefined,
        signal,
      ),
    ]);
    return { overview, materials, assignments, emails };
  },
};
function initial(): CoursePrivacyState {
  return {
    proof: null,
    masked: false,
    checking: false,
    blocked: false,
    uncertain: false,
    writePending: false,
    error: "",
    notice: "Open this course to check its current saved details.",
    epoch: 0,
    readEpoch: 0,
  };
}
/** Owns access and source proofs. Component drafts survive only an unchanged, freshly proved snapshot. */
export class CoursePrivacySession {
  private state = initial();
  private listeners = new Set<() => void>();
  private generation = 0;
  private reads = new Set<AbortController>();
  private writes = 0;
  private deadline: number | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private signature: string | null = null;
  constructor(
    readonly accountId: string,
    readonly courseId: string,
    private api: CoursePrivacyApi = coursePrivacyApi,
    private now: () => number = () => performance.now(),
  ) {}
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  private update(change: Partial<CoursePrivacyState>) {
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }
  private fence() {
    ++this.generation;
    for (const request of this.reads) request.abort();
    this.reads.clear();
  }
  clear = (notice = "Course details and unsaved edits were cleared.") => {
    this.fence();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.deadline = null;
    this.signature = null;
    this.update({
      ...initial(),
      notice,
      uncertain: this.writes > 0,
      writePending: this.writes > 0,
      epoch: this.state.epoch + 1,
      readEpoch: this.state.readEpoch + 1,
    });
  };
  private failed(cause: unknown, uncertain = false, ownWrite = false) {
    const previousUncertain = this.state.uncertain;
    const status = cause instanceof CourseRequestError ? cause.status : null;
    this.clear(
      "Previous course details were cleared. Reload to check current records.",
    );
    this.update({
      blocked: status === 401 || status === 403,
      uncertain:
        previousUncertain || uncertain || this.writes > (ownWrite ? 1 : 0),
      error:
        status === 401 || status === 403
          ? "Your account could not be checked. Sign in again before opening this course."
          : status === 404
            ? "This course or saved source is no longer available."
            : status === 409
              ? "This course or saved source changed. Reload its current details."
              : "The saved course could not be checked. Previous private details were cleared.",
    });
  }
  rejectSource = () => {
    this.failed(sourceChanged());
  };
  suspend = () => {
    this.fence();
    if (this.state.proof && this.deadline === null) {
      this.deadline = this.now() + COURSE_PRIVATE_TTL_MS;
      this.timer = setTimeout(
        () =>
          this.clear(
            "Unsaved course edits expired while you were away. Reload current records.",
          ),
        COURSE_PRIVATE_TTL_MS,
      );
    }
    this.update({
      masked: true,
      checking: false,
      error: "",
      uncertain: this.state.uncertain || this.writes > 0,
      writePending: this.writes > 0,
      readEpoch: this.state.readEpoch + 1,
    });
  };
  refresh = async () => {
    if (this.state.checking || this.state.blocked) return false;
    if (this.writes) {
      this.update({
        notice:
          "A change is still finishing. Wait, then reload the saved course.",
      });
      return false;
    }
    if (this.deadline !== null && this.now() >= this.deadline) {
      this.clear(
        "This course editor expired. Open it again to check current records.",
      );
      return false;
    }
    this.fence();
    const token = this.generation,
      request = new AbortController();
    this.reads.add(request);
    this.update({
      masked: true,
      checking: true,
      error: "",
      readEpoch: this.state.readEpoch + 1,
    });
    const current = () => token === this.generation && !request.signal.aborted;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) return false;
      const proof = await this.api.proof(this.courseId, request.signal);
      if (!current()) return false;
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) return false;
      if (!courseProofCurrent(proof, this.courseId)) throw sourceChanged();
      if (this.deadline !== null && this.now() >= this.deadline) {
        this.clear(
          "This course editor expired. Open it again to check current records.",
        );
        return false;
      }
      const signature = JSON.stringify(proof),
        changed = this.signature !== null && signature !== this.signature;
      this.signature = signature;
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      this.deadline = null;
      this.update({
        proof,
        masked: false,
        checking: false,
        uncertain: false,
        error: "",
        notice: changed
          ? "The saved course changed. Previous edits and source views were cleared."
          : "Current saved course details checked.",
        epoch: this.state.epoch + (changed ? 1 : 0),
      });
      return true;
    } catch (cause) {
      if (current()) this.failed(cause);
      return false;
    } finally {
      this.reads.delete(request);
    }
  };
  async read<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
    if (this.state.masked || !this.state.proof || this.state.blocked)
      throw stopped();
    const token = this.generation,
      request = new AbortController();
    this.reads.add(request);
    const current = () => token === this.generation && !request.signal.aborted;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) throw stopped();
      const value = await operation(request.signal);
      if (!current()) throw stopped();
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) throw stopped();
      return value;
    } catch (cause) {
      if (current()) this.failed(cause);
      throw stopped();
    } finally {
      this.reads.delete(request);
    }
  }
  async write<T>(operation: () => Promise<T>): Promise<T> {
    if (
      this.state.masked ||
      !this.state.proof ||
      this.state.blocked ||
      this.state.uncertain ||
      this.writes
    )
      throw stopped();
    const token = this.generation,
      request = new AbortController();
    ++this.writes;
    this.update({ writePending: true });
    let sent = false,
      completed = false;
    const current = () => token === this.generation;
    try {
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) throw stopped();
      sent = true;
      const value = await operation();
      completed = true;
      if (!current()) throw stopped();
      if ((await this.api.account(request.signal)) !== this.accountId)
        throw new CourseRequestError("Account changed", 403);
      if (!current()) throw stopped();
      return value;
    } catch (cause) {
      if (
        current() &&
        sent &&
        !completed &&
        cause instanceof CourseRequestError &&
        [413, 415, 422, 429].includes(cause.status ?? 0)
      ) {
        try {
          if ((await this.api.account(request.signal)) !== this.accountId)
            throw new CourseRequestError("Account changed", 403);
          if (!current()) throw stopped();
          const proof = await this.api.proof(this.courseId, request.signal);
          if (!current()) throw stopped();
          if ((await this.api.account(request.signal)) !== this.accountId)
            throw new CourseRequestError("Account changed", 403);
          if (!current()) throw stopped();
          if (
            !courseProofCurrent(proof, this.courseId) ||
            JSON.stringify(proof) !== this.signature
          )
            throw sourceChanged();
          this.update({
            proof,
            error:
              "This change was not accepted. Review your fields or selected file.",
            uncertain: false,
            readEpoch: this.state.readEpoch + 1,
          });
        } catch (checked) {
          if (current()) this.failed(checked, false, true);
          throw stopped();
        }
        throw new CourseRequestError(
          "This change was not accepted. Review your fields or selected file.",
          cause.status,
          false,
        );
      }
      const uncertain =
        sent &&
        (completed ||
          !(cause instanceof CourseRequestError) ||
          cause.uncertain ||
          !current());
      if (current()) this.failed(cause, uncertain, true);
      else if (uncertain)
        this.update({
          uncertain: true,
          notice:
            "The last change may have saved. Reload saved details before continuing.",
        });
      throw new CourseRequestError(
        "The change could not be checked. Reload saved details before continuing.",
        null,
        uncertain,
      );
    } finally {
      --this.writes;
      this.update({ writePending: this.writes > 0 });
    }
  }
}
export function observeCoursePrivacy(
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
  const visible = () => {
      if (focused && surface.document.visibilityState === "visible") resume();
    },
    blur = () => {
      focused = false;
      suspend();
    },
    focus = () => {
      focused = true;
      visible();
    },
    visibility = () => {
      if (surface.document.visibilityState !== "visible") suspend();
      else visible();
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
