// @vitest-environment happy-dom

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  bootstrapEnterpriseIdentity,
  currentAuthenticatedAccount,
  currentAuthenticationMode,
  enterpriseMutationHeaders,
  resetEnterpriseIdentityForTests,
} from './enterpriseIdentity';

function setInternalFaeSurface(path = '/daq/') {
  document.head.innerHTML = '<meta name="fae-browser-base" content="/daq">'
    + '<meta name="fae-api-base" content="/daq/api">';
  window.history.replaceState(null, '', path);
}

function setBrowserUrl(url: string) {
  (window as typeof window & { happyDOM: { setURL(value: string): void } })
    .happyDOM.setURL(url);
}


afterEach(() => {
  resetEnterpriseIdentityForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setBrowserUrl('http://localhost:3000/app/');
  window.history.replaceState(null, '', '/app/');
  document.head.innerHTML = '';
});


describe('FAE enterprise identity bootstrap', () => {
  it('removes the launch fragment before exchanging it', async () => {
    const launchCode = 'l'.repeat(43);
    window.history.replaceState(
      null,
      '',
      `/app/#platform_launch=${launchCode}`,
    );
    const fetchMock = vi.fn(async (
      input: string | URL | Request,
      _init?: RequestInit,
    ) => {
      expect(window.location.hash).toBe('');
      expect(String(input)).toBe('/enterprise/session');
      return new Response(JSON.stringify({
        authenticated: true,
        authentication_mode: 'platform_enterprise',
        display_name: null,
        partner_display_name: null,
        csrf_token: 'csrf-from-fae',
      }), { status: 201 });
    });
    vi.stubGlobal('fetch', fetchMock);

    await bootstrapEnterpriseIdentity();

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ code: launchCode });
    expect(enterpriseMutationHeaders()).toEqual({
      'X-FAE-Enterprise-CSRF': 'csrf-from-fae',
    });
  });

  it('keeps the existing public customer flow when no enterprise session exists', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'enterprise_session_required' },
    }), { status: 401 })));

    await bootstrapEnterpriseIdentity();

    expect(enterpriseMutationHeaders()).toEqual({});
    expect(currentAuthenticationMode()).toBe('public_customer');
    expect(currentAuthenticatedAccount()).toBeNull();
  });

  it('requires Platform login instead of entering public mode on the internal surface', async () => {
    setInternalFaeSurface('/daq/conversations/safe-1');
    const replace = vi.fn();
    Object.defineProperty(window.location, 'replace', {
      configurable: true,
      value: replace,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'enterprise_session_required' },
      }), { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('platform_login_required');

    expect(currentAuthenticationMode()).toBe('public_customer');
    expect(sessionStorage.getItem('daq:internal-return')).toBe('/daq/conversations/safe-1');
    expect(replace).toHaveBeenCalledWith(
      `/login?return_path=${encodeURIComponent('/daq/conversations/safe-1')}`,
    );
  });

  it('reloads a same-document root launch after Platform account CSRF', async () => {
    setInternalFaeSurface('/daq/');
    setBrowserUrl('https://agent.orbbec.com.cn/daq/');
    const replace = vi.fn();
    const reload = vi.fn();
    Object.defineProperty(window.location, 'replace', {
      configurable: true,
      value: replace,
    });
    Object.defineProperty(window.location, 'reload', {
      configurable: true,
      value: reload,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'enterprise_session_required' },
      }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        csrf_token: 'platform-csrf',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        launch_url: `https://agent.orbbec.com.cn/daq/#platform_launch=${'l'.repeat(43)}`,
      }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('platform_launch_redirect');

    expect(fetchMock.mock.calls[1]).toEqual([
      '/api/v1/account',
      { credentials: 'include' },
    ]);
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/agents/ai-daq-fae-agent/launch');
    expect(fetchMock.mock.calls[2][1]).toMatchObject({
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': 'platform-csrf' },
    });
    expect(sessionStorage.getItem('daq:internal-return')).toBe('/daq/');
    expect(replace).toHaveBeenCalledWith(
      `https://agent.orbbec.com.cn/daq/#platform_launch=${'l'.repeat(43)}`,
    );
    expect(reload).toHaveBeenCalledTimes(1);
    expect(replace.mock.invocationCallOrder[0])
      .toBeLessThan(reload.mock.invocationCallOrder[0]);
  });

  it('lets a deep-link Platform launch perform its cross-document navigation', async () => {
    setInternalFaeSurface('/daq/conversations/safe-1');
    setBrowserUrl('https://agent.orbbec.com.cn/daq/conversations/safe-1');
    const replace = vi.fn();
    const reload = vi.fn();
    Object.defineProperty(window.location, 'replace', {
      configurable: true,
      value: replace,
    });
    Object.defineProperty(window.location, 'reload', {
      configurable: true,
      value: reload,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'enterprise_session_required' },
      }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        csrf_token: 'platform-csrf',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        launch_url: `https://agent.orbbec.com.cn/daq/#platform_launch=${'l'.repeat(43)}`,
      }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('platform_launch_redirect');

    expect(sessionStorage.getItem('daq:internal-return')).toBe('/daq/conversations/safe-1');
    expect(replace).toHaveBeenCalledWith(
      `https://agent.orbbec.com.cn/daq/#platform_launch=${'l'.repeat(43)}`,
    );
    expect(reload).not.toHaveBeenCalled();
  });

  it.each(['https://evil.orbbec.com.cn/daq/', 'https://agent.orbbec.com.cn/fae/'])('rejects launch responses outside the DAQ origin route: %s', async (launchBase) => {
    setInternalFaeSurface('/daq/assets/main.js');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'enterprise_session_required' },
      }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        csrf_token: 'platform-csrf',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        launch_url: `${launchBase}#platform_launch=${'l'.repeat(43)}`,
      }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('platform_launch_invalid');

    expect(sessionStorage.getItem('daq:internal-return')).toBe('/daq/');
  });

  it('restores only a safe internal return path after launch exchange', async () => {
    const launchCode = 'l'.repeat(43);
    setInternalFaeSurface(`/daq/#platform_launch=${launchCode}`);
    sessionStorage.setItem('daq:internal-return', '/daq/conversations/session-1');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_enterprise',
      display_name: 'FAE',
      partner_display_name: null,
      csrf_token: 'csrf-from-fae',
    }), { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);

    await bootstrapEnterpriseIdentity();

    expect(sessionStorage.getItem('daq:internal-return')).toBeNull();
    expect(window.location.pathname).toBe('/daq/conversations/session-1');
    expect(window.location.hash).toBe('');
  });

  it('maps internal Platform 403 to the explicit no-permission state', async () => {
    setInternalFaeSurface('/daq/');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'enterprise_session_required' },
      }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        csrf_token: 'platform-csrf',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 403 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('没有数采 FAE 使用权限');
  });

  it('restores a FAE session and rotates csrf on reload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_enterprise',
      display_name: null,
      partner_display_name: null,
      csrf_token: 'rotated-csrf',
    }), { status: 200 })));

    await bootstrapEnterpriseIdentity();

    expect(enterpriseMutationHeaders()).toEqual({
      'X-FAE-Enterprise-CSRF': 'rotated-csrf',
    });
  });

  it('does not downgrade an invalid enterprise cookie to public mode', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'identity_binding_invalid' },
    }), { status: 401 })));

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow(
      '企业身份已经失效',
    );
    expect(currentAuthenticatedAccount()).toBeNull();
  });
});

describe('FAE partner identity bootstrap', () => {
  it('accepts a partner launch without exposing provider identity', async () => {
    const launchCode = 'p'.repeat(43);
    window.history.replaceState(null, '', `/app/#partner_launch=${launchCode}`);
    const fetchMock = vi.fn(async (
      input: string | URL | Request,
      _init?: RequestInit,
    ) => {
      expect(window.location.hash).toBe('');
      expect(String(input)).toBe('/enterprise/session');
      return new Response(JSON.stringify({
        authenticated: true,
        authentication_mode: 'platform_partner',
        display_name: '坐席一',
        partner_display_name: '合作方甲',
        csrf_token: 'csrf',
      }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetchMock);

    expect(await bootstrapEnterpriseIdentity()).toBe('platform_partner');

    expect(currentAuthenticationMode()).toBe('platform_partner');
    expect(currentAuthenticatedAccount()).toEqual({
      mode: 'platform_partner',
      displayName: '坐席一',
      partnerDisplayName: '合作方甲',
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ code: launchCode });
    const serialized = JSON.stringify(currentAuthenticatedAccount());
    expect(serialized).not.toContain('provider_subject');
    expect(serialized).not.toContain('subject_id');
    expect(serialized).not.toContain('csrf');
  });

  it('keeps only safe projections when the response carries extra identity fields', async () => {
    window.history.replaceState(null, '', `/app/#partner_launch=${'q'.repeat(43)}`);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_partner',
      display_name: '坐席二',
      partner_display_name: '合作方乙',
      csrf_token: 'csrf',
      subject_id: '4fd1686f-3e89-4e32-9fe9-932c41e4274c',
      identity_binding_id: '6dbedcf8-5263-493f-91f5-6324be037d7c',
      provider_subject: 'wechat-openid',
    }), { status: 201 })));

    await bootstrapEnterpriseIdentity();

    expect(Object.keys(currentAuthenticatedAccount() ?? {})).toEqual([
      'mode',
      'displayName',
      'partnerDisplayName',
    ]);
    expect(JSON.stringify(currentAuthenticatedAccount()))
      .not.toContain('4fd1686f');
  });

  it('removes the whole launch fragment before rejecting a malformed code', async () => {
    window.history.replaceState(null, '', '/app/#partner_launch=too-short');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('启动参数无效');

    expect(window.location.hash).toBe('');
    expect(window.location.pathname).toBe('/app/');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(currentAuthenticationMode()).toBe('public_customer');
    expect(currentAuthenticatedAccount()).toBeNull();
  });

  it('fails explicitly when both launch parameters are present', async () => {
    window.history.replaceState(
      null,
      '',
      `/app/#platform_launch=${'l'.repeat(43)}&partner_launch=${'p'.repeat(43)}`,
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('启动参数无效');

    expect(window.location.hash).toBe('');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(currentAuthenticationMode()).toBe('public_customer');
  });

  it('fails explicitly when one launch parameter is duplicated', async () => {
    window.history.replaceState(
      null,
      '',
      `/app/#partner_launch=${'p'.repeat(43)}&partner_launch=${'q'.repeat(43)}`,
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('启动参数无效');

    expect(window.location.hash).toBe('');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(currentAuthenticationMode()).toBe('public_customer');
  });

  it('rejects an unknown authentication mode instead of guessing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_root',
      display_name: '未知',
      partner_display_name: null,
      csrf_token: 'csrf',
    }), { status: 200 })));

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('身份响应格式无效');

    expect(currentAuthenticationMode()).toBe('public_customer');
    expect(enterpriseMutationHeaders()).toEqual({});
  });

  it('reads an enterprise account whose Platform name is temporarily absent', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_enterprise',
      display_name: null,
      partner_display_name: null,
      csrf_token: 'rotated-csrf',
    }), { status: 200 })));

    expect(await bootstrapEnterpriseIdentity()).toBe('platform_enterprise');

    const account = currentAuthenticatedAccount();
    expect(account?.mode).toBe('platform_enterprise');
    expect(account?.displayName).toBe('企业用户');
    expect(account?.partnerDisplayName).toBeNull();
  });

  it('never attaches a partner organization to an enterprise account', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      authentication_mode: 'platform_enterprise',
      display_name: '企业成员',
      partner_display_name: '不该出现的合作方',
      csrf_token: 'csrf',
    }), { status: 200 })));

    await bootstrapEnterpriseIdentity();

    expect(currentAuthenticatedAccount()).toEqual({
      mode: 'platform_enterprise',
      displayName: '企业成员',
      partnerDisplayName: null,
    });
  });

  it('rejects a mode-less authenticated response instead of guessing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      authenticated: true,
      csrf_token: 'legacy-csrf',
    }), { status: 200 })));

    await expect(bootstrapEnterpriseIdentity()).rejects.toThrow('身份响应格式无效');
    expect(currentAuthenticatedAccount()).toBeNull();
    expect(currentAuthenticationMode()).toBe('public_customer');
  });
});
