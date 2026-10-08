import { useEffect, useState } from 'react';
import type { AuthenticatedAccount } from './enterpriseIdentity';
import { loadFaeManagementWorkspaceUrl } from './managementNavigation';

type ManagementUrlLoader = (
  account: AuthenticatedAccount | null,
) => Promise<'/daq/manage/' | null>;

export function FaeWorkspaceActions({
  account,
  loader = loadFaeManagementWorkspaceUrl,
}: {
  account: AuthenticatedAccount | null;
  loader?: ManagementUrlLoader;
}) {
  const [managementUrl, setManagementUrl] = useState<'/daq/manage/' | null>(null);

  useEffect(() => {
    let active = true;
    setManagementUrl(null);
    void loader(account)
      .then((url) => {
        if (active) setManagementUrl(url);
      })
      .catch(() => {
        if (active) setManagementUrl(null);
      });
    return () => { active = false; };
  }, [account, loader]);

  return (
    <div className="fae-workspace-actions">
      {managementUrl && <a href={managementUrl}>管理工作台</a>}
    </div>
  );
}
