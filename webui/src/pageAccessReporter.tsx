import { useEffect } from 'react';

import type { AuthenticatedAccount } from './enterpriseIdentity';
import type { FaeBrowserRoute } from './routes';
import { isInternalFaeSurface } from './runtimePaths';

function pageKey(route: FaeBrowserRoute): string | null {
  if (route.name !== 'chat') return null;
  return route.sessionId ? 'daq.conversation' : 'daq.workspace';
}

export function FaePageAccessReporter({
  account,
  route,
}: {
  account: AuthenticatedAccount | null;
  route: FaeBrowserRoute;
}) {
  const selectedPageKey = pageKey(route);
  const enabled = isInternalFaeSurface() && account?.mode === 'platform_enterprise';
  useEffect(() => {
    if (!enabled || !selectedPageKey) return;
    void Promise.resolve().then(() => {
      const body = {
        access_event_id: crypto.randomUUID(),
        workspace_key: 'daq',
        page_key: selectedPageKey,
      };
      return fetch('/api/v1/access-events/page-view', {
        method: 'POST', credentials: 'include', keepalive: true,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
    }).catch(() => undefined);
  }, [enabled, selectedPageKey]);
  return null;
}
