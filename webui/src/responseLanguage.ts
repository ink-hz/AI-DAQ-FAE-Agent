export type ResponseLanguage = 'zh-CN' | 'en-US';
const STORAGE_KEY = 'daq.response-language.v1';

export function readPreferredResponseLanguage(): ResponseLanguage {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'en-US' ? 'en-US' : 'zh-CN';
  } catch {
    return 'zh-CN';
  }
}

export function writePreferredResponseLanguage(language: ResponseLanguage): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // The selection remains active for this page when storage is unavailable.
  }
}
