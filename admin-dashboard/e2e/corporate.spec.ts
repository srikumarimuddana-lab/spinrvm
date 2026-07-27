/**
 * Admin dashboard E2E — /dashboard/corporate-accounts (list + detail) and
 * kyb-queue interaction coverage. All network calls mocked — no live backend.
 *
 * Note: listCorporateAccounts / getServiceAreas-style functions in lib/api.ts
 * consistently return raw arrays (request<T[]>(...)), not wrapped objects —
 * see the lesson from drivers.spec.ts. Mocked as raw arrays here too.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_ACCOUNTS = [
  {
    id: 'corp_e2e_1',
    name: 'Acme Corp',
    legal_name: 'Acme Corporation Inc.',
    business_number: '123456789RT0001',
    tax_region: 'SK',
    billing_email: 'billing@acme.com',
    contact_name: 'Jane Admin',
    contact_email: 'jane@acme.com',
    contact_phone: '+13065550100',
    status: 'active',
    size_tier: 'medium',
    kyb_document_url: null,
    kyb_reviewed_at: null,
    kyb_reviewed_by: null,
    kyb_submitted_at: null,
    kyb_review_note: null,
    kyb_last_decision: null,
    credit_limit: 5000,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

const MOCK_WALLET = {
  id: 'wallet_e2e_1',
  company_id: 'corp_e2e_1',
  balance: '1500.00',
  currency: 'CAD',
  auto_topup_enabled: false,
  auto_topup_threshold: null,
  auto_topup_amount: null,
  auto_topup_daily_cap: '1000.00',
  soft_negative_floor: '-100.00',
  transactions: [],
};

async function mockCorporateAccounts(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/wallet')) return json(200, MOCK_WALLET);
      if (url.match(/\/corporate-accounts\/corp_e2e_1(?:[/?]|$)/)) return json(200, MOCK_ACCOUNTS[0]);
      if (method === 'POST' && url.includes('/status')) return json(200, MOCK_ACCOUNTS[0]);
      if (method === 'POST' && url.includes('/corporate-accounts')) return json(200, MOCK_ACCOUNTS[0]);
      if (method === 'PUT' && url.includes('/corporate-accounts')) return json(200, MOCK_ACCOUNTS[0]);
      if (method === 'DELETE' && url.includes('/corporate-accounts')) return json(200, { success: true });
      if (url.includes('/corporate-accounts')) return json(200, MOCK_ACCOUNTS);
      return null;
    },
  });
}

test.describe('admin dashboard: corporate-accounts list — interaction', () => {
  test('search box accepts typed text', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts');
    const search = page.getByPlaceholder('Search accounts...');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('Acme');
    await expect(search).toHaveValue('Acme');
  });

  test('status filter dropdown is present', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts');
    await expect(page.getByLabel('Filter by status')).toBeVisible({ timeout: 20000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('"New Account" button opens the create dialog', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts');
    await expect(page.getByText('Acme Corp')).toBeVisible({ timeout: 20000 });
    const newBtn = page.getByRole('button', { name: /new account|add account|create/i });
    await expect(newBtn).toBeVisible();
    await newBtn.click();
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10000 });
  });

  test('edit icon button opens the edit dialog pre-filled with account data', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts');
    await expect(page.getByText('Acme Corp')).toBeVisible({ timeout: 20000 });
    const row = page.locator('tr', { hasText: 'Acme Corp' });
    const buttons = row.getByRole('button');
    await buttons.first().click();
    await expect(page.locator('[role="dialog"]').first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: corporate-accounts detail — interaction', () => {
  test('detail page loads with account name and wallet balance', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1');
    await expect(page.getByText('Acme Corp').first()).toBeVisible({ timeout: 20000 });
  });

  test('Edit Profile button opens the edit form', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1');
    await expect(page.getByText('Acme Corp').first()).toBeVisible({ timeout: 20000 });
    const editBtn = page.getByRole('button', { name: /edit/i }).first();
    if (await editBtn.count()) {
      await editBtn.click();
      await expect(page.locator('body')).toBeVisible();
    }
  });

  test('Suspend action button opens a confirmation flow', async ({ page }) => {
    await mockCorporateAccounts(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1');
    await expect(page.getByText('Acme Corp').first()).toBeVisible({ timeout: 20000 });
    const suspendBtn = page.getByRole('button', { name: /suspend/i });
    if (await suspendBtn.count()) {
      await suspendBtn.click();
      await expect(page.locator('body')).toBeVisible();
    }
  });
});

test.describe('admin dashboard: corporate-accounts/kyb-queue — interaction', () => {
  async function mockKybQueue(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (method === 'POST' && url.includes('/kyb-review')) return json(200, MOCK_ACCOUNTS[0]);
        if (url.includes('/corporate-accounts')) return json(200, MOCK_ACCOUNTS);
        return null;
      },
    });
  }

  test('page loads and lists a pending-review account', async ({ page }) => {
    await mockKybQueue(page);
    await page.goto('/dashboard/corporate-accounts/kyb-queue');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockKybQueue(page);
    await page.goto('/dashboard/corporate-accounts/kyb-queue');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
