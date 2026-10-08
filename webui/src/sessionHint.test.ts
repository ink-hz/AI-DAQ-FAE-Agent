import { describe, expect, it } from 'vitest';
import { latestSessionHint, sessionHintText } from './sessionHint';
import type { ChatMessage } from './types';

function assistantMessage(id: string, done?: ChatMessage['done']): ChatMessage {
  return { id, role: 'assistant', content: '回答', createdAt: 1, done };
}

describe('latestSessionHint', () => {
  it('returns null when no assistant message carries a hint', () => {
    const messages: ChatMessage[] = [
      { id: 'u1', role: 'user', content: '问题', createdAt: 1 },
      assistantMessage('a1', { trace_id: 't1' }),
    ];
    expect(latestSessionHint(messages)).toBeNull();
  });

  it('returns the hint from the latest assistant done', () => {
    const messages: ChatMessage[] = [
      assistantMessage('a1'),
      assistantMessage('a2', {
        session_hint: { suggest_new_session: true, reason: 'session_length', assistant_turns: 10 },
      }),
    ];
    expect(latestSessionHint(messages)).toEqual({
      suggest_new_session: true,
      reason: 'session_length',
      assistant_turns: 10,
    });
  });

  it('does not surface a stale hint from an earlier turn', () => {
    const messages: ChatMessage[] = [
      assistantMessage('a1', {
        session_hint: { suggest_new_session: true, assistant_turns: 10 },
      }),
      assistantMessage('a2', { trace_id: 't2' }),
    ];
    expect(latestSessionHint(messages)).toBeNull();
  });

  it('ignores a hint without suggest_new_session', () => {
    const messages: ChatMessage[] = [
      assistantMessage('a1', { session_hint: { reason: 'session_length' } }),
    ];
    expect(latestSessionHint(messages)).toBeNull();
  });
});

describe('sessionHintText', () => {
  it('mentions the turn count when available', () => {
    expect(sessionHintText({ suggest_new_session: true, assistant_turns: 12 }))
      .toContain('12 轮');
  });

  it('falls back to generic wording without a count', () => {
    expect(sessionHintText({ suggest_new_session: true })).toContain('本次咨询已较长');
  });
});
