import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Mock all API calls for dashboard tests
async function mockDashboardAPIs(page: any) {
  await page.route('**/api/admin/**', async (route: any) => {
    const url = route.request().url();
    if (url.includes('/auth/session') || url.includes('/auth/me')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }) });
    } else if (url.includes('/drivers')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ drivers: [], total: 0 }) });
    } else if (url.includes('/rides')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rides: [], total: 0 }) });
    } else if (url.includes('/stats') || url.includes('/dashboard')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_rides: 0, active_drivers: 0, revenue: 0 }) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
    }
  });

  // Inject auth token into localStorage
  await page.addInitScript(() => {
    localStorage.setItem('spinr_admin_token', 'test-admin-token-123');
  });
}

test.describe('Dashboard navigation', () => {
  test('drivers page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/drivers');
    await expect(page).toHaveURL(/dashboard\/drivers/);
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 5000 });
  });

  test('rides page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/rides');
    await expect(page).toHaveURL(/dashboard\/rides/);
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 5000 });
  });

  test('settings page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/settings');
    await expect(page).toHaveURL(/dashboard\/settings/);
  });

  test('promotions page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/promotions');
    await expect(page).toHaveURL(/dashboard\/promotions/);
  });

  test('dashboard pages have no critical accessibility violations (axe-core)', async ({ page }) => {
    await mockDashboardAPIs(page);
    const pagesToCheck = ['/login', '/dashboard/drivers'];
    for (const path of pagesToCheck) {
      await page.goto(path);
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .analyze();
      const critical = results.violations.filter(v => v.impact === 'critical');
      if (critical.length > 0) {
        console.warn(`Critical a11y violations on ${path}:`, critical.map(v => v.id));
      }
      expect(critical, `Critical axe violations on ${path}`).toHaveLength(0);
    }
  });
});
