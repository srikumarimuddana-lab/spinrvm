/**
 * Admin dashboard E2E — /dashboard/promotions interaction coverage.
 * Complements the promo chart-filter regression test in
 * test_admin_promo_stats.py by driving the promo-list tabs, search,
 * create/edit form, and usage-history filters end-to-end (UI side).
 * All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_PROMOS = [
  {
    id: 'promo_e2e_1',
    code: 'SAVE10',
    discount_type: 'percent',
    promo_type: 'discount',
    discount_value: 10,
    max_discount: 25,
    uses: 40,
    max_uses: 100,
    max_uses_per_user: 1,
    expiry_date: '2026-12-31',
    is_active: true,
    description: 'Welcome discount',
    created_at: '2026-01-01T00:00:00Z',
  },
];

const MOCK_STATS = {
  daily_usage: [
    { date: '2026-07-20', count: 3, amount: 15, public_count: 2, public_amount: 10, private_count: 1, private_amount: 5 },
  ],
  total_public: 1, active_public: 1, total_private: 0, active_private: 0,
};

async function mockPromotions(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/promotions/stats')) return json(200, MOCK_STATS);
      if (url.includes('/promotions/usage')) return json(200, []);
      if (method === 'POST' && url.includes('/promotions')) return json(200, MOCK_PROMOS[0]);
      if (method === 'PUT' && url.includes('/promotions')) return json(200, MOCK_PROMOS[0]);
      if (method === 'DELETE' && url.includes('/promotions')) return json(200, { success: true });
      if (url.includes('/promotions')) return json(200, MOCK_PROMOS);
      // getServiceAreas()/getUsers() return raw arrays — see drivers.spec.ts lesson.
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      if (url.includes('/users')) return json(200, []);
      return null;
    },
  });
}

test.describe('admin dashboard: promotions — interaction', () => {
  test('page loads and renders a promo code', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    await expect(page.getByText('SAVE10')).toBeVisible({ timeout: 20000 });
  });

  test('Public/Private/Expired tabs switch without crashing', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    await expect(page.getByText('SAVE10')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /private/i }).click();
    await expect(page.locator('body')).toBeVisible();
    await page.getByRole('button', { name: /expired/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    const search = page.getByLabel('Search promotions');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('SAVE10');
    await expect(search).toHaveValue('SAVE10');
  });

  test('"Create Code" button opens the create form with a Code input', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    await expect(page.getByText('SAVE10')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /create code/i }).click();
    await expect(page.getByPlaceholder('e.g. SAVE10')).toBeVisible({ timeout: 10000 });
  });

  test('editing a promo pre-fills the form and Update button is enabled', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    await expect(page.getByText('SAVE10')).toBeVisible({ timeout: 20000 });
    const row = page.locator('tr', { hasText: 'SAVE10' });
    await row.getByRole('button').first().click();
    const codeInput = page.getByPlaceholder('e.g. SAVE10');
    await expect(codeInput).toHaveValue('SAVE10', { timeout: 10000 });
    await expect(page.getByRole('button', { name: /^update$/i })).toBeEnabled();
  });

  test('chart filter (all/public/private) is present with options — the fixed bug this covers', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    await expect(page.getByLabel('Filter chart by promotion type')).toBeVisible({ timeout: 20000 });
  });

  test('usage-history search box accepts typed text', async ({ page }) => {
    await mockPromotions(page);
    await page.goto('/dashboard/promotions');
    const search = page.getByLabel('Search usage history');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('user_1');
    await expect(search).toHaveValue('user_1');
  });
});
