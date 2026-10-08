import { describe, expect, it } from 'vitest';
import {
  conversationListTitle,
  formatConversationTimestamp,
  normalizeRestoredConversation,
} from './authenticatedHistory';

const detail = {
  session_id: 'session-1',
  channel: 'fae',
  messages: [
    { role: 'user', content: 'Gemini 335L 支持哪些同步模式？' },
    { role: 'assistant', content: '结论：支持硬件同步。' },
    { role: 'user', content: '那多机级联呢？' },
    { role: 'assistant', content: '结论：需要主从配置。' },
  ],
  current_schema: null,
  attachments: [
    {
      turn_index: 0,
      source_id: 'source-1',
      display_name: 'wiring.png',
      kind: 'image',
      media_type: 'image/png',
      size_bytes: 2048,
      direction: 'inbound',
      ordinal: 0,
      association_kind: 'explicit',
      status: 'ready',
      created_at: '2026-08-30T02:00:00+00:00',
    },
  ],
};

describe('restored authenticated conversations', () => {
  it('gives every restored message a stable client id and safe render metadata', () => {
    const first = normalizeRestoredConversation(detail);
    const second = normalizeRestoredConversation(detail);

    expect(first.sessionId).toBe('session-1');
    expect(first.channel).toBe('fae');
    expect(first.messages.map((message) => message.id))
      .toEqual(second.messages.map((message) => message.id));
    expect(new Set(first.messages.map((message) => message.id)).size).toBe(4);
    expect(first.messages.map((message) => message.role))
      .toEqual(['user', 'assistant', 'user', 'assistant']);
    expect(first.messages.every((message) => message.restored === true)).toBe(true);
  });

  it('never invents source evidence, stages or a bearer attachment id', () => {
    const restored = normalizeRestoredConversation(detail);
    const assistant = restored.messages[1];

    expect(assistant.sources).toEqual([]);
    expect(assistant.stages).toEqual([]);
    expect(assistant.done?.trace_id).toBeUndefined();
    expect(assistant.done?.turn_id).toBeUndefined();
    expect(assistant.done?.session_id).toBe('session-1');
    expect(restored.messages[0].attachments).toBeUndefined();
    expect(JSON.stringify(restored)).not.toContain('attachment_id');
    expect(restored.attachments[0]).toEqual(detail.attachments[0]);
  });

  it('keeps restored assistant turns feedback-eligible by message index', () => {
    const restored = normalizeRestoredConversation(detail);

    expect(restored.messages[3].done).toBeTruthy();
    expect(restored.messages.indexOf(restored.messages[3])).toBe(3);
  });

  it('rejects a malformed conversation instead of rendering a partial one', () => {
    expect(() => normalizeRestoredConversation({ ...detail, session_id: '' }))
      .toThrow('历史会话响应格式无效');
    expect(() => normalizeRestoredConversation({ ...detail, channel: 'sms' }))
      .toThrow('历史会话响应格式无效');
    expect(() => normalizeRestoredConversation({
      ...detail,
      messages: [{ role: 'system', content: 'x' }],
    })).toThrow('历史会话响应格式无效');
    expect(() => normalizeRestoredConversation({
      ...detail,
      messages: [{ role: 'user', content: 42 }],
    })).toThrow('历史会话响应格式无效');
  });

  it('accepts an empty restored conversation', () => {
    const restored = normalizeRestoredConversation({
      ...detail,
      messages: [],
      attachments: [],
    });

    expect(restored.messages).toEqual([]);
    expect(restored.attachments).toEqual([]);
  });

  it('formats a list timestamp without leaking raw identity or crashing', () => {
    expect(formatConversationTimestamp('2026-08-30T02:30:00+00:00')).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/,
    );
    expect(formatConversationTimestamp('not-a-date')).toBe('');
    expect(formatConversationTimestamp(undefined)).toBe('');
  });

  it('falls back to a neutral list title when the server title is empty', () => {
    expect(conversationListTitle('Gemini 335L 配置')).toBe('Gemini 335L 配置');
    expect(conversationListTitle('Gemini\n335L\u0000 配置')).toBe('Gemini335L 配置');
    expect(conversationListTitle('   ')).toBe('未命名会话');
    expect(conversationListTitle(undefined)).toBe('未命名会话');
  });
});
