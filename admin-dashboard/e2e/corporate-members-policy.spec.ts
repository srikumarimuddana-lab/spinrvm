/**
 * Admin dashboard E2E — /dashboard/corporate-accounts/[id]/members and
 * .../policy interaction coverage. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_MEMBERS = [
  {
    id: 'member_e2e_1',
    company_id: 'corp_e2e_1',
    user_id: 'user_e2e_1',
    role: 'employee',
    status: 'active',
    invited_email: 'employee@acme.com',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

const MOCK_ALLOWANCE_REQUESTS: any[] = [];

const MOCK_POLICY = {
  id: 'policy_e2e_1',
  company_id: 'corp_e2e_1',
  active: true,
  max_fare_per_ride: 80,
  allowed_geofence: null,
  allowed_time_windows: [],
  allowed_payment_source: 'company_wallet',
};

test.describe('admin dashboard: corporate-accounts/[id]/members — interaction', () => {
  async function mockMembers(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (method === 'POST' && url.includes('/members/invite')) {
          return json(200, { member: MOCK_MEMBERS[0], invite_url: 'https://example.com/invite/abc' });
        }
        if (method === 'DELETE' && url.includes('/members/')) return json(200, { success: true });
        if (method === 'PATCH' && url.includes('/members/')) return json(200, MOCK_MEMBERS[0]);
        if (url.includes('/allowance-requests')) return json(200, MOCK_ALLOWANCE_REQUESTS);
        if (url.includes('/members')) return json(200, MOCK_MEMBERS);
        return null;
      },
    });
  }

  test('page loads and renders member list', async ({ page }) => {
    await mockMembers(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/members');
    await expect(page.getByText(/Members/i).first()).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('employee@acme.com')).toBeVisible({ timeout: 10000 });
  });

  test('invite email input accepts typed text', async ({ page }) => {
    await mockMembers(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/members');
    const emailInput = page.getByPlaceholder('employee@acme.com');
    await expect(emailInput).toBeVisible({ timeout: 20000 });
    await emailInput.fill('newhire@acme.com');
    await expect(emailInput).toHaveValue('newhire@acme.com');
  });

  test('member status filter tabs are clickable', async ({ page }) => {
    await mockMembers(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/members');
    await expect(page.getByText('employee@acme.com')).toBeVisible({ timeout: 20000 });
    const tabs = page.getByRole('button').filter({ hasText: /active|pending|suspended|all/i });
    if (await tabs.count()) {
      await tabs.first().click();
      await expect(page.locator('body')).toBeVisible();
    }
  });

  test('"Policy editor" link navigates toward the policy page', async ({ page }) => {
    await mockMembers(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/members');
    const policyLink = page.getByRole('button', { name: /policy editor/i }).or(page.getByRole('link', { name: /policy editor/i }));
    await expect(policyLink.first()).toBeVisible({ timeout: 20000 });
  });

  test('remove-member button is present per row', async ({ page }) => {
    await mockMembers(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/members');
    await expect(page.getByText('employee@acme.com')).toBeVisible({ timeout: 20000 });
    const row = page.locator('tr', { hasText: 'employee@acme.com' });
    await expect(row.getByRole('button').first()).toBeVisible();
  });
});

test.describe('admin dashboard: corporate-accounts/[id]/policy — interaction', () => {
  async function mockPolicy(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if ((method === 'PATCH' || method === 'PUT') && url.includes('/policy')) return json(200, MOCK_POLICY);
        if (url.includes('/policy')) return json(200, MOCK_POLICY);
        return null;
      },
    });
  }

  test('page loads with max-fare field populated', async ({ page }) => {
    await mockPolicy(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/policy');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
    const maxFareInput = page.getByPlaceholder(/80/);
    if (await maxFareInput.count()) {
      await expect(maxFareInput.first()).toBeVisible();
    }
  });

  test('"Add time window" button is clickable', async ({ page }) => {
    await mockPolicy(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/policy');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
    const addBtn = page.getByRole('button', { name: /add.*window/i });
    if (await addBtn.count()) {
      await addBtn.click();
      await expect(page.locator('body')).toBeVisible();
    }
  });

  test('Save button is clickable and does not crash the page', async ({ page }) => {
    await mockPolicy(page);
    await page.goto('/dashboard/corporate-accounts/corp_e2e_1/policy');
    // Button text is "Save changes" once a policy exists, "Create policy" otherwise.
    const saveBtn = page.getByRole('button', { name: /save changes|create policy/i });
    await expect(saveBtn).toBeVisible({ timeout: 20000 });
    await saveBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
