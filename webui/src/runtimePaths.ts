export const AGENT_ID = 'ai-daq-fae-agent';

function normalizeBase(value: string | null, fallback: string): string {
  const raw = (value ?? fallback).trim();
  if (!raw || raw === '/') return '';
  return `/${raw.replace(/^\/+|\/+$/g, '')}`;
}

function metaContent(name: string): string | null {
  if (typeof document === 'undefined') return null;
  return document.querySelector<HTMLMetaElement>(`meta[name="${name}"]`)?.content ?? null;
}

export function faeBrowserBase(): string {
  return normalizeBase(metaContent('fae-browser-base'), '/app') || '/app';
}

export function faeApiBase(): string {
  return normalizeBase(metaContent('fae-api-base'), '');
}

export function faeBrowserPath(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${faeBrowserBase()}${suffix}`;
}

export function faeApiPath(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${faeApiBase()}${suffix}`;
}

export function isInternalFaeSurface(): boolean {
  if (faeBrowserBase() === '/daq') return true;
  return typeof window !== 'undefined' && (window.location.pathname === '/daq' || window.location.pathname.startsWith('/daq/'));
}
