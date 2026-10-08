import type { ChatMessage, DoneEvent, StageEvent } from './types';

export type ThinkingStatus = 'idle' | 'running' | 'done' | 'warning';

export interface ThinkingStep {
  id: string;
  title: string;
  detail: string;
  status: ThinkingStatus;
  elapsedMs?: number;
}

export function buildThinkingSteps(activeAssistant?: ChatMessage): ThinkingStep[] {
  if (!activeAssistant) {
    return [{
      id: 'empty',
      title: 'No active answer',
      detail: 'Select an answer to inspect the agent process.',
      status: 'idle',
    }];
  }

  // Persisted history currently contains only role/content. Its synthetic
  // `done` marker exists solely to keep feedback addressable by message index;
  // it is not evidence about planning, grounding, coverage, or fallback.
  if (activeAssistant.restored) return [];

  const steps = (activeAssistant.stages || []).map(stageToThinkingStep);
  const capabilityStep = capabilityPlanStep(activeAssistant.done);
  if (capabilityStep) steps.push(capabilityStep);

  const sourceCount = activeAssistant.sources?.length || 0;
  if (sourceCount > 0) {
    steps.push({
      id: 'sources',
      title: '检索证据',
      detail: `命中 ${sourceCount} 个来源`,
      status: 'done',
    });
  }

  const coverageStep = coverageSummaryStep(activeAssistant.done);
  if (coverageStep) steps.push(coverageStep);

  if (activeAssistant.streamError) {
    const streamError = activeAssistant.streamError;
    steps.push({
      id: 'stream-error',
      title: '请求流异常',
      detail: `${streamError.phase} · 请求 ${streamError.clientRequestId.slice(0, 8)} · ${streamError.message}`,
      status: 'warning',
    });
    return steps;
  }

  if (activeAssistant.done) {
    steps.push(riskStatusStep(activeAssistant.done));
  } else if (steps.length === 0) {
    steps.push({
      id: 'waiting',
      title: 'Waiting for agent events',
      detail: 'The agent has started, but no structured stage event has arrived yet.',
      status: 'running',
    });
  } else {
    steps.push({
      id: 'waiting',
      title: 'Waiting for final result',
      detail: 'The answer is still streaming.',
      status: 'running',
    });
  }

  return steps;
}

function stageToThinkingStep(stage: StageEvent, index: number): ThinkingStep {
  return {
    id: `stage-${index}`,
    title: stage.agent || stage.stage || 'Agent',
    detail: stage.message || stage.status || 'running',
    status: 'done',
    elapsedMs: stage.elapsed_ms,
  };
}

function capabilityPlanStep(done?: DoneEvent): ThinkingStep | null {
  const plan = isRecord(done?.plan) ? done?.plan : undefined;
  if (!plan) return null;
  const capabilities: string[] = [];
  const primary = plan.primary_capability;
  if (typeof primary === 'string' && primary) capabilities.push(primary);
  const extras = Array.isArray(plan.extra_capabilities) ? plan.extra_capabilities : [];
  for (const capability of extras) {
    if (typeof capability === 'string' && capability) capabilities.push(capability);
  }
  if (capabilities.length === 0) return null;
  return {
    id: 'capabilities',
    title: '规划能力',
    detail: capabilities.join(' + '),
    status: 'done',
  };
}

function coverageSummaryStep(done?: DoneEvent): ThinkingStep | null {
  const coverage = isRecord(done?.capability_coverage) ? done?.capability_coverage : undefined;
  if (!coverage) return null;
  const pairs = Object.entries(coverage)
    .filter(([, value]) => typeof value === 'string' && value.length > 0)
    .map(([key, value]) => `${key}: ${value}`);
  if (pairs.length === 0) return null;
  return {
    id: 'coverage',
    title: '证据覆盖',
    detail: pairs.join('; '),
    status: 'done',
  };
}

function riskStatusStep(done: DoneEvent): ThinkingStep {
  if (done.fallback_used) {
    return {
      id: 'risk',
      title: '风险状态',
      detail: `fallback: ${done.fallback_reason || 'unknown'}`,
      status: 'warning',
    };
  }
  return {
    id: 'risk',
    title: '风险状态',
    detail: 'No fallback reported',
    status: 'done',
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
