import type { ChatMessage, SessionHint } from './types';

/**
 * 最新一条 assistant 消息携带的长会话提示。
 *
 * 后端契约:done.session_hint(evals/compensation_baseline.json
 * session_length_new_session_hint)。只取最新一条,避免旧提示常驻。
 */
export function latestSessionHint(messages: ChatMessage[]): SessionHint | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message.role !== 'assistant') continue;
    if (!message.done) continue;
    const hint = message.done.session_hint;
    return hint?.suggest_new_session ? hint : null;
  }
  return null;
}

export function sessionHintText(hint: SessionHint): string {
  const turns = hint.assistant_turns;
  const prefix = turns ? `本次咨询已进行 ${turns} 轮，` : '本次咨询已较长，';
  return `${prefix}较早确认的约束和结论可能不再被完整记住。建议把新问题放到新会话继续。`;
}
