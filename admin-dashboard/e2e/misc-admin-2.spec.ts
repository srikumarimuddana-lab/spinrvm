/**
 * Admin dashboard E2E — batch 2 of miscellaneous admin routes:
 * users, vehicle-types, venues, documents (redirect),
 * documents/requirements, driver-offers. Lighter-touch than the
 * domain-focused specs — page-load + a couple of key interactions per
 * route. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

test.describe('admin dashboard: users — interaction', () => {
  const MOCK_USER = {
    id: 'user_e2e_1', first_name: 'Jane', last_name: 'Rider', email: 'jane@example.com',
    phone: '+13065550100', role: 'rider', is_active: true, created_at: '2026-01-01T00:00:00Z',
  };

  async function mockUsers(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/stats')) {
          return json(200, { total_rides: 0, completed_rides: 0, cancelled_rides: 0, active_rides: 0, total_drivers: 0, online_drivers: 0, total_users: 1, total_driver_earnings: 0, total_admin_earnings: 0, total_tips: 0 });
        }
        if (url.includes('/users')) return json(200, [MOCK_USER]);
        return null;
      },
    });
  }

  test('page loads and renders a user', async ({ page }) => {
    await mockUsers(page);
    await page.goto('/dashboard/users');
    await expect(page.getByLabel('Search users')).toBeVisible({ timeout: 20000 });
    // Email is masked by default (showPii starts false) — the name is not.
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 10000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockUsers(page);
    await page.goto('/dashboard/users');
    const search = page.getByLabel('Search users');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('Jane');
    await expect(search).toHaveValue('Jane');
  });

  test('role filter dropdown is present', async ({ page }) => {
    await mockUsers(page);
    await page.goto('/dashboard/users');
    await expect(page.getByLabel('Filter by role')).toBeVisible({ timeout: 20000 });
  });
});

test.describe('admin dashboard: vehicle-types — interaction', () => {
  const MOCK_VEHICLE_TYPE = { id: 'vt_e2e_1', name: 'Spinr Go', description: 'Affordable rides', icon: 'car', is_active: true };

  async function mockVehicleTypes(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (method === 'POST' && url.includes('/vehicle-types')) return json(200, MOCK_VEHICLE_TYPE);
        if (url.includes('/vehicle-types')) return json(200, [MOCK_VEHICLE_TYPE]);
        return null;
      },
    });
  }

  test('page loads and renders a vehicle type', async ({ page }) => {
    await mockVehicleTypes(page);
    await page.goto('/dashboard/vehicle-types');
    await expect(page.getByRole('heading', { name: 'Vehicle Types' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Spinr Go')).toBeVisible({ timeout: 10000 });
  });

  test('"Add Vehicle Type" opens the create form', async ({ page }) => {
    await mockVehicleTypes(page);
    await page.goto('/dashboard/vehicle-types');
    await expect(page.getByText('Spinr Go')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /add vehicle type|new vehicle type/i }).first().click();
    await expect(page.getByPlaceholder('e.g. Spinr Go')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: venues — interaction', () => {
  // center_lat/center_lng are rendered via .toFixed(5) with no null guard —
  // omitting them crashes the row into the page's error boundary.
  const MOCK_VENUE = {
    id: 'venue_e2e_1', name: 'Cornwall Centre', service_area_id: 'saskatoon',
    center_lat: 50.4452, center_lng: -104.6189, radius_m: 150, pickup_points: [],
  };

  async function mockVenues(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
        // getVenues() returns { venues: [...] } — a wrapped shape, unlike
        // most list endpoints in this codebase (see lesson from
        // drivers.spec.ts about not assuming a shape without checking).
        if (url.includes('/venues')) return json(200, { venues: [MOCK_VENUE] });
        return null;
      },
    });
  }

  test('page loads and renders a venue', async ({ page }) => {
    await mockVenues(page);
    await page.goto('/dashboard/venues');
    await expect(page.getByRole('heading', { name: 'Pickup Venues' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Cornwall Centre')).toBeVisible({ timeout: 10000 });
  });

  test('"New" button opens the create form with a name input', async ({ page }) => {
    await mockVenues(page);
    await page.goto('/dashboard/venues');
    await expect(page.getByText('Cornwall Centre')).toBeVisible({ timeout: 20000 });
    const newBtn = page.getByRole('button', { name: /add venue/i });
    await expect(newBtn).toBeVisible();
    await newBtn.click();
    await expect(page.getByPlaceholder('Cornwall Centre')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: documents — redirect', () => {
  test('/dashboard/documents redirects to /dashboard/service-areas', async ({ page }) => {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, []);
        return null;
      },
    });
    await page.goto('/dashboard/documents');
    await expect(page).toHaveURL(/dashboard\/service-areas/, { timeout: 10000 });
  });
});

test.describe('admin dashboard: documents/requirements — interaction', () => {
  const MOCK_REQUIREMENT = { id: 'req_e2e_1', name: "Driver's License", description: 'Valid license', field_key: 'drivers_license', is_active: true, is_required: true };

  async function mockRequirements(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (method === 'POST' && url.includes('/documents/requirements')) return json(200, MOCK_REQUIREMENT);
        if (url.includes('/documents/requirements')) return json(200, [MOCK_REQUIREMENT]);
        return null;
      },
    });
  }

  test('page loads and renders a requirement', async ({ page }) => {
    await mockRequirements(page);
    await page.goto('/dashboard/documents/requirements');
    await expect(page.getByText('Document Requirements')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText("Driver's License")).toBeVisible({ timeout: 10000 });
  });

  test('"Add Requirement" opens the create form', async ({ page }) => {
    await mockRequirements(page);
    await page.goto('/dashboard/documents/requirements');
    await expect(page.getByText("Driver's License")).toBeVisible({ timeout: 20000 });
    const addBtn = page.getByRole('button', { name: /add requirement|new requirement/i });
    await expect(addBtn).toBeVisible();
    await addBtn.click();
    await expect(page.getByPlaceholder(/Driver's License, Vehicle Insurance/i)).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: driver-offers — interaction', () => {
  const MOCK_STATS = {
    drivers: [{ driver_id: 'driver_e2e_1', name: 'John Driver', offered: 20, accepted: 15, declined: 3, ignored: 2 }],
    total_drivers: 1,
  };

  async function mockDriverOffers(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/driver-offer-trends')) return json(200, { date_range: '30d', driver_id: null, daily_chart: [] });
        if (url.includes('/driver-offer-stats')) return json(200, MOCK_STATS);
        if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
        return null;
      },
    });
  }

  test('page loads and renders a driver row', async ({ page }) => {
    await mockDriverOffers(page);
    await page.goto('/dashboard/driver-offers');
    await expect(page.getByText('Driver Offer Analytics')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 10000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockDriverOffers(page);
    await page.goto('/dashboard/driver-offers');
    const search = page.getByPlaceholder('Search driver…');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('John');
    await expect(search).toHaveValue('John');
  });

  test('clicking a driver row selects it and shows its trend', async ({ page }) => {
    await mockDriverOffers(page);
    await page.goto('/dashboard/driver-offers');
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 20000 });
    await page.getByText('John Driver').click();
    await expect(page.getByText(/Offer Trend — John Driver/i)).toBeVisible({ timeout: 10000 });
  });
});
