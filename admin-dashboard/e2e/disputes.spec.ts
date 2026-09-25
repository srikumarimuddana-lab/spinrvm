/**
 * Admin dashboard E2E — /dashboard/disputes after in-app disputes were
 * disabled (2026-09-25). The page shows card-network chargebacks only:
 * no "Rider Disputes" tab, no resolve/refund dialog. All network calls
 * mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_CHARGEBACK = {
  id: 'cb_e2e_1',
  stripe_dispute_id: 'dp_e2e_1',
  ride_id: 'ride_e2e_1',
  ride_code: 'RIDE-E2E-1',
  amount_cents: 2500,
  reason: 'fraudulent',
  status: 'needs_response',
  evidence_due_by: '2026-10-05T00:00:00Z',
  evidence_submitted_at: null,
  days_remaining: 10,
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-20T10:00:00Z',
};

async function mockChargebacks(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/disputes/chargebacks')) return json(200, [MOCK_CHARGEBACK]);
      if (url.includes('/disputes')) return json(200, []);
      return null;
    },
  });
}

test.describe('admin dashboard: disputes page — chargebacks only', () => {
  test('page loads and renders a chargeback row', async ({ page }) => {
    await mockChargebacks(page);
    await page.goto('/dashboard/disputes');
    await expect(page.getByText('RIDE-E2E-1')).toBeVisible({ timeout: 20000 });
  });

  test('no Rider Disputes tab and no Resolve action', async ({ page }) => {
    await mockChargebacks(page);
    await page.goto('/dashboard/disputes');
    await expect(page.getByText('RIDE-E2E-1')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Rider Disputes')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Resolve', exact: true })).toHaveCount(0);
  });
});
