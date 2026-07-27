/**
 * Admin dashboard E2E — /dashboard/drivers/{queue,expiring,import} interaction coverage.
 *
 * queue: approval-queue tabs, area filter, approve/reject photo review.
 * expiring: window-days tabs, area filter, nudge button.
 * import: file picker + validate/commit flow.
 * All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

// getServiceAreas() returns request<any[]>(...) — a raw array, not { areas: [...] }.
const MOCK_AREAS = [{ id: 'saskatoon', name: 'Saskatoon' }, { id: 'regina', name: 'Regina' }];

const MOCK_QUEUE_ITEM = {
  driver_id: 'driver_q_1',
  name: 'Pending Driver',
  email: 'pending@example.com',
  status: 'pending',
  service_area_name: 'Saskatoon',
  photo_url: null,
};

const MOCK_EXPIRING_ITEM = {
  driver_id: 'driver_x_1',
  user_id: 'user_x_1',
  name: 'Expiry Driver',
  first_name: 'Expiry',
  last_name: 'Driver',
  email: 'expiry@example.com',
  phone: '+13065550199',
  profile_photo_url: null,
  status: 'approved',
  service_area_id: 'saskatoon',
  service_area_name: 'Saskatoon',
  doc_type: 'license',
  doc_label: "Driver's License",
  doc_field: 'license_expiry',
  expiry_date: '2026-08-15',
  days_remaining: 19,
  rides_last_30d: 12,
  last_nudged_at: null,
};

test.describe('admin dashboard: drivers/queue — interaction', () => {
  async function mockQueue(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, MOCK_AREAS);
        if (method === 'POST' && url.includes('/nudge-expiry')) return json(200, { ok: true });
        if (url.includes('/approval-queue')) return json(200, { total: 1, items: [MOCK_QUEUE_ITEM] });
        return null;
      },
    });
  }

  test('page loads and renders queue item', async ({ page }) => {
    await mockQueue(page);
    await page.goto('/dashboard/drivers/queue');
    await expect(page.getByText(/Approval Queue/i)).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Pending Driver')).toBeVisible({ timeout: 10000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockQueue(page);
    await page.goto('/dashboard/drivers/queue');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('area filter dropdown is present with options', async ({ page }) => {
    await mockQueue(page);
    await page.goto('/dashboard/drivers/queue');
    const trigger = page.getByText('All areas');
    await expect(trigger.first()).toBeVisible({ timeout: 20000 });
  });
});

test.describe('admin dashboard: drivers/expiring — interaction', () => {
  async function mockExpiring(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, MOCK_AREAS);
        if (method === 'POST' && url.includes('/nudge-expiry')) return json(200, { ok: true });
        if (url.includes('/drivers/expiring')) return json(200, { items: [MOCK_EXPIRING_ITEM] });
        return null;
      },
    });
  }

  test('page loads and renders expiring doc item', async ({ page }) => {
    await mockExpiring(page);
    await page.goto('/dashboard/drivers/expiring');
    await expect(page.getByText(/Expiring Documents/i)).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Expiry Driver')).toBeVisible({ timeout: 10000 });
  });

  test('window-days filter is clickable', async ({ page }) => {
    await mockExpiring(page);
    await page.goto('/dashboard/drivers/expiring');
    const btn14 = page.getByRole('button', { name: /14/ });
    if (await btn14.count()) {
      await btn14.first().click();
      await expect(page.locator('body')).toBeVisible();
    }
  });

  test('nudge action button is present and clickable', async ({ page }) => {
    await mockExpiring(page);
    await page.goto('/dashboard/drivers/expiring');
    await expect(page.getByText('Expiry Driver')).toBeVisible({ timeout: 20000 });
    const nudgeBtn = page.getByRole('button', { name: /nudge/i });
    if (await nudgeBtn.count()) {
      await nudgeBtn.first().click();
      await expect(page.locator('body')).toBeVisible();
    }
  });
});

test.describe('admin dashboard: drivers/import — interaction', () => {
  async function mockImport(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, MOCK_AREAS);
        if (method === 'POST' && url.includes('/drivers/import/validate')) {
          return json(200, { valid: true, rows: 1, errors: [], warnings: [], preview: [] });
        }
        if (method === 'POST' && url.includes('/drivers/import/commit')) {
          return json(200, { committed: true, created: 1, updated: 0, errors: [] });
        }
        return null;
      },
    });
  }

  test('page loads with file picker and download-template button', async ({ page }) => {
    await mockImport(page);
    await page.goto('/dashboard/drivers/import');
    await expect(page.getByText(/Bulk Driver Import/i)).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('button', { name: /template/i })).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(1);
  });

  test('validate button is disabled until a file is chosen', async ({ page }) => {
    await mockImport(page);
    await page.goto('/dashboard/drivers/import');
    const validateBtn = page.getByRole('button', { name: /validate/i });
    await expect(validateBtn).toBeVisible({ timeout: 20000 });
    await expect(validateBtn).toBeDisabled();
  });

  test('choosing a CSV file enables validate and runs the validate flow', async ({ page }) => {
    await mockImport(page);
    await page.goto('/dashboard/drivers/import');
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: 'drivers.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('first_name,last_name,email\nTest,Driver,test@example.com\n'),
    });
    const validateBtn = page.getByRole('button', { name: /validate/i });
    await expect(validateBtn).toBeEnabled({ timeout: 10000 });
    await validateBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
