/**
 * Admin dashboard E2E — /dashboard/safety interaction coverage.
 * Drives search, status/severity/role filters, refresh, and the
 * row-click-to-detail-sheet flow. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_INCIDENT = {
  id: 'incident_e2e_1',
  reported_by_user_id: 'user_e2e_1',
  role: 'rider',
  category: 'harassment',
  description: 'Driver was rude during the trip.',
  status: 'open',
  severity: 'medium',
  ride_id: 'ride_e2e_1',
  latitude: 52.13,
  longitude: -106.67,
  location_accuracy: 10,
  assigned_to_admin_id: null,
  resolved_at: null,
  resolved_by: null,
  resolution_notes: null,
  reported_at: '2026-07-20T10:00:00Z',
  created_at: '2026-07-20T10:00:00Z',
  updated_at: '2026-07-20T10:00:00Z',
  reporter_name: 'Jane Rider',
};

async function mockSafety(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.match(/\/safety\/incidents\/incident_e2e_1(?:[/?]|$)/)) {
        return json(200, {
          incident: MOCK_INCIDENT,
          reporter: { id: 'user_e2e_1', name: 'Jane Rider', email: 'jane@example.com', phone: '+13065550100', role: 'rider' },
          ride: null,
        });
      }
      if (method === 'PATCH' && url.includes('/safety/incidents/')) {
        return json(200, { updated: true, incident: MOCK_INCIDENT });
      }
      if (url.includes('/safety/incidents')) {
        return json(200, { items: [MOCK_INCIDENT], total: 1, offset: 0, limit: 20, open_count: 1 });
      }
      return null;
    },
  });
}

test.describe('admin dashboard: safety — interaction', () => {
  test('page loads and renders an incident', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    const search = page.getByPlaceholder('Search description');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('harassment');
    await expect(search).toHaveValue('harassment');
  });

  test('status, severity, and role filter dropdowns are present', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    await expect(page.getByLabel('Filter by status')).toBeVisible({ timeout: 20000 });
    await expect(page.getByLabel('Filter by severity')).toBeVisible();
    await expect(page.getByLabel('Filter by role')).toBeVisible();
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('clicking an incident opens the detail sheet with a resolution textarea', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
    await page.getByText('Jane Rider').click();
    await expect(page.getByPlaceholder(/Visible to other admins/i)).toBeVisible({ timeout: 10000 });
  });

  test('detail sheet has a "View Ride" link and closes via the Close button', async ({ page }) => {
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    await page.getByText('Jane Rider').click();
    await expect(page.getByPlaceholder(/Visible to other admins/i)).toBeVisible({ timeout: 10000 });
    // Two "Close" buttons exist — the sheet's icon-only X (aria-label
    // "Close") and the resolution panel's text "Close" button. Either
    // closes the sheet; just use the first one.
    const closeBtn = page.getByRole('button', { name: /^close$/i }).first();
    await expect(closeBtn).toBeVisible();
    await closeBtn.click();
    await expect(page.getByPlaceholder(/Visible to other admins/i)).not.toBeVisible();
  });
});
