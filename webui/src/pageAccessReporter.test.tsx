/** @vitest-environment happy-dom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FaePageAccessReporter } from './pageAccessReporter';
import type { AuthenticatedAccount } from './enterpriseIdentity';

const enterprise: AuthenticatedAccount = { mode: 'platform_enterprise', displayName: '苍渊', partnerDisplayName: null };
const partner: AuthenticatedAccount = { mode: 'platform_partner', displayName: '合作方', partnerDisplayName: '合作方' };

describe('FAE page access reporter', () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;
  beforeEach(() => {
    container = document.createElement('div'); document.body.append(container); root = createRoot(container);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const meta = document.createElement('meta'); meta.name = 'fae-browser-base'; meta.content = '/daq'; document.head.append(meta);
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => '00000000-0000-4000-8000-000000000003') });
  });
  afterEach(async () => {
    await act(async () => root.unmount()); container.remove();
    document.querySelectorAll('meta[name="fae-browser-base"]').forEach((meta) => meta.remove());
    vi.unstubAllGlobals();
  });

  it('reports internal enterprise conversations without their session id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 })); vi.stubGlobal('fetch', fetchMock);
    await act(async () => root.render(<FaePageAccessReporter account={enterprise} route={{ name: 'chat', sessionId: 'private-session' }} />));
    const body = String((fetchMock.mock.calls[0][1] as RequestInit).body);
    expect(JSON.parse(body)).toEqual({ access_event_id: '00000000-0000-4000-8000-000000000003', workspace_key: 'daq', page_key: 'daq.conversation' });
    expect(body).not.toContain('private-session');
  });

  it('never reports partner or public FAE usage', async () => {
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    await act(async () => root.render(<FaePageAccessReporter account={partner} route={{ name: 'chat' }} />));
    await act(async () => root.render(<FaePageAccessReporter account={null} route={{ name: 'chat' }} />));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('cannot break enterprise FAE when event id generation is unavailable', async () => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => { throw new Error('unsupported'); }) });
    const fetchMock = vi.fn(); vi.stubGlobal('fetch', fetchMock);
    await expect(act(async () => root.render(<FaePageAccessReporter account={enterprise} route={{ name: 'chat' }} />))).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
