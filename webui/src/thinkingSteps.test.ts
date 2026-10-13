import { describe, expect, it } from 'vitest';
import { buildThinkingSteps } from './thinkingSteps';
import type { ChatMessage } from './types';

describe('buildThinkingSteps', () => {
  it('uses the terminal event for one persisted stage id', () => {
    const steps = buildThinkingSteps({
      id: 'a-stage', role: 'assistant', content: '完成', createdAt: 2,
      stages: [
        { stage: 'loop', stage_id: 'loop-1', status: 'running' },
        { stage: 'loop', stage_id: 'loop-1', status: 'completed', elapsed_ms: 8 },
      ],
      done: { outcome: 'resolved', fallback_used: false },
    });
    expect(steps.filter((step) => step.title === 'AI DAQ FAE Agent')).toHaveLength(1);
    expect(steps.find((step) => step.title === 'AI DAQ FAE Agent')).toMatchObject({
      status: 'done', elapsedMs: 8,
    });
  });

  it('explains DAQ workflow stages without exposing raw stage codes', () => {
    const steps = buildThinkingSteps({
      id: 'a-stages', role: 'assistant', content: '', createdAt: 1,
      stages: [
        { stage: 'guardrail', status: 'completed', elapsed_ms: 1 },
        { stage: 'schema', status: 'completed', elapsed_ms: 5647 },
        { stage: 'loop', status: 'completed', elapsed_ms: 14097 },
        { stage: 'coverage', status: 'completed' },
        { stage: 'outcome', status: 'completed' },
      ],
      done: { outcome: 'resolved', capability_coverage: { service_identity: 'full' } },
    });
    expect(steps.slice(0, 5).map((step) => step.title)).toEqual([
      'Boundary Guard Agent', 'Intent Analyst Agent', 'AI DAQ FAE Agent',
      '证据覆盖检查', '回答结果',
    ]);
    expect(steps[1].detail).toContain('问题结构化完成');
  });
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
        status: 'running',
      },
      {
        id: 'waiting',
        title: 'Waiting for final result',
        detail: 'The answer is still streaming.',
        status: 'running',
      },
    ]);
  });

  it('shows safe abstention as an answer outcome, not absence of fallback', () => {
    const steps = buildThinkingSteps({
      id: 'a2', role: 'assistant', content: '资料不足', createdAt: 2,
      sources: [], stages: [],
      done: { outcome: 'safe_abstained', fallback_used: false, trace_id: 'trace2' },
    });
    expect(steps.at(-1)).toMatchObject({
      id: 'risk', status: 'warning', detail: '安全弃答 · 关键证据仍不足',
    });
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
