import { describe, expect, it } from 'vitest';

const { readFileSync } = await import('node:fs');
const styles = readFileSync(new URL('./styles.css', import.meta.url), 'utf8') as string;

function cssBlocks(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return Array.from(styles.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g')))
    .map((match) => match[1])
    .join('\n');
}

describe('conversation workspace layout', () => {
  it('bounds desktop rails to the viewport with independent scroll regions', () => {
    expect(cssBlocks('.app-shell')).toContain('height: 100dvh;');
    expect(cssBlocks('.app-shell')).toContain('overflow: hidden;');
    // The rail holds a variable number of blocks (account, history, turns), so
    // it is a bounded flex column whose scroll regions grow instead of a fixed
    // three-row grid.
    expect(cssBlocks('.conversation-rail')).toContain('flex-direction: column;');
    expect(cssBlocks('.conversation-rail')).toContain('min-height: 0;');
    expect(cssBlocks('.conversation-rail')).toContain('overflow: hidden;');
    expect(cssBlocks('.turn-list')).toContain('flex: 1 1 auto;');
    expect(cssBlocks('.turn-list')).toContain('overflow-y: auto;');
    expect(cssBlocks('.authenticated-history')).toContain('min-height: 0;');
    expect(cssBlocks('.authenticated-history')).toContain('overflow-y: auto;');
    expect(cssBlocks('.chat-workspace')).toContain('height: 100dvh;');
    expect(cssBlocks('.chat-workspace')).toContain('overflow: hidden;');
    expect(cssBlocks('.message-list')).toContain('min-height: 0;');
  });

  it('restores document-flow sizing for the mobile layout', () => {
    const mobile = styles.slice(styles.indexOf('@media (max-width: 760px)'));

    expect(mobile).toMatch(/\.app-shell\s*\{[^}]*height:\s*auto;/s);
    expect(mobile).toMatch(/\.app-shell\s*\{[^}]*overflow:\s*visible;/s);
    expect(mobile).toMatch(/\.chat-workspace\s*\{[^}]*height:\s*auto;/s);
    expect(mobile).toMatch(/\.turn-list\s*\{[^}]*overflow-x:\s*auto;/s);
  });
});
