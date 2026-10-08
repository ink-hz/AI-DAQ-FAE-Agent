import { describe, expect, it } from 'vitest';
import {
  FEEDBACK_REASON_OPTIONS,
  buildFeedbackPayload,
  canSendFeedback,
} from './feedbackPayload';
import type { ChatMessage } from './types';

describe('feedback payload helpers', () => {
  it('builds a linked positive feedback payload for a completed assistant answer', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: {
        session_id: 'session-1',
        turn_id: 'turn-1',
        trace_id: 'trace-1',
      },
    };

    expect(canSendFeedback(message)).toBe(true);
    expect(buildFeedbackPayload({
      message,
      messageIndex: 1,
      fallbackSessionId: 'session-fallback',
      rating: 'good',
    })).toEqual({
      sessionId: 'session-1',
      messageIndex: 1,
      rating: 'good',
      turnId: 'turn-1',
      traceId: 'trace-1',
      reasonCode: undefined,
      comment: undefined,
    });
  });

  it('keeps the selected issue reason and trims detailed comments', () => {
    const message: ChatMessage = {
      id: 'a2',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: {
        trace_id: 'trace-2',
      },
    };

    expect(buildFeedbackPayload({
      message,
      messageIndex: 3,
      fallbackSessionId: 'session-2',
      rating: 'bad',
      reasonCode: 'fact_error',
      comment: '  温度范围写错了  ',
    })).toMatchObject({
      sessionId: 'session-2',
      messageIndex: 3,
      rating: 'bad',
      traceId: 'trace-2',
      reasonCode: 'fact_error',
      comment: '温度范围写错了',
    });
  });

  it('does not allow feedback while the assistant answer is still streaming', () => {
    const message: ChatMessage = {
      id: 'a3',
      role: 'assistant',
      content: '正在生成',
      createdAt: 1,
    };

    expect(canSendFeedback(message)).toBe(false);
    expect(() => buildFeedbackPayload({
      message,
      messageIndex: 1,
      fallbackSessionId: 'session-3',
      rating: 'good',
    })).toThrow('completed assistant answer');
  });

  it('offers concise issue reasons for downstream data-flywheel review', () => {
    expect(FEEDBACK_REASON_OPTIONS.map((option) => option.value)).toEqual([
      'fact_error',
      'missed_context',
      'weak_evidence',
      'wrong_product',
      'not_actionable',
      'other',
    ]);
  });
});
