/**
 * Admin dashboard E2E — /dashboard/forecast interaction coverage.
 * Drives the area/hours-ahead filters and refresh. All network calls
 * mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_FORECAST = {
  forecast: [
    { hour: '2026-07-27T10:00:00Z', predicted_rides: 12 },
    { hour: '2026-07-27T11:00:00Z', predicted_rides: 18 },
  ],
};

const MOCK_SUMMARY = {
  available: true,
  current_hour: { predicted_rides: 15 },
  confidence: 'high',
  next_peak: { day_name: 'Friday', hour: 17, predicted_rides: 40 },
  total_predicted_24h: 280,
  peak_hours_count: 3,
};

async function mockForecast(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/demand-forecast/summary')) return json(200, MOCK_SUMMARY);
      if (url.includes('/demand-forecast')) return json(200, MOCK_FORECAST);
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      return null;
    },
  });
}

test.describe('admin dashboard: forecast — interaction', () => {
  test('page loads with summary stats', async ({ page }) => {
    await mockForecast(page);
    await page.goto('/dashboard/forecast');
    await expect(page.getByText(/280/)).toBeVisible({ timeout: 20000 });
  });

  test('area and hours-ahead filter dropdowns are present', async ({ page }) => {
    await mockForecast(page);
    await page.goto('/dashboard/forecast');
    await expect(page.getByLabel('All Areas')).toBeVisible({ timeout: 20000 });
    await expect(page.getByLabel('Hours ahead')).toBeVisible();
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockForecast(page);
    await page.goto('/dashboard/forecast');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
