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
  // Full grantable-module list (backend/routes/admin/staff.py
  // AVAILABLE_MODULES). useRequireModule no longer treats role "admin" as a
  // bypass (Admin #4), so the default mock user must actually hold the module
  // of whichever page a spec drives — specs testing denial paths override
  // `user` with a narrower grant, and super-admin-only pages still deny this
  // user because the role stays "admin".
  modules: [
    'dashboard', 'users', 'drivers', 'rides', 'earnings', 'promotions',
    'service_areas', 'vehicle_types', 'support', 'disputes', 'notifications',
    'settings', 'corporate_accounts', 'documents', 'audit', 'support_tickets',
  ],
};

// `route.fulfill()` resolves to `undefined`, which is indistinguishable
// from "fell through, didn't handle it" — so `json()` resolves to an
// explicit 'handled' sentinel instead, and callers `return json(...)`.
export type JsonFn = (status: number, body: unknown) => Promise<'handled'>;

/**
 * Per-spec override hook. `return json(...)` to short-circuit with that
 * response; `return null`/`undefined` to fall through to the common
 * auth/session handling below.
 */
export type MockOverride = (
  route: Route,
  url: string,
  method: string,
  json: JsonFn
) => Promise<'handled' | null | undefined>;

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

    const json: JsonFn = async (status, body) => {
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      return 'handled';
    };

    if (opts.extra) {
      const handled = await opts.extra(route, url, method, json);
      if (handled === 'handled') return;
    }

    if (url.includes('/auth/refresh')) {
      return json(200, { token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' });
    }
    if (url.includes('/auth/session') || url.includes('/auth/me') || url.includes('/admin/me')) {
      return json(200, { authenticated: true, user });
    }

    // /api/admin/service-areas returns a bare array in production
    // (backend/routes/admin/service_areas.py::admin_get_service_areas) —
    // needs its own case since the generic object-shaped fallback below
    // would make `serviceAreas.map()` in EntitySearchTable throw.
    if (url.includes('/api/admin/service-areas') && method === 'GET') {
      return json(200, []);
    }

    // /api/admin/rides returns { rides, total_count, limit, offset }
    // (admin-dashboard/src/lib/api/rides.ts::getRides) — needs its own case
    // for the same reason as service-areas above: the generic { items,
    // data, total, ... } fallback has no `rides` key, so RidesPage's
    // `setRides(res.rides)` stores `undefined` and a child component
    // crashes on `.map()`. Found 2026-09-02 seeding visual-regression
    // baselines: dashboard-rides rendered the dashboard error boundary
    // instead of the page.
    if (url.includes('/api/admin/rides') && method === 'GET') {
      return json(200, { rides: [], total_count: 0, limit: 25, offset: 0 });
    }

    // Generic fallback: empty-but-valid shapes so list pages render an
    // empty state instead of crashing on `undefined.map`.
    return json(200, { items: [], data: [], total: 0, page: 1, per_page: 20 });
  });
}
