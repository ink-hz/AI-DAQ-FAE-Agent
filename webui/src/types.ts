export type Role = 'user' | 'assistant';

export interface SourceRef {
  type?: string;
  title?: string;
  kb_id?: string;
  model?: string;
  path?: string;
  lines?: string;
  source_id?: string;
  locator?: Record<string, unknown>;
  confidence_layer?: string;
}

export interface StageEvent {
  stage?: string;
  status?: string;
  message?: string;
  agent?: string;
  elapsed_ms?: number;
  trace_id?: string;
  session_id?: string;
}

export interface SessionHint {
  suggest_new_session?: boolean;
  reason?: string;
  assistant_turns?: number;
}

export interface DoneEvent {
  bucket?: string;
  template?: string;
  trace_id?: string;
  turn_id?: string;
  session_id?: string;
  text_len?: number;
  fallback_used?: boolean;
  fallback_reason?: string | null;
  risk_notes?: string[];
  error?: string | null;
  session_hint?: SessionHint;
  [key: string]: unknown;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  restored?: boolean;
  sources?: SourceRef[];
  stages?: StageEvent[];
  done?: DoneEvent;
  streamError?: StreamFailureState;
  feedback?: MessageFeedbackState;
  attachments?: import('./attachments').SentAttachment[];
}

export interface IdentityCapabilities {
  partnerLoginAvailable: boolean;
}

/** Mirrors GET /authenticated/conversations exactly. */
export interface AuthenticatedConversationSummary {
  session_id: string;
  title: string;
  channel: string;
  created_at: string;
  last_active_at: string;
}

export interface AuthenticatedConversationsPage {
  items: AuthenticatedConversationSummary[];
  next_cursor: string | null;
}

/** Mirrors GET /authenticated/conversations/{session_id} exactly. */
export interface AuthenticatedConversationMessage {
  role: string;
  content: string;
}

export interface AuthenticatedConversationAttachment {
  turn_index: number;
  source_id: string;
  display_name: string;
  kind: string;
  media_type?: string | null;
  size_bytes?: number | null;
  direction: string;
  ordinal: number;
  association_kind: string;
  status: string;
  created_at: string;
}

export interface AuthenticatedConversationDetail {
  session_id: string;
  channel: string;
  messages: AuthenticatedConversationMessage[];
  current_schema: Record<string, unknown> | null;
  attachments: AuthenticatedConversationAttachment[];
}

export interface AssistantTurn {
  content: string;
  sessionId?: string;
  sources: SourceRef[];
  stages: StageEvent[];
  done?: DoneEvent;
}

export type StreamFailurePhase = 'connect' | 'read' | 'protocol';

export interface StreamFailureState {
  phase: StreamFailurePhase;
  clientRequestId: string;
  message: string;
}

export type SseEvent =
  | { event: 'session'; data: { session_id?: string } }
  | { event: 'text_delta'; data: { delta?: string } }
  | { event: 'sources'; data: SourceRef[] }
  | { event: 'stage'; data: StageEvent }
  | { event: 'done'; data: DoneEvent }
  | { event: string; data: unknown };

export type FeedbackRating = 'good' | 'bad';
export type FeedbackStatus = 'idle' | 'submitting' | 'submitted' | 'error';

export interface MessageFeedbackState {
  rating?: FeedbackRating;
  status: FeedbackStatus;
  reasonCode?: string;
  comment?: string;
  error?: string;
}

export interface ReviewSessionSummary {
  external_session_id: string;
  channel?: string;
  first_question?: string;
  turns_count?: number;
  last_active_at?: string;
  bad_feedback_count?: number;
  fallback_count?: number;
  latest_priority?: string;
  latest_review_status?: string;
}

export interface ReviewSessionsPage {
  items: ReviewSessionSummary[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface ReviewTurn {
  id: string;
  turn_index?: number;
  trace_id?: string;
  question: string;
  answer: string;
  sources?: SourceRef[];
  stages?: StageEvent[];
  done?: DoneEvent;
  fallback_used?: boolean;
  fallback_reason?: string | null;
  outcome?: string | null;
  duration_ms?: number | null;
  created_at?: string;
}

export interface ReviewFeedback {
  id: string;
  turn_id?: string | null;
  rating: 'good' | 'bad';
  reason_code?: string | null;
  comment?: string;
  created_at?: string;
}

export interface ReviewTurnReview {
  id: string;
  turn_id?: string | null;
  priority?: string;
  review_status?: string;
  failure_layer?: string | null;
  failure_reason?: string;
  corrected_answer?: string;
  reviewer?: string;
  created_at?: string;
}

export interface ReviewSessionDetail {
  session: ReviewSessionSummary;
  turns: ReviewTurn[];
  feedback: ReviewFeedback[];
  reviews: ReviewTurnReview[];
}

export interface ReviewFeedbackRow {
  feedback_id: string;
  turn_id?: string | null;
  external_session_id?: string;
  rating: 'good' | 'bad';
  reason_code?: string | null;
  comment?: string;
  question?: string;
  answer?: string;
  trace_id?: string;
  created_at?: string;
  outcome?: string | null;
  fallback_used?: boolean;
  latest_review_status?: string | null;
  latest_priority?: string | null;
  synced_from?: string | null;
}

export interface ReviewFeedbackPage {
  items: ReviewFeedbackRow[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface ReviewMetrics {
  bad_feedback_pending?: number;
  qa_pending?: number;
  p0_p1_open?: number;
  qa_candidates?: number;
}

export interface TurnDecisionPayload {
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  review_status: string;
  failure_layer?: string;
  failure_reason?: string;
  expected_answer_notes?: string;
  corrected_answer?: string;
  reviewer?: string;
  flags?: {
    add_to_eval?: boolean;
    update_knowledge?: boolean;
    create_qa?: boolean;
  };
  knowledge_area?: string;
  gap_summary?: string;
  qa_tags?: Record<string, string[]>;
  metadata?: Record<string, unknown>;
}

export interface ReviewQaItem {
  id: string;
  source_type?: 'knowledge_qa' | 'chat_turn' | 'manual' | 'corrected_feedback';
  source_ref?: string;
  turn_id?: string | null;
  question: string;
  original_answer?: string;
  reviewed_answer?: string;
  product_tags?: string[];
  scenario_tags?: string[];
  technical_tags?: string[];
  sdk_tags?: string[];
  quality_score?: number | null;
  review_status?: 'pending' | 'approved' | 'needs_fix' | 'rejected' | 'candidate';
  reviewer?: string;
  review_notes?: string;
  should_export_to_knowledge?: boolean;
  updated_at?: string;
}

export interface ReviewQaItemsPage {
  items: ReviewQaItem[];
  total?: number;
  limit?: number;
  offset?: number;
}

export interface QaItemPayload {
  source_type?: 'knowledge_qa' | 'chat_turn' | 'manual' | 'corrected_feedback';
  source_ref?: string;
  turn_id?: string | null;
  question?: string;
  original_answer?: string;
  reviewed_answer?: string;
  product_tags?: string[];
  scenario_tags?: string[];
  technical_tags?: string[];
  sdk_tags?: string[];
  quality_score?: number | null;
  review_status?: 'pending' | 'approved' | 'needs_fix' | 'rejected' | 'candidate';
  reviewer?: string;
  review_notes?: string;
  should_export_to_knowledge?: boolean;
  metadata?: Record<string, unknown>;
}
