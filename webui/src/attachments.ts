export const MAX_ATTACHMENT_FILES = 5;
export const MAX_ATTACHMENT_BATCH_BYTES = 50 * 1024 * 1024;

export type AttachmentKind = 'image' | 'pdf' | 'document' | 'spreadsheet' | 'text' | 'code';
export type AttachmentDraftStatus = 'selected' | 'uploading' | 'ready' | 'failed';

export interface AttachmentDraft {
  clientId: string;
  file: File;
  displayName: string;
  kind: AttachmentKind;
  sizeBytes: number;
  previewUrl?: string;
  status: AttachmentDraftStatus;
  attachmentId?: string;
  sourceId?: string;
  parseCoverage?: 'full' | 'partial' | 'empty';
  warnings?: string[];
  error?: string;
}

export interface SentAttachment {
  sourceId: string;
  displayName: string;
  kind: AttachmentKind;
  sizeBytes: number;
  previewUrl?: string;
  statusAtSend: 'ready';
}

export interface PublicAttachment {
  attachment_id: string;
  source_id: string;
  display_name: string;
  kind: AttachmentKind;
  status: string;
  parse_coverage: 'full' | 'partial' | 'empty';
  warnings: string[];
}

export type AttachmentUploadResult =
  | { ok: true; attachment: PublicAttachment }
  | { ok: false; error: { code: string; message: string } };

export type AttachmentAction =
  | { type: 'add'; drafts: AttachmentDraft[] }
  | { type: 'uploading'; clientIds: string[] }
  | { type: 'upload_results'; clientIds: string[]; results: AttachmentUploadResult[] }
  | { type: 'failed'; clientIds: string[]; error: string }
  | { type: 'remove'; clientId: string }
  | { type: 'clear' };

function clientId(): string {
  return `attachment-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function safeAttachmentDisplayName(filename: string): string {
  const leaf = (filename || '').replace(/\\/g, '/').split('/').pop() || '';
  const cleaned = leaf.replace(/[\u0000-\u001f\u007f-\u009f]/g, '').trim();
  return cleaned.slice(0, 255) || 'attachment';
}

function inferKind(file: File): AttachmentKind {
  const extension = file.name.toLowerCase().split('.').pop() || '';
  if (file.type.startsWith('image/')) return 'image';
  if (extension === 'pdf') return 'pdf';
  if (extension === 'docx') return 'document';
  if (['xlsx', 'csv'].includes(extension)) return 'spreadsheet';
  if (['py', 'js', 'ts', 'tsx', 'jsx', 'c', 'cc', 'cpp', 'h', 'hpp', 'java'].includes(extension)) {
    return 'code';
  }
  return 'text';
}

export function validateAttachmentSelection(files: File[], allowImages = true): void {
  if (!allowImages && files.some((file) => inferKind(file) === 'image')) {
    throw new Error('当前环境图片理解未启用，请改用文档附件');
  }
  if (files.length > MAX_ATTACHMENT_FILES) {
    throw new Error('每轮最多 5 个附件');
  }
  const total = files.reduce((sum, item) => sum + item.size, 0);
  if (total > MAX_ATTACHMENT_BATCH_BYTES) {
    throw new Error('每轮附件总大小不能超过 50 MiB');
  }
}

export function createAttachmentDrafts(files: File[]): AttachmentDraft[] {
  validateAttachmentSelection(files);
  return files.map((file) => ({
    clientId: clientId(),
    file,
    displayName: safeAttachmentDisplayName(file.name),
    kind: inferKind(file),
    sizeBytes: file.size,
    status: 'selected',
  }));
}

export function attachmentReducer(
  state: AttachmentDraft[], action: AttachmentAction,
): AttachmentDraft[] {
  if (action.type === 'add') return [...state, ...action.drafts];
  if (action.type === 'clear') return [];
  if (action.type === 'remove') return state.filter((item) => item.clientId !== action.clientId);
  if (action.type === 'uploading') {
    const selected = new Set(action.clientIds);
    return state.map((item) => selected.has(item.clientId)
      ? { ...item, status: 'uploading', error: undefined }
      : item);
  }
  if (action.type === 'failed') {
    const selected = new Set(action.clientIds);
    return state.map((item) => selected.has(item.clientId)
      ? { ...item, status: 'failed', error: action.error }
      : item);
  }
  const byClientId = new Map(action.clientIds.map((id, index) => [id, action.results[index]]));
  return state.map((item) => {
    const result = byClientId.get(item.clientId);
    if (!result) return item;
    if (!result.ok) return { ...item, status: 'failed', error: result.error.message };
    return {
      ...item,
      status: 'ready',
      attachmentId: result.attachment.attachment_id,
      sourceId: result.attachment.source_id,
      displayName: result.attachment.display_name,
      kind: result.attachment.kind,
      parseCoverage: result.attachment.parse_coverage,
      warnings: result.attachment.warnings,
      error: undefined,
    };
  });
}

export function buildChatPayload(message: string, drafts: AttachmentDraft[]) {
  return {
    message,
    attachment_ids: drafts
      .filter((item) => item.status === 'ready' && item.attachmentId)
      .map((item) => item.attachmentId as string),
  };
}

export function freezeSentAttachments(drafts: AttachmentDraft[]): SentAttachment[] {
  return drafts
    .filter((item) => item.status === 'ready' && item.sourceId)
    .map((item) => ({
      sourceId: item.sourceId as string,
      displayName: item.displayName,
      kind: item.kind,
      sizeBytes: item.sizeBytes,
      previewUrl: item.previewUrl,
      statusAtSend: 'ready',
    }));
}

export function canSendWithAttachments(
  message: string, drafts: AttachmentDraft[], streaming: boolean,
): boolean {
  return Boolean(message.trim())
    && !streaming
    && drafts.every((item) => item.status === 'ready');
}
