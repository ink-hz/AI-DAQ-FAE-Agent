import { useState, type FormHTMLAttributes } from 'react';
import { clipboardFiles, droppedFiles, hasFileTransfer } from './attachmentInput';

export interface AttachmentInputSurfaceProps extends FormHTMLAttributes<HTMLFormElement> {
  onFiles: (files: File[]) => Promise<void> | void;
  onFileError: (error: unknown) => void;
  fileInputDisabled?: boolean;
}

export function AttachmentInputSurface({
  onFiles,
  onFileError,
  fileInputDisabled = false,
  ...props
}: AttachmentInputSurfaceProps) {
  const [dragActive, setDragActive] = useState(false);
  const className = [props.className, dragActive && 'file-drag-active'].filter(Boolean).join(' ');

  function deliverFiles(files: File[]) {
    try {
      void Promise.resolve(onFiles(files)).catch(onFileError);
    } catch (fileError) {
      onFileError(fileError);
    }
  }

  return (
    <form
      {...props}
      className={className}
      onDragEnter={(event) => {
        props.onDragEnter?.(event);
        if (hasFileTransfer(event.dataTransfer.types)) {
          event.preventDefault();
          setDragActive(true);
        }
      }}
      onDragOver={(event) => {
        props.onDragOver?.(event);
        if (hasFileTransfer(event.dataTransfer.types)) {
          event.preventDefault();
          setDragActive(true);
        }
      }}
      onDragLeave={(event) => {
        props.onDragLeave?.(event);
        setDragActive(false);
      }}
      onDrop={(event) => {
        props.onDrop?.(event);
        const files = droppedFiles(event.dataTransfer);
        if (files.length) event.preventDefault();
        setDragActive(false);
        if (files.length && !fileInputDisabled) deliverFiles(files);
      }}
      onPaste={(event) => {
        props.onPaste?.(event);
        const files = clipboardFiles(event.clipboardData);
        if (files.length && !fileInputDisabled) {
          event.preventDefault();
          deliverFiles(files);
        }
      }}
    />
  );
}
