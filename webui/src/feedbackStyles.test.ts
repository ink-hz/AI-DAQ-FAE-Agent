import { describe, expect, it } from 'vitest';

const { readFileSync } = await import('node:fs');
const styles = readFileSync(new URL('./styles.css', import.meta.url), 'utf8') as string;

function cssBlock(selector: string): string {
  const selectorStart = styles.indexOf(selector);
  if (selectorStart < 0) return '';
  const blockStart = styles.indexOf('{', selectorStart);
  const blockEnd = styles.indexOf('}', blockStart);
  if (blockStart < 0 || blockEnd < 0) return '';
  return styles.slice(blockStart + 1, blockEnd);
}

describe('feedback popover styles', () => {
  it('opens the issue form to the right of the feedback icons', () => {
    expect(styles).toContain('.feedback-issue-form');
    const block = cssBlock('.feedback-issue-form');

    expect(block).toContain('left: 0;');
    expect(block).not.toContain('right: 0;');
  });
});
