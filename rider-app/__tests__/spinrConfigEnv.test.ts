import { readFileSync } from 'fs';
import { join } from 'path';

// Regression guard for the "rider login broken after native build" issue.
//
// babel-preset-expo inlines EXPO_PUBLIC_* env vars at bundle time by doing a
// STATIC find-and-replace of the literal `process.env.EXPO_PUBLIC_NAME`
// member expression. A dynamic/computed read (`process.env[someKey]`) is NOT
// inlined and resolves to `undefined` in production/standalone builds, which
// silently dropped the configured EXPO_PUBLIC_BACKEND_URL and fell through to
// the hardcoded production URL — so a build pointed at any other backend could
// never log in.
//
// Jest cannot reproduce Metro's inlining transform, so we assert on the source
// itself: the shared config must read these vars statically.
describe('shared/config/spinr.config.ts env var access', () => {
  const source = readFileSync(
    join(__dirname, '../../shared/config/spinr.config.ts'),
    'utf8',
  );

  it('reads EXPO_PUBLIC_BACKEND_URL via static member access (Metro-inlinable)', () => {
    expect(source).toContain('process.env.EXPO_PUBLIC_BACKEND_URL');
  });

  it('does not read env vars via dynamic computed access (defeats inlining)', () => {
    // Strip line comments so the explanatory `process.env[key]` note in the
    // source doesn't trip the guard — only real code access should match.
    const codeOnly = source.replace(/\/\/.*$/gm, '');
    expect(codeOnly).not.toMatch(/process\.env\s*\[/);
  });
});
