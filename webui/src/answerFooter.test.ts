import { describe, expect, it } from 'vitest';
import { buildAnswerFooterMeta } from './answerFooter';
import type { ChatMessage } from './types';

describe('buildAnswerFooterMeta', () => {
  it('shows answer length without exposing trace identifiers', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: 'answer text',
      createdAt: 1,
      done: {
        trace_id: 'trace-secret',
        text_len: 11,
        fallback_used: false,
      },
    };

    expect(buildAnswerFooterMeta(message)).toEqual({
      lengthText: '11 chars',
      fallbackReason: undefined,
    });
  });

  it('keeps fallback visible without requiring trace display', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: 'failed',
      createdAt: 1,
      done: {
        trace_id: 'trace-secret',
        fallback_used: true,
        fallback_reason: 'stream_crash',
      },
    };

    expect(buildAnswerFooterMeta(message)).toEqual({
      lengthText: '6 chars',
      fallbackReason: 'stream_crash',
    });
  });
});
