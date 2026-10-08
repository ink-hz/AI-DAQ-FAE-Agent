export type ComposerKeyEvent = {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  isComposing?: boolean;
};

export function shouldSubmitComposer(event: ComposerKeyEvent): boolean {
  return event.key === 'Enter'
    && !event.shiftKey
    && !event.ctrlKey
    && !event.metaKey
    && !event.altKey
    && !event.isComposing;
}
