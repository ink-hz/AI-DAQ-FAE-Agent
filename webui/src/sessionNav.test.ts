import { describe, expect, it } from 'vitest';
import { buildTurnSummaries } from './sessionNav';
import type { ChatMessage } from './types';

describe('buildTurnSummaries', () => {
  it('uses the previous user message as the turn title', () => {
    const messages: ChatMessage[] = [
      {
        id: 'u1',
        role: 'user',
        content: 'Gemini 335Lg 工作温度范围是多少？',
        createdAt: 1,
      },
      {
        id: 'a1',
        role: 'assistant',
        content: '工作温度是...',
        createdAt: 2,
        sources: [{ title: 'Gemini 335Lg hardware' }, { title: 'QA' }],
        done: { trace_id: 'trace-abcdef123456', fallback_used: false },
      },
    ];

    expect(buildTurnSummaries(messages)).toEqual([
      {
        id: 'a1',
        index: 1,
        title: 'Gemini 335Lg 工作温度范围是多少？',
        meta: 'answered · 2 sources',
        isFallback: false,
      },
    ]);
  });

  it('marks streaming turns without sources as thinking', () => {
    const messages: ChatMessage[] = [
      { id: 'u1', role: 'user', content: '如果我要毫米级精度呢？', createdAt: 1 },
      { id: 'a1', role: 'assistant', content: '', createdAt: 2, sources: [], stages: [] },
    ];

    expect(buildTurnSummaries(messages)[0]).toMatchObject({
      id: 'a1',
      index: 1,
      title: '如果我要毫米级精度呢？',
      meta: 'thinking',
      isFallback: false,
    });
  });

  it('keeps fallback visible in the left rail metadata', () => {
    const messages: ChatMessage[] = [
      { id: 'u1', role: 'user', content: '这条请求失败了吗？', createdAt: 1 },
      {
        id: 'a1',
        role: 'assistant',
        content: '抱歉,服务暂时异常',
        createdAt: 2,
        sources: [],
        done: {
          trace_id: 'trace-fallback999',
          fallback_used: true,
          fallback_reason: 'stream_crash',
        },
      },
    ];

    expect(buildTurnSummaries(messages)[0]).toMatchObject({
      meta: 'fallback · stream_crash',
      isFallback: true,
    });
  });

  it('falls back to answer numbering when no previous user message exists', () => {
    const messages: ChatMessage[] = [
      {
        id: 'a1',
        role: 'assistant',
        content: 'orphan answer',
        createdAt: 1,
        done: { trace_id: 'trace-orphan' },
      },
    ];

    expect(buildTurnSummaries(messages)[0]?.title).toBe('Answer 1');
  });
  it('labels a restored turn instead of claiming it answered with no sources', () => {
    const messages: ChatMessage[] = [
      {
        id: 'history-session-1-0',
        role: 'user',
        content: '同步模式怎么配？',
        createdAt: 0,
        restored: true,
        sources: [],
        stages: [],
      },
      {
        id: 'history-session-1-1',
        role: 'assistant',
        content: '结论：使用硬件同步线。',
        createdAt: 1,
        restored: true,
        sources: [],
        stages: [],
        done: { session_id: 'session-1' },
      },
    ];

    expect(buildTurnSummaries(messages)[0]).toMatchObject({
      title: '同步模式怎么配？',
      meta: '历史会话',
      isFallback: false,
    });
  });
});
