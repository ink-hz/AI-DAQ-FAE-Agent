// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AuthenticatedAccount } from './enterpriseIdentity';
import { FaeWorkspaceActions } from './FaeWorkspaceActions';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const account: AuthenticatedAccount = {
  mode: 'platform_enterprise',
  displayName: '苍渊',
  partnerDisplayName: null,
};

const mounted: Array<{ container: HTMLDivElement; root: Root }> = [];

function render(loader: () => Promise<'/daq/manage/' | null>) {
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mounted.push({ container, root });
  act(() => root.render(<FaeWorkspaceActions account={account} loader={loader} />));
  return container;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  mounted.splice(0).forEach(({ container, root }) => {
    act(() => root.unmount());
    container.remove();
  });
});

describe('FaeWorkspaceActions', () => {
  it('renders the exact management link only after an authorized projection', async () => {
    const container = render(() => Promise.resolve('/daq/manage/'));

    expect(container.textContent).toBe('');
    await flush();

    const link = container.querySelector('a');
    expect(link?.textContent).toBe('管理工作台');
    expect(link?.getAttribute('href')).toBe('/daq/manage/');
  });

  it('renders nothing when management is not granted', async () => {
    const container = render(() => Promise.resolve(null));

    await flush();

    expect(container.textContent).toBe('');
  });

  it('keeps the FAE usage surface available when navigation lookup fails', async () => {
    const loader = vi.fn().mockRejectedValue(new Error('unavailable'));
    const container = render(loader);

    await flush();

    expect(loader).toHaveBeenCalledOnce();
    expect(container.textContent).toBe('');
  });
});
