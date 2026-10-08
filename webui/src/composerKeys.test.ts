import { describe, expect, it } from 'vitest';
import { shouldSubmitComposer } from './composerKeys';

describe('shouldSubmitComposer', () => {
  it('submits on plain Enter', () => {
    expect(shouldSubmitComposer({ key: 'Enter' })).toBe(true);
  });

  it('keeps modified Enter as a newline', () => {
    expect(shouldSubmitComposer({ key: 'Enter', shiftKey: true })).toBe(false);
    expect(shouldSubmitComposer({ key: 'Enter', ctrlKey: true })).toBe(false);
    expect(shouldSubmitComposer({ key: 'Enter', altKey: true })).toBe(false);
    expect(shouldSubmitComposer({ key: 'Enter', metaKey: true })).toBe(false);
  });

  it('does not submit while an IME composition is active', () => {
    expect(shouldSubmitComposer({ key: 'Enter', isComposing: true })).toBe(false);
  });
});
