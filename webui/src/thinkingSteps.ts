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

  const stageEvents = activeAssistant.stages || [];
  const lastById = new Map<string, number>();
  stageEvents.forEach((stage, index) => {
    if (stage.stage_id) lastById.set(stage.stage_id, index);
  });
  const english = activeAssistant.responseLanguage === 'en-US'
    || activeAssistant.done?.answer_language === 'en';
  const steps = stageEvents
    .filter((stage, index) => !stage.stage_id || lastById.get(stage.stage_id) === index)
    .map((stage, index) => stageToThinkingStep(stage, index, english));
  const capabilityStep = capabilityPlanStep(activeAssistant.done, english);
  if (capabilityStep) steps.push(capabilityStep);

  const sourceCount = activeAssistant.sources?.length || 0;
  if (sourceCount > 0) {
    steps.push({
      id: 'sources',
      title: english ? 'Evidence sources' : '检索证据',
      detail: english ? `${sourceCount} sources retrieved` : `命中 ${sourceCount} 个来源`,
      status: 'done',
    });
  }

  const coverageStep = coverageSummaryStep(activeAssistant.done, english);
  if (coverageStep) steps.push(coverageStep);

  if (activeAssistant.streamError) {
    const streamError = activeAssistant.streamError;
    steps.push({
      id: 'stream-error',
      title: english ? 'Request interrupted' : '请求流异常',
      detail: english ? `${streamError.phase} · request ${streamError.clientRequestId.slice(0, 8)}`
        : `${streamError.phase} · 请求 ${streamError.clientRequestId.slice(0, 8)} · ${streamError.message}`,
      status: 'warning',
    });
    return steps;
  }

  if (activeAssistant.done) {
    steps.push(riskStatusStep(activeAssistant.done, english));
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

function stageToThinkingStep(stage: StageEvent, index: number, english = false): ThinkingStep {
  const known: Record<string, [string, string, string, string]> = {
    guardrail: ['Boundary Guard Agent', '检查问题是否在 Orbbec 技术服务范围内',
      'Boundary Guard Agent', 'Checked the technical service boundary'],
    schema: ['Intent Analyst Agent', '问题结构化完成',
      'Intent Analyst Agent', 'Structured the request and constraints'],
    loop: ['AI DAQ FAE Agent', '模型驱动工具循环取证作答',
      'AI DAQ FAE Agent', 'Gathered evidence and drafted the answer'],
    coverage: ['证据覆盖检查', '核对本轮目标与实际证据',
      'Evidence coverage', 'Checked goals against available evidence'],
    outcome: ['回答结果', '记录本轮结果与回退状态',
      'Answer outcome', 'Recorded the result and fallback status'],
    tool_call: ['知识工具', '执行本轮证据查询',
      'Evidence tool', 'Queried the available evidence'],
  };
  const display = stage.stage ? known[stage.stage] : undefined;
  const running: Record<string, [string, string]> = {
    guardrail: ['正在检查服务范围', 'Checking the service boundary'],
    schema: ['正在理解问题与约束', 'Understanding the request and constraints'],
    loop: ['正在检索证据并生成回答', 'Gathering evidence and drafting an answer'],
  };
  const runningDetail = stage.status === 'running' && stage.stage
    ? running[stage.stage]?.[english ? 1 : 0] : undefined;
  return {
    id: `stage-${index}`,
    title: display ? display[english ? 2 : 0] : stage.agent || stage.stage || 'Agent',
    detail: runningDetail || (display ? display[english ? 3 : 1]
      : stage.message || stage.status || 'running'),
    status: stage.status === 'running' ? 'running'
      : stage.status === 'error' ? 'warning' : 'done',
    elapsedMs: stage.elapsed_ms,
  };
}

function capabilityPlanStep(done?: DoneEvent, english = false): ThinkingStep | null {
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
    title: english ? 'Capabilities' : '规划能力',
    detail: capabilities.join(' + '),
    status: 'done',
  };
}

function coverageSummaryStep(done?: DoneEvent, english = false): ThinkingStep | null {
  const coverage = isRecord(done?.capability_coverage) ? done?.capability_coverage : undefined;
  if (!coverage) return null;
  const pairs = Object.entries(coverage)
    .filter(([, value]) => typeof value === 'string' && value.length > 0)
    .map(([key, value]) => `${key}: ${value}`);
  if (pairs.length === 0) return null;
  return {
    id: 'coverage',
    title: english ? 'Evidence coverage' : '证据覆盖',
    detail: pairs.join('; '),
    status: 'done',
  };
}

function riskStatusStep(done: DoneEvent, english = false): ThinkingStep {
  if (done.fallback_used) {
    return {
      id: 'risk',
      title: english ? 'Fallback status' : '风险状态',
      detail: `fallback: ${done.fallback_reason || 'unknown'}`,
      status: 'warning',
    };
  }
  if (done.outcome === 'safe_abstained') {
    return {
      id: 'risk', title: english ? 'Fallback status' : '风险状态',
      detail: english ? 'Unable to conclude · key evidence is missing'
        : '安全弃答 · 关键证据仍不足', status: 'warning',
    };
  }
  if (done.outcome && done.outcome !== 'resolved') {
    return {
      id: 'risk', title: english ? 'Fallback status' : '风险状态',
      detail: english ? `Request incomplete · ${done.outcome}`
        : `请求未完成 · ${done.outcome}`, status: 'warning',
    };
  }
  return {
    id: 'risk',
    title: english ? 'Fallback status' : '风险状态',
    detail: english ? 'Completed' : '已完成',
    status: 'done',
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
