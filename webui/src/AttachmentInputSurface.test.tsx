// @vitest-environment happy-dom

import { act, type ComponentProps, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AttachmentInputSurface } from './AttachmentInputSurface';
import { clipboardFiles, droppedFiles, hasFileTransfer } from './attachmentInput';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

function renderSurface(
  onFiles = vi.fn(),
  props: Partial<ComponentProps<typeof AttachmentInputSurface>> = {},
  children: ReactNode = <textarea aria-label="question" />,
) {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });

  act(() => {
    root.render(
      <AttachmentInputSurface
        aria-label="composer"
        className="composer"
        onFiles={onFiles}
        onFileError={() => undefined}
        {...props}
      >
        {children}
      </AttachmentInputSurface>,
    );
  });

  return {
    form: container.querySelector('form') as HTMLFormElement,
    textarea: container.querySelector('textarea') as HTMLTextAreaElement,
  };
}

function transferEvent(
  type: 'dragenter' | 'dragover' | 'dragleave' | 'drop',
  dataTransfer: object,
) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, 'dataTransfer', { value: dataTransfer });
  return event;
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
});

describe('attachment transfer extraction', () => {
  it('prefers non-null clipboard file items and de-duplicates by object identity', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const fallback = new File(['fallback'], 'fallback.png', { type: 'image/png' });

    expect(clipboardFiles({
      items: [
        { kind: 'file', getAsFile: () => image },
        { kind: 'file', getAsFile: () => image },
        { kind: 'file', getAsFile: () => null },
        { kind: 'string', getAsFile: () => fallback },
      ],
      files: [fallback],
    })).toEqual([image]);
  });

  it('falls back to clipboard files only when file items produce no files', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });

    expect(clipboardFiles({
      items: [{ kind: 'file', getAsFile: () => null }],
      files: [image, image],
    })).toEqual([image]);
  });

  it('extracts unique dropped files and recognizes file transfer types', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });

    expect(droppedFiles({ files: [image, image] })).toEqual([image]);
    expect(hasFileTransfer(['text/plain', 'Files'])).toBe(true);
    expect(hasFileTransfer(['text/plain'])).toBe(false);
  });
});

describe('AttachmentInputSurface', () => {
  it('extracts pasted file items once and prevents the file paste default', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const { form, textarea } = renderSurface(onFiles);
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [{ kind: 'file', getAsFile: () => image }], files: [image] },
    });

    textarea.dispatchEvent(paste);

    expect(form.getAttribute('aria-label')).toBe('composer');
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledWith([image]);
    expect(paste.defaultPrevented).toBe(true);
  });

  it('uses the clipboard files fallback without duplicating the callback', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const { textarea } = renderSurface(onFiles);
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [], files: [image] },
    });

    textarea.dispatchEvent(paste);

    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledWith([image]);
    expect(paste.defaultPrevented).toBe(true);
  });

  it('reports a rejected transfer callback without an unhandled Promise', async () => {
    const failure = new Error('upload owner failed');
    const onFiles = vi.fn().mockRejectedValue(failure);
    const onFileError = vi.fn();
    const { textarea } = renderSurface(onFiles, { onFileError });
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [{ kind: 'file', getAsFile: () => image }], files: [image] },
    });

    await act(async () => {
      textarea.dispatchEvent(paste);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onFileError).toHaveBeenCalledWith(failure);
  });

  it('composes a caller paste handler with file transfer handling exactly once', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const onPaste = vi.fn();
    const { textarea } = renderSurface(onFiles, { onPaste });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [{ kind: 'file', getAsFile: () => image }], files: [image] },
    });

    textarea.dispatchEvent(paste);

    expect(onPaste).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledWith([image]);
  });

  it('preserves pure-text paste defaults', () => {
    const onFiles = vi.fn();
    const { textarea } = renderSurface(onFiles);
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [{ kind: 'string', getAsFile: () => null }], files: [] },
    });

    textarea.dispatchEvent(paste);

    expect(onFiles).not.toHaveBeenCalled();
    expect(paste.defaultPrevented).toBe(false);
  });

  it('does not consume pasted files while file input is disabled', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const { textarea } = renderSurface(onFiles, { fileInputDisabled: true });
    const paste = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(paste, 'clipboardData', {
      value: { items: [{ kind: 'file', getAsFile: () => image }], files: [image] },
    });

    textarea.dispatchEvent(paste);

    expect(onFiles).not.toHaveBeenCalled();
    expect(paste.defaultPrevented).toBe(false);
  });

  it('shows file drag state, preserves text drags, and clears on dragleave', () => {
    const { form, textarea } = renderSurface();
    const textDrag = transferEvent('dragenter', { types: ['text/plain'], files: [] });

    act(() => textarea.dispatchEvent(textDrag));

    expect(form.classList.contains('file-drag-active')).toBe(false);
    expect(textDrag.defaultPrevented).toBe(false);

    const fileDrag = transferEvent('dragover', { types: ['Files'], files: [] });
    act(() => textarea.dispatchEvent(fileDrag));

    expect(form.classList.contains('file-drag-active')).toBe(true);
    expect(fileDrag.defaultPrevented).toBe(true);

    const leave = transferEvent('dragleave', { types: ['Files'], files: [] });
    act(() => textarea.dispatchEvent(leave));

    expect(form.classList.contains('file-drag-active')).toBe(false);
  });

  it('accepts a file dropped on the textarea and clears file drag state', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const { form, textarea } = renderSurface(onFiles);
    const enter = transferEvent('dragenter', { types: ['Files'], files: [image] });
    act(() => textarea.dispatchEvent(enter));
    const drop = transferEvent('drop', { types: ['Files'], files: [image] });

    act(() => textarea.dispatchEvent(drop));

    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledWith([image]);
    expect(drop.defaultPrevented).toBe(true);
    expect(form.classList.contains('file-drag-active')).toBe(false);
  });

  it('composes a caller drop handler with file transfer handling exactly once', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const onDrop = vi.fn();
    const { textarea } = renderSurface(onFiles, { onDrop });
    const drop = transferEvent('drop', { types: ['Files'], files: [image] });

    act(() => textarea.dispatchEvent(drop));

    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledTimes(1);
    expect(onFiles).toHaveBeenCalledWith([image]);
  });

  it('does not consume non-file drops or disabled file drops', () => {
    const image = new File(['image'], 'viewer.png', { type: 'image/png' });
    const onFiles = vi.fn();
    const { textarea } = renderSurface(onFiles, { fileInputDisabled: true });
    const textDrop = transferEvent('drop', { types: ['text/plain'], files: [] });

    act(() => textarea.dispatchEvent(textDrop));

    expect(textDrop.defaultPrevented).toBe(false);

    const fileDrop = transferEvent('drop', { types: ['Files'], files: [image] });
    act(() => textarea.dispatchEvent(fileDrop));

    expect(fileDrop.defaultPrevented).toBe(true);
    expect(onFiles).not.toHaveBeenCalled();
  });
});
