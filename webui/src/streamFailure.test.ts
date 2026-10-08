import { describe, expect, it } from 'vitest';
import { ChatStreamError } from './api';
import { mergeStreamFailure } from './streamFailure';
import type { ChatMessage } from './types';

describe('mergeStreamFailure', () => {
  it('keeps partial answer data and attaches terminal failure metadata', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: '',
      createdAt: 1,
      sources: [],
      stages: [],
    };
    const error = new ChatStreamError(
      'read',
      'web-12345678',
      {
        content: 'partial answer',
        sessionId: 's1',
        sources: [{ title: 'source' }],
        stages: [{ stage: 'planner' }],
      },
      'socket closed',
    );

    expect(mergeStreamFailure(message, error)).toMatchObject({
      content: 'partial answer',
      sources: [{ title: 'source' }],
      stages: [{ stage: 'planner' }],
      streamError: {
        phase: 'read',
        clientRequestId: 'web-12345678',
        message: 'socket closed',
      },
    });
  });

  it('shows a correlated failure message when no answer text arrived', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: '',
      createdAt: 1,
    };
    const error = new ChatStreamError(
      'connect',
      'web-12345678',
      { content: '', sources: [], stages: [] },
      'Failed to fetch',
    );

    const merged = mergeStreamFailure(message, error);

    expect(merged.content).toContain('connect');
    expect(merged.content).toContain('web-1234');
  });
});
