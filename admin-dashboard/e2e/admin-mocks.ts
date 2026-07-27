/**
 * Shared mock-API fixtures for admin-dashboard interaction-level E2E specs.
 *
 * Mirrors the pattern already used in ride-management.spec.ts and in
 * rider-app/driver-app's e2e/fixtures.ts: intercept every `/api/**` call
 * with `page.route()` so specs run fully offline, no live backend or
 * Supabase involved.
 *
 * Usage in a spec:
 *   import { setupAdminMocks } from './admin-mocks';
 *   await setupAdminMocks(page, { extra: async (route, url, method, json) => {
 *     if (url.includes('/my-endpoint')) return json(200, { ... });
 *     return null; // fall through to the common handler
 *   }});
 */
import type { Page, Route } from '@playwright/test';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';

export const MOCK_ADMIN_USER = {
  id: 'admin_1',
  email: 'admin@spinr.ca',
  role: 'admin',
  modules: ['dashboard'],
};

export type JsonFn = (status: number, body: unknown) => Promise<void>;

/**
 * Per-spec override hook. Return a value from `json(...)` to short-circuit
 * with that response; return `null`/`undefined` to fall through to the
 * common auth/session handling below.
 */
export type MockOverride = (
  route: Route,
  url: string,
  method: string,
  json: JsonFn
) => Promise<void> | null | undefined | Promise<null | undefined>;

/**
 * Installs the admin_token cookie (so Next.js edge middleware lets the
 * request through) plus a single catch-all `/api/**` handler covering
 * auth/session/refresh — the requests every dashboard page fires before
 * it will render anything. Pass `extra` to layer in spec-specific routes;
 * it is checked before the generic fallback.
 */
export async function setupAdminMocks(
  page: Page,
  opts: { extra?: MockOverride; user?: Partial<typeof MOCK_ADMIN_USER> } = {}
) {
  await setAdminAuthCookie(page);
  const user = { ...MOCK_ADMIN_USER, ...opts.user };

  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    const method = route.request().method();

    const json: JsonFn = (status, body) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (opts.extra) {
      const handled = await opts.extra(route, url, method, json);
      if (handled !== null && handled !== undefined) return handled;
    }

    if (url.includes('/auth/refresh')) {
      return json(200, { token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' });
    }
    if (url.includes('/auth/session') || url.includes('/auth/me') || url.includes('/admin/me')) {
      return json(200, { authenticated: true, user });
    }

    // Generic fallback: empty-but-valid shapes so list pages render an
    // empty state instead of crashing on `undefined.map`.
    return json(200, { items: [], data: [], total: 0, page: 1, per_page: 20 });
  });
}
