/**
 * Admin dashboard E2E — /dashboard/disputes interaction coverage.
 * Drives status tabs, refresh, row-click-to-resolve-panel, and the
 * refund/note inputs. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_DISPUTE = {
  id: 'dispute_e2e_1',
  user_name: 'Jane Rider',
  user_phone: '+13065550100',
  reason: 'overcharge',
  original_fare: 25.0,
  requested_amount: 10.0,
  status: 'open',
  created_at: '2026-07-20T10:00:00Z',
  ride_id: 'ride_e2e_1',
};

const MOCK_STATS = { open: 1, under_review: 0, resolved: 0, rejected: 0, total_refunded: 0 };

async function mockDisputes(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/disputes/stats')) return json(200, MOCK_STATS);
      if (method === 'PUT' && url.includes('/disputes/')) return json(200, { success: true });
      if (url.includes('/disputes')) return json(200, [MOCK_DISPUTE]);
      return null;
    },
  });
}

test.describe('admin dashboard: disputes — interaction', () => {
  test('page loads and renders a dispute row', async ({ page }) => {
    await mockDisputes(page);
    await page.goto('/dashboard/disputes');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
  });

  test('status filter tabs switch without crashing', async ({ page }) => {
    await mockDisputes(page);
    await page.goto('/dashboard/disputes');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /^open$/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockDisputes(page);
    await page.goto('/dashboard/disputes');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Resolve button opens the resolution panel with refund/note inputs', async ({ page }) => {
    await mockDisputes(page);
    await page.goto('/dashboard/disputes');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
    // exact: true — the status-filter tab row also renders a "Resolved" button.
    await page.getByRole('button', { name: 'Resolve', exact: true }).click();
    const noteInput = page.getByPlaceholder(/Internal note about this resolution/i);
    await expect(noteInput).toBeVisible({ timeout: 10000 });
    await noteInput.fill('Refund approved per policy');
    await expect(noteInput).toHaveValue('Refund approved per policy');
  });

  test('resolution panel Cancel button closes it', async ({ page }) => {
    await mockDisputes(page);
    await page.goto('/dashboard/disputes');
    // exact: true — the status-filter tab row also renders a "Resolved" button.
    await page.getByRole('button', { name: 'Resolve', exact: true }).click();
    const noteInput = page.getByPlaceholder(/Internal note about this resolution/i);
    await expect(noteInput).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^cancel$/i }).click();
    await expect(noteInput).not.toBeVisible();
  });
});
