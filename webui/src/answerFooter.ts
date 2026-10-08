import type { ChatMessage } from './types';

export interface AnswerFooterMeta {
  lengthText: string;
  fallbackReason?: string;
}

export function buildAnswerFooterMeta(message: ChatMessage): AnswerFooterMeta | null {
  if (message.role !== 'assistant' || !message.done) return null;

  return {
    lengthText: `${message.done.text_len || message.content.length} chars`,
    fallbackReason: message.done.fallback_used
      ? message.done.fallback_reason || 'fallback'
      : undefined,
  };
}
