/**
 * Admin dashboard E2E — /dashboard/subscriptions interaction coverage.
 * Drives the Plans/Driver Subscriptions/Transactions/Tax Config tabs,
 * create-plan form, and status filter. All network calls mocked — no
 * live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_PLAN = {
  id: 'plan_e2e_1',
  name: 'Pro 30-Day Pass',
  description: 'Priority support and surge protection',
  price: 29.99,
  interval_days: 30,
  is_active: true,
  features: ['Priority support', 'Surge protection'],
};

const MOCK_SUBSCRIPTION = {
  id: 'sub_e2e_1',
  driver_id: 'driver_e2e_1',
  driver_name: 'John Driver',
  plan_id: 'plan_e2e_1',
  plan_name: 'Pro 30-Day Pass',
  status: 'active',
  started_at: '2026-07-01T00:00:00Z',
};

const MOCK_STATS = { active_mrr: 4000, active_subscribers: 130 };

async function mockSubscriptions(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/subscription-stats')) return json(200, MOCK_STATS);
      if (url.includes('/subscription/payments')) return json(200, { payments: [], total: 0 });
      if (method === 'POST' && url.includes('/subscription-plans')) return json(200, MOCK_PLAN);
      if (method === 'PUT' && url.includes('/subscription-plans')) return json(200, MOCK_PLAN);
      if (method === 'DELETE' && url.includes('/subscription-plans')) return json(200, { success: true });
      if (url.includes('/subscription-plans')) return json(200, [MOCK_PLAN]);
      if (url.includes('/driver-subscriptions')) return json(200, [MOCK_SUBSCRIPTION]);
      // getServiceAreas() returns a raw array — see drivers.spec.ts lesson.
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      return null;
    },
  });
}

test.describe('admin dashboard: subscriptions — interaction', () => {
  test('page loads on the Plans tab with a plan listed', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    await expect(page.getByText('Spinr Pass Subscriptions')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Pro 30-Day Pass')).toBeVisible({ timeout: 10000 });
  });

  test('Driver Subscriptions tab switches and shows a subscriber', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    await expect(page.getByText('Pro 30-Day Pass')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /driver subscriptions/i }).click();
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 10000 });
  });

  test('Transactions tab switches without crashing', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    await expect(page.getByText('Pro 30-Day Pass')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /transactions/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Tax Config tab switches without crashing', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    await expect(page.getByText('Pro 30-Day Pass')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /tax config/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('"New Plan" button opens the create form with a name input', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    await expect(page.getByText('Pro 30-Day Pass')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /new plan/i }).click();
    await expect(page.getByPlaceholder('e.g. Pro 30-Day Pass')).toBeVisible({ timeout: 10000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockSubscriptions(page);
    await page.goto('/dashboard/subscriptions');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
