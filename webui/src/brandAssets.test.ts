import { describe, expect, it } from 'vitest';
import { BRAND_LOGO_PATH } from './brandAssets';

describe('brand assets', () => {
  it('uses the AI DAQ FAE lens logo path for UI branding', () => {
    expect(BRAND_LOGO_PATH).toBe('/app/ai-fae-logo.svg');
  });
});
