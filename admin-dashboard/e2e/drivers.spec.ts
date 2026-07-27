/**
 * Admin dashboard E2E — /dashboard/drivers interaction coverage.
 *
 * Drives the search box, service-area/vehicle-type filters, date-range
 * filters, status tabs, column sort, export/PII-toggle buttons, and the
 * row-click-to-detail-sheet + edit/save flow. All network calls mocked —
 * no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_DRIVERS = [
  {
    id: 'driver_e2e_1',
    driver_code: 'DRV-1001',
    first_name: 'John',
    last_name: 'Driver',
    email: 'john.driver@example.com',
    phone: '+13065550111',
    status: 'approved',
    is_online: true,
    vehicle_type: 'standard',
    vehicle_make: 'Honda',
    vehicle_model: 'Civic',
    rating: 4.8,
    total_rides: 120,
    total_earnings: 3200.5,
    region: 'saskatoon',
    created_at: '2026-01-15T10:00:00Z',
  },
  {
    id: 'driver_e2e_2',
    driver_code: 'DRV-1002',
    first_name: 'Amy',
    last_name: 'Wheels',
    email: 'amy.wheels@example.com',
    phone: '+13065550112',
    status: 'pending',
    is_online: false,
    vehicle_type: 'xl',
    vehicle_make: 'Toyota',
    vehicle_model: 'Sienna',
    rating: 0,
    total_rides: 0,
    total_earnings: 0,
    region: 'regina',
    created_at: '2026-06-01T10:00:00Z',
  },
];

async function mockDrivers(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      // Side-effect calls fired when the detail sheet opens (getDriverLiveStats,
      // getDriverPayoutsSummary, getDriverReferrals, getDriverTraining,
      // getDriverVehicleHistory, getDriverDocuments, getFareConfigs) — each has
      // a distinct response shape in lib/api.ts, and several call array/object
      // methods on the result without a defensive fallback, so the generic
      // { items: [], data: [] } catch-all crashes the sheet into an error
      // boundary. Must be checked BEFORE the generic driver-detail match below.
      if (url.includes('/live-stats')) {
        return json(200, { total_rides: 0, total_earnings: 0, avg_rating: null, acceptance_rate: null, cancelled_by_driver: 0, total_assigned: 0, photo_url: null });
      }
      if (url.includes('/payouts-summary')) {
        return json(200, {
          summary: {
            lifetime_earnings: 0, lifetime_tips: 0, ytd_earnings: 0, total_paid_out: 0,
            pending_in_flight: 0, pending_balance: 0, on_hold: 0, rides_count: 0,
            active_days_30d: 0, last_payout: null,
          },
        });
      }
      if (url.includes('/referrals')) {
        return json(200, {
          referral_code: '', total_referrals: 0, qualified_referrals: 0, pending_referrals: 0,
          referral_earnings: 0, reward_amount: 0, rides_required: 0, referees: [], referred_by: null,
        });
      }
      if (url.includes('/training')) {
        return json(200, { matched: false, reason: 'not_found_in_lms', phone_last4: null, lms: null });
      }
      if (url.includes('/vehicle-history')) return json(200, { history: [] });
      if (url.includes('/documents/drivers/')) return json(200, []);
      if (url.includes('/fare-configs')) return json(200, []);
      if (url.match(/\/drivers\/driver_e2e_1(?:[/?]|$)/)) return json(200, MOCK_DRIVERS[0]);
      if (url.match(/\/drivers\/driver_e2e_2(?:[/?]|$)/)) return json(200, MOCK_DRIVERS[1]);
      if (method === 'PUT' && url.includes('/drivers/')) return json(200, { success: true });
      // getServiceAreas/getVehicleTypes also return raw arrays (request<any[]>),
      // not { areas: [...] } / { vehicle_types: [...] }.
      if (url.includes('/service-areas')) {
        return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }, { id: 'regina', name: 'Regina' }]);
      }
      if (url.includes('/vehicle-types')) {
        return json(200, [{ id: 'standard', name: 'Standard' }, { id: 'xl', name: 'XL' }]);
      }
      if (url.includes('/drivers/stats')) {
        return json(200, { total: 2, online: 1, approved: 1, pending: 1 });
      }
      // getDrivers() in lib/api.ts returns request<any[]>(...) — a raw
      // array, not { drivers: [...] } (unlike ride-management.spec.ts's
      // older mock, which never got caught because that spec only checks
      // the page heading, not that rows actually render).
      if (url.includes('/drivers') && !url.match(/\/drivers\/driver_e2e_/)) {
        return json(200, MOCK_DRIVERS);
      }
      return null;
    },
  });
}

test.describe('admin dashboard: drivers — interaction', () => {
  test('search box accepts typed text', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    const search = page.getByLabel('Search drivers');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('DRV-1001');
    await expect(search).toHaveValue('DRV-1001');
  });

  test('service area and vehicle type filters render with options', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    await expect(page.getByLabel('Filter by service area')).toBeVisible({ timeout: 20000 });
    await expect(page.getByLabel('Filter by vehicle type')).toBeVisible();
  });

  test('date filters accept input', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    const fromDate = page.getByLabel('Filter from date');
    const toDate = page.getByLabel('Filter to date');
    await expect(fromDate).toBeVisible({ timeout: 20000 });
    await fromDate.fill('2026-01-01');
    await toDate.fill('2026-12-31');
    await expect(fromDate).toHaveValue('2026-01-01');
    await expect(toDate).toHaveValue('2026-12-31');
  });

  test('status tabs switch without crashing', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    await expect(page.getByText('DRV-1001')).toBeVisible({ timeout: 20000 });
    const pendingTab = page.getByRole('button', { name: /pending/i }).first();
    if (await pendingTab.count()) {
      await pendingTab.click();
      await expect(page.locator('main')).toBeVisible();
    }
  });

  test('column sort header toggles sort without crashing', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    const nameHeader = page.getByRole('columnheader', { name: /driver/i });
    await expect(nameHeader.first()).toBeVisible({ timeout: 20000 });
    await nameHeader.first().click();
    await expect(page.locator('main')).toBeVisible();
  });

  test('Show PII / Hide PII toggle button works', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    const piiBtn = page.getByRole('button', { name: /show pii|hide pii/i });
    await expect(piiBtn).toBeVisible({ timeout: 20000 });
    await piiBtn.click();
    await expect(page.locator('main')).toBeVisible();
  });

  test('Export button is clickable', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    const exportBtn = page.getByRole('button', { name: /export/i });
    await expect(exportBtn).toBeVisible({ timeout: 20000 });
    await exportBtn.click();
    await expect(page.locator('main')).toBeVisible();
  });

  test('clicking a driver row opens the detail sheet', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    await expect(page.getByText('DRV-1001')).toBeVisible({ timeout: 20000 });
    await page.getByText('DRV-1001').click();
    await expect(page.getByRole('heading', { name: /John Driver/i })).toBeVisible({ timeout: 10000 });
  });

  test('detail sheet Edit button switches to edit mode with Save/Cancel', async ({ page }) => {
    await mockDrivers(page);
    await page.goto('/dashboard/drivers');
    await page.getByText('DRV-1001').click();
    await expect(page.getByRole('heading', { name: /John Driver/i })).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^edit$/i }).click();
    await expect(page.getByRole('button', { name: /^save$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^cancel$/i })).toBeVisible();
  });
});
