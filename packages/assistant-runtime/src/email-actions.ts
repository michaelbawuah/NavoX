import type {
  AssistantEmailAction,
  AssistantEmailDraft,
  AssistantEmailDraftVersion,
} from "@navox/contracts";
import { AssistantError } from "./errors";
import type { AccountScope, NavoxUpstream } from "./gateway";
import type { AssistantGoalService } from "./goal-service";
import type { AssistantStore, AssistantTurnRecord } from "./store";
import { isRecord, isUuid } from "./validate";

const HASH = /^[0-9a-f]{64}$/;

function invalid(message: string): never {
  throw new AssistantError("invalid_request", message);
}

function unreadable(): never {
  throw new AssistantError(
    "unavailable",
    "The email action response was unreadable.",
  );
}

function exactBody(
  value: unknown,
  fields: readonly string[],
): Record<string, unknown> {
  if (
    !isRecord(value) ||
    Object.keys(value).some((key) => !fields.includes(key))
  ) {
    invalid("The email action request contains an unsupported field.");
  }
  return value;
}

function requestUuid(value: unknown, name: string): string {
  if (!isUuid(value)) invalid(`A valid ${name} is required.`);
  return value;
}

function requestText(value: unknown, name: string, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max) {
    invalid(`A bounded ${name} is required.`);
  }
  return value.trim();
}

function responseText(value: unknown, max: number): string {
  if (typeof value !== "string" || !value.trim() || value.length > max)
    unreadable();
  return value;
}

function responseUuid(value: unknown): string {
  if (!isUuid(value)) unreadable();
  return value;
}

function responseVersion(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 1) unreadable();
  return value as number;
}

export function parseEmailDraft(payload: unknown): AssistantEmailDraft {
  if (!isRecord(payload) || !Array.isArray(payload.versions)) unreadable();
  if (
    payload.binding_kind !== "KNOWLEDGE_EMAIL" ||
    payload.commitment_id !== null ||
    payload.versions.length < 1 ||
    payload.versions.length > 100
  )
    unreadable();
  const versions: AssistantEmailDraftVersion[] = payload.versions.map((raw) => {
    if (!isRecord(raw) || !Array.isArray(raw.to) || raw.to.length !== 1)
      unreadable();
    const hash = responseText(raw.payload_hash, 64);
    if (!HASH.test(hash)) unreadable();
    if (raw.created_by !== "AI" && raw.created_by !== "USER") unreadable();
    return {
      version: responseVersion(raw.version),
      to: [responseText(raw.to[0], 320)],
      subject: responseText(raw.subject, 256),
      body: responseText(raw.body, 50_000),
      created_by: raw.created_by,
      payload_hash: hash,
    };
  });
  const currentVersion = responseVersion(payload.current_version);
  if (versions.at(-1)?.version !== currentVersion) unreadable();
  if (payload.action_id !== null && !isUuid(payload.action_id)) unreadable();
  return {
    id: responseUuid(payload.id),
    binding_kind: "KNOWLEDGE_EMAIL",
    commitment_id: null,
    source_id: responseUuid(payload.source_id),
    current_version: currentVersion,
    status: responseText(payload.status, 32),
    action_id: payload.action_id,
    versions,
  };
}

export function parseEmailAction(payload: unknown): AssistantEmailAction {
  if (
    !isRecord(payload) ||
    !isRecord(payload.payload) ||
    !isRecord(payload.result)
  )
    unreadable();
  const data = payload.payload;
  const reply = data.reply;
  if (!isRecord(reply) || data.post_send_state !== "unchanged") unreadable();
  const hash = responseText(payload.payload_hash, 64);
  if (!HASH.test(hash)) unreadable();
  const approval = payload.approval;
  let parsedApproval: AssistantEmailAction["approval"] = null;
  if (approval !== null) {
    if (!isRecord(approval)) unreadable();
    const approvedHash = responseText(approval.action_payload_hash, 64);
    if (approvedHash !== hash) unreadable();
    parsedApproval = {
      status: responseText(approval.status, 32),
      expires_at: responseText(approval.expires_at, 64),
      action_payload_hash: approvedHash,
    };
  }
  const result: AssistantEmailAction["result"] = {};
  if (payload.result.message_id !== undefined) {
    result.message_id = responseText(payload.result.message_id, 512);
  }
  if (payload.result.thread_id !== undefined) {
    result.thread_id = responseText(payload.result.thread_id, 512);
  }
  const status = responseText(payload.status, 32);
  if (status === "completed" && !result.message_id) unreadable();
  return {
    id: responseUuid(payload.id),
    status,
    payload_hash: hash,
    payload: {
      sender: responseText(data.sender, 320),
      to: responseText(data.to, 320),
      subject: responseText(data.subject, 256),
      body_text: responseText(data.body_text, 50_000),
      draft_id: responseUuid(data.draft_id),
      draft_version: responseVersion(data.draft_version),
      connection_id: responseUuid(data.connection_id),
      post_send_state: "unchanged",
      reply: {
        source_message_id: responseText(reply.source_message_id, 512),
        thread_id: responseText(reply.thread_id, 512),
      },
    },
    approval: parsedApproval,
    result,
  };
}

function emailResource(turn: AssistantTurnRecord): string {
  if (
    turn.state !== "READY" ||
    turn.decision.capability_id !== "email.search" ||
    turn.decision.reason !== "email.search.found_one" ||
    turn.plan.intents.length !== 1 ||
    turn.plan.intents[0]?.kind !== "email.search"
  )
    invalid("Select one resolved email in this conversation first.");
  const items = turn.presentation.blocks.filter(
    (block) => block.kind === "ITEM",
  );
  if (items.length !== 1)
    invalid("Select one resolved email in this conversation first.");
  const item = items[0]?.item;
  if (
    item?.type !== "EMAIL" ||
    !isUuid(item.id) ||
    !item.sources.some(
      (source) =>
        source.evidence_id === item.id && source.source_type === "EMAIL",
    )
  )
    invalid("Select one exact email message, not a thread.");
  return item.id;
}

function assertDraftSource(
  draft: AssistantEmailDraft,
  resourceId: string,
  draftId?: string,
): void {
  if (draft.source_id !== resourceId || (draftId && draft.id !== draftId)) {
    throw new AssistantError(
      "forbidden",
      "That draft does not belong to the selected email.",
    );
  }
}

function assertActionMatchesDraft(
  action: AssistantEmailAction,
  draft: AssistantEmailDraft,
): void {
  const version = draft.versions.at(-1);
  if (
    !version ||
    action.payload.draft_id !== draft.id ||
    action.payload.draft_version !== draft.current_version ||
    action.payload.to !== version.to[0] ||
    action.payload.subject !== version.subject ||
    action.payload.body_text !== version.body ||
    draft.action_id !== action.id
  )
    throw new AssistantError(
      "conflict",
      "The reviewed draft changed. Reload before approving.",
    );
}

export interface AssistantEmailActionDeps {
  store: AssistantStore;
  upstream: NavoxUpstream;
  /**
   * The bounded goal recorder. When present, preparing an action also records
   * the durable verification goal for it. It adds no authority: the action
   * still needs its own exact SPEC-001 approval before it can execute.
   */
  goals?: AssistantGoalService;
  now?: () => Date;
}

export function createAssistantEmailActions(deps: AssistantEmailActionDeps) {
  const now = deps.now ?? (() => new Date());

  async function context(
    cookie: string,
    sessionId: string,
    turnId: string,
  ): Promise<string> {
    const scope: AccountScope = await deps.upstream.fetchAccount(cookie);
    const session = await deps.store.readSession({
      session_id: requestUuid(sessionId, "session ID"),
      scope,
    });
    if (!session || Date.parse(session.expires_at) <= now().getTime()) {
      throw new AssistantError(
        "not_found",
        "That assistant session is no longer available.",
      );
    }
    const turns = await deps.store.listTurns({ session_id: sessionId, scope });
    const turn = turns.find(
      (candidate) => candidate.id === requestUuid(turnId, "source turn ID"),
    );
    if (!turn)
      throw new AssistantError(
        "not_found",
        "That email turn is not in this conversation.",
      );
    return emailResource(turn);
  }

  async function boundDraft(
    cookie: string,
    resourceId: string,
    draftId: string,
  ): Promise<AssistantEmailDraft> {
    const id = requestUuid(draftId, "draft ID");
    const draft = parseEmailDraft(
      await deps.upstream.getCommunicationDraft(cookie, id),
    );
    assertDraftSource(draft, resourceId, id);
    return draft;
  }

  return {
    async createEmailDraft(input: {
      cookie: string;
      session_id: string;
      body: unknown;
    }): Promise<AssistantEmailDraft> {
      const body = exactBody(input.body, ["source_turn_id", "instructions"]);
      const resourceId = await context(
        input.cookie,
        input.session_id,
        requestUuid(body.source_turn_id, "source turn ID"),
      );
      const instructions = requestText(
        body.instructions,
        "draft instruction",
        4000,
      );
      const draft = parseEmailDraft(
        await deps.upstream.createKnowledgeEmailDraft(input.cookie, {
          sourceId: resourceId,
          instructions,
        }),
      );
      assertDraftSource(draft, resourceId);
      return draft;
    },

    async readEmailDraft(input: {
      cookie: string;
      session_id: string;
      source_turn_id: string;
      draft_id: string;
    }): Promise<AssistantEmailDraft> {
      const resourceId = await context(
        input.cookie,
        input.session_id,
        input.source_turn_id,
      );
      return boundDraft(input.cookie, resourceId, input.draft_id);
    },

    async reviseEmailDraft(input: {
      cookie: string;
      session_id: string;
      draft_id: string;
      body: unknown;
    }): Promise<AssistantEmailDraft> {
      const body = exactBody(input.body, [
        "source_turn_id",
        "expected_version",
        "subject",
        "body",
      ]);
      const resourceId = await context(
        input.cookie,
        input.session_id,
        requestUuid(body.source_turn_id, "source turn ID"),
      );
      const draft = await boundDraft(input.cookie, resourceId, input.draft_id);
      if (body.expected_version !== draft.current_version) {
        throw new AssistantError(
          "conflict",
          "The draft changed. Reload before editing.",
        );
      }
      const recipient = draft.versions.at(-1)?.to[0];
      if (!recipient) unreadable();
      const revised = parseEmailDraft(
        await deps.upstream.reviseCommunicationDraft(input.cookie, draft.id, {
          expectedVersion: draft.current_version,
          to: recipient,
          subject: requestText(body.subject, "subject", 256),
          body: requestText(body.body, "message", 50_000),
        }),
      );
      assertDraftSource(revised, resourceId, draft.id);
      return revised;
    },

    async prepareEmailDraft(input: {
      cookie: string;
      session_id: string;
      draft_id: string;
      body: unknown;
    }): Promise<AssistantEmailAction> {
      const body = exactBody(input.body, [
        "source_turn_id",
        "expected_version",
        "request_id",
      ]);
      const resourceId = await context(
        input.cookie,
        input.session_id,
        requestUuid(body.source_turn_id, "source turn ID"),
      );
      const draft = await boundDraft(input.cookie, resourceId, input.draft_id);
      if (body.expected_version !== draft.current_version) {
        throw new AssistantError(
          "conflict",
          "The draft changed. Reload before review.",
        );
      }
      const action = parseEmailAction(
        await deps.upstream.prepareCommunicationDraft(input.cookie, draft.id, {
          expectedVersion: draft.current_version,
          requestId: requestUuid(body.request_id, "request ID"),
        }),
      );
      // Preparation may have just populated action_id, so bind it against the
      // exact draft version and source response before returning it for review.
      const prepared = await boundDraft(input.cookie, resourceId, draft.id);
      assertActionMatchesDraft(action, prepared);
      if (deps.goals) {
        // Re-derive the scope for this write; the goal row is fenced by the
        // session's own workspace and user, so a changed account fails closed.
        const scope: AccountScope = await deps.upstream.fetchAccount(
          input.cookie,
        );
        await deps.goals.recordActionGoal({
          scope,
          session_id: input.session_id,
          action_id: action.id,
        });
      }
      return action;
    },

    async approveEmailDraft(input: {
      cookie: string;
      session_id: string;
      draft_id: string;
      body: unknown;
    }): Promise<AssistantEmailAction> {
      const body = exactBody(input.body, [
        "source_turn_id",
        "request_id",
        "expected_payload_hash",
        "draft_version",
        "confirmed",
      ]);
      if (
        body.confirmed !== true ||
        typeof body.expected_payload_hash !== "string" ||
        !HASH.test(body.expected_payload_hash)
      ) {
        invalid("Review and confirm the exact email before approving.");
      }
      const resourceId = await context(
        input.cookie,
        input.session_id,
        requestUuid(body.source_turn_id, "source turn ID"),
      );
      const draft = await boundDraft(input.cookie, resourceId, input.draft_id);
      if (!draft.action_id)
        throw new AssistantError(
          "conflict",
          "Prepare the current draft first.",
        );
      const reviewed = parseEmailAction(
        await deps.upstream.getAction(input.cookie, draft.action_id),
      );
      assertActionMatchesDraft(reviewed, draft);
      if (
        reviewed.status !== "awaiting_approval" ||
        reviewed.approval?.status !== "pending" ||
        reviewed.payload_hash !== body.expected_payload_hash ||
        reviewed.payload.draft_version !== body.draft_version
      ) {
        throw new AssistantError(
          "conflict",
          "The approval request changed. Review it again.",
        );
      }
      const approved = parseEmailAction(
        await deps.upstream.approveCommunicationDraft(input.cookie, draft.id, {
          requestId: requestUuid(body.request_id, "request ID"),
          expectedPayloadHash: reviewed.payload_hash,
          draftVersion: reviewed.payload.draft_version,
        }),
      );
      if (
        approved.id !== reviewed.id ||
        approved.payload_hash !== reviewed.payload_hash
      ) {
        unreadable();
      }
      return approved;
    },

    async readEmailAction(input: {
      cookie: string;
      session_id: string;
      source_turn_id: string;
      draft_id: string;
    }): Promise<AssistantEmailAction> {
      const resourceId = await context(
        input.cookie,
        input.session_id,
        input.source_turn_id,
      );
      const draft = await boundDraft(input.cookie, resourceId, input.draft_id);
      if (!draft.action_id)
        throw new AssistantError(
          "not_found",
          "No send action is prepared for this draft.",
        );
      const action = parseEmailAction(
        await deps.upstream.getAction(input.cookie, draft.action_id),
      );
      assertActionMatchesDraft(action, draft);
      return action;
    },
  };
}
