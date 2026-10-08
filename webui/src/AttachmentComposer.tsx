import { useRef, useState, type ChangeEvent } from 'react';
import { Paperclip, RefreshCw, X } from 'lucide-react';
import type { AttachmentDraft } from './attachments';
import { AttachmentKindIcon, formatAttachmentSize } from './AttachmentVisuals';

export const ATTACHMENT_ACCEPT = [
  '.jpg', '.jpeg', '.png', '.webp', '.pdf', '.docx', '.xlsx', '.csv',
  '.txt', '.md', '.log', '.py', '.js', '.ts', '.tsx', '.jsx', '.c',
  '.cc', '.cpp', '.h', '.hpp', '.java',
].join(',');
const DOCUMENT_ATTACHMENT_ACCEPT = ATTACHMENT_ACCEPT.split(',')
  .filter((item) => !['.jpg', '.jpeg', '.png', '.webp'].includes(item))
  .join(',');

interface AttachmentComposerProps {
  drafts: AttachmentDraft[];
  visionEnabled?: boolean;
  disabled?: boolean;
  onFiles: (files: File[]) => Promise<void> | void;
  onFileError: (error: unknown) => void;
  onRemove: (draft: AttachmentDraft) => void;
  onRetry: (draft: AttachmentDraft) => void;
}

function statusLabel(draft: AttachmentDraft): string {
  if (draft.status === 'uploading' || draft.status === 'selected') return '上传并解析中';
  if (draft.status === 'failed') return draft.error || '处理失败';
  if (draft.warnings?.includes('ocr_unavailable')) return '已就绪（OCR 不可用）';
  return '已就绪';
}

export function AttachmentComposer({
  drafts, visionEnabled = false, disabled = false, onFiles, onFileError, onRemove, onRetry,
}: AttachmentComposerProps) {
  const pickerPendingRef = useRef(false);
  const [pickerPending, setPickerPending] = useState(false);

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    if (pickerPendingRef.current) return;
    const files = Array.from(input.files || []);
    if (!files.length) {
      input.value = '';
      return;
    }
    pickerPendingRef.current = true;
    setPickerPending(true);
    try {
      await onFiles(files);
    } catch (fileError) {
      onFileError(fileError);
    } finally {
      input.value = '';
      pickerPendingRef.current = false;
      setPickerPending(false);
    }
  }

  return (
    <div className="attachment-composer">
      <label className="attachment-picker">
        <Paperclip size={16} />
        <span>{visionEnabled ? '添加图片或附件' : '添加附件'}</span>
        <input
          type="file"
          multiple
          accept={visionEnabled ? ATTACHMENT_ACCEPT : DOCUMENT_ATTACHMENT_ACCEPT}
          disabled={disabled}
          aria-busy={pickerPending}
          onClick={(event) => {
            if (pickerPendingRef.current) event.preventDefault();
          }}
          onChange={choose}
        />
      </label>
      {drafts.length > 0 && (
        <div className="attachment-drafts" role="list">
          {drafts.map((draft) => (
            <div className={`attachment-card ${draft.status}`} role="listitem" key={draft.clientId}>
              {draft.kind === 'image' && draft.previewUrl
                ? <img className="attachment-thumbnail" src={draft.previewUrl} alt="" />
                : <AttachmentKindIcon kind={draft.kind} />}
              <span className="attachment-card-copy">
                <strong>{draft.displayName}</strong>
                <small>{draft.kind} · {formatAttachmentSize(draft.sizeBytes)}</small>
                <small role="status">{statusLabel(draft)}</small>
              </span>
              {draft.status === 'failed' && (
                <button type="button" onClick={() => onRetry(draft)} aria-label={`重试 ${draft.displayName}`}>
                  <RefreshCw size={14} />
                </button>
              )}
              <button type="button" onClick={() => onRemove(draft)} aria-label={`移除 ${draft.displayName}`}>
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
      {!visionEnabled && (
        <p className="attachment-disclosure">
          当前环境图片理解未启用；仍可上传 PDF、Office、文本、日志和代码附件。
        </p>
      )}
    </div>
  );
}
