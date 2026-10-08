import {
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileType,
} from 'lucide-react';
import type { AttachmentKind } from './attachments';

const KIBIBYTE = 1024;
const MEBIBYTE = KIBIBYTE * KIBIBYTE;

function formatUnit(value: number): string {
  return String(Math.round(value * 10) / 10);
}

export function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes < KIBIBYTE) return `${sizeBytes} B`;
  if (sizeBytes < MEBIBYTE) return `${formatUnit(sizeBytes / KIBIBYTE)} KiB`;
  return `${formatUnit(sizeBytes / MEBIBYTE)} MiB`;
}

const KIND_ICONS = {
  image: FileImage,
  pdf: FileText,
  document: FileType,
  spreadsheet: FileSpreadsheet,
  text: FileText,
  code: FileCode,
} satisfies Record<AttachmentKind, typeof FileImage>;

export function AttachmentKindIcon({ kind }: { kind: AttachmentKind }) {
  const Icon = KIND_ICONS[kind];
  return <Icon className="attachment-kind-icon" size={20} aria-hidden="true" />;
}
