import { useEffect, useState } from 'react';
import App from './App';
import { bootstrapEnterpriseIdentity } from './enterpriseIdentity';

/**
 * Holds protected content back until identity settles.
 *
 * While the bootstrap is in flight nothing is rendered: a "preparing" placeholder
 * would be a second, partner-visible UI state, and rendering the workspace early
 * would leak public chat as a fallback. A failure stays an explicit failure.
 */
export function IdentityGate() {
  const [state, setState] = useState<'loading' | 'ready' | 'failed' | 'forbidden'>('loading');

  useEffect(() => {
    let mounted = true;
    void bootstrapEnterpriseIdentity()
      .then(() => { if (mounted) setState('ready'); })
      .catch((error) => {
        if (mounted) {
          setState(error instanceof Error && error.message.includes('没有数采 FAE 使用权限')
            ? 'forbidden'
            : 'failed');
        }
      });
    return () => { mounted = false; };
  }, []);

  if (state === 'loading') {
    return null;
  }
  if (state === 'failed') {
    return (
      <main className="identity-gate">
        <h1>暂时无法进入数采 FAE</h1>
        <p>企业身份校验未完成，请返回 Agent Platform 重新打开。</p>
        <button type="button" onClick={() => window.location.reload()}>重新尝试</button>
      </main>
    );
  }
  if (state === 'forbidden') {
    return (
      <main className="identity-gate">
        <h1>没有数采 FAE 使用权限</h1>
        <p>请联系管理员开通 AI DAQ FAE Agent 权限后再试。</p>
      </main>
    );
  }
  return <App />;
}
