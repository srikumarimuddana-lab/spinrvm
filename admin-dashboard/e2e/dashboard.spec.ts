import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';

// Mock all API calls for dashboard tests
async function mockDashboardAPIs(page: any) {
  // Set the HttpOnly-equivalent auth cookie so Next.js edge middleware
  // sees a valid session before the first navigation.
  await setAdminAuthCookie(page);

  await page.route('**/api/admin/**', async (route: any) => {
    const url = route.request().url();
    // silentRefresh POSTs here — must return a token so isLoading resolves
    if (url.includes('/auth/refresh')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' }) });
    } else if (url.includes('/auth/session') || url.includes('/auth/me')) {
      // checkAuth expects { authenticated: true, user }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ authenticated: true, user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }) });
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

  // Also intercept the set-cookie route so silentRefresh's setAuthCookie
  // doesn't hit the real server (which may not have cookies to return).
  await page.route('**/api/auth/set-cookie', async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true }) });
  });

  // Catch-all for any other /api/ calls (logout, etc.)
  await page.route('**/api/**', async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
  });
}

test.describe('Dashboard navigation', () => {
  test('drivers page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/drivers');
    // Wait for hydration + auth initialization to settle (silentRefresh + checkAuth)
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/dashboard\/drivers/);
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 15000 });
  });

  test('rides page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/rides');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/dashboard\/rides/);
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 15000 });
  });

  test('settings page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/settings');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/dashboard\/settings/);
    await expect(page.locator('body')).toBeVisible();
  });

  test('promotions page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/promotions');
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/dashboard\/promotions/);
    await expect(page.locator('body')).toBeVisible();
  });

  test('dashboard pages have no critical accessibility violations (axe-core)', async ({ page }) => {
    await mockDashboardAPIs(page);
    const pagesToCheck = ['/login', '/dashboard/drivers'];
    for (const path of pagesToCheck) {
      await page.goto(path);
      await page.waitForLoadState('networkidle');
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
