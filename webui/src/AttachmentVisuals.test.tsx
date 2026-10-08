import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { AttachmentKindIcon, formatAttachmentSize } from './AttachmentVisuals';
import type { AttachmentKind } from './attachments';

describe('attachment visual helpers', () => {
  it('formats byte sizes with binary units', () => {
    expect(formatAttachmentSize(0)).toBe('0 B');
    expect(formatAttachmentSize(1023)).toBe('1023 B');
    expect(formatAttachmentSize(1024)).toBe('1 KiB');
    expect(formatAttachmentSize(1536)).toBe('1.5 KiB');
    expect(formatAttachmentSize(1048576)).toBe('1 MiB');
    expect(formatAttachmentSize(2 * 1024 * 1024)).toBe('2 MiB');
  });

  it.each<[AttachmentKind, string]>([
    ['image', 'lucide-file-image'],
    ['pdf', 'lucide-file-text'],
    ['document', 'lucide-file-type'],
    ['spreadsheet', 'lucide-file-spreadsheet'],
    ['text', 'lucide-file-text'],
    ['code', 'lucide-file-code'],
  ])('renders the %s kind icon as decorative markup', (kind, iconClass) => {
    const html = renderToStaticMarkup(<AttachmentKindIcon kind={kind} />);
    expect(html).toContain(iconClass);
    expect(html).toContain('aria-hidden="true"');
  });
});
