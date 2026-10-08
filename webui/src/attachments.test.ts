import { describe, expect, it } from 'vitest';
import {
  MAX_ATTACHMENT_BATCH_BYTES,
  createAttachmentDrafts,
  attachmentReducer,
  buildChatPayload,
  canSendWithAttachments,
  freezeSentAttachments,
  validateAttachmentSelection,
} from './attachments';

function file(name: string, size: number, type = 'text/plain'): File {
  return new File([new Uint8Array(size)], name, { type });
}

describe('attachment draft state', () => {
  it('rejects more than five files and batches over 50 MiB', () => {
    expect(() => createAttachmentDrafts(Array.from({ length: 6 }, (_, index) => (
      file(`${index}.txt`, 1)
    )))).toThrow(/最多 5 个/);
    expect(() => createAttachmentDrafts([
      file('large.log', MAX_ATTACHMENT_BATCH_BYTES + 1),
    ])).toThrow(/50 MiB/);
  });

  it('rejects image selection when health says vision is disabled', () => {
    expect(() => validateAttachmentSelection([
      file('viewer.png', 10, 'image/png'),
    ], false)).toThrow(/图片理解未启用/);
    expect(() => validateAttachmentSelection([
      file('device.log', 10),
    ], false)).not.toThrow();
  });

  it('maps ordered mixed upload results without dropping failed files', () => {
    const drafts = createAttachmentDrafts([
      file('ok.log', 2),
      file('bad.zip', 2, 'application/zip'),
    ]);
    const uploading = attachmentReducer(drafts, {
      type: 'uploading', clientIds: drafts.map((item) => item.clientId),
    });
    const result = attachmentReducer(uploading, {
      type: 'upload_results',
      clientIds: drafts.map((item) => item.clientId),
      results: [
        { ok: true, attachment: {
          attachment_id: 'opaque-a', source_id: 'att-src-a', display_name: 'ok.log',
          kind: 'text', status: 'ready', parse_coverage: 'full', warnings: [],
        } },
        { ok: false, error: { code: 'unsupported_attachment_type', message: '不支持' } },
      ],
    });

    expect(result.map((item) => item.status)).toEqual(['ready', 'failed']);
    expect(result[0].attachmentId).toBe('opaque-a');
    expect(result[1].error).toBe('不支持');
  });

  it('cleans initial names and freezes the backend canonical display name without bearer ids', () => {
    const [draft] = createAttachmentDrafts([
      file('C:/private\\folder\\\x00viewer\n.png', 2048, 'image/png'),
    ]);
    expect(draft.displayName).toBe('viewer.png');
    expect(createAttachmentDrafts([file('../\x00\n', 1)])[0].displayName).toBe('attachment');

    const [ready] = attachmentReducer([{
      ...draft,
      previewUrl: 'blob:viewer',
    }], {
      type: 'upload_results',
      clientIds: [draft.clientId],
      results: [{ ok: true, attachment: {
        attachment_id: 'opaque-a',
        source_id: 'att-src-a',
        display_name: 'canonical-viewer.png',
        kind: 'image',
        status: 'ready',
        parse_coverage: 'full',
        warnings: [],
      } }],
    });

    expect(ready.displayName).toBe('canonical-viewer.png');
    expect(ready.attachmentId).toBe('opaque-a');
    const [frozen] = freezeSentAttachments([ready]);
    expect(frozen).toEqual({
      sourceId: 'att-src-a',
      displayName: 'canonical-viewer.png',
      kind: 'image',
      sizeBytes: 2048,
      previewUrl: 'blob:viewer',
      statusAtSend: 'ready',
    });
    expect(frozen).not.toHaveProperty('attachmentId');
  });

  it('builds chat payload with bearer ids but freezes only safe source metadata', () => {
    const drafts = attachmentReducer(createAttachmentDrafts([
      file('viewer.png', 2048, 'image/png'),
    ]), {
      type: 'upload_results',
      clientIds: ['ignored'],
      results: [],
    });
    const ready = [{
      ...drafts[0], status: 'ready' as const,
      attachmentId: 'opaque-a', sourceId: 'att-src-a', kind: 'image' as const,
      previewUrl: 'blob:viewer',
    }];
    expect(buildChatPayload('读日志', ready)).toEqual({
      message: '读日志', attachment_ids: ['opaque-a'],
    });
    const frozen = freezeSentAttachments(ready);
    expect(frozen[0]).toEqual({
      sourceId: 'att-src-a', displayName: 'viewer.png', kind: 'image',
      sizeBytes: 2048, previewUrl: 'blob:viewer', statusAtSend: 'ready',
    });
    expect(frozen[0]).not.toHaveProperty('attachmentId');
    expect(canSendWithAttachments('读日志', ready, false)).toBe(true);
    expect(canSendWithAttachments('读日志', [{ ...ready[0], status: 'uploading' }], false)).toBe(false);
  });
});
