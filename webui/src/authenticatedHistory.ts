import type {
  AuthenticatedConversationAttachment,
  AuthenticatedConversationDetail,
  AuthenticatedConversationTurn,
  ChatMessage,
  Role,
} from './types';

export interface RestoredConversation {
  readOnly: boolean;
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
 * Match optional owner-scoped persisted turns to the sealed message sequence.
 * Older turns without a projection retain empty, synthetic render metadata.
 */
export function normalizeRestoredConversation(
  payload: unknown,
): RestoredConversation {
  if (payload === null || typeof payload !== 'object') invalid();
  const detail = payload as Partial<AuthenticatedConversationDetail>;
  const sessionId = detail.session_id;
  if (typeof sessionId !== 'string' || !sessionId) invalid();
  if (detail.channel !== 'fae') invalid();
  if (detail.read_only !== undefined && typeof detail.read_only !== 'boolean') invalid();
  if (!Array.isArray(detail.messages)) invalid();
  const turns = detail.turns ?? [];
  if (!Array.isArray(turns)) invalid();
  const byIndex = new Map<number, AuthenticatedConversationTurn>();
  for (const turn of turns) {
    if (turn === null || typeof turn !== 'object'
        || !Number.isInteger(turn.turn_index) || turn.turn_index < 0
        || typeof turn.turn_id !== 'string' || typeof turn.trace_id !== 'string'
        || typeof turn.question !== 'string' || typeof turn.answer !== 'string'
        || typeof turn.outcome !== 'string'
        || !Array.isArray(turn.sources) || !Array.isArray(turn.stages)
        || turn.done === null || typeof turn.done !== 'object' || Array.isArray(turn.done)
        || byIndex.has(turn.turn_index)) invalid();
    byIndex.set(turn.turn_index, turn);
  }
  let assistantIndex = 0;
  let previousUser: string | undefined;
  const messages = detail.messages.map((message, index): ChatMessage => {
    if (message === null || typeof message !== 'object') invalid();
    const role = readRole((message as { role?: unknown }).role);
    const content = (message as { content?: unknown }).content;
    if (typeof content !== 'string') invalid();
    if (role === 'user') previousUser = content;
    const turn = role === 'assistant' ? byIndex.get(assistantIndex++) : undefined;
    if (turn && (turn.question !== previousUser || turn.answer !== content)) invalid();
    return {
      id: `history-${sessionId}-${index}`,
      role,
      content,
      ...(turn?.done.answer_language === 'en' ? { responseLanguage: 'en-US' as const }
        : turn?.done.answer_language === 'zh' ? { responseLanguage: 'zh-CN' as const } : {}),
      // Synthetic monotonic order, not a clock reading: the server keeps no
      // per-message timestamp and the UI must not imply one.
      createdAt: index,
      restored: !turn,
      sources: turn?.sources ?? [],
      stages: turn?.stages ?? [],
      ...(role === 'assistant' ? { done: turn
        ? { ...turn.done, outcome: turn.outcome, trace_id: turn.trace_id,
            turn_id: turn.turn_id, session_id: sessionId }
        : { session_id: sessionId } } : {}),
    };
  });
  const attachments = detail.attachments;
  if (!Array.isArray(attachments)) invalid();
  return {
    sessionId,
    readOnly: detail.read_only === true,
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
