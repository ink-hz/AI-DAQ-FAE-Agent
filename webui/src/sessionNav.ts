import type { ChatMessage } from './types';

export interface TurnSummary {
  id: string;
  index: number;
  title: string;
  meta: string;
  isFallback: boolean;
}

export function buildTurnSummaries(messages: ChatMessage[]): TurnSummary[] {
  const summaries: TurnSummary[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const message = messages[i];
    if (message.role !== 'assistant') continue;

    const index = summaries.length + 1;
    const previousUser = findPreviousUserMessage(messages, i);
    summaries.push({
      id: message.id,
      index,
      title: previousUser?.content.trim() || `Answer ${index}`,
      meta: buildTurnMeta(message),
      isFallback: message.done?.fallback_used === true,
    });
  }
  return summaries;
}

function findPreviousUserMessage(messages: ChatMessage[], beforeIndex: number): ChatMessage | undefined {
  for (let i = beforeIndex - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role === 'user') return message;
  }
  return undefined;
}

function buildTurnMeta(message: ChatMessage): string {
  if (message.restored) {
    // Restored turns carry no persisted evidence, so the rail says so instead of
    // reporting "answered · no sources" as if the answer had been ungrounded.
    return '历史会话';
  }
  if (message.done?.fallback_used) {
    return `fallback · ${message.done.fallback_reason || 'unknown'}`;
  }
  if (!message.done) {
    return 'thinking';
  }
  const sourceCount = message.sources?.length || 0;
  return sourceCount > 0 ? `answered · ${sourceCount} sources` : 'answered · no sources';
}
