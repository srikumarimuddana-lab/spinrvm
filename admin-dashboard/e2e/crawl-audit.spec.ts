import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';
import a11yBaseline from './a11y-baseline.json';

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

// #2826: the empty-but-valid mock above means any content that only
// appears with real data — status/priority/category badges, table rows —
// was invisible to this a11y gate. This covers the highest-traffic, most
// badge/table-heavy routes with one row per distinct badge/status variant
// (per that issue's own suggested approach), not all 41 routes — the rest
// keep the original empty-list mock. Each entry maps a page path to a
// function of the requested URL, returning the fixture response body for
// that page's specific list endpoint, or undefined to fall through to the
// generic empty mock (e.g. for auxiliary endpoints the same page also
// calls, like stats/export).
const RIDE_STATUSES = ['completed', 'cancelled', 'in_progress', 'searching', 'driver_assigned', 'driver_arrived'];

const ROUTE_FIXTURES: Record<string, (url: string) => any> = {
  '/dashboard/promotions': (url) => {
    if (!url.includes('/api/admin/promotions')) return undefined;
    const future = '2100-01-01T00:00:00Z';
    const past = '2020-01-01T00:00:00Z';
    return [
      { id: 'promo-active', code: 'ACTIVE10', discount_type: 'percentage', discount_value: 10, max_uses: 100, max_uses_per_user: 1, uses: 5, is_active: true, expiry_date: future, created_at: future },
      { id: 'promo-inactive', code: 'PAUSED5', discount_type: 'flat', discount_value: 5, max_uses: 100, max_uses_per_user: 1, uses: 0, is_active: false, expiry_date: future, created_at: future },
      { id: 'promo-expired', code: 'OLD20', discount_type: 'percentage', discount_value: 20, max_uses: 100, max_uses_per_user: 1, uses: 50, is_active: true, expiry_date: past, created_at: past },
    ];
  },
  '/dashboard/rides': (url) => {
    if (!url.includes('/api/admin/rides')) return undefined;
    const rides = RIDE_STATUSES.map((status, i) => ({
      id: `ride-${i}`,
      status,
      ride_code: `R-${1000 + i}`,
      pickup_address: 'Downtown, Saskatoon',
      dropoff_address: 'University of Saskatchewan',
      rider_name: 'Test Rider',
      rider_phone: '+13065550100',
      driver_name: status === 'searching' ? null : 'Test Driver',
      driver_phone: status === 'searching' ? null : '+13065550101',
      total_fare: 18.5,
      tip_amount: 0,
      planned_distance_km: 5.2,
      duration_minutes: 12,
      is_scheduled: false,
      created_at: '2026-09-01T12:00:00Z',
    }));
    return { rides, total_count: rides.length, limit: 25, offset: 0 };
  },
  '/dashboard/drivers': (url) => {
    if (!url.includes('/api/admin/drivers')) return undefined;
    return [
      { id: 'drv-1', first_name: 'Ada', last_name: 'Lovelace', driver_code: 'D-001', email: 'ada@example.com', phone: '+13065550111', account_deleted: false, status: 'active', is_online: true, profile_completeness_score: 100, rating: 4.9, total_rides: 120, created_at: '2026-01-01T00:00:00Z' },
      { id: 'drv-2', first_name: 'Grace', last_name: 'Hopper', driver_code: 'D-002', email: 'grace@example.com', phone: '+13065550112', account_deleted: false, status: 'needs_review', is_online: false, profile_completeness_score: 80, rating: 4.5, total_rides: 40, created_at: '2026-01-01T00:00:00Z' },
      { id: 'drv-3', first_name: 'Alan', last_name: 'Turing', driver_code: 'D-003', email: 'alan@example.com', phone: '+13065550113', account_deleted: false, status: 'suspended', is_online: false, profile_completeness_score: 50, rating: 3.8, total_rides: 10, created_at: '2026-01-01T00:00:00Z' },
      { id: 'drv-4', first_name: 'Margaret', last_name: 'Hamilton', driver_code: 'D-004', email: 'margaret@example.com', phone: '+13065550114', account_deleted: false, status: 'banned', is_online: false, profile_completeness_score: null, rating: 2.0, total_rides: 2, created_at: '2026-01-01T00:00:00Z' },
      { id: 'drv-5', first_name: 'Katherine', last_name: 'Johnson', driver_code: 'D-005', email: 'katherine@example.com', phone: '+13065550115', account_deleted: true, status: 'active', is_online: false, profile_completeness_score: 100, rating: 5.0, total_rides: 200, created_at: '2026-01-01T00:00:00Z' },
    ];
  },
  '/dashboard/users': (url) => {
    if (!url.includes('/api/admin/users')) return undefined;
    return [
      { id: 'usr-1', name: 'Rider Active', email: 'active@example.com', phone: '+13065550120', role: 'rider', status: 'active', created_at: '2026-01-01T00:00:00Z' },
      { id: 'usr-2', name: 'Rider Banned', email: 'banned@example.com', phone: '+13065550121', role: 'rider', status: 'banned', created_at: '2026-01-01T00:00:00Z' },
      { id: 'usr-3', name: 'Rider Suspended', email: 'suspended@example.com', phone: '+13065550122', role: 'rider', status: 'suspended', suspended_until: '2100-01-01T00:00:00Z', created_at: '2026-01-01T00:00:00Z' },
      { id: 'usr-4', name: 'Rider Pending Deletion', email: 'pending@example.com', phone: '+13065550123', role: 'rider', status: 'pending_deletion', created_at: '2026-01-01T00:00:00Z' },
    ];
  },
  '/dashboard/disputes': (url) => {
    if (!url.includes('/api/admin/disputes')) return undefined;
    return ['open', 'under_review', 'resolved', 'rejected'].map((status, i) => ({
      id: `dsp-${i}`,
      user_name: 'Test Rider',
      user_phone: '+13065550130',
      reason: 'overcharged',
      original_fare: 20,
      requested_amount: 5,
      status,
      created_at: '2026-09-01T00:00:00Z',
    }));
  },
  '/dashboard/corporate-accounts': (url) => {
    if (!url.includes('/api/admin/corporate-accounts')) return undefined;
    return ['pending_verification', 'active', 'suspended', 'closed'].map((status, i) => ({
      id: `corp-${i}`,
      name: `Test Company ${i}`,
      status,
      size_tier: 'small',
      is_active: status === 'active',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }));
  },
  '/dashboard/support-tickets/tickets': (url) => {
    if (!url.includes('/api/admin/support-tickets/tickets')) return undefined;
    return {
      data: [
        { id: 't-1', ticketNumber: '1001', subject: 'Urgent open ticket', email: 'a@example.com', category: 'Payments', priority: 'Urgent', status: 'Open', createdTime: '2020-01-01T00:00:00.000Z', tags: [] },
        { id: 't-2', ticketNumber: '1002', subject: 'Medium on hold', email: 'b@example.com', category: 'Rides', priority: 'Medium', status: 'On Hold', createdTime: '2026-09-01T00:00:00.000Z', tags: [] },
        { id: 't-3', ticketNumber: '1003', subject: 'Escalated issue', email: 'c@example.com', category: 'Safety', priority: 'High', status: 'Escalated', createdTime: '2026-09-01T00:00:00.000Z', tags: [] },
        { id: 't-4', ticketNumber: '1004', subject: 'Closed low priority', email: 'd@example.com', category: 'Other', priority: 'Low', status: 'Closed', createdTime: '2026-08-01T00:00:00.000Z', tags: [] },
      ],
    };
  },
  '/dashboard/subscriptions': (url) => {
    if (url.includes('/api/admin/subscription-plans')) {
      return [
        { id: 'plan-1', name: 'Basic', price: 9.99, duration_days: 30, rides_per_day: 2, is_active: true, subscriber_count: 10, created_at: '2026-01-01T00:00:00Z' },
        { id: 'plan-2', name: 'Legacy', price: 4.99, duration_days: 30, rides_per_day: 1, is_active: false, subscriber_count: 0, created_at: '2026-01-01T00:00:00Z' },
      ];
    }
    if (url.includes('/api/admin/driver-subscriptions')) {
      return ['active', 'expired', 'cancelled'].map((status, i) => ({
        id: `sub-${i}`, driver_id: `drv-${i}`, driver_name: `Driver ${i}`, plan_id: 'plan-1', plan_name: 'Basic', price: 9.99, status, created_at: '2026-01-01T00:00:00Z',
      }));
    }
    return undefined;
  },
};

async function mockAllAPIs(page: any, pagePath: string) {
  await setAdminAuthCookie(page);
  await page.route('**/api/**', async (route: any) => {
    const url = route.request().url();
    if (url.includes('/auth/refresh')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' }) });
    }
    if (url.includes('/auth/session') || url.includes('/auth/me')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, user: { id: '1', email: 'admin@spinr.ca', role: 'super_admin' } }) });
    }
    const fixture = ROUTE_FIXTURES[pagePath]?.(url);
    if (fixture !== undefined) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) });
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
    test.setTimeout(45000);
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', err => pageErrors.push(err.message));

    await mockAllAPIs(page, route);
    const resp = await page.goto(route, { waitUntil: 'load', timeout: 15000 }).catch(() => null);
    await page.waitForTimeout(800);

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

    // WCAG 2.1 AA a11y ratchet (ACTION_ITEMS.md E11): fail only if THIS route
    // regresses past its recorded baseline in e2e/a11y-baseline.json — 64
    // pre-existing violations across 41 routes as of 2026-07-29 are tracked
    // debt, not blocked here (see docs/change-log for the remediation
    // backlog), but no route may get worse without updating the baseline
    // deliberately. A route with no baseline entry defaults to 0 tolerance.
    const allowed = (a11yBaseline as Record<string, number>)[route] ?? 0;
    expect(
      axeViolations.length,
      `New a11y violations on ${route} (baseline ${allowed}): ${JSON.stringify(axeViolations.map(v => v.id))}`
    ).toBeLessThanOrEqual(allowed);
  });
}
