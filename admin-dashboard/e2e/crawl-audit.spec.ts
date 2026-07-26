import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';

const ROUTES = [
  '/dashboard',
  '/dashboard/ai-console',
  '/dashboard/analytics',
  '/dashboard/audit-logs',
  '/dashboard/bulk-operations',
  '/dashboard/cloud-messaging',
  '/dashboard/corporate-accounts',
  '/dashboard/corporate-accounts/kyb-queue',
  '/dashboard/disputes',
  '/dashboard/documents',
  '/dashboard/documents/requirements',
  '/dashboard/driver-offers',
  '/dashboard/drivers',
  '/dashboard/drivers/expiring',
  '/dashboard/drivers/import',
  '/dashboard/drivers/queue',
  '/dashboard/earnings',
  '/dashboard/earnings/payouts',
  '/dashboard/faqs',
  '/dashboard/forecast',
  '/dashboard/heatmap',
  '/dashboard/monitoring',
  '/dashboard/monitoring/redis',
  '/dashboard/notifications',
  '/dashboard/promotions',
  '/dashboard/quests',
  '/dashboard/referrals',
  '/dashboard/rides',
  '/dashboard/safety',
  '/dashboard/service-areas',
  '/dashboard/settings',
  '/dashboard/staff',
  '/dashboard/subscriptions',
  '/dashboard/support',
  '/dashboard/support-tickets',
  '/dashboard/support-tickets/tickets',
  '/dashboard/support-tickets/trends',
  '/dashboard/surge',
  '/dashboard/users',
  '/dashboard/vehicle-types',
  '/dashboard/venues',
];

async function mockAllAPIs(page: any) {
  await setAdminAuthCookie(page);
  await page.route('**/api/**', async (route: any) => {
    const url = route.request().url();
    if (url.includes('/auth/refresh')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' }) });
    }
    if (url.includes('/auth/session') || url.includes('/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }) });
    }
    // Generic empty-but-valid shapes for list/stat endpoints
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        drivers: [], rides: [], users: [], data: [], items: [], results: [],
        total: 0, total_rides: 0, active_drivers: 0, revenue: 0,
        page: 1, pages: 1, count: 0,
      }),
    });
  });
}

for (const route of ROUTES) {
  test(`audit: ${route}`, async ({ page }) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', err => pageErrors.push(err.message));

    await mockAllAPIs(page);
    const resp = await page.goto(route, { waitUntil: 'networkidle' }).catch(() => null);
    await page.waitForTimeout(500);

    const status = resp?.status() ?? 0;
    const bodyText = await page.locator('body').innerText().catch(() => '');
    const has404 = /404|not found/i.test(bodyText) && bodyText.length < 200;

    let axeViolations: any[] = [];
    try {
      const results = await new AxeBuilder({ page }).analyze();
      axeViolations = results.violations;
    } catch { /* ignore axe failures on broken pages */ }

    test.info().annotations.push(
      { type: 'http-status', description: String(status) },
      { type: 'console-errors', description: JSON.stringify(consoleErrors.slice(0, 10)) },
      { type: 'page-errors', description: JSON.stringify(pageErrors.slice(0, 10)) },
      { type: 'axe-violations', description: JSON.stringify(axeViolations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }))) },
      { type: 'looks-404', description: String(has404) },
    );

    // Don't fail the run — this is an audit pass, not a gate.
    expect(true).toBe(true);
  });
}
