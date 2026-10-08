import {
  FormEvent, type MouseEvent, useEffect, useRef, useState,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity, AlertTriangle, Check, CircleAlert, Copy, ThumbsDown, ThumbsUp, X,
} from 'lucide-react';
import { buildAnswerFooterMeta } from './answerFooter';
import { AttachmentKindIcon, formatAttachmentSize } from './AttachmentVisuals';
import {
  FEEDBACK_REASON_OPTIONS,
  canSendFeedback,
} from './feedbackPayload';
import { buildThinkingSteps } from './thinkingSteps';
import { copyTextToClipboard } from './clipboard';
import type { ChatMessage } from './types';
import type { SentAttachment } from './attachments';
import type { FeedbackDraft, FeedbackReasonCode } from './feedbackPayload';
import type { ThinkingStep } from './thinkingSteps';

interface MessageArticleProps {
  message: ChatMessage;
  onSelect: () => void;
  onFeedback?: (draft: FeedbackDraft) => Promise<void> | void;
}

export function MessageArticle({ message, onSelect, onFeedback }: MessageArticleProps) {
  const [previewAttachment, setPreviewAttachment] = useState<SentAttachment | null>(null);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'error'>('idle');
  const [copyPending, setCopyPending] = useState(false);
  const previewDialogRef = useRef<HTMLDialogElement>(null);
  const previewCloseRef = useRef<HTMLButtonElement>(null);
  const previewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const copyResetTimerRef = useRef<number | undefined>(undefined);
  const copyPendingRef = useRef(false);
  const mountedRef = useRef(true);
  const answerFooter = buildAnswerFooterMeta(message);
  const thinkingSteps = message.role === 'assistant' ? buildThinkingSteps(message) : [];
  const hasThinking = message.role === 'assistant' && thinkingSteps.length > 0;
  const isComplete = Boolean(message.done);
  const thinkingSummary = hasThinking ? buildThinkingSummary(thinkingSteps) : '';
  const shouldShowFeedback = canSendFeedback(message) && Boolean(onFeedback);

  useEffect(() => {
    if (!previewAttachment) return;
    const dialog = previewDialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    previewCloseRef.current?.focus();
  }, [previewAttachment]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      copyPendingRef.current = false;
      if (copyResetTimerRef.current !== undefined) {
        window.clearTimeout(copyResetTimerRef.current);
      }
    };
  }, []);

  async function copyAnswer(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation();
    if (!isComplete || !message.content.trim() || copyPendingRef.current) return;
    copyPendingRef.current = true;
    setCopyPending(true);
    setCopyState('idle');
    if (copyResetTimerRef.current !== undefined) {
      window.clearTimeout(copyResetTimerRef.current);
      copyResetTimerRef.current = undefined;
    }
    try {
      await copyTextToClipboard(message.content);
      if (!mountedRef.current) return;
      setCopyState('copied');
      copyResetTimerRef.current = window.setTimeout(() => {
        setCopyState('idle');
        copyResetTimerRef.current = undefined;
      }, 2000);
    } catch {
      if (mountedRef.current) setCopyState('error');
    } finally {
      copyPendingRef.current = false;
      if (mountedRef.current) setCopyPending(false);
    }
  }

  function restorePreviewFocus() {
    const trigger = previewTriggerRef.current;
    previewTriggerRef.current = null;
    if (trigger?.isConnected) trigger.focus();
  }

  function closePreview() {
    const dialog = previewDialogRef.current;
    if (dialog?.open) dialog.close();
    setPreviewAttachment(null);
    restorePreviewFocus();
  }

  return (
    <article
      className={`message ${message.role}`}
      data-message-id={message.id}
      onClick={() => message.role === 'assistant' && onSelect()}
    >
      <div className="message-role">{message.role === 'user' ? 'User' : 'AI DAQ FAE'}</div>

      {hasThinking && !isComplete && (
        <ThinkingPanel steps={thinkingSteps} summary={thinkingSummary} />
      )}

      <div className="markdown-body">
        {message.content ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
        ) : message.restored ? (
          <p className="restored-empty-content">未保存回答内容</p>
        ) : (
          <div className="stream-placeholder">
            <Activity size={16} />
            <span>Agent is working</span>
          </div>
        )}
      </div>

      {message.role === 'user' && message.attachments && message.attachments.length > 0 && (
        <div className="sent-attachments" aria-label="本轮附件">
          {message.attachments.map((attachment) => {
            const hasImagePreview = attachment.kind === 'image' && attachment.previewUrl;
            return (
              <div className="sent-attachment" key={attachment.sourceId}>
                {hasImagePreview ? (
                  <button
                    type="button"
                    className="sent-attachment-visual"
                    aria-label={`查看 ${attachment.displayName} 大图`}
                    onClick={(event) => {
                      event.stopPropagation();
                      previewTriggerRef.current = event.currentTarget;
                      setPreviewAttachment(attachment);
                    }}
                  >
                    <img
                      className="attachment-thumbnail"
                      src={attachment.previewUrl}
                      alt=""
                    />
                  </button>
                ) : (
                  <span className="sent-attachment-visual">
                    <AttachmentKindIcon kind={attachment.kind} />
                  </span>
                )}
                <span className="sent-attachment-copy">
                  <strong>{attachment.displayName}</strong>
                  <small>{attachment.kind} · {formatAttachmentSize(attachment.sizeBytes)}</small>
                </span>
              </div>
            );
          })}
        </div>
      )}

      {previewAttachment?.previewUrl && (
        <dialog
          ref={previewDialogRef}
          className="attachment-preview-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`${previewAttachment.displayName} 大图预览`}
          onCancel={(event) => {
            event.preventDefault();
            closePreview();
          }}
          onClose={() => {
            setPreviewAttachment(null);
            restorePreviewFocus();
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            closePreview();
          }}
          onClick={(event) => {
            if (event.target !== event.currentTarget) return;
            event.stopPropagation();
            closePreview();
          }}
        >
          <div
            className="attachment-preview-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="attachment-preview-heading">
              <strong>{previewAttachment.displayName}</strong>
              <button
                ref={previewCloseRef}
                type="button"
                aria-label={`关闭 ${previewAttachment.displayName} 大图`}
                onClick={closePreview}
              >
                <X size={18} />
              </button>
            </div>
            <img
              className="attachment-preview-image"
              src={previewAttachment.previewUrl}
              alt={`${previewAttachment.displayName} 大图`}
            />
          </div>
        </dialog>
      )}

      {message.role === 'assistant' && (
        <div className="answer-footer">
          {answerFooter && <span>{answerFooter.lengthText}</span>}
          {answerFooter?.fallbackReason && (
            <span className="warning-pill">
              <AlertTriangle size={14} />
              {answerFooter.fallbackReason}
            </span>
          )}
          <div className="copy-controls" onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className={`copy-answer-button ${copyState}`}
              aria-label={copyPending
                ? '正在复制'
                : copyState === 'copied'
                ? '已复制'
                : copyState === 'error' ? '复制失败' : '复制回答'}
              title={copyPending
                ? '正在复制'
                : copyState === 'copied'
                ? '已复制'
                : copyState === 'error' ? '复制失败' : '复制回答'}
              disabled={copyPending || !isComplete || !message.content.trim()}
              onClick={copyAnswer}
            >
              {copyState === 'copied' ? <Check size={15} />
                : copyState === 'error' ? <CircleAlert size={15} />
                  : <Copy size={15} />}
            </button>
            {copyState !== 'idle' && (
              <span
                className={copyState === 'error' ? 'copy-state error' : 'copy-state'}
                role="status"
              >
                {copyState === 'copied' ? '已复制' : '复制失败'}
              </span>
            )}
          </div>
          {shouldShowFeedback && onFeedback && (
            <FeedbackControls
              feedback={message.feedback}
              onFeedback={onFeedback}
            />
          )}
        </div>
      )}

      {hasThinking && isComplete && (
        <ThinkingPanel steps={thinkingSteps} summary={thinkingSummary} collapsed />
      )}
    </article>
  );
}

interface FeedbackControlsProps {
  feedback?: ChatMessage['feedback'];
  onFeedback: (draft: FeedbackDraft) => Promise<void> | void;
}

function FeedbackControls({ feedback, onFeedback }: FeedbackControlsProps) {
  const [issueOpen, setIssueOpen] = useState(false);
  const [reasonCode, setReasonCode] = useState<FeedbackReasonCode>('fact_error');
  const [comment, setComment] = useState('');
  const isSubmitting = feedback?.status === 'submitting';
  const isGood = feedback?.status === 'submitted' && feedback.rating === 'good';
  const isBad = feedback?.status === 'submitted' && feedback.rating === 'bad';

  async function submitGood() {
    try {
      await onFeedback({ rating: 'good' });
      setIssueOpen(false);
    } catch {
      // Error details are reflected through message.feedback.
    }
  }

  async function submitIssue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await onFeedback({
        rating: 'bad',
        reasonCode,
        comment,
      });
      setIssueOpen(false);
    } catch {
      // Error details are reflected through message.feedback.
    }
  }

  return (
    <div className="feedback-controls" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className={`feedback-icon-button ${isGood ? 'active' : ''}`}
        aria-label="有用"
        title="有用"
        disabled={isSubmitting}
        onClick={submitGood}
      >
        <ThumbsUp size={15} />
      </button>
      <button
        type="button"
        className={`feedback-icon-button ${isBad ? 'active' : ''}`}
        aria-label="不达标"
        title="不达标"
        disabled={isSubmitting}
        onClick={() => setIssueOpen((current) => !current)}
      >
        <ThumbsDown size={15} />
      </button>
      {feedback?.status === 'submitted' && (
        <span className="feedback-state">已记录</span>
      )}
      {feedback?.status === 'error' && (
        <span className="feedback-error">{feedback.error || '反馈提交失败'}</span>
      )}

      {issueOpen && (
        <form className="feedback-issue-form" onSubmit={submitIssue}>
          <select
            aria-label="问题类型"
            value={reasonCode}
            disabled={isSubmitting}
            onChange={(event) => setReasonCode(event.target.value as FeedbackReasonCode)}
          >
            {FEEDBACK_REASON_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <textarea
            aria-label="补充原因"
            value={comment}
            disabled={isSubmitting}
            onChange={(event) => setComment(event.target.value)}
            placeholder="补充具体原因，便于后续复盘"
            rows={3}
          />
          <div className="feedback-issue-actions">
            <button
              type="button"
              className="feedback-text-button"
              disabled={isSubmitting}
              onClick={() => setIssueOpen(false)}
            >
              取消
            </button>
            <button
              type="submit"
              className="feedback-submit-button"
              disabled={isSubmitting}
            >
              提交
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

interface ThinkingPanelProps {
  steps: ThinkingStep[];
  summary: string;
  collapsed?: boolean;
}

function ThinkingPanel({ steps, summary, collapsed = false }: ThinkingPanelProps) {
  const body = (
    <div className="message-thinking-list">
      {steps.map((step) => (
        <div className={`message-thinking-step ${step.status}`} key={step.id}>
          <span className="message-thinking-dot" />
          <div>
            <strong>{step.title}</strong>
            <p>{step.detail}</p>
            {step.elapsedMs !== undefined && <small>{step.elapsedMs} ms</small>}
          </div>
        </div>
      ))}
    </div>
  );

  if (collapsed) {
    return (
      <details className="message-thinking collapsed" aria-label="AI DAQ FAE Thinking">
        <summary className="message-thinking-heading">
          <Activity size={14} />
          <span>AI DAQ FAE Thinking</span>
          <small>{summary}</small>
        </summary>
        {body}
      </details>
    );
  }

  return (
    <div className="message-thinking" aria-label="AI DAQ FAE Thinking">
      <div className="message-thinking-heading">
        <Activity size={14} />
        <span>AI DAQ FAE Thinking</span>
        <small>{summary}</small>
      </div>
      {body}
    </div>
  );
}

function buildThinkingSummary(steps: ThinkingStep[]): string {
  const parts = [`${steps.length} steps`];
  const maxElapsed = Math.max(...steps.map((step) => step.elapsedMs || 0));
  if (maxElapsed > 0) {
    parts.push(formatElapsed(maxElapsed));
  }

  const capabilities = steps.find((step) => step.id === 'capabilities')?.detail;
  if (capabilities) {
    parts.push(capabilities);
  }

  return parts.join(' · ');
}

function formatElapsed(ms: number): string {
  if (ms >= 1000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  return `${ms} ms`;
}
