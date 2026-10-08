import { faeBrowserBase, faeBrowserPath } from './runtimePaths';

export type FaeBrowserRoute =
  | { name: 'chat'; sessionId?: string }
  | { name: 'review' }
  | { name: 'not-found' };

const CONVERSATION_ID = /^[A-Za-z0-9._~!$&'()*+,;=:@%-]+$/;

function safeDecodeSegment(value: string): string | null {
  if (!value || !CONVERSATION_ID.test(value)) return null;
  try {
    const decoded = decodeURIComponent(value);
    if (!decoded || decoded.includes('/')) return null;
    return decoded;
  } catch {
    return null;
  }
}

export function parseFaeBrowserRoute(pathname: string): FaeBrowserRoute {
  const normalized = pathname.replace(/\/+$/, '') || '/';
  const parts = normalized.split('/').filter(Boolean);
  if (parts.length === 1 && (parts[0] === 'app' || parts[0] === 'daq')) {
    return { name: 'chat' };
  }
  if (parts[0] === 'app' && parts[1] === 'review') {
    return { name: 'review' };
  }
  if (
    parts.length === 3
    && (parts[0] === 'app' || parts[0] === 'daq')
    && parts[1] === 'conversations'
  ) {
    const sessionId = safeDecodeSegment(parts[2]);
    return sessionId ? { name: 'chat', sessionId } : { name: 'not-found' };
  }
  return { name: 'not-found' };
}

export function sessionConversationPath(sessionId: string): string {
  return faeBrowserPath(`/conversations/${encodeURIComponent(sessionId)}`);
}

export function surfaceRootPath(): string {
  return `${faeBrowserBase()}/`;
}
