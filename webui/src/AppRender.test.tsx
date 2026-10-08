// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import {
  bootstrapEnterpriseIdentity,
  resetEnterpriseIdentityForTests,
} from './enterpriseIdentity';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

function renderInteractiveApp() {
  window.history.replaceState(null, '', '/app/');
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => root.render(<App initialPath="/app/" />));
  return { container, root };
}

function renderInteractiveAppAt(path: string) {
  window.history.replaceState(null, '', path);
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => root.render(<App initialPath={path} />));
  return { container, root };
}

function appFetch(input: string | URL | Request, init?: RequestInit): Promise<Response> {
  const path = String(input);
  if (path === '/health') {
    return Promise.resolve(new Response(JSON.stringify({
      attachments: { vision_enabled: true },
    })));
  }
  if (path === '/identity/capabilities') {
    return Promise.resolve(new Response(JSON.stringify({
      partner_login_available: false,
    })));
  }
  if (path === '/attachments' && init?.method === 'POST') {
    const files = Array.from((init.body as FormData).getAll('files')) as File[];
    return Promise.resolve(new Response(JSON.stringify({
      results: files.map((file, index) => ({
        ok: true,
        attachment: {
          attachment_id: `opaque-${index}`,
          source_id: `source-${index}`,
          display_name: file.name,
          kind: file.type.startsWith('image/') ? 'image' : 'text',
          status: 'ready',
          parse_coverage: 'full',
          warnings: [],
        },
      })),
    }), { status: 201 }));
  }
  if (path === '/chat' && init?.method === 'POST') {
    return Promise.resolve(new Response(
      'event: text_delta\ndata: {"delta":"完成"}\n\n'
      + 'event: done\ndata: {"outcome":"resolved"}\n\n',
    ));
  }
  if (path.startsWith('/attachments/') && init?.method === 'DELETE') {
    return Promise.resolve(new Response(null, { status: 204 }));
  }
  return Promise.reject(new Error(`Unexpected fetch: ${path}`));
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function chooseFiles(container: HTMLElement, files: File[]) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(input, 'files', { configurable: true, value: files });
  await act(async () => {
    input.dispatchEvent(new Event('change', { bubbles: true }));
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function transferFileOnNestedComposer(
  container: HTMLElement,
  type: 'paste' | 'drop',
  file: File,
) {
  const target = container.querySelector('.attachment-composer') as HTMLDivElement;
  const event = new Event(type, { bubbles: true, cancelable: true });
  if (type === 'paste') {
    Object.defineProperty(event, 'clipboardData', {
      value: {
        items: [{ kind: 'file', getAsFile: () => file }],
        files: [file],
      },
    });
  } else {
    Object.defineProperty(event, 'dataTransfer', {
      value: { types: ['Files'], files: [file] },
    });
  }
  await act(async () => {
    target.dispatchEvent(event);
    await Promise.resolve();
    await Promise.resolve();
  });
  return event;
}

function attachmentUploadCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input, init]) => (
    String(input) === '/attachments' && (init as RequestInit | undefined)?.method === 'POST'
  ));
}

function enterComposerText(textarea: HTMLTextAreaElement, value: string) {
  const valueSetter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    'value',
  )?.set;
  valueSetter?.call(textarea, value);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
}

async function sendQuestion(container: HTMLElement, question: string) {
  const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
  await act(async () => {
    enterComposerText(textarea, question);
    await Promise.resolve();
  });
  const send = Array.from(container.querySelectorAll('button'))
    .find((button) => button.textContent?.includes('Send')) as HTMLButtonElement;
  await act(async () => {
    send.click();
    await Promise.resolve();
    await Promise.resolve();
  });
  await flushEffects();
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  resetEnterpriseIdentityForTests();
  document.head.innerHTML = '';
  window.history.replaceState(null, '', '/app/');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('App initial render', () => {
  it('uses the same FAE Agent workspace at the public and internal roots', () => {
    const publicHtml = renderToStaticMarkup(<App initialPath="/app/" />);
    const internalHtml = renderToStaticMarkup(<App initialPath="/daq/" />);

    for (const html of [publicHtml, internalHtml]) {
      expect(html).toContain('AI DAQ FAE Agent');
      expect(html).toContain('AI DAQ FAE 技术咨询');
      expect(html).toContain('输入采集设备、组合连接、Viewer/SDK、录制流程或排障问题。');
    }
  });

  it('renders only the session rail and chat workspace in the default UI', () => {
    const html = renderToStaticMarkup(<App />);

    expect(html).toContain('conversation-rail');
    expect(html).toContain('chat-workspace');
    expect(html).not.toContain('class="inspector"');
    expect(html).not.toContain('Sources');
    expect(html).not.toContain('Useful');
    expect(html).not.toContain('Issue');
    expect(html).not.toContain('Eval JSON');
    expect(html).not.toContain('Thinking');
    expect(html).not.toContain('Trace');
    expect(html).not.toContain('Debug Trace');
    expect(html).toContain('添加附件');
    expect(html).toContain('图片理解未启用');
    expect(html).toContain('type="file"');
    expect(html).toContain('data-attachment-input-surface="true"');
  });

  it('uses compact non-wrapping brand treatment in the left rail', () => {
    const html = renderToStaticMarkup(<App />);

    expect(html).toContain('class="brand-name"');
    expect(html).toContain('class="brand-eyebrow"');
    expect(html).toContain('brand-reset');
  });

  it('keeps Review out of the default chat workspace', () => {
    const html = renderToStaticMarkup(<App />);

    expect(html).toContain('conversation-rail');
    expect(html).toContain('chat-workspace');
    expect(html).not.toContain('workspace-switch');
    expect(html).not.toContain('review-workspace');
    expect(html).not.toContain('QA 复审');
  });

  it('scrolls the matching answer into view when a rail question is selected', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    const scrollIntoView = vi.fn();
    const original = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'scrollIntoView',
    );
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    try {
      const { container } = renderInteractiveApp();
      await flushEffects();
      await sendQuestion(container, '第一个问题');
      await sendQuestion(container, '第二个问题');

      const railItems = container.querySelectorAll<HTMLButtonElement>('.turn-item');
      const assistantMessages = container.querySelectorAll<HTMLElement>('.message.assistant');
      expect(railItems).toHaveLength(2);
      expect(assistantMessages).toHaveLength(2);

      act(() => railItems[0].click());

      expect(assistantMessages[0].dataset.messageId).toBeTruthy();
      expect(scrollIntoView).toHaveBeenCalledWith({
        behavior: 'smooth',
        block: 'start',
      });
      expect(scrollIntoView.mock.instances[0]).toBe(assistantMessages[0]);
    } finally {
      if (original) {
        Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', original);
      } else {
        delete (HTMLElement.prototype as { scrollIntoView?: unknown }).scrollIntoView;
      }
    }
  });

  it('renders Review Center as an independent admin page under /app/review', () => {
    const html = renderToStaticMarkup(<App initialPath="/app/review" />);

    expect(html).toContain('review-page-shell');
    expect(html).toContain('review-workspace');
    expect(html).toContain('Session 回放');
    expect(html).toContain('QA 复审');
    expect(html).not.toContain('conversation-rail');
    expect(html).not.toContain('chat-workspace');
  });

  it('creates preview URLs only for images and revokes an unsent image on removal', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:viewer');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const { container } = renderInteractiveApp();
    await flushEffects();
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const text = new File(['text'], 'notes.txt', { type: 'text/plain' });

    await chooseFiles(container, [image, text]);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledWith(image);
    expect(container.querySelector('img[src="blob:viewer"]')).not.toBeNull();

    const remove = container.querySelector(
      'button[aria-label="移除 viewer.png"]',
    ) as HTMLButtonElement;
    act(() => remove.click());
    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:viewer');
  });

  it('keeps a picker file alive and reselects it once after a failed upload settles', async () => {
    const uploads = [deferred<Response>(), deferred<Response>()];
    let uploadIndex = 0;
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/attachments' && init?.method === 'POST') {
        return uploads[uploadIndex++].promise;
      }
      return appFetch(input, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:photo');
    const { container } = renderInteractiveApp();
    await flushEffects();
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const photo = new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });
    Object.defineProperty(input, 'files', { configurable: true, value: [photo] });
    Object.defineProperty(input, 'value', {
      configurable: true,
      writable: true,
      value: 'C:\\fakepath\\photo.jpg',
    });

    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(input.value).toBe('C:\\fakepath\\photo.jpg');
    expect(attachmentUploadCalls(fetchMock)).toHaveLength(1);

    await act(async () => {
      uploads[0].resolve(new Response(JSON.stringify({
        results: [{
          index: 0,
          ok: false,
          error: {
            code: 'unsupported_attachment_type',
            message: '不支持该附件格式',
          },
        }],
      }), { status: 207 }));
      await uploads[0].promise;
      await Promise.resolve();
    });

    expect(input.value).toBe('');
    expect(container.querySelector('.attachment-card.failed')).not.toBeNull();

    Object.defineProperty(input, 'value', {
      configurable: true,
      writable: true,
      value: 'C:\\fakepath\\photo.jpg',
    });
    await act(async () => {
      input.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(input.value).toBe('C:\\fakepath\\photo.jpg');
    expect(attachmentUploadCalls(fetchMock)).toHaveLength(2);

    await act(async () => {
      uploads[1].resolve(new Response(JSON.stringify({
        results: [{
          index: 0,
          ok: true,
          attachment: {
            attachment_id: 'opaque-0',
            source_id: 'source-0',
            display_name: 'photo.jpg',
            kind: 'image',
            status: 'ready',
            parse_coverage: 'full',
            warnings: [],
          },
        }],
      }), { status: 201 }));
      await uploads[1].promise;
      await Promise.resolve();
    });

    expect(input.value).toBe('');
    expect(attachmentUploadCalls(fetchMock)).toHaveLength(2);
  });

  it('rolls back earlier preview URLs when a later allocation in the batch fails', async () => {
    const fetchMock = vi.fn(appFetch);
    vi.stubGlobal('fetch', fetchMock);
    const createObjectURL = vi.spyOn(URL, 'createObjectURL')
      .mockReturnValueOnce('blob:viewer-a')
      .mockImplementationOnce(() => {
        throw new Error('preview allocation failed');
      });
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const { container } = renderInteractiveApp();
    await flushEffects();

    await chooseFiles(container, [
      new File(['a'], 'viewer-a.png', { type: 'image/png' }),
      new File(['b'], 'viewer-b.png', { type: 'image/png' }),
    ]);

    expect(createObjectURL).toHaveBeenCalledTimes(2);
    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:viewer-a');
    expect(container.querySelectorAll('.attachment-card')).toHaveLength(0);
    expect(container.querySelector('.error-line')?.textContent).toContain(
      'preview allocation failed',
    );
    expect(attachmentUploadCalls(fetchMock)).toHaveLength(0);
  });

  it.each(['paste', 'drop'] as const)(
    'delivers a nested composer %s file through the upload path exactly once',
    async (type) => {
      const fetchMock = vi.fn(appFetch);
      vi.stubGlobal('fetch', fetchMock);
      vi.spyOn(URL, 'createObjectURL').mockReturnValue(`blob:nested-${type}`);
      const { container } = renderInteractiveApp();
      await flushEffects();
      const image = new File(['image'], `${type}.png`, { type: 'image/png' });

      const event = await transferFileOnNestedComposer(container, type, image);

      expect(event.defaultPrevented).toBe(true);
      expect(attachmentUploadCalls(fetchMock)).toHaveLength(1);
      expect(container.querySelectorAll('.attachment-card')).toHaveLength(1);
    },
  );

  it('keeps aggregate file-count validation for nested composer transfers', async () => {
    const fetchMock = vi.fn(appFetch);
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveApp();
    await flushEffects();
    await chooseFiles(container, Array.from({ length: 5 }, (_, index) => (
      new File([String(index)], `notes-${index}.txt`, { type: 'text/plain' })
    )));

    await transferFileOnNestedComposer(
      container,
      'drop',
      new File(['six'], 'notes-5.txt', { type: 'text/plain' }),
    );

    expect(attachmentUploadCalls(fetchMock)).toHaveLength(1);
    expect(container.querySelectorAll('.attachment-card')).toHaveLength(5);
    expect(container.querySelector('.error-line')?.textContent).toContain('每轮最多 5 个附件');
  });

  it('preserves sent preview URLs until a new session revokes all of them', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    const createObjectURL = vi.spyOn(URL, 'createObjectURL')
      .mockReturnValueOnce('blob:viewer-a')
      .mockReturnValueOnce('blob:viewer-b');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const { container } = renderInteractiveApp();
    await flushEffects();
    const images = [
      new File(['a'], 'viewer-a.png', { type: 'image/png' }),
      new File(['b'], 'viewer-b.png', { type: 'image/png' }),
    ];

    await chooseFiles(container, images);
    expect(container.querySelectorAll('.attachment-card.ready')).toHaveLength(2);
    const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
    await act(async () => {
      enterComposerText(textarea, '读截图');
      await Promise.resolve();
    });
    expect(textarea.value).toBe('读截图');
    const send = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Send')) as HTMLButtonElement;
    expect(send.disabled).toBe(false);
    await act(async () => {
      send.click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createObjectURL).toHaveBeenCalledTimes(2);
    expect(revokeObjectURL).not.toHaveBeenCalled();
    expect(container.querySelector('button[aria-label="查看 viewer-a.png 大图"]')).not.toBeNull();
    expect(container.querySelector('button[aria-label="查看 viewer-b.png 大图"]')).not.toBeNull();

    const reset = container.querySelector('button[aria-label="新会话"]') as HTMLButtonElement;
    act(() => reset.click());
    expect(revokeObjectURL).toHaveBeenCalledTimes(2);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:viewer-a');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:viewer-b');
  });

  it('revokes tracked image preview URLs when the workspace unmounts', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:viewer');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const { container, root } = renderInteractiveApp();
    await flushEffects();
    await chooseFiles(container, [
      new File(['image'], 'viewer.png', { type: 'image/png' }),
    ]);

    act(() => root.unmount());
    mountedRoots.splice(mountedRoots.findIndex((item) => item.root === root), 1);

    expect(revokeObjectURL).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:viewer');
  });
});

const PARTNER_SESSION = {
  authenticated: true,
  authentication_mode: 'platform_partner',
  display_name: '坐席一',
  partner_display_name: '合作方甲',
  csrf_token: 'partner-csrf',
};

async function bootstrapIdentity(payload: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify(payload), { status: 200 }),
  ));
  await bootstrapEnterpriseIdentity();
  vi.unstubAllGlobals();
}

function historyPageResponse() {
  return new Response(JSON.stringify({
    items: [{
      session_id: 'session-1',
      title: 'Gemini 335L 配置',
      channel: 'fae',
      created_at: '2026-08-30T02:00:00+00:00',
      last_active_at: '2026-08-30T02:30:00+00:00',
    }],
    next_cursor: null,
  }), { status: 200 });
}

function historyDetailResponse() {
  return new Response(JSON.stringify({
    session_id: 'session-1',
    channel: 'fae',
    messages: [
      { role: 'user', content: '同步模式怎么配？' },
      { role: 'assistant', content: '结论：使用硬件同步线。' },
    ],
    current_schema: null,
    attachments: [],
  }), { status: 200 });
}

function authenticatedFetch(
  input: string | URL | Request,
  init?: RequestInit,
): Promise<Response> {
  const path = String(input);
  if (path === '/authenticated/conversations?limit=30') {
    return Promise.resolve(historyPageResponse());
  }
  if (path === '/authenticated/conversations/session-1') {
    return Promise.resolve(historyDetailResponse());
  }
  return appFetch(input, init);
}

function chatCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input, init]) => (
    String(input) === '/chat' && (init as RequestInit | undefined)?.method === 'POST'
  ));
}

function conversationDetailCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([input]) => (
    String(input) === '/authenticated/conversations/session-1'
  ));
}

function clickByText(container: HTMLElement, text: string) {
  const element = Array.from(container.querySelectorAll<HTMLElement>('button, a'))
    .find((candidate) => candidate.textContent?.includes(text));
  if (!element) throw new Error(`missing clickable element: ${text}`);
  return element;
}

describe('App partner-aware identity surface', () => {
  it('renders an explicit not-found page for unknown FAE browser routes', () => {
    const html = renderToStaticMarkup(<App initialPath="/app/unknown-route" />);

    expect(html).toContain('FAE 页面不存在');
    expect(html).not.toContain('chat-workspace');
    expect(html).not.toContain('review-workspace');
  });

  it('restores a deep-linked owned conversation without rendering review', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/authenticated/conversations/session-1') {
        return Promise.resolve(historyDetailResponse());
      }
      return authenticatedFetch(input, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveAppAt('/app/conversations/session-1');
    await flushEffects();

    expect(fetchMock.mock.calls.map(([input]) => String(input)))
      .toContain('/authenticated/conversations/session-1');
    expect(container.textContent).toContain('同步模式怎么配？');
    expect(container.textContent).not.toContain('QA 复审');
  });

  it('does not let a stale deep-link restore win after popstate returns to a new chat', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const pendingDetail = deferred<Response>();
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/authenticated/conversations/session-1') {
        return pendingDetail.promise;
      }
      return authenticatedFetch(input, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveAppAt('/app/conversations/session-1');
    await flushEffects();

    window.history.pushState(null, '', '/app/');
    await act(async () => {
      window.dispatchEvent(new PopStateEvent('popstate'));
      await Promise.resolve();
    });
    pendingDetail.resolve(historyDetailResponse());
    await flushEffects();

    expect(container.querySelectorAll('.message')).toHaveLength(0);
    expect(container.textContent).toContain('AI DAQ FAE 技术咨询');
    expect(container.textContent).not.toContain('同步模式怎么配？');
  });

  it('replaces the browser URL with the surface root when starting a new chat', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const fetchMock = vi.fn(authenticatedFetch);
    vi.stubGlobal('fetch', fetchMock);
    const replaceState = vi.spyOn(window.history, 'replaceState');
    const { container } = renderInteractiveAppAt('/app/conversations/session-1');
    await flushEffects();

    const reset = container.querySelector('button[aria-label="新会话"]') as HTMLButtonElement;
    act(() => reset.click());

    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/app/');
  });

  it('restores chat state from popstate without reloading the document', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const fetchMock = vi.fn(authenticatedFetch);
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveAppAt('/app/');
    await flushEffects();

    window.history.pushState(null, '', '/app/conversations/session-1');
    await act(async () => {
      window.dispatchEvent(new PopStateEvent('popstate'));
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    expect(container.textContent).toContain('同步模式怎么配？');
    expect(conversationDetailCalls(fetchMock)).toHaveLength(1);
  });

  it('switches to the review workspace after same-document navigation', async () => {
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => {
      const path = String(input);
      if (path === '/review/metrics') {
        return Promise.resolve(new Response(JSON.stringify({ bad_feedback_pending: 0 })));
      }
      if (path.startsWith('/review/feedback')) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 })));
      }
      if (path.startsWith('/review/sessions')) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 })));
      }
      if (path.startsWith('/review/qa-items')) {
        return Promise.resolve(new Response(JSON.stringify({ items: [], total: 0 })));
      }
      return appFetch(input);
    }));
    const { container } = renderInteractiveAppAt('/app/');
    await flushEffects();

    window.history.pushState(null, '', '/app/review');
    await act(async () => {
      window.dispatchEvent(new PopStateEvent('popstate'));
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    expect(container.querySelector('.review-workspace')).not.toBeNull();
    expect(container.querySelector('.chat-workspace')).toBeNull();
  });

  it('switches to not-found after same-document navigation to an unknown route', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    const { container } = renderInteractiveAppAt('/app/');
    await flushEffects();

    window.history.pushState(null, '', '/app/not-owned');
    await act(async () => {
      window.dispatchEvent(new PopStateEvent('popstate'));
      await Promise.resolve();
    });

    expect(container.textContent).toContain('FAE 页面不存在');
    expect(container.querySelector('.chat-workspace')).toBeNull();
  });

  it('renders no partner login control when the capability is false', async () => {
    vi.stubGlobal('fetch', vi.fn(appFetch));
    const { container } = renderInteractiveApp();
    await flushEffects();

    expect(container.querySelector('a[href="/partner/login"]')).toBeNull();
    expect(container.textContent).not.toContain('合作方客服登录');
    expect(container.textContent).not.toContain('准备中');
    expect(container.textContent).not.toContain('即将上线');
  });

  it('renders the partner login control as a same-origin FAE route when available', async () => {
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/identity/capabilities') {
        return Promise.resolve(new Response(JSON.stringify({
          partner_login_available: true,
        })));
      }
      return appFetch(input, init);
    }));
    const { container } = renderInteractiveApp();
    await flushEffects();

    const link = container.querySelector('a[href="/partner/login"]');
    expect(link).not.toBeNull();
    expect(link?.textContent).toContain('合作方客服登录');
    expect(link?.getAttribute('target')).toBeNull();
  });

  it('renders no partner login control when the capability read fails', async () => {
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/identity/capabilities') {
        return Promise.reject(new Error('capability read failed'));
      }
      return appFetch(input, init);
    }));
    const { container } = renderInteractiveApp();
    await flushEffects();

    expect(container.querySelector('a[href="/partner/login"]')).toBeNull();
    expect(container.textContent).not.toContain('合作方客服登录');
  });

  it('keeps the public anonymous workspace free of account and history UI', async () => {
    const fetchMock = vi.fn(appFetch);
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveApp();
    await flushEffects();

    expect(container.querySelector('.account-block')).toBeNull();
    expect(container.querySelector('.authenticated-history')).toBeNull();
    expect(fetchMock.mock.calls.map(([input]) => String(input)))
      .not.toContain('/authenticated/conversations?limit=30');
    expect(container.querySelector('.composer')).not.toBeNull();
  });

  it('renders only the safe account projection for a partner session', async () => {
    await bootstrapIdentity({
      ...PARTNER_SESSION,
      subject_id: '4fd1686f-3e89-4e32-9fe9-932c41e4274c',
    });
    vi.stubGlobal('fetch', vi.fn(authenticatedFetch));
    const { container } = renderInteractiveApp();
    await flushEffects();

    const account = container.querySelector('.account-block');
    expect(account?.textContent).toContain('坐席一');
    expect(account?.textContent).toContain('合作方甲');
    expect(container.querySelector('.authenticated-history')).not.toBeNull();
    expect(container.querySelector('a[href="/partner/login"]')).toBeNull();
    const markup = container.innerHTML;
    expect(markup).not.toContain('4fd1686f');
    expect(markup).not.toContain('partner-csrf');
    expect(markup).not.toContain('platform_partner');
  });

  it('opens an owned conversation in place and continues that session', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const fetchMock = vi.fn(authenticatedFetch);
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveApp();
    await flushEffects();

    await act(async () => {
      clickByText(container, 'Gemini 335L 配置').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    expect(container.textContent).toContain('同步模式怎么配？');
    expect(container.textContent).toContain('结论：使用硬件同步线。');
    expect(container.textContent).not.toContain('AI DAQ FAE Thinking');
    expect(container.textContent).not.toContain('风险状态');
    expect(container.querySelectorAll('.message')).toHaveLength(2);
    expect(container.querySelector('.session-meta code')?.textContent).toBe('session-1');

    await sendQuestion(container, '再确认一次');

    const lastChat = chatCalls(fetchMock).at(-1);
    expect(JSON.parse(String((lastChat?.[1] as RequestInit).body)).session_id)
      .toBe('session-1');
    expect(container.querySelectorAll('.message')).toHaveLength(4);
  });

  it('stops an in-flight answer before replacing the workspace with history', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const pending = deferred<Response>();
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input) === '/chat' && init?.method === 'POST') return pending.promise;
      return authenticatedFetch(input, init);
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveApp();
    await flushEffects();
    const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
    await act(async () => {
      enterComposerText(textarea, '进行中的问题');
      await Promise.resolve();
    });
    await act(async () => {
      clickByText(container, 'Send').click();
      await Promise.resolve();
    });
    expect(container.textContent).toContain('Stop');

    await act(async () => {
      clickByText(container, 'Gemini 335L 配置').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();

    expect(container.textContent).not.toContain('Stop');
    expect(container.textContent).toContain('结论：使用硬件同步线。');
    expect(container.textContent).not.toContain('进行中的问题');
  });

  it('clears only client state when a new conversation follows a restore', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    const fetchMock = vi.fn(authenticatedFetch);
    vi.stubGlobal('fetch', fetchMock);
    const { container } = renderInteractiveApp();
    await flushEffects();
    await act(async () => {
      clickByText(container, 'Gemini 335L 配置').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await flushEffects();
    const callsBeforeReset = fetchMock.mock.calls.length;

    const reset = container.querySelector('button[aria-label="新会话"]') as HTMLButtonElement;
    act(() => reset.click());

    expect(container.querySelectorAll('.message')).toHaveLength(0);
    expect(container.querySelector('.session-meta code')?.textContent).toBe('new');
    // No archive, delete or attachment cleanup request may follow a restore.
    expect(fetchMock.mock.calls.length).toBe(callsBeforeReset);
    expect(container.textContent).toContain('Gemini 335L 配置');

    await sendQuestion(container, '新的问题');

    const lastChat = chatCalls(fetchMock).at(-1);
    expect(JSON.parse(String((lastChat?.[1] as RequestInit).body)).session_id)
      .toBeUndefined();
  });

  it('surfaces a history list failure without hiding the chat workspace', async () => {
    await bootstrapIdentity(PARTNER_SESSION);
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      if (String(input).startsWith('/authenticated/conversations')) {
        return Promise.resolve(new Response(null, { status: 503 }));
      }
      return appFetch(input, init);
    }));
    const { container } = renderInteractiveApp();
    await flushEffects();

    expect(container.textContent).toContain('历史会话加载失败');
    expect(container.querySelector('.composer')).not.toBeNull();
    expect(container.querySelector('.account-block')).not.toBeNull();
  });
});
