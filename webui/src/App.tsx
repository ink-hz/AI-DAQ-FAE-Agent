import {
  FormEvent, useEffect, useMemo, useReducer, useRef, useState,
} from 'react';
import {
  RotateCcw,
  Send,
  Square,
} from 'lucide-react';
import {
  ChatStreamError,
  deleteAttachment,
  fetchAuthenticatedConversation,
  fetchAttachmentCapabilities,
  fetchIdentityCapabilities,
  sendFeedback,
  streamChat,
  uploadAttachments,
} from './api';
import { AttachmentComposer } from './AttachmentComposer';
import { AttachmentInputSurface } from './AttachmentInputSurface';
import {
  attachmentReducer,
  canSendWithAttachments,
  createAttachmentDrafts,
  freezeSentAttachments,
  validateAttachmentSelection,
} from './attachments';
import { AuthenticatedSessionNav } from './AuthenticatedSessionNav';
import { BRAND_LOGO_PATH } from './brandAssets';
import { shouldSubmitComposer } from './composerKeys';
import { currentAuthenticatedAccount } from './enterpriseIdentity';
import { buildFeedbackPayload } from './feedbackPayload';
import { MessageArticle } from './MessageArticle';
import { ReviewCenter } from './ReviewCenter';
import { parseFaeBrowserRoute, surfaceRootPath } from './routes';
import { buildTurnSummaries } from './sessionNav';
import { latestSessionHint, sessionHintText } from './sessionHint';
import { mergeStreamFailure } from './streamFailure';
import {
  normalizeRestoredConversation,
  type RestoredConversation,
} from './authenticatedHistory';
import type { ChatMessage, SseEvent } from './types';
import type { FeedbackDraft } from './feedbackPayload';
import type { AttachmentDraft } from './attachments';
import { FaePageAccessReporter } from './pageAccessReporter';
import { FaeWorkspaceActions } from './FaeWorkspaceActions';

function newId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

type AppProps = {
  initialPath?: string;
};

type RoutedLocation = {
  pathname: string;
  version: number;
};

function getCurrentPath(): string {
  if (typeof window === 'undefined') return '/app/';
  const { pathname } = window.location;
  return pathname.startsWith('/app') || pathname.startsWith('/daq') ? pathname : '/app/';
}

export default function App({ initialPath }: AppProps = {}) {
  const [location, setLocation] = useState<RoutedLocation>(() => ({
    pathname: initialPath ?? getCurrentPath(),
    version: 0,
  }));

  useEffect(() => {
    function onPopState() {
      setLocation((current) => ({
        pathname: getCurrentPath(),
        version: current.version + 1,
      }));
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const route = parseFaeBrowserRoute(location.pathname);
  if (route.name === 'review') {
    return <ReviewCenter />;
  }
  if (route.name === 'not-found') {
    return <NotFoundPage />;
  }
  return (
    <ChatWorkspace
        routeSessionId={route.name === 'chat' ? route.sessionId : undefined}
        routeVersion={location.version}
      />
  );
}

function NotFoundPage() {
  return (
    <main className="identity-gate">
      <h1>FAE 页面不存在</h1>
      <p>请返回 FAE 工作区重新打开会话。</p>
    </main>
  );
}

function ChatWorkspace({
  routeSessionId,
  routeVersion,
}: {
  routeSessionId?: string;
  routeVersion: number;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeAssistantId, setActiveAssistantId] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [partnerLoginAvailable, setPartnerLoginAvailable] = useState(false);
  const [attachmentDrafts, dispatchAttachments] = useReducer(attachmentReducer, []);
  const abortRef = useRef<AbortController | null>(null);
  const uploadAbortRef = useRef(new Map<string, AbortController>());
  const previewUrlsRef = useRef(new Set<string>());
  const restoreVersionRef = useRef(0);
  // Server-side projection only: the browser never supplies its own account data.
  const account = currentAuthenticatedAccount();

  const activeAssistant = useMemo(() => {
    if (activeAssistantId) {
      const found = messages.find((message) => message.id === activeAssistantId);
      if (found?.role === 'assistant') return found;
    }
    return [...messages].reverse().find((message) => message.role === 'assistant');
  }, [activeAssistantId, messages]);
  const turnSummaries = useMemo(() => buildTurnSummaries(messages), [messages]);
  const sessionHint = useMemo(() => latestSessionHint(messages), [messages]);

  useEffect(() => {
    let active = true;
    void fetchAttachmentCapabilities()
      .then((capabilities) => {
        if (active) setVisionEnabled(capabilities.visionEnabled);
      })
      .catch(() => {
        if (active) setVisionEnabled(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => () => {
    previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewUrlsRef.current.clear();
  }, []);

  useEffect(() => {
    // The control only exists for anonymous visitors, and only when the server
    // says the route is configured. Any other answer renders nothing at all.
    if (account) return undefined;
    let active = true;
    void fetchIdentityCapabilities().then((capabilities) => {
      if (active) setPartnerLoginAvailable(capabilities.partnerLoginAvailable);
    });
    return () => { active = false; };
  }, [account]);

  useEffect(() => {
    let active = true;
    async function restoreSession(targetSessionId: string) {
      const restoreVersion = restoreVersionRef.current + 1;
      restoreVersionRef.current = restoreVersion;
      try {
        const detail = await fetchAuthenticatedConversation(targetSessionId);
        if (!active || restoreVersionRef.current !== restoreVersion) return;
        openRestoredConversation(normalizeRestoredConversation(detail));
      } catch {
        if (active && restoreVersionRef.current === restoreVersion) {
          setError('会话打开失败');
        }
      }
    }
    if (routeSessionId) {
      void restoreSession(routeSessionId);
    } else if (routeVersion > 0) {
      resetSession({ updateHistory: false });
    }
    return () => {
      active = false;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId, routeVersion]);

  function releasePreviewUrl(url?: string) {
    if (!url || !previewUrlsRef.current.delete(url)) return;
    URL.revokeObjectURL(url);
  }

  function releaseAllPreviewUrls() {
    previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewUrlsRef.current.clear();
  }

  async function uploadDrafts(drafts: AttachmentDraft[]) {
    if (!drafts.length) return;
    const abort = new AbortController();
    const clientIds = drafts.map((draft) => draft.clientId);
    clientIds.forEach((id) => uploadAbortRef.current.set(id, abort));
    dispatchAttachments({ type: 'uploading', clientIds });
    try {
      const results = await uploadAttachments(drafts.map((draft) => draft.file), abort.signal);
      dispatchAttachments({
        type: 'upload_results', clientIds, results,
      });
    } catch (uploadError) {
      if (!abort.signal.aborted) {
        dispatchAttachments({
          type: 'failed',
          clientIds,
          error: uploadError instanceof Error ? uploadError.message : String(uploadError),
        });
      }
    } finally {
      clientIds.forEach((id) => {
        if (uploadAbortRef.current.get(id) === abort) uploadAbortRef.current.delete(id);
      });
    }
  }

  async function addAttachmentFiles(files: File[]): Promise<void> {
    if (!files.length) return;
    const allocatedPreviewUrls: string[] = [];
    try {
      validateAttachmentSelection([
        ...attachmentDrafts.map((item) => item.file),
        ...files,
      ], visionEnabled);
      const added = createAttachmentDrafts(files).map((draft) => {
        if (draft.kind !== 'image') return draft;
        const previewUrl = URL.createObjectURL(draft.file);
        allocatedPreviewUrls.push(previewUrl);
        previewUrlsRef.current.add(previewUrl);
        return { ...draft, previewUrl };
      });
      dispatchAttachments({ type: 'add', drafts: added });
      setError(null);
      await uploadDrafts(added);
    } catch (selectionError) {
      allocatedPreviewUrls.forEach((url) => releasePreviewUrl(url));
      setError(selectionError instanceof Error ? selectionError.message : String(selectionError));
    }
  }

  function reportAttachmentFileError(fileError: unknown) {
    setError(fileError instanceof Error ? fileError.message : String(fileError));
  }

  function removeAttachment(draft: AttachmentDraft) {
    const controller = uploadAbortRef.current.get(draft.clientId);
    if (controller) {
      const relatedIds = Array.from(uploadAbortRef.current.entries())
        .filter(([, value]) => value === controller)
        .map(([id]) => id);
      controller.abort();
      relatedIds.forEach((id) => uploadAbortRef.current.delete(id));
      dispatchAttachments({
        type: 'failed',
        clientIds: relatedIds.filter((id) => id !== draft.clientId),
        error: '同批上传已取消，请重试',
      });
    }
    releasePreviewUrl(draft.previewUrl);
    dispatchAttachments({ type: 'remove', clientId: draft.clientId });
    if (draft.attachmentId) void deleteAttachment(draft.attachmentId).catch(() => undefined);
  }

  function retryAttachment(draft: AttachmentDraft) {
    void uploadDrafts([draft]);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!canSendWithAttachments(text, attachmentDrafts, isStreaming)) return;

    const sentAttachments = freezeSentAttachments(attachmentDrafts);
    const attachmentIds = attachmentDrafts
      .filter((item) => item.status === 'ready' && item.attachmentId)
      .map((item) => item.attachmentId as string);

    const userMessage: ChatMessage = {
      id: newId('user'),
      role: 'user',
      content: text,
      createdAt: Date.now(),
      attachments: sentAttachments,
    };
    const assistantId = newId('assistant');
    const assistantMessage: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
      sources: [],
      stages: [],
    };
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setActiveAssistantId(assistantId);
    setInput('');
    dispatchAttachments({ type: 'clear' });
    setError(null);
    setIsStreaming(true);

    const abort = new AbortController();
    abortRef.current = abort;

    try {
      await streamChat({
        message: text,
        sessionId,
        channel: 'fae',
        clientRequestId: newId('request'),
        attachmentIds,
        signal: abort.signal,
        onEvent: (_event: SseEvent, turn) => {
          if (turn.sessionId) setSessionId(turn.sessionId);
          setMessages((current) => current.map((message) => {
            if (message.id !== assistantId) return message;
            return {
              ...message,
              content: turn.content,
              sources: turn.sources,
              stages: turn.stages,
              done: turn.done,
            };
          }));
        },
      });
    } catch (err) {
      if (!abort.signal.aborted) {
        if (err instanceof ChatStreamError) {
          const shortRequestId = err.clientRequestId.slice(0, 8);
          setError(`${err.phase}: ${err.message}（请求 ${shortRequestId}）`);
          if (err.partialTurn.sessionId) setSessionId(err.partialTurn.sessionId);
          setMessages((current) => current.map((item) => (
            item.id === assistantId ? mergeStreamFailure(item, err) : item
          )));
        } else {
          const message = err instanceof Error ? err.message : String(err);
          setError(message);
          setMessages((current) => current.map((item) => (
            item.id === assistantId
              ? { ...item, content: `请求失败：${message}` }
              : item
          )));
        }
      }
    } finally {
      setIsStreaming(false);
      abortRef.current = null;
    }
  }

  function resetSession(options: { updateHistory?: boolean } = {}) {
    restoreVersionRef.current += 1;
    abortRef.current?.abort();
    discardUnsentAttachments();
    setMessages([]);
    setSessionId(undefined);
    setActiveAssistantId(undefined);
    setError(null);
    setIsStreaming(false);
    if (options.updateHistory !== false) {
      window.history.replaceState(null, '', surfaceRootPath());
    }
  }

  function discardUnsentAttachments() {
    uploadAbortRef.current.forEach((controller) => controller.abort());
    uploadAbortRef.current.clear();
    // Only drafts that were never sent are removed server-side. Attachments that
    // already belong to a persisted conversation are never deleted from here.
    attachmentDrafts.forEach((draft) => {
      if (draft.attachmentId) void deleteAttachment(draft.attachmentId).catch(() => undefined);
    });
    releaseAllPreviewUrls();
    dispatchAttachments({ type: 'clear' });
  }

  function openRestoredConversation(conversation: RestoredConversation) {
    // Replace the workspace in place: the next /chat continues this exact
    // session instead of starting a parallel one.
    abortRef.current?.abort();
    abortRef.current = null;
    discardUnsentAttachments();
    setInput('');
    setMessages(conversation.messages);
    setSessionId(conversation.sessionId);
    setActiveAssistantId(undefined);
    setError(null);
    setIsStreaming(false);
  }

  function stopStreaming() {
    abortRef.current?.abort();
    setIsStreaming(false);
  }

  async function handleFeedback(
    messageId: string,
    messageIndex: number,
    draft: FeedbackDraft,
  ) {
    const target = messages.find((message) => message.id === messageId);
    if (!target) return;

    setMessages((current) => current.map((message) => (
      message.id === messageId
        ? {
          ...message,
          feedback: {
            rating: draft.rating,
            reasonCode: draft.reasonCode,
            comment: draft.comment,
            status: 'submitting',
          },
        }
        : message
    )));

    try {
      const payload = buildFeedbackPayload({
        message: target,
        messageIndex,
        fallbackSessionId: sessionId,
        rating: draft.rating,
        reasonCode: draft.reasonCode,
        comment: draft.comment,
      });
      await sendFeedback(payload);
      setMessages((current) => current.map((message) => (
        message.id === messageId
          ? {
            ...message,
            feedback: {
              rating: draft.rating,
              reasonCode: draft.reasonCode,
              comment: draft.comment?.trim() || undefined,
              status: 'submitted',
            },
          }
          : message
      )));
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      setMessages((current) => current.map((message) => (
        message.id === messageId
          ? {
            ...message,
            feedback: {
              rating: draft.rating,
              reasonCode: draft.reasonCode,
              comment: draft.comment,
              status: 'error',
              error: errorMessage,
            },
          }
          : message
      )));
      throw err;
    }
  }

  function selectAssistantTurn(messageId: string) {
    setActiveAssistantId(messageId);
    const messageElement = Array.from(
      document.querySelectorAll<HTMLElement>('[data-message-id]'),
    ).find((element) => element.dataset.messageId === messageId);
    messageElement?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  return (
    <main className="app-shell">
      <FaePageAccessReporter
        account={account}
        route={{ name: 'chat', sessionId: routeSessionId ?? sessionId }}
      />
      <aside className="conversation-rail">
        <div className="brand-block">
          <div className="brand-title">
            <img className="brand-logo" src={BRAND_LOGO_PATH} alt="" aria-hidden="true" />
            <div className="brand-copy">
              <div className="brand-eyebrow">Orbbec</div>
              <h1 className="brand-name">AI DAQ FAE Agent</h1>
            </div>
          </div>
          <button className="icon-button brand-reset" onClick={() => resetSession()} title="新会话" aria-label="新会话">
            <RotateCcw size={16} />
          </button>
        </div>

        <div className="session-meta">
          <span>Session</span>
          <code>{sessionId ? sessionId.slice(0, 12) : 'new'}</code>
        </div>

        {account && (
          <div className="account-block">
            <span className="account-name">{account.displayName}</span>
            {account.partnerDisplayName && (
              <span className="account-partner">{account.partnerDisplayName}</span>
            )}
          </div>
        )}

        {!account && partnerLoginAvailable && (
          <a className="partner-login-link" href="/partner/login">合作方客服登录</a>
        )}

        {account && <AuthenticatedSessionNav onOpen={openRestoredConversation} />}

        <nav className="turn-list" aria-label="对话列表">
          {turnSummaries.map((turn) => (
            <button
              key={turn.id}
              className={`turn-item ${turn.id === activeAssistant?.id ? 'active' : ''}`}
              onClick={() => selectAssistantTurn(turn.id)}
              title={turn.title}
            >
              <span className="turn-index">{turn.index}</span>
              <span className="turn-copy">
                <span className="turn-title">{turn.title}</span>
                <small className={turn.isFallback ? 'turn-meta warning' : 'turn-meta'}>
                  {turn.meta}
                </small>
              </span>
            </button>
          ))}
        </nav>
      </aside>

      <section className="chat-workspace">
        <FaeWorkspaceActions account={account} />
        <div className="message-list" aria-live="polite">
          {messages.length === 0 && (
            <div className="empty-state">
              <h2>AI DAQ FAE 技术咨询</h2>
              <p>输入采集设备、组合连接、Viewer/SDK、录制流程或排障问题。</p>
            </div>
          )}

          {messages.map((message, index) => (
            <MessageArticle
              key={message.id}
              message={message}
              onSelect={() => setActiveAssistantId(message.id)}
              onFeedback={(draft) => handleFeedback(message.id, index, draft)}
            />
          ))}
        </div>

        {error && <div className="error-line">{error}</div>}

        {sessionHint && !isStreaming && (
          <div className="session-hint-line" role="status">
            <span>{sessionHintText(sessionHint)}</span>
            <button type="button" className="session-hint-action" onClick={() => resetSession()}>
              开启新会话
            </button>
          </div>
        )}

        <AttachmentInputSurface
          className="composer"
          data-attachment-input-surface="true"
          onSubmit={handleSubmit}
          onFiles={addAttachmentFiles}
          onFileError={reportAttachmentFileError}
          fileInputDisabled={isStreaming}
        >
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (!shouldSubmitComposer({
                key: event.key,
                shiftKey: event.shiftKey,
                ctrlKey: event.ctrlKey,
                metaKey: event.metaKey,
                altKey: event.altKey,
                isComposing: event.nativeEvent.isComposing,
              })) {
                return;
              }
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }}
            placeholder="描述设备组合、软件版本、采集任务或现场问题"
            rows={3}
          />
          <AttachmentComposer
            drafts={attachmentDrafts}
            visionEnabled={visionEnabled}
            disabled={isStreaming}
            onFiles={addAttachmentFiles}
            onFileError={reportAttachmentFileError}
            onRemove={removeAttachment}
            onRetry={retryAttachment}
          />
          <div className="composer-actions">
            {isStreaming ? (
              <button type="button" className="secondary-button" onClick={stopStreaming}>
                <Square size={16} />
                Stop
              </button>
            ) : (
              <button
                type="submit"
                className="primary-button"
                disabled={!canSendWithAttachments(input, attachmentDrafts, isStreaming)}
              >
                <Send size={16} />
                Send
              </button>
            )}
          </div>
        </AttachmentInputSurface>
      </section>
    </main>
  );
}
