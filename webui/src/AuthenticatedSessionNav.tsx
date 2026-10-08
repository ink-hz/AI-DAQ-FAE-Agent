import { useCallback, useEffect, useState } from 'react';
import { fetchAuthenticatedConversation, fetchAuthenticatedConversations } from './api';
import {
  conversationListTitle,
  formatConversationTimestamp,
  normalizeRestoredConversation,
  type RestoredConversation,
} from './authenticatedHistory';
import { sessionConversationPath } from './routes';
import type { AuthenticatedConversationSummary } from './types';

interface AuthenticatedSessionNavProps {
  onOpen: (conversation: RestoredConversation) => void;
}

export function AuthenticatedSessionNav({ onOpen }: AuthenticatedSessionNavProps) {
  const [items, setItems] = useState<AuthenticatedConversationSummary[]>([]);
  // The cursor is opaque server state: it lives here and is never rendered.
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [openingId, setOpeningId] = useState<string | null>(null);

  const loadPage = useCallback(async (nextCursor?: string) => {
    setLoading(true);
    setListError(null);
    try {
      const page = await fetchAuthenticatedConversations(nextCursor);
      setItems((current) => (nextCursor ? [...current, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch {
      setListError('历史会话加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPage();
  }, [loadPage]);

  async function openConversation(sessionId: string) {
    setOpeningId(sessionId);
    setOpenError(null);
    try {
      const detail = await fetchAuthenticatedConversation(sessionId);
      const conversation = normalizeRestoredConversation(detail);
      onOpen(conversation);
      window.history.pushState(null, '', sessionConversationPath(conversation.sessionId));
    } catch {
      setOpenError('会话打开失败');
    } finally {
      setOpeningId(null);
    }
  }

  return (
    <nav
      className="authenticated-history"
      aria-label="历史会话"
      aria-busy={loading ? true : undefined}
    >
      <div className="authenticated-history-heading">历史会话</div>

      {items.map((item) => (
        <button
          key={item.session_id}
          type="button"
          className="history-item"
          onClick={() => { void openConversation(item.session_id); }}
          disabled={openingId !== null}
        >
          <span className="history-item-title">{conversationListTitle(item.title)}</span>
          <small className="history-item-time">
            {formatConversationTimestamp(item.last_active_at)}
          </small>
        </button>
      ))}

      {!loading && !listError && items.length === 0 && (
        <p className="history-empty">暂无历史会话</p>
      )}

      {listError && (
        <div className="history-error" role="status">
          <span>{listError}</span>
          <button type="button" onClick={() => { void loadPage(); }}>重试</button>
        </div>
      )}

      {openError && <p className="history-error" role="status">{openError}</p>}

      {cursor && !loading && !listError && (
        <button
          type="button"
          className="history-more"
          onClick={() => { void loadPage(cursor); }}
        >
          加载更多
        </button>
      )}
    </nav>
  );
}
