// @vitest-environment happy-dom

import { afterEach, describe, expect, it } from 'vitest';
import { faeApiPath, faeBrowserPath, isInternalFaeSurface } from './runtimePaths';

function setRuntimeBase(browserBase: string, apiBase: string) {
  document.head.innerHTML = '';
  const browser = document.createElement('meta');
  browser.name = 'fae-browser-base';
  browser.content = browserBase;
  document.head.appendChild(browser);
  const api = document.createElement('meta');
  api.name = 'fae-api-base';
  api.content = apiBase;
  document.head.appendChild(api);
}

afterEach(() => {
  document.head.innerHTML = '';
  window.history.replaceState(null, '', '/app/');
});

describe('runtime FAE browser and API paths', () => {
  it('prefixes browser and API paths on the internal /daq surface', () => {
    setRuntimeBase('/daq', '/daq/api');
    window.history.replaceState(null, '', '/daq/');

    expect(faeBrowserPath('/conversations/s%3A1')).toBe('/daq/conversations/s%3A1');
    expect(faeApiPath('/chat')).toBe('/daq/api/chat');
    expect(isInternalFaeSurface()).toBe(true);
  });

  it('keeps public /app API calls byte-compatible with root paths', () => {
    setRuntimeBase('/app', '');
    window.history.replaceState(null, '', '/app/');

    expect(faeBrowserPath('/conversations/s%3A1')).toBe('/app/conversations/s%3A1');
    expect(faeApiPath('/chat')).toBe('/chat');
    expect(isInternalFaeSurface()).toBe(false);
  });
});


it('does not classify another Agent workspace or a lookalike as DAQ internal', () => {
  setRuntimeBase('/app', '');
  window.history.replaceState(null, '', '/fae/');
  expect(isInternalFaeSurface()).toBe(false);
  window.history.replaceState(null, '', '/daq-unrelated/');
  expect(isInternalFaeSurface()).toBe(false);
});
