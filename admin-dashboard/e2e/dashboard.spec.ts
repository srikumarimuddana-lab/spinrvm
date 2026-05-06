import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { setAdminAuthCookie, TEST_ADMIN_JWT } from './auth-fixture';

// Single catch-all handler so Playwright's LIFO route evaluation doesn't
// cause the generic fallback to fire before the auth-specific branches.
async function mockDashboardAPIs(page: any) {
  await setAdminAuthCookie(page);

  await page.route('**/api/**', async (route: any) => {
    const url = route.request().url();

    // silentRefresh — must return token + access_expires_at so scheduleTokenRefresh
    // sets a far-future timer (NaN delay → 0ms → infinite loop otherwise)
    if (url.includes('/auth/refresh')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf' }),
      });
    }

    // checkAuth — must return { authenticated: true, user } so isAuthenticated flips true
    if (url.includes('/auth/session') || url.includes('/auth/me')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ authenticated: true, user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }),
      });
    }

    if (url.includes('/drivers')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ drivers: [], total: 0 }) });
    }
    if (url.includes('/rides')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rides: [], total: 0 }) });
    }
    if (url.includes('/stats') || url.includes('/dashboard')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total_rides: 0, active_drivers: 0, revenue: 0 }) });
    }

    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) });
  });
}

test.describe('Dashboard navigation', () => {
  test('drivers page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/drivers');
    // h1 only renders after auth resolves (isLoading→false, isAuthenticated→true)
    await expect(page.locator('h1').first()).toBeVisible({ timeout: 20000 });
    await expect(page).toHaveURL(/dashboard\/drivers/);
  });

  test('rides page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/rides');
    await expect(page.locator('h1').first()).toBeVisible({ timeout: 20000 });
    await expect(page).toHaveURL(/dashboard\/rides/);
  });

  test('settings page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/settings');
    // <main> is rendered by the dashboard layout only when isAuthenticated=true
    await expect(page.locator('main')).toBeVisible({ timeout: 20000 });
    await expect(page).toHaveURL(/dashboard\/settings/);
  });

  test('promotions page loads', async ({ page }) => {
    await mockDashboardAPIs(page);
    await page.goto('/dashboard/promotions');
    await expect(page.locator('main')).toBeVisible({ timeout: 20000 });
    await expect(page).toHaveURL(/dashboard\/promotions/);
  });

  test('dashboard pages have no critical accessibility violations (axe-core)', async ({ page }) => {
    await mockDashboardAPIs(page);
    const pagesToCheck = ['/login', '/dashboard/drivers'];
    for (const path of pagesToCheck) {
      await page.goto(path);
      // #email on /login; h1 on dashboard — all require page to be fully rendered
      await page.locator('h1, #email').first().waitFor({ state: 'visible', timeout: 20000 });
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
