// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AttachmentComposer } from './AttachmentComposer';
import type { AttachmentDraft } from './attachments';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const base: AttachmentDraft = {
  clientId: 'd1',
  file: new File(['E42'], 'viewer.png', { type: 'image/png' }),
  displayName: 'viewer.png',
  kind: 'image',
  sizeBytes: 3,
  status: 'ready',
  attachmentId: 'opaque-a',
  sourceId: 'att-src-a',
  previewUrl: 'blob:viewer',
};

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((settle, fail) => {
    resolve = settle;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function renderInteractive(
  onFiles: (files: File[]) => Promise<void> | void,
  onFileError: (error: unknown) => void = () => undefined,
) {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => {
    root.render(
      <AttachmentComposer
        drafts={[]}
        visionEnabled
        onFiles={onFiles}
        onFileError={onFileError}
        onRemove={() => undefined}
        onRetry={() => undefined}
      />,
    );
  });
  return container.querySelector('input[type="file"]') as HTMLInputElement;
}

function dispatchSelection(input: HTMLInputElement, file: File) {
  Object.defineProperty(input, 'files', { configurable: true, value: [file] });
  Object.defineProperty(input, 'value', {
    configurable: true,
    writable: true,
    value: `C:\\fakepath\\${file.name}`,
  });
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
});

describe('AttachmentComposer', () => {
  it('keeps the selected file input alive until the upload callback settles', async () => {
    const upload = deferred<void>();
    const onFiles = vi.fn(() => upload.promise);
    const input = renderInteractive(onFiles);
    const photo = new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });

    act(() => dispatchSelection(input, photo));

    expect(onFiles).toHaveBeenCalledWith([photo]);
    expect(input.value).toBe('C:\\fakepath\\photo.jpg');

    await act(async () => {
      upload.resolve();
      await upload.promise;
    });

    expect(input.value).toBe('');
  });

  it('allows the same file to be selected again after each callback settles', async () => {
    let upload = deferred<void>();
    const onFiles = vi.fn(() => upload.promise);
    const input = renderInteractive(onFiles);
    const photo = new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });

    act(() => dispatchSelection(input, photo));
    await act(async () => {
      upload.resolve();
      await upload.promise;
    });

    upload = deferred<void>();
    act(() => dispatchSelection(input, photo));

    expect(onFiles).toHaveBeenCalledTimes(2);
    expect(input.value).toBe('C:\\fakepath\\photo.jpg');

    await act(async () => {
      upload.resolve();
      await upload.promise;
    });
    expect(input.value).toBe('');
  });

  it('blocks reopening the shared picker until the current upload settles', async () => {
    const upload = deferred<void>();
    const onFiles = vi.fn(() => upload.promise);
    const input = renderInteractive(onFiles);
    const photo = new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });
    act(() => dispatchSelection(input, photo));

    const blockedClick = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => input.dispatchEvent(blockedClick));

    expect(blockedClick.defaultPrevented).toBe(true);
    expect(onFiles).toHaveBeenCalledTimes(1);

    await act(async () => {
      upload.resolve();
      await upload.promise;
    });

    const allowedClick = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => input.dispatchEvent(allowedClick));
    expect(allowedClick.defaultPrevented).toBe(false);
  });

  it('reports a rejected picker callback and unlocks the input', async () => {
    const upload = deferred<void>();
    const onFileError = vi.fn();
    const input = renderInteractive(() => upload.promise, onFileError);
    const photo = new File(['jpeg'], 'photo.jpg', { type: 'image/jpeg' });
    act(() => dispatchSelection(input, photo));
    const failure = new Error('upload owner failed');

    await act(async () => {
      upload.reject(failure);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onFileError).toHaveBeenCalledWith(failure);
    expect(input.value).toBe('');
    const allowedClick = new MouseEvent('click', { bubbles: true, cancelable: true });
    act(() => input.dispatchEvent(allowedClick));
    expect(allowedClick.defaultPrevented).toBe(false);
  });

  it('renders accessible picker, ready image preview, and removal without the warning', () => {
    const html = renderToStaticMarkup(
      <AttachmentComposer
        drafts={[base]}
        visionEnabled
        onFiles={() => undefined}
        onFileError={() => undefined}
        onRemove={() => undefined}
        onRetry={() => undefined}
      />,
    );
    expect(html).toContain('type="file"');
    expect(html).toContain('multiple=""');
    expect(html).toContain('viewer.png');
    expect(html).toContain('已就绪');
    expect(html).toContain('aria-label="移除 viewer.png"');
    expect(html).toContain('src="blob:viewer"');
    expect(html).not.toContain(
      '图片将发送给配置的视觉模型分析；请勿上传客户机密或敏感数据。',
    );
  });

  it('renders a kind icon and size metadata for document drafts', () => {
    const html = renderToStaticMarkup(
      <AttachmentComposer
        drafts={[{
          ...base,
          clientId: 'document',
          file: new File([new Uint8Array(2048)], 'guide.docx'),
          displayName: 'guide.docx',
          kind: 'document',
          sizeBytes: 2048,
          previewUrl: undefined,
        }]}
        visionEnabled
        onFiles={() => undefined}
        onFileError={() => undefined}
        onRemove={() => undefined}
        onRetry={() => undefined}
      />,
    );
    expect(html).toContain('lucide-file-type');
    expect(html).toContain('document · 2 KiB');
    expect(html).not.toContain('<img');
  });

  it('disables image selection and states the boundary when vision is off', () => {
    const html = renderToStaticMarkup(
      <AttachmentComposer
        drafts={[]}
        visionEnabled={false}
        onFiles={() => undefined}
        onFileError={() => undefined}
        onRemove={() => undefined}
        onRetry={() => undefined}
      />,
    );
    expect(html).not.toContain('.png');
    expect(html).toContain(
      '当前环境图片理解未启用；仍可上传 PDF、Office、文本、日志和代码附件。',
    );
  });

  it('renders retry for failed cards and status text for uploading', () => {
    const html = renderToStaticMarkup(
      <AttachmentComposer
        drafts={[
          { ...base, clientId: 'failed', status: 'failed', error: '解析失败' },
          { ...base, clientId: 'uploading', status: 'uploading' },
        ]}
        onFiles={() => undefined}
        onFileError={() => undefined}
        onRemove={() => undefined}
        onRetry={() => undefined}
      />,
    );
    expect(html).toContain('aria-label="重试 viewer.png"');
    expect(html).toContain('解析失败');
    expect(html).toContain('上传并解析中');
    expect(html).toContain('role="status"');
  });
});
