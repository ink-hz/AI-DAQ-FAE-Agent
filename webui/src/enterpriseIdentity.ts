import { AGENT_ID, faeApiPath, isInternalFaeSurface } from './runtimePaths';

export type EnterpriseAuthenticationMode =
  | 'public_customer'
  | 'platform_enterprise'
  | 'platform_partner';

export type AuthenticatedMode = Exclude<EnterpriseAuthenticationMode, 'public_customer'>;

export type AuthenticatedAccount = {
  mode: AuthenticatedMode;
  displayName: string;
  partnerDisplayName: string | null;
};

type SessionProjection = {
  csrfToken: string;
  account: AuthenticatedAccount;
};

const LAUNCH_PARAMETERS = ['platform_launch', 'partner_launch'] as const;
const LAUNCH_CODE = /^[A-Za-z0-9_-]{32,256}$/;
const INTERNAL_RETURN_SESSION_ID = /^[A-Za-z0-9._~!$&'()*+,;=:@%-]+$/;
const INTERNAL_RETURN_STORAGE_KEY = 'daq:internal-return';
const DISPLAY_NAME_LIMIT = 64;
const FALLBACK_DISPLAY_NAME: Record<AuthenticatedMode, string> = {
  platform_enterprise: '企业用户',
  platform_partner: '合作方坐席',
};

let authenticationMode: EnterpriseAuthenticationMode = 'public_customer';
let authenticatedAccount: AuthenticatedAccount | null = null;
let csrfToken: string | null = null;
let bootstrapPromise: Promise<EnterpriseAuthenticationMode> | null = null;

function safeName(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const cleaned = value.replace(/[\u0000-\u001f\u007f]/g, '').trim();
  return cleaned ? cleaned.slice(0, DISPLAY_NAME_LIMIT) : null;
}

function readMode(value: unknown): AuthenticatedMode {
  if (value === 'platform_enterprise' || value === 'platform_partner') return value;
  throw new Error('企业身份响应格式无效');
}

function parsePayload(payload: unknown): SessionProjection {
  if (
    payload === null
    || typeof payload !== 'object'
    || (payload as { authenticated?: unknown }).authenticated !== true
    || typeof (payload as { csrf_token?: unknown }).csrf_token !== 'string'
    || !(payload as { csrf_token: string }).csrf_token
  ) {
    throw new Error('企业身份响应格式无效');
  }
  const raw = payload as Record<string, unknown>;
  const mode = readMode(raw.authentication_mode);
  return {
    csrfToken: raw.csrf_token as string,
    // Only these three fields ever leave this module: any other identity field
    // the server may add stays unread instead of reaching component state.
    account: {
      mode,
      displayName: safeName(raw.display_name) ?? FALLBACK_DISPLAY_NAME[mode],
      partnerDisplayName: mode === 'platform_partner'
        ? safeName(raw.partner_display_name)
        : null,
    },
  };
}

function takeLaunchCode(): string | null {
  const parameters = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const present = LAUNCH_PARAMETERS.filter((name) => parameters.get(name) !== null);
  if (present.length === 0) return null;
  // The whole fragment goes before any validation or exchange, so a rejected
  // launch cannot stay in history, referrers or a reload.
  window.history.replaceState(
    null,
    '',
    `${window.location.pathname}${window.location.search}`,
  );
  if (
    present.length > 1
    || parameters.getAll(present[0]).length !== 1
  ) {
    throw new Error('企业身份启动参数无效');
  }
  const code = parameters.get(present[0]) as string;
  if (!LAUNCH_CODE.test(code)) {
    throw new Error('企业身份启动参数无效');
  }
  return code;
}

function safeInternalPathname(pathname: string): string {
  if (pathname === '/daq' || pathname === '/daq/') return '/daq/';
  const prefix = '/daq/conversations/';
  if (!pathname.startsWith(prefix)) return '/daq/';
  const sessionId = pathname.slice(prefix.length);
  if (!sessionId || sessionId.includes('/') || !INTERNAL_RETURN_SESSION_ID.test(sessionId)) {
    return '/daq/';
  }
  try {
    const decoded = decodeURIComponent(sessionId);
    if (!decoded || decoded.includes('/')) return '/daq/';
  } catch {
    return '/daq/';
  }
  return pathname;
}

export function safeInternalFaeReturnPath(location: Location): string {
  return safeInternalPathname(location.pathname);
}

function restoreInternalReturnPathAfterLaunch(): void {
  if (!isInternalFaeSurface()) return;
  const restored = safeInternalPathname(
    sessionStorage.getItem(INTERNAL_RETURN_STORAGE_KEY) ?? '/daq/',
  );
  sessionStorage.removeItem(INTERNAL_RETURN_STORAGE_KEY);
  window.history.replaceState(null, '', restored);
}

function safeLaunchUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (
    parsed.origin !== 'https://agent.orbbec.com.cn'
    || parsed.pathname !== '/daq/'
    || parsed.search
  ) {
    return null;
  }
  const parameters = new URLSearchParams(parsed.hash.replace(/^#/, ''));
  if (
    parameters.getAll('platform_launch').length !== 1
    || parameters.toString() !== `platform_launch=${parameters.get('platform_launch')}`
  ) {
    return null;
  }
  const code = parameters.get('platform_launch');
  return code && LAUNCH_CODE.test(code) ? value : null;
}

async function requestPlatformLaunch(): Promise<never> {
  const returnPath = safeInternalFaeReturnPath(window.location);
  sessionStorage.setItem(INTERNAL_RETURN_STORAGE_KEY, returnPath);
  const accountResponse = await fetch('/api/v1/account', { credentials: 'include' });
  if (accountResponse.status === 401) {
    window.location.replace(`/login?return_path=${encodeURIComponent(returnPath)}`);
    throw new Error('platform_login_required');
  }
  if (!accountResponse.ok) {
    throw new Error('platform_account_invalid');
  }
  const account = await accountResponse.json() as { csrf_token?: unknown };
  if (typeof account.csrf_token !== 'string' || !account.csrf_token) {
    throw new Error('platform_account_invalid');
  }
  const launchResponse = await fetch(`/api/v1/agents/${AGENT_ID}/launch`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRF-Token': account.csrf_token },
  });
  if (launchResponse.status === 403) {
    throw new Error('没有数采 FAE 使用权限');
  }
  if (!launchResponse.ok) {
    throw new Error('platform_launch_failed');
  }
  const payload = await launchResponse.json() as { launch_url?: unknown };
  const launchUrl = safeLaunchUrl(payload.launch_url);
  if (!launchUrl) {
    throw new Error('platform_launch_invalid');
  }
  const currentUrl = new URL(window.location.href);
  const targetUrl = new URL(launchUrl);
  const sameDocument = currentUrl.origin === targetUrl.origin
    && currentUrl.pathname === targetUrl.pathname
    && currentUrl.search === targetUrl.search;
  window.location.replace(launchUrl);
  // A fragment-only replacement does not reload the document, so the bootstrap
  // must be restarted explicitly. Deep links already perform a cross-document
  // replacement and must not race it with a reload of the old document.
  if (sameDocument) window.location.reload();
  throw new Error('platform_launch_redirect');
}

function stayPublic(): EnterpriseAuthenticationMode {
  authenticationMode = 'public_customer';
  authenticatedAccount = null;
  csrfToken = null;
  return authenticationMode;
}

async function initializeEnterpriseIdentity(): Promise<EnterpriseAuthenticationMode> {
  const launchCode = takeLaunchCode();
  const response = launchCode
    ? await fetch(faeApiPath('/enterprise/session'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ code: launchCode }),
    })
    : await fetch(faeApiPath('/enterprise/session'), { method: 'GET' });
  if (!launchCode && response.status === 404) {
    if (isInternalFaeSurface()) return requestPlatformLaunch();
    return stayPublic();
  }
  if (!launchCode && response.status === 401) {
    const payload = await response.json().catch(() => null) as {
      error?: { code?: unknown };
    } | null;
    if (payload?.error?.code === 'enterprise_session_required') {
      if (isInternalFaeSurface()) return requestPlatformLaunch();
      return stayPublic();
    }
    throw new Error('企业身份已经失效，请返回 Agent Platform 重新打开');
  }
  if (!response.ok) {
    throw new Error(
      launchCode
        ? '无法确认企业身份，请返回 Agent Platform 重试'
        : '企业身份暂时无法验证，请稍后重试',
    );
  }
  const projection = parsePayload(await response.json());
  csrfToken = projection.csrfToken;
  authenticatedAccount = projection.account;
  authenticationMode = projection.account.mode;
  if (launchCode) restoreInternalReturnPathAfterLaunch();
  return authenticationMode;
}

export function bootstrapEnterpriseIdentity(): Promise<EnterpriseAuthenticationMode> {
  bootstrapPromise ??= initializeEnterpriseIdentity();
  return bootstrapPromise;
}

export function enterpriseMutationHeaders(): Record<string, string> {
  return csrfToken ? { 'X-FAE-Enterprise-CSRF': csrfToken } : {};
}

export function currentAuthenticationMode(): EnterpriseAuthenticationMode {
  return authenticationMode;
}

export function currentAuthenticatedAccount(): AuthenticatedAccount | null {
  return authenticatedAccount;
}

export function resetEnterpriseIdentityForTests(): void {
  authenticationMode = 'public_customer';
  authenticatedAccount = null;
  csrfToken = null;
  bootstrapPromise = null;
}
