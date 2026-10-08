import type {
  AuthenticatedConversationAttachment,
  AuthenticatedConversationDetail,
  ChatMessage,
  Role,
} from './types';

export interface RestoredConversation {
  sessionId: string;
  channel: string;
  messages: ChatMessage[];
  attachments: AuthenticatedConversationAttachment[];
}

const RESTORED_ROLES: Role[] = ['user', 'assistant'];
const UNNAMED_CONVERSATION = '未命名会话';

function invalid(): never {
  throw new Error('历史会话响应格式无效');
}

function readRole(value: unknown): Role {
  if (typeof value !== 'string') invalid();
  const role = RESTORED_ROLES.find((candidate) => candidate === value);
  if (role === undefined) invalid();
  return role;
}

/**
 * Turn one owned conversation into renderable messages.
 *
 * The server persists only `{role, content}`, so the render metadata here is
 * deliberately synthetic and empty: no source evidence, no stages, no trace or
 * turn id and no bearer attachment id is invented. Message order is preserved
 * 1:1 with the server list so feedback keeps addressing turns by index.
 */
export function normalizeRestoredConversation(
  payload: unknown,
): RestoredConversation {
  if (payload === null || typeof payload !== 'object') invalid();
  const detail = payload as Partial<AuthenticatedConversationDetail>;
  const sessionId = detail.session_id;
  if (typeof sessionId !== 'string' || !sessionId) invalid();
  if (detail.channel !== 'fae') invalid();
  if (!Array.isArray(detail.messages)) invalid();
  const messages = detail.messages.map((message, index): ChatMessage => {
    if (message === null || typeof message !== 'object') invalid();
    const role = readRole((message as { role?: unknown }).role);
    const content = (message as { content?: unknown }).content;
    if (typeof content !== 'string') invalid();
    return {
      id: `history-${sessionId}-${index}`,
      role,
      content,
      // Synthetic monotonic order, not a clock reading: the server keeps no
      // per-message timestamp and the UI must not imply one.
      createdAt: index,
      restored: true,
      sources: [],
      stages: [],
      ...(role === 'assistant' ? { done: { session_id: sessionId } } : {}),
    };
  });
  const attachments = detail.attachments;
  if (!Array.isArray(attachments)) invalid();
  return {
    sessionId,
    channel: detail.channel,
    messages,
    attachments,
  };
}

export function formatConversationTimestamp(value: string | undefined): string {
  if (typeof value !== 'string' || !value) return '';
  const parsed = new Date(value);
  const time = parsed.getTime();
  if (Number.isNaN(time)) return '';
  const pad = (part: number) => String(part).padStart(2, '0');
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`
    + ` ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

export function conversationListTitle(title: string | undefined): string {
  const safeTitle = typeof title === 'string'
    ? title.replace(/[\u0000-\u001f\u007f]/g, '').trim()
    : '';
  return safeTitle || UNNAMED_CONVERSATION;
}
