import type { AuthenticatedAccount } from './enterpriseIdentity';
import { isInternalFaeSurface } from './runtimePaths';

type Fetcher = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

function parseManagementWorkspaceUrl(payload: unknown): '/daq/manage/' | null {
  if (payload === null || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('FAE 管理入口响应格式无效');
  }
  const record = payload as Record<string, unknown>;
  if (
    Object.keys(record).length !== 1
    || !Object.prototype.hasOwnProperty.call(record, 'management_workspace_url')
  ) {
    throw new Error('FAE 管理入口响应格式无效');
  }
  if (record.management_workspace_url === null) return null;
  if (record.management_workspace_url !== '/daq/manage/') {
    throw new Error('FAE 管理入口响应格式无效');
  }
  return record.management_workspace_url;
}

export async function loadFaeManagementWorkspaceUrl(
  account: AuthenticatedAccount | null,
  fetcher: Fetcher = fetch,
): Promise<'/daq/manage/' | null> {
  if (
    !isInternalFaeSurface()
    || account?.mode !== 'platform_enterprise'
  ) {
    return null;
  }
  const response = await fetcher('/api/v1/workspaces/daq/navigation', {
    credentials: 'include',
  });
  if (!response.ok) {
    throw new Error('FAE 管理入口暂时不可用');
  }
  return parseManagementWorkspaceUrl(await response.json());
}
