// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createEmptyAssistantTurn,
  fetchAuthenticatedConversation,
  fetchAuthenticatedConversations,
  fetchIdentityCapabilities,
  parseSseBuffer,
  reduceAssistantTurn,
  sendFeedback,
  sourceLabel,
  streamChat,
  uploadAttachments,
  deleteAttachment,
  fetchAttachmentCapabilities,
} from './api';

describe('AI DAQ FAE WebUI API helpers', () => {
  afterEach(() => {
    document.head.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('parses named SSE events and preserves partial trailing buffers', () => {
    const { events, remainder } = parseSseBuffer(
      [
        'event: session',
        'data: {"session_id":"s1"}',
        '',
        'event: text_delta',
        'data: {"delta":"hello"}',
        '',
        'event: done',
        'data: {"trace_id":"abc"',
      ].join('\n'),
    );

    expect(events).toEqual([
      { event: 'session', data: { session_id: 's1' } },
      { event: 'text_delta', data: { delta: 'hello' } },
    ]);
    expect(remainder).toContain('event: done');
  });

  it('reduces SSE events into an assistant turn', () => {
    let turn = createEmptyAssistantTurn();
    turn = reduceAssistantTurn(turn, { event: 'session', data: { session_id: 's1' } });
    turn = reduceAssistantTurn(turn, { event: 'stage', data: { agent: 'Planner', message: '规划能力' } });
    turn = reduceAssistantTurn(turn, { event: 'text_delta', data: { delta: '结论' } });
    turn = reduceAssistantTurn(turn, { event: 'sources', data: [{ title: 'hardware.md' }] });
    turn = reduceAssistantTurn(turn, {
      event: 'done',
      data: { trace_id: 'trace1', fallback_used: false, session_id: 's1' },
    });

    expect(turn.sessionId).toBe('s1');
    expect(turn.content).toBe('结论');
    expect(turn.stages).toHaveLength(1);
    expect(turn.sources).toEqual([{ title: 'hardware.md' }]);
    expect(turn.done?.trace_id).toBe('trace1');
  });

  it('ignores heartbeat events in assistant content and thinking stages', () => {
    let turn = createEmptyAssistantTurn();
    turn = reduceAssistantTurn(turn, { event: 'text_delta', data: { delta: '结论' } });
    turn = reduceAssistantTurn(turn, {
      event: 'heartbeat',
      data: { elapsed_ms: 10000, heartbeat_count: 1 },
    });

    expect(turn.content).toBe('结论');
    expect(turn.stages).toEqual([]);
    expect(turn.done).toBeUndefined();
  });

  it('formats source labels from common source payloads', () => {
    expect(sourceLabel({ title: 'Gemini 335L hardware' })).toBe('Gemini 335L hardware');
    expect(sourceLabel({ kb_id: 'FAE-001' })).toBe('FAE-001');
    expect(sourceLabel({ model: 'Gemini_335L' })).toBe('Gemini_335L');
  });

  it('sends turn and trace identifiers with feedback', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    await sendFeedback({
      sessionId: 'session-1',
      messageIndex: 1,
      rating: 'bad',
      turnId: 'turn-123',
      traceId: 'trace-123',
      reasonCode: 'fact_error',
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock).toHaveBeenCalledWith('/feedback', expect.any(Object));
    expect(JSON.parse(String(request.body))).toMatchObject({
      session_id: 'session-1',
      message_index: 1,
      rating: 'bad',
      turn_id: 'turn-123',
      trace_id: 'trace-123',
      reason_code: 'fact_error',
    });
  });

  it('uses the internal FAE API base when runtime meta selects /daq', async () => {
    document.head.innerHTML = '<meta name="fae-api-base" content="/daq/api">';
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      'event: done\ndata: {"outcome":"resolved"}\n\n',
    ));
    vi.stubGlobal('fetch', fetchMock);

    await streamChat({
      message: 'Q',
      clientRequestId: 'web-internal',
      onEvent: vi.fn(),
    });

    expect(fetchMock.mock.calls[0][0]).toBe('/daq/api/chat');
  });

  it('classifies fetch rejection as a connect failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    );

    await expect(streamChat({
      message: 'Q',
      clientRequestId: 'web-1',
      onEvent: vi.fn(),
    })).rejects.toMatchObject({
      name: 'ChatStreamError',
      phase: 'connect',
      clientRequestId: 'web-1',
      partialTurn: { content: '', stages: [], sources: [] },
    });
  });

  it('preserves received events when the response reader fails', async () => {
    let pullCount = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (pullCount++ === 0) {
          controller.enqueue(new TextEncoder().encode(
            'event: stage\ndata: {"stage":"planner"}\n\n'
            + 'event: text_delta\ndata: {"delta":"partial"}\n\n',
          ));
          return;
        }
        controller.error(new Error('socket closed'));
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));

    await expect(streamChat({
      message: 'Q',
      clientRequestId: 'web-2',
      onEvent: vi.fn(),
    })).rejects.toMatchObject({
      name: 'ChatStreamError',
      phase: 'read',
      clientRequestId: 'web-2',
      partialTurn: {
        content: 'partial',
        stages: [{ stage: 'planner' }],
        sources: [],
      },
    });
  });

  it('classifies clean EOF without done as a protocol failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'event: text_delta\ndata: {"delta":"partial"}\n\n',
    )));

    await expect(streamChat({
      message: 'Q',
      clientRequestId: 'web-3',
      onEvent: vi.fn(),
    })).rejects.toMatchObject({
      name: 'ChatStreamError',
      phase: 'protocol',
      clientRequestId: 'web-3',
      partialTurn: { content: 'partial' },
    });
  });

  it('sends the client request id and resolves a complete done stream', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      'event: text_delta\ndata: {"delta":"answer"}\n\n'
      + 'event: done\ndata: {"outcome":"resolved"}\n\n',
    ));
    vi.stubGlobal('fetch', fetchMock);

    const turn = await streamChat({
      message: 'Q',
      clientRequestId: 'web-4',
      onEvent: vi.fn(),
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      message: 'Q',
      client_request_id: 'web-4',
    });
    expect(turn.content).toBe('answer');
    expect(turn.done?.outcome).toBe('resolved');
  });

  it('sends ready attachment ids in the chat payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      'event: done\ndata: {"outcome":"resolved"}\n\n',
    ));
    vi.stubGlobal('fetch', fetchMock);

    await streamChat({
      message: '读附件',
      attachmentIds: ['opaque-a'],
      clientRequestId: 'web-att',
      onEvent: vi.fn(),
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toMatchObject({
      message: '读附件',
      attachment_ids: ['opaque-a'],
    });
  });

  it('maps 207 mixed attachment upload results in server order', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      results: [
        { ok: true, attachment: {
          attachment_id: 'opaque-a', source_id: 'att-src-a', display_name: 'ok.log',
          kind: 'text', status: 'ready', parse_coverage: 'full', warnings: [],
        } },
        { ok: false, error: { code: 'unsupported_attachment_type', message: '不支持' } },
      ],
    }), { status: 207 }));
    vi.stubGlobal('fetch', fetchMock);
    const files = [new File(['ok'], 'ok.log'), new File(['bad'], 'bad.zip')];

    const results = await uploadAttachments(files);

    expect(results.map((item) => item.ok)).toEqual([true, false]);
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.body).toBeInstanceOf(FormData);
    expect(Array.from((request.body as FormData).getAll('files'))).toHaveLength(2);
  });

  it('reads the server vision capability without exposing provider configuration', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      attachments: { vision_enabled: false, vision_reason: 'disabled_by_config' },
    }))));

    await expect(fetchAttachmentCapabilities()).resolves.toEqual({
      visionEnabled: false,
      visionReason: 'disabled_by_config',
    });
  });

  it('deletes an unsent ready attachment without exposing it elsewhere', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await deleteAttachment('opaque/a');
    expect(fetchMock).toHaveBeenCalledWith('/attachments/opaque%2Fa', { method: 'DELETE' });
  });

  it('keeps public /app request URLs root-relative when no API base is injected', async () => {
    document.head.innerHTML = '<meta name="fae-api-base" content="">';
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attachments: { vision_enabled: true },
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        partner_login_available: true,
      })));
    vi.stubGlobal('fetch', fetchMock);

    await fetchAttachmentCapabilities();
    await fetchIdentityCapabilities();

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/health',
      '/identity/capabilities',
    ]);
  });

  it('treats done as terminal even if a later read would fail', async () => {
    const reader = {
      read: vi.fn()
        .mockResolvedValueOnce({
          value: new TextEncoder().encode(
            'event: text_delta\ndata: {"delta":"answer"}\n\n'
            + 'event: done\ndata: {"outcome":"resolved"}\n\n',
          ),
          done: false,
        })
        .mockRejectedValueOnce(new Error('late socket error')),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      body: { getReader: () => reader },
    }));

    const turn = await streamChat({
      message: 'Q',
      clientRequestId: 'web-5',
      onEvent: vi.fn(),
    });

    expect(turn.content).toBe('answer');
    expect(turn.done?.outcome).toBe('resolved');
    expect(reader.read).toHaveBeenCalledTimes(1);
    expect(reader.cancel).toHaveBeenCalledOnce();
  });
});

describe('authenticated identity and conversation helpers', () => {
  afterEach(() => {
    document.head.innerHTML = '';
    vi.unstubAllGlobals();
  });

  it('reads the partner login capability as an explicit boolean', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      partner_login_available: true,
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchIdentityCapabilities()).resolves.toEqual({
      partnerLoginAvailable: true,
    });
    expect(fetchMock).toHaveBeenCalledWith('/identity/capabilities');
  });

  it.each([
    ['a 404 route', new Response(null, { status: 404 })],
    ['an unrelated payload', new Response(JSON.stringify({ ok: 1 }), { status: 200 })],
    ['a non-boolean flag', new Response(JSON.stringify({
      partner_login_available: 'true',
    }), { status: 200 })],
  ])('reports no partner login for %s', async (_label, response) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    await expect(fetchIdentityCapabilities()).resolves.toEqual({
      partnerLoginAvailable: false,
    });
  });

  it('reports no partner login when the capability read fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));

    await expect(fetchIdentityCapabilities()).resolves.toEqual({
      partnerLoginAvailable: false,
    });
  });

  it('requests owned conversations with the fixed page size', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [{
        session_id: 'session-1',
        title: 'Gemini 335L 配置',
        channel: 'fae',
        created_at: '2026-08-30T02:00:00+00:00',
        last_active_at: '2026-08-30T02:30:00+00:00',
      }],
      next_cursor: 'next-1',
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const page = await fetchAuthenticatedConversations();

    expect(fetchMock.mock.calls[0][0]).toBe('/authenticated/conversations?limit=30');
    expect(page.items[0].session_id).toBe('session-1');
    expect(page.next_cursor).toBe('next-1');
  });

  it('uses the internal FAE API base for owned conversation reads', async () => {
    document.head.innerHTML = '<meta name="fae-api-base" content="/daq/api">';
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [],
        next_cursor: null,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        session_id: 'session-1',
        channel: 'fae',
        messages: [],
        current_schema: null,
        attachments: [],
      }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchAuthenticatedConversations();
    await fetchAuthenticatedConversation('session-1');

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      '/daq/api/authenticated/conversations?limit=30',
      '/daq/api/authenticated/conversations/session-1',
    ]);
  });

  it('passes an opaque cursor through without reinterpreting it', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [],
      next_cursor: null,
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchAuthenticatedConversations('cur sor/1+2');

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/authenticated/conversations?cursor=cur+sor%2F1%2B2&limit=30',
    );
  });

  it('fails loudly when the conversation page shape is wrong', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: 'nope',
    }), { status: 200 })));

    await expect(fetchAuthenticatedConversations()).rejects.toThrow('历史会话');
  });

  it('rejects malformed conversation summaries and cursors', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ title: '缺少 session id' }],
        next_cursor: null,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [],
        next_cursor: 42,
      }), { status: 200 })));

    await expect(fetchAuthenticatedConversations()).rejects.toThrow('历史会话响应格式无效');
    await expect(fetchAuthenticatedConversations()).rejects.toThrow('历史会话响应格式无效');
  });

  it('encodes the session id when loading one owned conversation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      session_id: 'sess/1',
      channel: 'fae',
      messages: [{ role: 'user', content: '问题' }],
      current_schema: null,
      attachments: [],
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const detail = await fetchAuthenticatedConversation('sess/1') as {
      messages: Array<{ role: string; content: string }>;
    };

    expect(fetchMock.mock.calls[0][0]).toBe('/authenticated/conversations/sess%2F1');
    expect(detail.messages).toEqual([{ role: 'user', content: '问题' }]);
  });

  it('surfaces an owner-scoped 404 as an explicit failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 404 })));

    await expect(fetchAuthenticatedConversation('session-1')).rejects.toThrow('404');
  });
});
