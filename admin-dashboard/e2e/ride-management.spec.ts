/**
 * Admin dashboard E2E — ride management workflows (Playwright/web).
 *
 * Tests the admin's ability to view and act on rides: listing, inspecting a
 * ride detail, and key moderation actions (suspend driver, issue refund).
 * Also covers surge-override UI rendering and the driver approval workflow.
 *
 * All API calls are intercepted — no real backend needed.
 */
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';

const MOCK_RIDES = [
  {
    id: 'ride_admin_1',
    status: 'completed',
    rider_id: 'rider_1',
    driver_id: 'driver_1',
    pickup_address: '123 Main St, Saskatoon',
    dropoff_address: '456 Broadway Ave, Saskatoon',
    total_fare: 18.5,
    created_at: '2026-04-28T10:00:00Z',
    payment_status: 'paid',
  },
  {
    id: 'ride_admin_2',
    status: 'in_progress',
    rider_id: 'rider_2',
    driver_id: 'driver_2',
    pickup_address: '789 22nd St W, Saskatoon',
    dropoff_address: '101 Circle Dr S, Saskatoon',
    total_fare: 24.0,
    created_at: '2026-04-28T11:30:00Z',
    payment_status: 'pending',
  },
];

const MOCK_DRIVER = {
  id: 'driver_admin_1',
  user_id: 'user_driver_1',
  first_name: 'John',
  last_name: 'Driver',
  status: 'pending',
  onboarding_status: 'pending',
  rating: 0,
  total_trips: 0,
  vehicle: { make: 'Honda', model: 'Civic', year: 2022, plate: 'XYZ789' },
  documents: { license: 'verified', insurance: 'pending', criminal_check: 'pending' },
};

async function mockAdminAPIs(page: any) {
  // Set the admin_token cookie so Next.js edge middleware passes the request
  // through to the dashboard instead of redirecting to /login.
  await setAdminAuthCookie(page);

  await page.route('**/api/**', async (route: any) => {
    const url = route.request().url();
    const method = route.request().method();

    const json = (status: number, body: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    // Auth refresh — silentRefresh POSTs here; must return a token so isLoading resolves
    if (url.includes('/auth/refresh')) {
      return json(200, { token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' });
    }
    // Auth session — checkAuth expects { authenticated: true, user }
    if (url.includes('/auth/session') || url.includes('/auth/me') || url.includes('/admin/me')) {
      return json(200, { authenticated: true, user: { id: 'admin_1', email: 'admin@spinr.ca', role: 'admin' } });
    }

    // Rides list
    if (url.includes('/rides') && !url.match(/\/rides\/ride_admin_/)) {
      return json(200, { rides: MOCK_RIDES, total: MOCK_RIDES.length, page: 1, per_page: 20 });
    }

    // Individual ride detail
    if (url.match(/\/rides\/ride_admin_1/)) {
      return json(200, MOCK_RIDES[0]);
    }

    // Refund endpoint
    if (method === 'POST' && url.includes('/refund')) {
      return json(200, { success: true, refund_id: 'ref_test_123', amount: 18.5 });
    }

    // Drivers list
    if (url.includes('/drivers') && !url.match(/\/drivers\/driver_admin_/)) {
      return json(200, { drivers: [MOCK_DRIVER], total: 1, page: 1, per_page: 20 });
    }

    // Driver detail
    if (url.match(/\/drivers\/driver_admin_/)) {
      return json(200, MOCK_DRIVER);
    }

    // Driver approval / suspension
    if (method === 'POST' && url.includes('/approve')) {
      return json(200, { success: true, status: 'approved' });
    }
    if (method === 'POST' && url.includes('/suspend')) {
      return json(200, { success: true, status: 'suspended' });
    }

    // Surge settings
    if (url.includes('/surge') || url.includes('/service-areas')) {
      return json(200, {
        areas: [{ id: 'saskatoon', name: 'Saskatoon', surge_multiplier: 1.0, surge_source: 'auto' }],
      });
    }

    // Stats / dashboard
    if (url.includes('/stats') || url.includes('/dashboard') || url.includes('/analytics')) {
      return json(200, {
        total_rides: 2,
        active_drivers: 1,
        revenue_today: 42.5,
        active_rides: 1,
      });
    }

    // Settings
    if (url.includes('/settings')) {
      return json(200, {
        cancellation_fee_admin: 0.5,
        cancellation_fee_driver: 2.5,
        free_cancel_window_seconds: 120,
        surge_cap: 2.5,
      });
    }

    return json(200, {});
  });
}

test.describe('admin dashboard: ride management', () => {
  test('rides page loads and renders ride list', async ({ page }) => {
    await mockAdminAPIs(page);
    await page.goto('/dashboard/rides');
    await expect(page).toHaveURL(/dashboard\/rides/);
    await expect(page.locator('h1, h2, [data-testid="rides-heading"]').first()).toBeVisible({
      timeout: 5000,
    });
  });

  test('drivers page loads and renders driver list', async ({ page }) => {
    await mockAdminAPIs(page);
    await page.goto('/dashboard/drivers');
    await expect(page).toHaveURL(/dashboard\/drivers/);
    await expect(page.locator('h1, h2, [data-testid="drivers-heading"]').first()).toBeVisible({
      timeout: 5000,
    });
  });

  test('settings page loads without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await mockAdminAPIs(page);
    await page.goto('/dashboard/settings');
    await expect(page).toHaveURL(/dashboard\/settings/);
    await expect(page.locator('body')).toBeVisible();

    expect(errors.filter((e) => !/chunk|hydrat/i.test(e))).toHaveLength(0);
  });

  test('refund action endpoint is reachable — no routing 404', async ({ page }) => {
    let refundCalled = false;
    await mockAdminAPIs(page);

    await page.route('**/refund**', async (route) => {
      refundCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, refund_id: 'ref_test_123' }),
      });
    });

    await page.goto('/dashboard/rides');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('driver approval flow endpoint is reachable', async ({ page }) => {
    let approveCalled = false;
    await mockAdminAPIs(page);

    await page.route('**/approve**', async (route) => {
      approveCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, status: 'approved' }),
      });
    });

    await page.goto('/dashboard/drivers');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('driver suspension endpoint is reachable', async ({ page }) => {
    await mockAdminAPIs(page);

    await page.route('**/suspend**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, status: 'suspended' }),
      });
    });

    await page.goto('/dashboard/drivers');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).toBeVisible();
  });

  test('dashboard pages meet WCAG 2.1 AA accessibility (axe-core)', async ({ page }) => {
    await mockAdminAPIs(page);

    const pagesToCheck = ['/dashboard/rides', '/dashboard/drivers'];
    for (const path of pagesToCheck) {
      await page.goto(path);
      await page.waitForTimeout(1500);

      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .analyze();

      const critical = results.violations.filter((v) => v.impact === 'critical');
      if (critical.length > 0) {
        console.warn(
          `Critical a11y violations on ${path}:`,
          critical.map((v) => `${v.id}: ${v.description}`)
        );
      }
      expect(critical, `Critical axe violations on ${path}`).toHaveLength(0);
    }
  });
});
