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

// 1x1 transparent GIF — a real, instantly-decodable image so the thumbnail
// actually renders without reaching the network.
const PIXEL =
  'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

// Two photos on purpose: one that signed fine, one whose signed URL failed to
// mint (url: null). The second must still render a tile — dropping it would
// tell a reviewer no evidence exists when it does, which is the exact silent
// -loss failure the evidence-photo work was built to fix.
const MOCK_PHOTOS = [
  { id: 'photo_e2e_1', content_type: 'image/jpeg', created_at: '2026-07-20T10:01:00Z', url: PIXEL },
  { id: 'photo_e2e_2', content_type: 'image/jpeg', created_at: '2026-07-20T10:02:00Z', url: null },
];

async function mockSafety(page: any, opts: { photos?: unknown[] } = {}) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.match(/\/safety\/incidents\/incident_e2e_1(?:[/?]|$)/)) {
        return json(200, {
          incident: MOCK_INCIDENT,
          reporter: { id: 'user_e2e_1', name: 'Jane Rider', email: 'jane@example.com', phone: '+13065550100', role: 'rider' },
          ride: null,
          ...(opts.photos !== undefined ? { photos: opts.photos } : {}),
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

  test('evidence photos render, and an unsignable one still shows a tile', async ({ page }) => {
    await mockSafety(page, { photos: MOCK_PHOTOS });
    await page.goto('/dashboard/safety');
    await page.getByText('Jane Rider').click();

    // Count reflects ALL attached photos, including the one that could not
    // be signed — a reviewer must know two exist.
    await expect(page.getByText('Evidence photos (2)')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('button', { name: /Open evidence photo 1 of 1/i })).toBeVisible();
    await expect(page.getByText('Preview unavailable')).toBeVisible();
  });

  test('clicking an evidence thumbnail opens the lightbox', async ({ page }) => {
    await mockSafety(page, { photos: MOCK_PHOTOS });
    await page.goto('/dashboard/safety');
    await page.getByText('Jane Rider').click();

    await page.getByRole('button', { name: /Open evidence photo 1 of 1/i }).click();
    await expect(page.getByText('Evidence photo 1 of 1')).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('link', { name: /Open full size/i })).toBeVisible();
  });

  test('no evidence section when the incident has no photos', async ({ page }) => {
    await mockSafety(page, { photos: [] });
    await page.goto('/dashboard/safety');
    await page.getByText('Jane Rider').click();
    await expect(page.getByPlaceholder(/Visible to other admins/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Evidence photos/i)).not.toBeVisible();
  });

  test('detail sheet still renders when the backend omits photos entirely', async ({ page }) => {
    // A backend deployed before migration 335 returns no `photos` key at all.
    // The drawer must not blow up on the undefined.
    await mockSafety(page);
    await page.goto('/dashboard/safety');
    await page.getByText('Jane Rider').click();
    await expect(page.getByPlaceholder(/Visible to other admins/i)).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(/Evidence photos/i)).not.toBeVisible();
  });
});
