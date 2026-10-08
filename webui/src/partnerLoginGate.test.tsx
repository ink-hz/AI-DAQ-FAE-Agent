// @vitest-environment happy-dom

/**
 * The partner-login control must stay absent while the Partner Provider release
 * is unselected. The backend gate answers exactly `{"partner_login_available":
 * false}`; these tests prove the shipped UI renders no control against that
 * answer, has no dev-only or build-mode escape hatch that could reveal it, and
 * never substitutes a "preparing" placeholder for the missing control.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { fetchIdentityCapabilities } from './api';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const mounted: Array<{ container: HTMLDivElement; root: Root }> = [];

function baseFetch(input: string | URL | Request): Promise<Response> {
  const path = String(input);
  if (path === '/health') {
    return Promise.resolve(new Response(JSON.stringify({
      attachments: { vision_enabled: true },
    })));
  }
  return Promise.reject(new Error(`Unexpected fetch: ${path}`));
}

function renderAgainstCapability(
  capability: () => Promise<Response>,
): HTMLDivElement {
  vi.stubGlobal('fetch', vi.fn((input: string | URL | Request) => (
    String(input) === '/identity/capabilities' ? capability() : baseFetch(input)
  )));
  const container = document.createElement('div');
  const root = createRoot(container);
  document.body.appendChild(container);
  mounted.push({ container, root });
  act(() => root.render(<App initialPath="/app/" />));
  return container;
}

async function flushEffects() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function expectNoPartnerSurface(container: HTMLElement) {
  expect(container.querySelector('a[href="/partner/login"]')).toBeNull();
  expect(container.querySelector('.partner-login-link')).toBeNull();
  expect(container.innerHTML).not.toContain('/partner/login');
  expect(container.textContent).not.toContain('合作方客服登录');
  expect(container.textContent).not.toContain('准备中');
  expect(container.textContent).not.toContain('即将上线');
  expect(container.textContent).not.toContain('敬请期待');
}

afterEach(() => {
  while (mounted.length) {
    const entry = mounted.pop();
    if (!entry) continue;
    act(() => entry.root.unmount());
    entry.container.remove();
  }
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const GATED_PAYLOADS: Array<[string, unknown]> = [
  ['the gated production answer', { partner_login_available: false }],
  ['a truthy string', { partner_login_available: 'true' }],
  ['a truthy number', { partner_login_available: 1 }],
  ['an absent key', {}],
  ['a null capability', { partner_login_available: null }],
];

describe('partner login production gate', () => {
  it.each(GATED_PAYLOADS)(
    'renders no partner login control for %s',
    async (_label, payload) => {
      const container = renderAgainstCapability(() => Promise.resolve(
        new Response(JSON.stringify(payload)),
      ));
      await flushEffects();

      expectNoPartnerSurface(container);
    },
  );

  it('renders no partner login control when the gate returns 404', async () => {
    const container = renderAgainstCapability(() => Promise.resolve(
      new Response(JSON.stringify({ partner_login_available: true }), { status: 404 }),
    ));
    await flushEffects();

    expectNoPartnerSurface(container);
  });

  it('reads the gated capability as unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(
      new Response(JSON.stringify({ partner_login_available: false })),
    )));

    await expect(fetchIdentityCapabilities()).resolves.toEqual({
      partnerLoginAvailable: false,
    });
  });

  it('exposes no partner login control in a static production render', () => {
    const markup = renderToStaticMarkup(<App initialPath="/app/" />);

    expect(markup).not.toContain('/partner/login');
    expect(markup).not.toContain('合作方客服登录');
  });
});
