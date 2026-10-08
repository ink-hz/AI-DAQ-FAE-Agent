import type {
  AssistantTurn,
  AuthenticatedConversationSummary,
  AuthenticatedConversationsPage,
  ChatMessage,
  DoneEvent,
  IdentityCapabilities,
  QaItemPayload,
  ReviewFeedbackPage,
  ReviewMetrics,
  ReviewQaItemsPage,
  ReviewQaItem,
  ReviewSessionDetail,
  ReviewSessionsPage,
  ReviewSessionSummary,
  SourceRef,
  SseEvent,
  StreamFailurePhase,
  TurnDecisionPayload,
} from './types';
import { enterpriseMutationHeaders } from './enterpriseIdentity';
import { faeApiPath } from './runtimePaths';

export const AUTHENTICATED_PAGE_LIMIT = 30;

export function createEmptyAssistantTurn(): AssistantTurn {
  return {
    content: '',
    sources: [],
    stages: [],
  };
}

export class ChatStreamError extends Error {
  readonly phase: StreamFailurePhase;
  readonly clientRequestId: string;
  readonly partialTurn: AssistantTurn;

  constructor(
    phase: StreamFailurePhase,
    clientRequestId: string,
    partialTurn: AssistantTurn,
    message: string,
  ) {
    super(message);
    this.name = 'ChatStreamError';
    this.phase = phase;
    this.clientRequestId = clientRequestId;
    this.partialTurn = partialTurn;
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function parseSseBuffer(buffer: string): { events: SseEvent[]; remainder: string } {
  const normalized = buffer.replace(/\r\n?/g, '\n');
  const chunks = normalized.split('\n\n');
  const remainder = chunks.pop() ?? '';
  const events = chunks
    .map(parseSseBlock)
    .filter((event): event is SseEvent => event !== null);
  return { events, remainder };
}

export function reduceAssistantTurn(turn: AssistantTurn, event: SseEvent): AssistantTurn {
  if (event.event === 'session' && isRecord(event.data)) {
    const sessionId = stringValue(event.data.session_id);
    return sessionId ? { ...turn, sessionId } : turn;
  }
  if (event.event === 'stage' && isRecord(event.data)) {
    return { ...turn, stages: [...turn.stages, event.data] };
  }
  if (event.event === 'text_delta' && isRecord(event.data)) {
    return { ...turn, content: turn.content + (stringValue(event.data.delta) ?? '') };
  }
  if (event.event === 'sources' && Array.isArray(event.data)) {
    return { ...turn, sources: event.data.filter(isRecord) as SourceRef[] };
  }
  if (event.event === 'done' && isRecord(event.data)) {
    const done = event.data as DoneEvent;
    return {
      ...turn,
      done,
      sessionId: done.session_id || turn.sessionId,
    };
  }
  return turn;
}

export async function streamChat(params: {
  message: string;
  sessionId?: string;
  channel?: 'fae' | 'ecom';
  clientRequestId: string;
  attachmentIds?: string[];
  signal?: AbortSignal;
  onEvent: (event: SseEvent, turn: AssistantTurn) => void;
}): Promise<AssistantTurn> {
  let turn = createEmptyAssistantTurn();
  let response: Response;
  try {
    response = await fetch(faeApiPath('/chat'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...enterpriseMutationHeaders(),
      },
      body: JSON.stringify({
        message: params.message,
        session_id: params.sessionId,
        channel: params.channel || 'fae',
        client_request_id: params.clientRequestId,
        attachment_ids: params.attachmentIds || [],
      }),
      signal: params.signal,
    });
  } catch (error) {
    throw new ChatStreamError(
      'connect',
      params.clientRequestId,
      turn,
      errorMessage(error),
    );
  }
  if (!response.ok) {
    throw new Error(`AI DAQ FAE request failed: ${response.status} ${response.statusText}`);
  }
  if (!response.body) {
    throw new ChatStreamError(
      'protocol',
      params.clientRequestId,
      turn,
      'AI DAQ FAE response has no readable stream',
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSseBuffer(buffer);
      buffer = parsed.remainder;
      for (const event of parsed.events) {
        turn = reduceAssistantTurn(turn, event);
        params.onEvent(event, turn);
      }
      if (turn.done) {
        await reader.cancel().catch(() => undefined);
        return turn;
      }
    }

    buffer += decoder.decode();
    const parsed = parseSseBuffer(`${buffer}\n\n`);
    for (const event of parsed.events) {
      turn = reduceAssistantTurn(turn, event);
      params.onEvent(event, turn);
    }
  } catch (error) {
    throw new ChatStreamError(
      'read',
      params.clientRequestId,
      turn,
      errorMessage(error),
    );
  }

  if (!turn.done) {
    throw new ChatStreamError(
      'protocol',
      params.clientRequestId,
      turn,
      'AI DAQ FAE stream ended without a done event',
    );
  }
  return turn;
}

export async function uploadAttachments(
  files: File[], signal?: AbortSignal,
): Promise<import('./attachments').AttachmentUploadResult[]> {
  const body = new FormData();
  files.forEach((file) => body.append('files', file, file.name));
  const enterpriseHeaders = enterpriseMutationHeaders();
  const response = await fetch(faeApiPath('/attachments'), {
    method: 'POST',
    body,
    signal,
    ...(Object.keys(enterpriseHeaders).length ? { headers: enterpriseHeaders } : {}),
  });
  if (response.status !== 201 && response.status !== 207) {
    throw new Error(`附件上传失败：${response.status} ${response.statusText}`);
  }
  const payload = await response.json() as {
    results?: import('./attachments').AttachmentUploadResult[];
  };
  if (!Array.isArray(payload.results) || payload.results.length !== files.length) {
    throw new Error('附件上传响应格式无效');
  }
  return payload.results;
}

export async function deleteAttachment(attachmentId: string): Promise<void> {
  const enterpriseHeaders = enterpriseMutationHeaders();
  const response = await fetch(faeApiPath(`/attachments/${encodeURIComponent(attachmentId)}`), {
    method: 'DELETE',
    ...(Object.keys(enterpriseHeaders).length ? { headers: enterpriseHeaders } : {}),
  });
  if (!response.ok) {
    throw new Error(`附件删除失败：${response.status} ${response.statusText}`);
  }
}

export async function fetchAttachmentCapabilities(): Promise<{
  visionEnabled: boolean;
  visionReason?: string;
}> {
  const response = await fetch(faeApiPath('/health'));
  if (!response.ok) throw new Error(`能力状态读取失败：${response.status}`);
  const payload = await response.json() as {
    attachments?: { vision_enabled?: boolean; vision_reason?: string };
  };
  return {
    visionEnabled: payload.attachments?.vision_enabled === true,
    visionReason: payload.attachments?.vision_reason,
  };
}

export async function fetchIdentityCapabilities(): Promise<IdentityCapabilities> {
  // A capability read must never block or fail the workspace: anything other
  // than an explicit `true` means the control is simply not rendered.
  try {
    const response = await fetch(faeApiPath('/identity/capabilities'));
    if (!response.ok) return { partnerLoginAvailable: false };
    const payload = await response.json() as { partner_login_available?: unknown };
    return { partnerLoginAvailable: payload?.partner_login_available === true };
  } catch {
    return { partnerLoginAvailable: false };
  }
}

export async function fetchAuthenticatedConversations(
  cursor?: string,
): Promise<AuthenticatedConversationsPage> {
  const query = new URLSearchParams();
  if (cursor) query.set('cursor', cursor);
  query.set('limit', String(AUTHENTICATED_PAGE_LIMIT));
  const response = await fetch(faeApiPath(`/authenticated/conversations?${query.toString()}`));
  if (!response.ok) {
    throw new Error(`历史会话读取失败：${response.status} ${response.statusText}`);
  }
  const payload = await response.json() as unknown;
  if (
    !isRecord(payload)
    || !Array.isArray(payload.items)
    || !payload.items.every(isAuthenticatedConversationSummary)
    || !(payload.next_cursor === null || typeof payload.next_cursor === 'string')
  ) {
    throw new Error('历史会话响应格式无效');
  }
  return {
    items: payload.items,
    next_cursor: typeof payload.next_cursor === 'string' ? payload.next_cursor : null,
  };
}

export async function fetchAuthenticatedConversation(
  sessionId: string,
): Promise<unknown> {
  const response = await fetch(
    faeApiPath(`/authenticated/conversations/${encodeURIComponent(sessionId)}`),
  );
  if (!response.ok) {
    throw new Error(`会话读取失败：${response.status} ${response.statusText}`);
  }
  return await response.json() as unknown;
}

function isAuthenticatedConversationSummary(
  value: unknown,
): value is AuthenticatedConversationSummary {
  if (!isRecord(value)) return false;
  return ['session_id', 'title', 'channel', 'created_at', 'last_active_at']
    .every((field) => typeof value[field] === 'string');
}

export interface SendFeedbackParams {
  sessionId: string;
  messageIndex: number;
  rating: 'good' | 'bad';
  comment?: string;
  turnId?: string;
  traceId?: string;
  reasonCode?: string;
}

export async function sendFeedback(params: SendFeedbackParams): Promise<void> {
  const response = await fetch(faeApiPath('/feedback'), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...enterpriseMutationHeaders(),
    },
    body: JSON.stringify({
      session_id: params.sessionId,
      message_index: params.messageIndex,
      rating: params.rating,
      comment: params.comment || '',
      turn_id: params.turnId,
      trace_id: params.traceId,
      reason_code: params.reasonCode,
    }),
  });
  if (!response.ok) {
    throw new Error(`Feedback failed: ${response.status} ${response.statusText}`);
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const method = (init?.method || 'GET').toUpperCase();
  const headers = new Headers(init?.headers);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    Object.entries(enterpriseMutationHeaders()).forEach(([key, value]) => {
      headers.set(key, value);
    });
  }
  const response = await fetch(url, init ? { ...init, headers } : init);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function fetchReviewSessions(params: {
  rating?: string;
  review_status?: string;
  priority?: string;
  channel?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ReviewSessionsPage> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') {
      query.set(key, String(value));
    }
  });
  const suffix = query.toString();
  return fetchJson(`/review/sessions${suffix ? `?${suffix}` : ''}`);
}

export async function fetchReviewSession(externalSessionId: string): Promise<ReviewSessionDetail> {
  return fetchJson(`/review/sessions/${encodeURIComponent(externalSessionId)}`);
}

export async function saveTurnDecision(
  turnId: string,
  payload: TurnDecisionPayload,
): Promise<{ review_id: string }> {
  return fetchJson(`/review/turns/${turnId}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function fetchReviewMetrics(): Promise<ReviewMetrics> {
  return fetchJson('/review/metrics');
}

export async function fetchReviewFeedback(params: {
  rating?: string;
  channel?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ReviewFeedbackPage> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') {
      query.set(key, String(value));
    }
  });
  const suffix = query.toString();
  return fetchJson(`/review/feedback${suffix ? `?${suffix}` : ''}`);
}

export async function fetchQaItems(params: {
  source_type?: string;
  review_status?: string;
  tag?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<ReviewQaItemsPage> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') {
      query.set(key, String(value));
    }
  });
  const suffix = query.toString();
  return fetchJson(`/review/qa-items${suffix ? `?${suffix}` : ''}`);
}

export async function createQaItem(payload: QaItemPayload): Promise<{ id: string }> {
  return fetchJson('/review/qa-items', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function updateQaItem(
  itemId: string,
  payload: QaItemPayload,
): Promise<{ id: string; updated?: boolean }> {
  return fetchJson(`/review/qa-items/${itemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function promoteQaItem(itemId: string): Promise<{ id: string; updated?: boolean }> {
  return fetchJson(`/review/qa-items/${itemId}/promote`, {
    method: 'POST',
  });
}

export function sourceLabel(source: SourceRef): string {
  return source.title || source.kb_id || source.model || source.path || '(unnamed source)';
}

export function buildEvalDraft(messages: ChatMessage[]): string {
  const latestAssistant = [...messages].reverse().find((message) => message.role === 'assistant');
  const latestUser = [...messages].reverse().find((message) => message.role === 'user');
  return JSON.stringify({
    question: latestUser?.content || '',
    answer: latestAssistant?.content || '',
    trace_id: latestAssistant?.done?.trace_id || '',
    sources: latestAssistant?.sources || [],
    stages: latestAssistant?.stages || [],
  }, null, 2);
}

function parseSseBlock(block: string): SseEvent | null {
  const lines = block.split('\n');
  let eventName = 'message';
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('event:')) {
      eventName = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  try {
    return { event: eventName, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}
