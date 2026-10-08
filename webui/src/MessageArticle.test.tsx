// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MessageArticle } from './MessageArticle';
import type { ChatMessage } from './types';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Array<{
  container: HTMLDivElement;
  root: ReturnType<typeof createRoot>;
}> = [];

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

const previewMessage: ChatMessage = {
  id: 'u-preview',
  role: 'user',
  content: '读截图',
  createdAt: 1,
  attachments: [{
    sourceId: 'att-src-preview',
    displayName: 'viewer.png',
    kind: 'image',
    sizeBytes: 2048,
    previewUrl: 'blob:viewer',
    statusAtSend: 'ready',
  }],
};

function renderPreviewMessage() {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => {
    root.render(<MessageArticle message={previewMessage} onSelect={() => undefined} />);
  });
  const thumbnail = container.querySelector(
    'button[aria-label="查看 viewer.png 大图"]',
  ) as HTMLButtonElement;
  return { container, thumbnail };
}

function renderMessage(message: ChatMessage, onSelect = vi.fn()) {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => {
    root.render(<MessageArticle message={message} onSelect={onSelect} />);
  });
  return { container, root, onSelect };
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  vi.useRealTimers();
  vi.unstubAllGlobals();
  if ('execCommand' in document) {
    Reflect.deleteProperty(document, 'execCommand');
  }
});

describe('MessageArticle', () => {
  it('collapses completed assistant Thinking after the answer without exposing trace id', () => {
    const message: ChatMessage = {
      id: 'a1',
      role: 'assistant',
      content: '最终答案',
      createdAt: 1,
      stages: [
        { agent: 'Planner', message: '分析规格问题', elapsed_ms: 12 },
      ],
      sources: [{ title: 'Gemini 335L' }],
      done: {
        trace_id: 'trace-secret-123',
        text_len: 4,
        fallback_used: false,
      },
    };

    const html = renderToStaticMarkup(
      <MessageArticle message={message} onSelect={() => undefined} />,
    );

    expect(html).toContain('Thinking');
    expect(html).toContain('AI DAQ FAE Thinking');
    expect(html).toContain('Planner');
    expect(html).toContain('分析规格问题');
    expect(html).toContain('最终答案');
    expect(html.indexOf('最终答案')).toBeLessThan(html.indexOf('AI DAQ FAE Thinking'));
    expect(html).toContain('<details');
    expect(html).not.toContain('<details open');
    expect(html).not.toContain('trace-secret-123');
  });

  it('renders lightweight icon feedback controls for completed assistant answers', () => {
    const message: ChatMessage = {
      id: 'a-feedback',
      role: 'assistant',
      content: '最终答案',
      createdAt: 1,
      done: {
        session_id: 'session-1',
        turn_id: 'turn-1',
        trace_id: 'trace-1',
        text_len: 4,
        fallback_used: false,
      },
    };

    const html = renderToStaticMarkup(
      <MessageArticle
        message={message}
        onSelect={() => undefined}
        onFeedback={() => undefined}
      />,
    );

    expect(html).toContain('aria-label="有用"');
    expect(html).toContain('aria-label="不达标"');
    expect(html).toContain('aria-label="复制回答"');
    expect(html).not.toContain('>有用<');
    expect(html).not.toContain('>不达标<');
  });

  it('does not render feedback controls before an assistant answer is complete', () => {
    const message: ChatMessage = {
      id: 'a-streaming',
      role: 'assistant',
      content: '正在生成',
      createdAt: 1,
    };

    const html = renderToStaticMarkup(
      <MessageArticle
        message={message}
        onSelect={() => undefined}
        onFeedback={() => undefined}
      />,
    );

    expect(html).not.toContain('aria-label="有用"');
    expect(html).not.toContain('aria-label="不达标"');
    expect(html).toContain('aria-label="复制回答"');
    expect(html).toContain('disabled=""');
  });

  it('keeps streaming assistant Thinking expanded before the answer', () => {
    const message: ChatMessage = {
      id: 'a2',
      role: 'assistant',
      content: '正在生成的答案',
      createdAt: 1,
      stages: [
        { agent: 'Planner', message: '正在规划', elapsed_ms: 12 },
      ],
    };

    const html = renderToStaticMarkup(
      <MessageArticle message={message} onSelect={() => undefined} />,
    );

    expect(html).toContain('AI DAQ FAE Thinking');
    expect(html.indexOf('AI DAQ FAE Thinking')).toBeLessThan(html.indexOf('正在生成的答案'));
    expect(html).not.toContain('<details');
  });

  it('does not render Thinking for user messages', () => {
    const message: ChatMessage = {
      id: 'u1',
      role: 'user',
      content: 'Gemini 335Lg 工作温度？',
      createdAt: 1,
    };

    const html = renderToStaticMarkup(
      <MessageArticle message={message} onSelect={() => undefined} />,
    );

    expect(html).not.toContain('Thinking');
    expect(html).toContain('Gemini 335Lg 工作温度？');
    expect(html).not.toContain('aria-label="复制回答"');
  });

  it('renders restored answers without invented Thinking or a live placeholder', () => {
    const html = renderToStaticMarkup(
      <MessageArticle
        message={{
          id: 'history-session-1-1',
          role: 'assistant',
          content: '',
          createdAt: 1,
          restored: true,
          sources: [],
          stages: [],
          done: { session_id: 'session-1' },
        }}
        onSelect={() => undefined}
        onFeedback={() => undefined}
      />,
    );

    expect(html).not.toContain('AI DAQ FAE Thinking');
    expect(html).not.toContain('风险状态');
    expect(html).not.toContain('Agent is working');
    expect(html).toContain('未保存回答内容');
  });

  it('copies only the assistant Markdown and does not select the answer card', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const message: ChatMessage = {
      id: 'a-copy',
      role: 'assistant',
      content: '# 结论\n\n```cpp\nrun();\n```',
      createdAt: 1,
      stages: [{ agent: 'Planner', message: '不应复制 Thinking' }],
      sources: [{ title: '不应复制来源' }],
      done: {
        trace_id: 'trace-not-copied',
        text_len: 24,
        fallback_used: false,
      },
    };
    const onSelect = vi.fn();
    const { container } = renderMessage(message, onSelect);
    const copy = container.querySelector(
      'button[aria-label="复制回答"]',
    ) as HTMLButtonElement;

    await act(async () => {
      copy.click();
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledOnce();
    expect(writeText).toHaveBeenCalledWith(message.content);
    expect(onSelect).not.toHaveBeenCalled();
    expect(container.querySelector('[role="status"]')?.textContent).toBe('已复制');
  });

  it('restores the copy action two seconds after success', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    const { container } = renderMessage({
      id: 'a-copy-timer',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: { text_len: 2, fallback_used: false },
    });
    const copy = container.querySelector(
      'button[aria-label="复制回答"]',
    ) as HTMLButtonElement;

    await act(async () => {
      copy.click();
      await Promise.resolve();
    });
    expect(copy.getAttribute('aria-label')).toBe('已复制');

    act(() => vi.advanceTimersByTime(1999));
    expect(copy.getAttribute('aria-label')).toBe('已复制');

    act(() => vi.advanceTimersByTime(1));
    expect(copy.getAttribute('aria-label')).toBe('复制回答');
  });

  it('shows an explicit copy failure and allows a successful retry', async () => {
    const writeText = vi.fn()
      .mockRejectedValueOnce(new Error('permission denied'))
      .mockResolvedValueOnce(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const { container } = renderMessage({
      id: 'a-copy-retry',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: { text_len: 2, fallback_used: false },
    });
    const copy = container.querySelector(
      'button[aria-label="复制回答"]',
    ) as HTMLButtonElement;

    await act(async () => {
      copy.click();
      await Promise.resolve();
    });
    expect(copy.getAttribute('aria-label')).toBe('复制失败');
    expect(container.querySelector('[role="status"]')?.textContent).toBe('复制失败');

    await act(async () => {
      copy.click();
      await Promise.resolve();
    });
    expect(copy.getAttribute('aria-label')).toBe('已复制');
    expect(writeText).toHaveBeenCalledTimes(2);
  });

  it('allows only one copy operation while the clipboard Promise is pending', async () => {
    const pendingCopy = deferred<void>();
    const writeText = vi.fn(() => pendingCopy.promise);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const { container } = renderMessage({
      id: 'a-copy-pending',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: { text_len: 2, fallback_used: false },
    });
    const copy = container.querySelector(
      'button[aria-label="复制回答"]',
    ) as HTMLButtonElement;

    await act(async () => {
      copy.click();
      copy.click();
      await Promise.resolve();
    });

    expect(writeText).toHaveBeenCalledOnce();
    expect(copy.disabled).toBe(true);

    await act(async () => {
      pendingCopy.resolve();
      await pendingCopy.promise;
    });
    expect(copy.getAttribute('aria-label')).toBe('已复制');
  });

  it('does not update state or install a timer when copy settles after unmount', async () => {
    vi.useFakeTimers();
    const pendingCopy = deferred<void>();
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn(() => pendingCopy.promise) },
    });
    const { container, root } = renderMessage({
      id: 'a-copy-unmount',
      role: 'assistant',
      content: '答案',
      createdAt: 1,
      done: { text_len: 2, fallback_used: false },
    });
    const copy = container.querySelector(
      'button[aria-label="复制回答"]',
    ) as HTMLButtonElement;
    act(() => copy.click());

    act(() => root.unmount());
    mountedRoots.splice(mountedRoots.findIndex((item) => item.root === root), 1);

    await act(async () => {
      pendingCopy.resolve();
      await pendingCopy.promise;
    });

    expect(vi.getTimerCount()).toBe(0);
  });

  it('renders frozen safe attachment metadata on the user turn', () => {
    const message: ChatMessage = {
      id: 'u-att',
      role: 'user',
      content: '读截图',
      createdAt: 1,
      attachments: [{
        sourceId: 'att-src-a',
        displayName: 'viewer.png',
        kind: 'image',
        sizeBytes: 2048,
        previewUrl: 'blob:viewer',
        statusAtSend: 'ready',
      }],
    };
    const html = renderToStaticMarkup(
      <MessageArticle message={message} onSelect={() => undefined} />,
    );
    expect(html).toContain('viewer.png');
    expect(html).toContain('attachment-thumbnail');
    expect(html).toContain('2 KiB');
    expect(html).toContain('aria-label="查看 viewer.png 大图"');
    expect(html).toContain('src="blob:viewer"');
    expect(html).not.toContain('opaque-');
  });

  it('renders non-image sent attachments with a kind icon and size', () => {
    const message: ChatMessage = {
      id: 'u-document',
      role: 'user',
      content: '读文档',
      createdAt: 1,
      attachments: [{
        sourceId: 'att-src-doc',
        displayName: 'guide.docx',
        kind: 'document',
        sizeBytes: 2048,
        statusAtSend: 'ready',
      }],
    };

    const html = renderToStaticMarkup(
      <MessageArticle message={message} onSelect={() => undefined} />,
    );

    expect(html).toContain('sent-attachment-visual');
    expect(html).toContain('lucide-file-type');
    expect(html).toContain('guide.docx');
    expect(html).toContain('2 KiB');
    expect(html).not.toContain('attachment-thumbnail');
  });

  it('opens and closes an accessible full preview from a sent image thumbnail', () => {
    const { container, thumbnail } = renderPreviewMessage();

    expect(container.querySelector('[role="dialog"]')).toBeNull();
    act(() => thumbnail.click());

    const dialog = container.querySelector('[role="dialog"]') as HTMLDivElement;
    expect(dialog).not.toBeNull();
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.getAttribute('aria-label')).toBe('viewer.png 大图预览');
    expect(dialog.querySelector('img')?.getAttribute('src')).toBe('blob:viewer');

    const close = dialog.querySelector(
      'button[aria-label="关闭 viewer.png 大图"]',
    ) as HTMLButtonElement;
    act(() => close.click());
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });

  it('opens a native modal and moves focus inside the preview', () => {
    const { container, thumbnail } = renderPreviewMessage();
    thumbnail.focus();

    act(() => thumbnail.click());

    const dialog = container.querySelector('[role="dialog"]') as HTMLDialogElement;
    expect(dialog).toBeInstanceOf(HTMLDialogElement);
    expect(dialog.open).toBe(true);
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it('closes the preview with Escape and restores focus to its thumbnail', () => {
    const { container, thumbnail } = renderPreviewMessage();
    thumbnail.focus();
    act(() => thumbnail.click());
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;

    act(() => {
      dialog.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Escape',
        bubbles: true,
        cancelable: true,
      }));
    });

    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(thumbnail);
  });

  it('handles the native dialog cancel event and restores focus to its thumbnail', () => {
    const { container, thumbnail } = renderPreviewMessage();
    thumbnail.focus();
    act(() => thumbnail.click());
    const dialog = container.querySelector('[role="dialog"]') as HTMLDialogElement;
    const cancel = new Event('cancel', { bubbles: false, cancelable: true });

    act(() => dialog.dispatchEvent(cancel));

    expect(cancel.defaultPrevented).toBe(true);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(thumbnail);
  });
});
