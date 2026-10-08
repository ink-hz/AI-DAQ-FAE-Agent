import { describe, expect, it } from 'vitest';

/**
 * Structural guard for the partner-login control: it may only appear behind the
 * backend capability, with no dev-only, build-mode or default-true escape that
 * could reveal it while the Partner Provider release is unselected.
 */

const { readFileSync } = await import('node:fs');
const app = readFileSync(new URL('./App.tsx', import.meta.url), 'utf8') as string;
const api = readFileSync(new URL('./api.ts', import.meta.url), 'utf8') as string;

describe('partner login source gate', () => {
  it('references the partner login route exactly once, behind the capability', () => {
    expect(app.match(/\/partner\/login/g) ?? []).toHaveLength(1);
    expect(app).toContain(
      '{!account && partnerLoginAvailable && (\n'
      + '          <a className="partner-login-link" href="/partner/login">',
    );
  });

  it('has no build-mode or dev-only escape around the control', () => {
    const guard = app
      .split('\n')
      .filter((line) => line.includes('partnerLoginAvailable'));

    expect(guard.length).toBeGreaterThan(0);
    for (const line of guard) {
      expect(line).not.toContain('import.meta.env');
      expect(line).not.toContain('DEV');
    }
    expect(app).not.toMatch(/partnerLoginAvailable\s*\|\|/);
    expect(app).not.toMatch(/useState(<[^>]*>)?\(true\)[^\n]*partnerLogin/);
    expect(app).not.toMatch(/setPartnerLoginAvailable\(true\)/);
  });

  it('renders no preparing placeholder in place of the control', () => {
    for (const text of ['准备中', '即将上线', '敬请期待']) {
      expect(app).not.toContain(text);
    }
  });

  it('defaults the capability to false in the api client', () => {
    expect(api).toContain('payload?.partner_login_available === true');
    expect(api).not.toMatch(/partnerLoginAvailable:\s*true/);
    expect(api.match(/partnerLoginAvailable: false/g) ?? []).toHaveLength(2);
  });
});
