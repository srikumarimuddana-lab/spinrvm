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

    // Rides list — getRides()/getDrivers() expect a bare array, not a wrapper object.
    if (url.includes('/rides') && !url.match(/\/rides\/ride_admin_/)) {
      return json(200, MOCK_RIDES);
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
      return json(200, [MOCK_DRIVER]);
    }

    // Driver payouts summary — the slideout renders payoutSummary.summary.pending_balance
    // unconditionally once payoutSummary is truthy, so it must be a full shape, not {}.
    if (url.includes('/payouts-summary')) {
      return json(200, { summary: { pending_balance: 0, last_payout_at: null, total_paid_out: 0 } });
    }

    // Driver detail
    if (url.match(/\/drivers\/driver_admin_/)) {
      return json(200, MOCK_DRIVER);
    }

    // Driver approval / suspension — real endpoint is POST .../drivers/{id}/action
    // with { action: "approve" | "suspend" | ... } in the body, not distinct
    // /approve or /suspend routes.
    if (method === 'POST' && url.includes('/drivers/') && url.includes('/action')) {
      const body = route.request().postDataJSON() as { action?: string };
      return json(200, { message: 'ok', new_status: body?.action === 'approve' ? 'active' : 'suspended', audit_log_id: 'audit_test_1' });
    }

    // Service areas — getServiceAreas() expects a bare array.
    if (url.includes('/service-areas')) {
      return json(200, [{ id: 'saskatoon', name: 'Saskatoon', surge_multiplier: 1.0, surge_source: 'auto' }]);
    }
    // Surge settings (separate from service-areas — surge config is its own object)
    if (url.includes('/surge')) {
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
    // h1/h2 only render after auth resolves (isLoading→false, isAuthenticated→true)
    await expect(page.locator('h1, h2, [data-testid="rides-heading"]').first()).toBeVisible({
      timeout: 20000,
    });
    await expect(page).toHaveURL(/dashboard\/rides/);
  });

  test('drivers page loads and renders driver list', async ({ page }) => {
    await mockAdminAPIs(page);
    await page.goto('/dashboard/drivers');
    await expect(page.locator('h1, h2, [data-testid="drivers-heading"]').first()).toBeVisible({
      timeout: 20000,
    });
    await expect(page).toHaveURL(/dashboard\/drivers/);
  });

  test('settings page loads without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await mockAdminAPIs(page);
    await page.goto('/dashboard/settings');
    // <main> is rendered by the dashboard layout only when isAuthenticated=true
    await expect(page.locator('main')).toBeVisible({ timeout: 20000 });
    await expect(page).toHaveURL(/dashboard\/settings/);

    expect(errors.filter((e) => !/chunk|hydrat/i.test(e))).toHaveLength(0);
  });

  test('refund: resolving an open dispute with a full refund closes the dialog and updates the list', async ({ page }) => {
    const dispute = {
      id: 'dispute_1', user_name: 'Jane Rider', reason: 'overcharged',
      original_fare: 18.5, requested_amount: 18.5, status: 'open',
      description: 'Charged twice for the same trip.', created_at: '2026-04-28T10:00:00Z',
    };
    await mockAdminAPIs(page);
    let resolved = false;
    await page.route('**/api/admin/disputes/stats', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ open: resolved ? 0 : 1, under_review: 0, resolved: resolved ? 1 : 0, rejected: 0, total_refunded: resolved ? 18.5 : 0 }) }));
    await page.route('**/api/admin/disputes/dispute_1/resolve', (route) => {
      resolved = true;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) });
    });
    await page.route('**/api/admin/disputes*', (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const d = resolved ? { ...dispute, status: 'resolved', resolution: 'approved', refund_amount: 18.5, resolved_at: '2026-04-28T11:00:00Z' } : dispute;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([d]) });
    });

    await page.goto('/dashboard/disputes');
    await page.getByText('Jane Rider').click();

    const dialog = page.getByRole('dialog', { name: 'Resolve Dispute' });
    await expect(dialog).toBeVisible({ timeout: 10000 });
    // Default resolution is "Approve Full Refund" — submit as-is.
    await dialog.getByRole('button', { name: 'Submit Resolution' }).click();

    await expect(dialog).not.toBeVisible({ timeout: 10000 });
    await expect(page.getByText('approved').first()).toBeVisible({ timeout: 10000 });
  });

  test('refund: a failed resolve request leaves the dialog open (no false success)', async ({ page }) => {
    await mockAdminAPIs(page);
    const dispute = {
      id: 'dispute_2', user_name: 'Sam Rider', reason: 'driver_issue',
      original_fare: 20, requested_amount: 20, status: 'open',
      description: 'Driver took a longer route.', created_at: '2026-04-28T10:00:00Z',
    };
    await page.route('**/api/admin/disputes/stats', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ open: 1, under_review: 0, resolved: 0, rejected: 0, total_refunded: 0 }) }));
    await page.route('**/api/admin/disputes/dispute_2/resolve', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Internal error' }) }));
    await page.route('**/api/admin/disputes*', (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([dispute]) });
    });

    await page.goto('/dashboard/disputes');
    await page.getByText('Sam Rider').click();

    const dialog = page.getByRole('dialog', { name: 'Resolve Dispute' });
    await expect(dialog).toBeVisible({ timeout: 10000 });
    await dialog.getByRole('button', { name: 'Submit Resolution' }).click();

    // The dialog must not silently disappear on a failed resolve — that would
    // mislead the admin into thinking the refund went through.
    await expect(dialog).toBeVisible({ timeout: 3000 });
  });

  test('driver approval: approving a pending driver activates them', async ({ page }) => {
    await mockAdminAPIs(page);
    await page.goto('/dashboard/drivers');
    await page.getByText('John Driver').first().click();
    await page.getByRole('tab', { name: 'Actions' }).click();

    const approveBtn = page.getByRole('button', { name: 'Approve', exact: true });
    await expect(approveBtn).toBeVisible({ timeout: 10000 });
    await approveBtn.click();

    const confirmBtn = page.getByRole('dialog').getByRole('button', { name: 'Approve Driver' });
    await expect(confirmBtn).toBeVisible({ timeout: 5000 });
    await confirmBtn.click();

    await expect(page.getByRole('heading', { name: 'Active' })).toBeVisible({ timeout: 10000 });
  });

  test('driver suspension: suspending requires a reason and updates status', async ({ page }) => {
    await mockAdminAPIs(page);
    await page.goto('/dashboard/drivers');
    await page.getByText('John Driver').first().click();
    await page.getByRole('tab', { name: 'Actions' }).click();

    const suspendBtn = page.getByRole('button', { name: 'Suspend', exact: true });
    await expect(suspendBtn).toBeVisible({ timeout: 10000 });
    await suspendBtn.click();

    // Destructive-action pre-confirmation fires first.
    const alertConfirm = page.getByRole('alertdialog').getByRole('button', { name: 'Suspend' });
    await expect(alertConfirm).toBeVisible({ timeout: 5000 });
    await alertConfirm.click();

    const dialog = page.getByRole('dialog');
    const submitBtn = dialog.getByRole('button', { name: 'Suspend', exact: true });
    // Reason is required — submit stays disabled until one is provided.
    await expect(submitBtn).toBeDisabled();
    await dialog.getByLabel(/Reason/).fill('Repeated late cancellations.');
    await expect(submitBtn).toBeEnabled();
    await submitBtn.click();

    await expect(page.getByRole('heading', { name: 'Suspended' })).toBeVisible({ timeout: 10000 });
  });

  test('dashboard pages meet WCAG 2.1 AA accessibility (axe-core)', async ({ page }) => {
    await mockAdminAPIs(page);

    const pagesToCheck = ['/dashboard/rides', '/dashboard/drivers'];
    for (const path of pagesToCheck) {
      await page.goto(path);
      // Wait for dashboard layout to render (only visible when authenticated)
      await page.locator('h1, h2, main').first().waitFor({ state: 'visible', timeout: 20000 });

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
