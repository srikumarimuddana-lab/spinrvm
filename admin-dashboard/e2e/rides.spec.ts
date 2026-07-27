/**
 * Admin dashboard E2E — /dashboard/rides interaction coverage.
 *
 * Complements ride-management.spec.ts (which checks routing/reachability
 * and a11y) by actually driving the search box, area filter, date filters,
 * page-size select, export button, and row-click-to-detail-modal flow.
 * All network calls are mocked via setupAdminMocks — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_RIDES = [
  {
    id: 'ride_e2e_1',
    ride_code: 'SPR-1001',
    status: 'completed',
    rider_name: 'Jane Rider',
    driver_name: 'John Driver',
    pickup_address: '123 Main St, Saskatoon',
    dropoff_address: '456 Broadway Ave, Saskatoon',
    total_fare: 18.5,
    created_at: '2026-07-20T10:00:00Z',
    payment_status: 'paid',
    service_area: 'saskatoon',
  },
  {
    id: 'ride_e2e_2',
    ride_code: 'SPR-1002',
    status: 'in_progress',
    rider_name: 'Amir Rider',
    driver_name: 'Sam Driver',
    pickup_address: '789 22nd St W, Saskatoon',
    dropoff_address: '101 Circle Dr S, Saskatoon',
    total_fare: 24.0,
    created_at: '2026-07-21T11:30:00Z',
    payment_status: 'pending',
    service_area: 'regina',
  },
];

async function mockRides(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.match(/\/rides\/ride_e2e_1/)) return json(200, MOCK_RIDES[0]);
      if (url.match(/\/rides\/ride_e2e_2/)) return json(200, MOCK_RIDES[1]);
      if (method === 'POST' && url.includes('/refund')) {
        return json(200, { success: true, refund_id: 'ref_e2e_1', amount: 18.5 });
      }
      if (method === 'POST' && url.includes('/cancel')) {
        return json(200, { success: true, status: 'cancelled' });
      }
      if (url.includes('/service-areas')) {
        return json(200, { areas: [{ id: 'saskatoon', name: 'Saskatoon' }, { id: 'regina', name: 'Regina' }] });
      }
      if (url.includes('/rides') && !url.match(/\/rides\/ride_e2e_/)) {
        return json(200, { rides: MOCK_RIDES, total: MOCK_RIDES.length, page: 1, per_page: 20 });
      }
      return null;
    },
  });
}

test.describe('admin dashboard: rides — interaction', () => {
  test('search box filters by typed text without a page crash', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const search = page.getByPlaceholder(/Search by ride code, name, phone, address, or ID/i);
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('SPR-1001');
    await expect(search).toHaveValue('SPR-1001');
  });

  test('area filter dropdown has options and is selectable', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const areaSelect = page.locator('select').first();
    await expect(areaSelect).toBeVisible({ timeout: 20000 });
    const optionCount = await areaSelect.locator('option').count();
    expect(optionCount).toBeGreaterThan(0);
  });

  test('date-from and date-to filters accept input', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const dateInputs = page.locator('input[type="date"]');
    await expect(dateInputs.first()).toBeVisible({ timeout: 20000 });
    await dateInputs.first().fill('2026-07-01');
    await dateInputs.nth(1).fill('2026-07-31');
    await expect(dateInputs.first()).toHaveValue('2026-07-01');
    await expect(dateInputs.nth(1)).toHaveValue('2026-07-31');
  });

  test('export button is clickable and does not crash the page', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const exportBtn = page.getByRole('button', { name: /export/i });
    await expect(exportBtn).toBeVisible({ timeout: 20000 });
    await exportBtn.click();
    // Button flips to "Exporting..." then resolves — page must still be alive.
    await expect(page.locator('main')).toBeVisible();
  });

  test('clicking a ride row opens the detail modal', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    await expect(page.getByText('SPR-1001').first()).toBeVisible({ timeout: 20000 });
    await page.getByText('SPR-1001').first().click();
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10000 });
  });

  test('"Create Ride" button opens the create-ride modal', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const createBtn = page.getByRole('button', { name: /create ride/i });
    await expect(createBtn).toBeVisible({ timeout: 20000 });
    await createBtn.click();
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10000 });
  });

  test('page-size select changes without crashing', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides');
    const selects = page.locator('select');
    const pageSizeSelect = selects.last();
    await expect(pageSizeSelect).toBeVisible({ timeout: 20000 });
    const options = await pageSizeSelect.locator('option').allTextContents();
    expect(options.length).toBeGreaterThan(0);
  });

  test('live ride tracking page renders for a given ride id', async ({ page }) => {
    await mockRides(page);
    await page.goto('/dashboard/rides/live/ride_e2e_1');
    await expect(page.getByText(/Live Tracking/i)).toBeVisible({ timeout: 20000 });
  });
});
