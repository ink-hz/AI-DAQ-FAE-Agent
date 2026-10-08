// @vitest-environment happy-dom

import { describe, expect, it } from 'vitest';
import { faeBrowserPath } from './runtimePaths';
import { parseFaeBrowserRoute, sessionConversationPath } from './routes';

describe('FAE browser routes', () => {
  it('parses canonical public and internal conversation URLs', () => {
    expect(parseFaeBrowserRoute('/app/conversations/s%3A1')).toEqual({
      name: 'chat',
      sessionId: 's:1',
    });
    expect(parseFaeBrowserRoute('/daq/conversations/s%3A1')).toEqual({
      name: 'chat',
      sessionId: 's:1',
    });
  });

  it('recognizes only the public review workspace route', () => {
    expect(parseFaeBrowserRoute('/app/review')).toEqual({ name: 'review' });
    expect(parseFaeBrowserRoute('/daq/review')).toEqual({ name: 'not-found' });
  });

  it('builds canonical conversation paths for the active surface', () => {
    document.head.innerHTML = '<meta name="fae-browser-base" content="/daq">';

    expect(sessionConversationPath('s:1')).toBe('/daq/conversations/s%3A1');
    expect(faeBrowserPath('/')).toBe('/daq/');
  });
});


it('rejects old Agent conversation routes', () => {
  expect(parseFaeBrowserRoute('/fae/conversations/session-1')).toEqual({ name: 'not-found' });
});
