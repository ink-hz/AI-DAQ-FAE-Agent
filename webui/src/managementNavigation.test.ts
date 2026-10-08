// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuthenticatedAccount } from './enterpriseIdentity';
import { loadFaeManagementWorkspaceUrl } from './managementNavigation';

const ENTERPRISE_ACCOUNT: AuthenticatedAccount = {
  mode: 'platform_enterprise',
  displayName: '苍渊',
  partnerDisplayName: null,
};

const PARTNER_ACCOUNT: AuthenticatedAccount = {
  mode: 'platform_partner',
  displayName: '合作方坐席',
  partnerDisplayName: '天猫',
};

beforeEach(() => {
  document.head.innerHTML = '<meta name="fae-browser-base" content="/daq">';
  window.history.replaceState(null, '', '/daq/');
});

describe('loadFaeManagementWorkspaceUrl', () => {
  it('loads the bounded navigation projection for an internal enterprise user', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      management_workspace_url: '/daq/manage/',
    })));

    await expect(
      loadFaeManagementWorkspaceUrl(ENTERPRISE_ACCOUNT, fetcher),
    ).resolves.toBe('/daq/manage/');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/workspaces/daq/navigation',
      { credentials: 'include' },
    );
  });

  it.each([
    ['public customer', null],
    ['partner account', PARTNER_ACCOUNT],
  ])('never requests management navigation for a %s', async (_label, account) => {
    const fetcher = vi.fn();

    await expect(
      loadFaeManagementWorkspaceUrl(account, fetcher),
    ).resolves.toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('does not request management navigation on the public FAE surface', async () => {
    document.head.innerHTML = '<meta name="fae-browser-base" content="/app">';
    window.history.replaceState(null, '', '/app/');
    const fetcher = vi.fn();

    await expect(
      loadFaeManagementWorkspaceUrl(ENTERPRISE_ACCOUNT, fetcher),
    ).resolves.toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('accepts an explicit null projection', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      management_workspace_url: null,
    })));

    await expect(
      loadFaeManagementWorkspaceUrl(ENTERPRISE_ACCOUNT, fetcher),
    ).resolves.toBeNull();
  });

  it.each([
    { management_workspace_url: '/fae/manage/' },
    { management_workspace_url: 'https://agent.orbbec.com.cn/daq/manage/' },
    { management_workspace_url: '/daq/manage/?view=all' },
    { management_workspace_url: '/admin/daq' },
    { management_workspace_url: '/daq/manage/', extra: true },
  ])('rejects an unsafe or expanded projection: %j', async (payload) => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload)),
    );

    await expect(
      loadFaeManagementWorkspaceUrl(ENTERPRISE_ACCOUNT, fetcher),
    ).rejects.toThrow('FAE 管理入口响应格式无效');
  });

  it('rejects a failed Platform response', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 503 }));

    await expect(
      loadFaeManagementWorkspaceUrl(ENTERPRISE_ACCOUNT, fetcher),
    ).rejects.toThrow('FAE 管理入口暂时不可用');
  });
});
