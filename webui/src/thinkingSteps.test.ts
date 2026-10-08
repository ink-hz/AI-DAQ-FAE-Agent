import { describe, expect, it } from 'vitest';
import { buildThinkingSteps } from './thinkingSteps';
import type { ChatMessage } from './types';

describe('buildThinkingSteps', () => {
  it('returns a stable empty state without an active answer', () => {
    expect(buildThinkingSteps(undefined)).toEqual([
      {
        id: 'empty',
        title: 'No active answer',
        detail: 'Select an answer to inspect the agent process.',
        status: 'idle',
      },
    ]);
  });

  it('maps real stage events into Thinking steps', () => {
    const active: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: '',
      createdAt: 1,
      stages: [
        { agent: 'Planner', message: '规划能力', elapsed_ms: 32 },
        { stage: 'retrieval', status: 'running' },
      ],
    };

    expect(buildThinkingSteps(active)).toEqual([
      {
        id: 'stage-0',
        title: 'Planner',
        detail: '规划能力',
        status: 'done',
        elapsedMs: 32,
      },
      {
        id: 'stage-1',
        title: 'retrieval',
        detail: 'running',
        status: 'done',
      },
      {
        id: 'waiting',
        title: 'Waiting for final result',
        detail: 'The answer is still streaming.',
        status: 'running',
      },
    ]);
  });

  it('adds capability, source, coverage, and risk summaries from done data', () => {
    const active: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: 'answer',
      createdAt: 1,
      sources: [{ title: 'Gemini 335L' }, { title: 'QA' }],
      done: {
        trace_id: 'trace-123456',
        fallback_used: true,
        fallback_reason: 'source_validation_failed',
        outcome: 'fallback_used',
        plan: {
          primary_capability: 'spec',
          extra_capabilities: ['risk_compliance'],
        },
        capability_coverage: {
          spec: 'full',
          risk_compliance: 'partial',
        },
      },
    };

    const steps = buildThinkingSteps(active);

    expect(steps).toContainEqual({
      id: 'capabilities',
      title: '规划能力',
      detail: 'spec + risk_compliance',
      status: 'done',
    });
    expect(steps).toContainEqual({
      id: 'sources',
      title: '检索证据',
      detail: '命中 2 个来源',
      status: 'done',
    });
    expect(steps).toContainEqual({
      id: 'coverage',
      title: '证据覆盖',
      detail: 'spec: full; risk_compliance: partial',
      status: 'done',
    });
    expect(steps).toContainEqual({
      id: 'risk',
      title: '风险状态',
      detail: 'fallback: source_validation_failed',
      status: 'warning',
    });
  });

  it('renders a terminal warning instead of waiting after stream failure', () => {
    const steps = buildThinkingSteps({
      id: 'a1',
      role: 'assistant',
      content: '',
      createdAt: 1,
      stages: [],
      streamError: {
        phase: 'connect',
        clientRequestId: 'web-12345678',
        message: 'Failed to fetch',
      },
    });

    expect(steps).toEqual([
      expect.objectContaining({
        id: 'stream-error',
        status: 'warning',
      }),
    ]);
    expect(steps[0].detail).toContain('web-1234');
  });

  it('does not invent provenance or risk steps for a restored answer', () => {
    expect(buildThinkingSteps({
      id: 'history-session-1-1',
      role: 'assistant',
      content: '历史回答',
      createdAt: 1,
      restored: true,
      sources: [],
      stages: [],
      done: { session_id: 'session-1' },
    })).toEqual([]);
  });
});
