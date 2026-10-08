// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyTextToClipboard } from './clipboard';

const originalExecCommand = Object.getOwnPropertyDescriptor(document, 'execCommand');

function stubExecCommand(
  implementation: (command: string) => boolean,
) {
  const execCommand = vi.fn(implementation);
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: execCommand,
  });
  return execCommand;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  if (originalExecCommand) {
    Object.defineProperty(document, 'execCommand', originalExecCommand);
  } else {
    Reflect.deleteProperty(document, 'execCommand');
  }
});

describe('copyTextToClipboard', () => {
  it('writes the exact Markdown through the modern clipboard API', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const markdown = '# 标题\n\n```cpp\nrun();\n```';

    await copyTextToClipboard(markdown);

    expect(writeText).toHaveBeenCalledWith(markdown);
    expect(document.querySelector('textarea[data-copy-fallback]')).toBeNull();
  });

  it('uses the HTTP-compatible fallback and restores focus when the API is absent', async () => {
    vi.stubGlobal('navigator', {});
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    const markdown = '**结论**\n\n- 参数 A';
    const execCommand = stubExecCommand((command) => {
      expect(command).toBe('copy');
      const textarea = document.querySelector(
        'textarea[data-copy-fallback]',
      ) as HTMLTextAreaElement;
      expect(textarea.value).toBe(markdown);
      return true;
    });

    await copyTextToClipboard(markdown);

    expect(execCommand).toHaveBeenCalledOnce();
    expect(document.querySelector('textarea[data-copy-fallback]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('falls back after the modern API rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const execCommand = stubExecCommand(() => true);

    await copyTextToClipboard('答案');

    expect(writeText).toHaveBeenCalledWith('答案');
    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('rejects explicitly and cleans up when both copy paths fail', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'));
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    stubExecCommand(() => false);

    await expect(copyTextToClipboard('答案')).rejects.toThrow('copy_failed');

    expect(document.querySelector('textarea[data-copy-fallback]')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});
