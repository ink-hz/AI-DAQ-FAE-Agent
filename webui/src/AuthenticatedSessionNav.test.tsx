// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthenticatedSessionNav } from './AuthenticatedSessionNav';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

function firstPage(nextCursor: string | null = 'next-1') {
  return new Response(JSON.stringify({
    items: [
      {
        session_id: 'session-1',
        title: 'Gemini 335L 配置',
        channel: 'fae',
        created_at: '2026-08-30T02:00:00+00:00',
        last_active_at: '2026-08-30T02:30:00+00:00',
      },
      {
        session_id: 'session-2',
        title: '',
        channel: 'fae',
        created_at: '2026-08-29T02:00:00+00:00',
        last_active_at: '2026-08-29T03:00:00+00:00',
      },
    ],
    next_cursor: nextCursor,
  }), { status: 200 });
}

function secondPage() {
  return new Response(JSON.stringify({
    items: [{
      session_id: 'session-3',
      title: 'Femto Mega 部署',
      channel: 'fae',
      created_at: '2026-08-28T02:00:00+00:00',
      last_active_at: '2026-08-28T02:10:00+00:00',
    }],
    next_cursor: null,
  }), { status: 200 });
}

function conversationDetail() {
  return new Response(JSON.stringify({
    session_id: 'session-1',
    channel: 'fae',
    messages: [
      { role: 'user', content: '同步模式？' },
      { role: 'assistant', content: '结论：支持硬件同步。' },
    ],
    current_schema: null,
    attachments: [],
  }), { status: 200 });
}

function renderNav(onOpen = vi.fn()) {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => root.render(<AuthenticatedSessionNav onOpen={onOpen} />));
  return { container, onOpen };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function byText(container: HTMLElement, text: string): HTMLElement {
  const found = Array.from(container.querySelectorAll<HTMLElement>('button, a, p, span'))
    .find((element) => element.textContent?.trim() === text);
  if (!found) throw new Error(`missing element with text: ${text}`);
  return found;
}

async function clickText(container: HTMLElement, text: string) {
  const element = byText(container, text);
  const target = element.closest('button') ?? element;
  await act(async () => {
    target.click();
    await Promise.resolve();
    await Promise.resolve();
  });
  await flush();
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  document.head.innerHTML = '';
  window.history.replaceState(null, '', '/app/');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('AuthenticatedSessionNav', () => {
  it('pushes a canonical deep conversation URL after opening a history item', async () => {
    document.head.innerHTML = '<meta name="fae-browser-base" content="/daq">';
    window.history.replaceState(null, '', '/daq/');
    const pushState = vi.spyOn(window.history, 'pushState');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(firstPage(null))
      .mockResolvedValueOnce(conversationDetail());
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderNav();
    await flush();

    await clickText(container, 'Gemini 335L 配置');

    expect(pushState).toHaveBeenCalledWith(null, '', '/daq/conversations/session-1');
  });

  it('paginates owned conversations and restores one in place', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(firstPage())
      .mockResolvedValueOnce(secondPage())
      .mockResolvedValueOnce(conversationDetail());
    vi.stubGlobal('fetch', fetchMock);
    const { container, onOpen } = renderNav();
    await flush();

    expect(fetchMock.mock.calls[0][0]).toBe('/authenticated/conversations?limit=30');
    expect(container.textContent).toContain('Gemini 335L 配置');
    expect(container.textContent).toContain('未命名会话');

    await clickText(container, '加载更多');

    expect(fetchMock.mock.calls[1][0]).toBe(
      '/authenticated/conversations?cursor=next-1&limit=30',
    );
    expect(container.textContent).toContain('Femto Mega 部署');
    expect(container.textContent).not.toContain('加载更多');

    await clickText(container, 'Gemini 335L 配置');

    expect(fetchMock.mock.calls[2][0]).toBe('/authenticated/conversations/session-1');
    expect(onOpen).toHaveBeenCalledTimes(1);
    const opened = onOpen.mock.calls[0][0];
    expect(opened).toEqual(expect.objectContaining({
      sessionId: 'session-1',
      channel: 'fae',
    }));
    expect(opened.messages).toHaveLength(2);
    expect(opened.messages[1].content).toBe('结论：支持硬件同步。');
  });

  it('keeps the pagination cursor out of the rendered DOM', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(firstPage()));
    const { container } = renderNav();
    await flush();

    expect(container.innerHTML).not.toContain('next-1');
  });

  it('reports an explicit list failure and retries on demand', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(firstPage(null));
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderNav();
    await flush();

    expect(container.textContent).toContain('历史会话加载失败');
    expect(container.textContent).not.toContain('Gemini');

    await clickText(container, '重试');

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('Gemini 335L 配置');
    expect(container.textContent).not.toContain('历史会话加载失败');
  });

  it('reports an explicit open failure without replacing the workspace', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(firstPage(null))
      .mockResolvedValueOnce(new Response(null, { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);
    const { container, onOpen } = renderNav();
    await flush();

    await clickText(container, 'Gemini 335L 配置');

    expect(onOpen).not.toHaveBeenCalled();
    expect(container.textContent).toContain('会话打开失败');
  });

  it('renders an empty state with no load-more control', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      items: [],
      next_cursor: null,
    }), { status: 200 })));
    const { container } = renderNav();
    await flush();

    expect(container.textContent).toContain('暂无历史会话');
    expect(container.textContent).not.toContain('加载更多');
  });

  it('marks the list busy while a page is in flight', async () => {
    let release: (value: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    vi.stubGlobal('fetch', vi.fn().mockReturnValue(pending));
    const { container } = renderNav();
    await flush();

    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();

    await act(async () => {
      release(firstPage(null));
      await pending;
      await Promise.resolve();
    });
    await flush();

    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(container.textContent).toContain('Gemini 335L 配置');
  });
});
