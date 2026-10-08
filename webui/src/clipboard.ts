function fallbackCopy(text: string): boolean {
  const previousFocus = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  const textarea = document.createElement('textarea');
  textarea.dataset.copyFallback = 'true';
  textarea.value = text;
  textarea.readOnly = true;
  textarea.setAttribute('aria-hidden', 'true');
  textarea.style.position = 'fixed';
  textarea.style.inset = '0 auto auto -9999px';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);

  try {
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    return typeof document.execCommand === 'function' && document.execCommand('copy');
  } finally {
    textarea.remove();
    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
  }
}

export async function copyTextToClipboard(text: string): Promise<void> {
  const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // HTTP and denied secure-context writes continue to the click-compatible fallback.
    }
  }
  if (fallbackCopy(text)) return;
  throw new Error('copy_failed');
}
