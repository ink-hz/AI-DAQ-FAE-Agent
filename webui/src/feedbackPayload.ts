import type { SendFeedbackParams } from './api';
import type { ChatMessage } from './types';

export type FeedbackRating = 'good' | 'bad';
export type FeedbackReasonCode =
  | 'fact_error'
  | 'missed_context'
  | 'weak_evidence'
  | 'wrong_product'
  | 'not_actionable'
  | 'other';

export interface FeedbackReasonOption {
  value: FeedbackReasonCode;
  label: string;
}

export interface FeedbackDraft {
  rating: FeedbackRating;
  reasonCode?: FeedbackReasonCode;
  comment?: string;
}

export const FEEDBACK_REASON_OPTIONS: FeedbackReasonOption[] = [
  { value: 'fact_error', label: '事实错误' },
  { value: 'missed_context', label: '没理解上下文' },
  { value: 'weak_evidence', label: '证据不足' },
  { value: 'wrong_product', label: '型号/产品不对' },
  { value: 'not_actionable', label: '不够可执行' },
  { value: 'other', label: '其他' },
];

export function canSendFeedback(message: ChatMessage): boolean {
  return message.role === 'assistant' && Boolean(message.done);
}

export function buildFeedbackPayload(params: {
  message: ChatMessage;
  messageIndex: number;
  fallbackSessionId?: string;
  rating: FeedbackRating;
  reasonCode?: FeedbackReasonCode;
  comment?: string;
}): SendFeedbackParams {
  const { message, messageIndex, fallbackSessionId, rating, reasonCode, comment } = params;
  if (!canSendFeedback(message)) {
    throw new Error('Feedback requires a completed assistant answer');
  }

  const sessionId = message.done?.session_id || fallbackSessionId;
  if (!sessionId) {
    throw new Error('Feedback requires a session id');
  }

  const trimmedComment = comment?.trim() || undefined;
  return {
    sessionId,
    messageIndex,
    rating,
    turnId: message.done?.turn_id,
    traceId: message.done?.trace_id,
    reasonCode: rating === 'bad' ? reasonCode : undefined,
    comment: trimmedComment,
  };
}
