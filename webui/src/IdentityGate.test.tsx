// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { bootstrap } = vi.hoisted(() => ({ bootstrap: vi.fn() }));

vi.mock('./enterpriseIdentity', () => ({
  bootstrapEnterpriseIdentity: () => bootstrap(),
}));

vi.mock('./App', () => ({
  default: () => <div className="chat-workspace">受保护的 FAE 工作区</div>,
}));

// eslint-disable-next-line import/first
import { IdentityGate } from './IdentityGate';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mountedRoots: Array<{ container: HTMLDivElement; root: Root }> = [];

function renderGate() {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mountedRoots.push({ container, root });
  act(() => root.render(<IdentityGate />));
  return container;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  for (const { container, root } of mountedRoots.splice(0)) {
    act(() => root.unmount());
    container.remove();
  }
  bootstrap.mockReset();
  vi.restoreAllMocks();
});

describe('IdentityGate', () => {
  it('shows no preparing placeholder and no protected UI while identity settles', async () => {
    bootstrap.mockReturnValue(new Promise(() => undefined));

    const container = renderGate();
    await flush();

    expect(container.textContent).toBe('');
    expect(container.textContent).not.toContain('正在进入 FAE');
    expect(container.textContent).not.toContain('准备中');
    expect(container.querySelector('.chat-workspace')).toBeNull();
  });

  it('renders the one shared chat workspace once identity settles', async () => {
    bootstrap.mockResolvedValue('platform_partner');

    const container = renderGate();
    await flush();

    expect(container.querySelectorAll('.chat-workspace')).toHaveLength(1);
  });

  it('keeps a bootstrap failure explicit instead of falling back to public chat', async () => {
    bootstrap.mockRejectedValue(new Error('身份校验失败'));

    const container = renderGate();
    await flush();

    expect(container.querySelector('.chat-workspace')).toBeNull();
    expect(container.textContent).toContain('暂时无法进入数采 FAE');
    expect(container.querySelector('.identity-gate button')?.textContent)
      .toBe('重新尝试');
  });
});
