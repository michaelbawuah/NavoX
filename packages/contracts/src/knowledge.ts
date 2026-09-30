export type KnowledgeResourceType =
	| "EMAIL"
	| "EMAIL_THREAD"
	| "CALENDAR_EVENT"
	| "DOCUMENT"
	| "FILE"
	| "CANVAS_ASSIGNMENT"
	| "CANVAS_ANNOUNCEMENT"
	| "MEETING"
	| "COMMITMENT"
	| "SUBSCRIPTION"
	| "NEWS_STORY"
	| "TASK"
	| "OTHER";

export type SearchMode = "AUTO" | "SEARCH" | "ASK";

export type RetrievalIntent =
	| "FIND_RESOURCE"
	| "QUESTION_ANSWERING"
	| "ENTITY_LOOKUP"
	| "TIMELINE"
	| "RELATIONSHIP"
	| "OPERATIONAL_STATE"
	| "AGGREGATION";

export type RetrieverMode = "FULLTEXT" | "STRUCTURED" | "SEMANTIC" | "GRAPH";

export type AnswerState = "NOT_REQUESTED" | "UNAVAILABLE" | "INCOMPLETE";

export type ExclusionScope = "SOURCE" | "FOLDER" | "RESOURCE" | "TYPE";

export interface EvidenceExcerpt {
	text: string;
	start: number;
	end: number;
	chunk_index: number | null;
	section_title: string | null;
}

export interface EvidenceResource {
	source_type: KnowledgeResourceType;
	resource_id: string;
	title: string | null;
	excerpts: EvidenceExcerpt[];
	canonical_url: string | null;
	source_updated_at: string | null;
	source_version: string | null;
	fresh_until?: string | null;
	indexed_at?: string | null;
	provenance: Record<string, string>;
	origin: "CONNECTED" | "NATIVE";
}

export interface StructuredFact {
	fact_id: string;
	label: string;
	value: string;
	source_type: KnowledgeResourceType;
	resource_id: string;
	source_updated_at: string | null;
	authority: string;
}

export interface SourceIssue {
	connection_id: string;
	source_label: string;
	state: "DEGRADED" | "AUTH_EXPIRED" | "RATE_LIMITED" | "SYNC_FAILED";
}

export interface SearchCoverage {
	source_issues?: SourceIssue[];
	candidate_bound: number;
	examined: number;
	returned: number;
	truncated: boolean;
	stale_dropped: number;
	exclusions_applied: number;
	/** Matching records whose source never retained searchable content. */
	not_searchable: number;
	/** Non-sensitive reason codes for coverage this phase cannot serve. */
	partial_reasons: string[];
}

export interface KnowledgeConflictValue {
	resource_id: string;
	source_type: KnowledgeResourceType;
	value: string;
	title: string | null;
	canonical_url: string | null;
	source_updated_at: string | null;
	authority: string;
}

export interface KnowledgeConflict {
	entity_key: string;
	predicate: "EVENT_START";
	values: [KnowledgeConflictValue, KnowledgeConflictValue];
	preferred_resource_id: string | null;
	explanation: string;
}

export interface KnowledgeRelationship {
	from_key: string;
	to_key: string;
	kind: string;
	evidence_keys: string[];
}

export interface SearchResponse {
	refresh?: {
		state: "NOT_NEEDED" | "REFRESHING" | "UNAVAILABLE" | "PARTIAL";
		queued: number;
		unavailable: number;
		bounded: boolean;
	} | null;
	interpreted_mode: SearchMode;
	intent: RetrievalIntent;
	results: EvidenceResource[];
	structured_facts: StructuredFact[];
	relationships?: KnowledgeRelationship[];
	conflicts?: KnowledgeConflict[];
	answer: string | null;
	answer_state: AnswerState;
	suggested_followups: string[];
	trace_id: string;
	session_id: string | null;
	coverage: SearchCoverage;
	unavailable_modes: RetrieverMode[];
	exclusion_count: number;
}

export interface RecentSearch {
	id: string;
	query: string;
	mode: SearchMode;
	result_count: number;
	created_at: string;
}

export interface SearchExclusion {
	id: string;
	scope: ExclusionScope;
	source_connection_id: string | null;
	external_id: string | null;
	resource_id: string | null;
	resource_type: KnowledgeResourceType | null;
	created_at: string;
}

export interface ResourceDetailChunk {
	chunk_index: number;
	section_title: string | null;
	page_number: number | null;
	text_content: string | null;
	token_count: number;
}

export interface ResourceDetail {
	resource_id: string;
	source_type: KnowledgeResourceType;
	title: string | null;
	canonical_url: string | null;
	sensitivity: string;
	source_updated_at: string | null;
	source_version: string | null;
	fresh_until: string | null;
	indexed_at: string | null;
	index_state: string | null;
	structured_kind: string | null;
	structured_at: string | null;
	chunks: ResourceDetailChunk[];
	provenance: Record<string, string>;
}

export interface SearchQueryBody {
	query: string;
	mode?: SearchMode;
	types?: KnowledgeResourceType[];
	sources?: string[];
	date_range?: { start?: string | null; end?: string | null } | null;
	limit?: number;
	offset?: number;
}

export type AskStatus = "RESERVED" | "COMPLETED" | "UNAVAILABLE" | "FAILED";

export type AskAnswerState =
	| "NOT_REQUESTED"
	| "READY"
	| "INSUFFICIENT"
	| "INCOMPLETE"
	| "UNAVAILABLE"
	| "WITHHELD";

/** One exact stored excerpt or structured fact. Never model-authored prose. */
export interface AnswerCitation {
	resource_id: string;
	source_type: KnowledgeResourceType;
	title: string | null;
	canonical_url: string | null;
	source_version: string | null;
	source_updated_at: string | null;
	excerpt_index: number | null;
	excerpt_text: string | null;
	fact_id: string | null;
	fact_label: string | null;
	fact_value: string | null;
	authority: string;
	sensitivity: string;
	origin: "CONNECTED" | "NATIVE";
}

export interface AskCoverage {
	source_issues?: SourceIssue[];
	examined: number;
	returned: number;
	truncated: boolean;
	evidence_resources: number;
	/** Non-sensitive reason codes; never source text. */
	partial_reasons: string[];
}

export interface AskResponse {
	session_id: string;
	turn_id: string;
	sequence: number;
	status: AskStatus;
	answer_state: AskAnswerState;
	citations: AnswerCitation[];
	results: EvidenceResource[];
	coverage: AskCoverage;
	suggested_followups: string[];
	trace_id: string;
	extractive: boolean;
	replay: boolean;
}

export interface AskTurnView {
	id: string;
	session_id: string;
	sequence: number;
	question: string;
	status: AskStatus;
	answer_state: AskAnswerState;
	created_at: string;
	trace_id: string;
	citations: AnswerCitation[];
	coverage: AskCoverage;
	extractive: boolean;
}

export interface AskSessionView {
	id: string;
	title: string | null;
	created_at: string;
	last_turn_at: string | null;
	turns: AskTurnView[];
}

export interface AskSessionSummary {
	id: string;
	title: string | null;
	turn_count: number;
	created_at: string;
	last_turn_at: string | null;
}

export interface AskQueryBody {
	question: string;
	request_id: string;
	session_id?: string | null;
	referent?: string | null;
	limit?: number;
}

export interface SearchTurnBody {
	query: string;
	request_id: string;
	session_id?: string | null;
}
