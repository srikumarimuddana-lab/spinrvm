import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { TEST_ADMIN_JWT } from './auth-fixture';

test.describe('Login page', () => {
  test.beforeEach(async ({ page }) => {
    // Single catch-all handler — avoids Playwright's LIFO route evaluation
    // causing the generic fallback to fire before auth-specific branches.
    //
    // access_expires_at MUST be present on the refresh response: without it,
    // scheduleTokenRefresh(undefined,…) computes delay=NaN → setTimeout fires
    // at 0 ms → infinite refresh loop → every test times out.
    await page.route('**/api/**', async route => {
      const url = route.request().url();
      const method = route.request().method();

      if (url.includes('/auth/refresh') && method === 'POST') {
        // The dashboard layout's initAuth() calls silentRefresh() on every
        // mount, which re-writes the admin_token cookie from this response.
        // A non-JWT placeholder here clobbers a real login's cookie moments
        // later and bounces every post-login navigation back to /login.
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ token: TEST_ADMIN_JWT, access_expires_at: '2100-01-01T00:00:00Z', csrf_token: 'test-csrf', user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }),
        });
      }

      if (url.includes('/auth/session') || url.includes('/auth/me')) {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ authenticated: true, user: { id: '1', email: 'admin@spinr.ca', role: 'admin' } }),
        });
      }

      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });
  });

  test('renders login form', async ({ page }) => {
    await page.goto('/login');
    // #email renders immediately on the public /login page; no auth wait needed
    await expect(page.locator('#email')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('button:has-text("Sign In"), button[type="submit"]')).toBeVisible();
  });

  test('sign in button disabled until both fields filled', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('#email')).toBeVisible({ timeout: 10000 });
    const btn = page.locator('button:has-text("Sign In"), button[type="submit"]');
    await expect(btn).toBeDisabled();
    await page.fill('#email', 'admin@spinr.ca');
    await expect(btn).toBeDisabled();
    await page.fill('#password', 'password123');
    await expect(btn).toBeEnabled();
  });

  test('shows error on bad credentials', async ({ page }) => {
    // Registered after beforeEach → evaluated first (LIFO) for this specific URL
    await page.route('**/api/admin/auth/login', async route => {
      await route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ detail: 'Invalid credentials' }) });
    });
    await page.goto('/login');
    await expect(page.locator('#email')).toBeVisible({ timeout: 10000 });
    await page.fill('#email', 'wrong@example.com');
    await page.fill('#password', 'wrongpassword');
    await page.click('button:has-text("Sign In"), button[type="submit"]');
    // request() throws new Error(body.detail) on a 401 login response, and the
    // page renders it verbatim in the error banner — assert the exact text.
    await expect(page.locator('.text-destructive', { hasText: 'Invalid credentials' })).toBeVisible({ timeout: 5000 });
    // Must not have navigated away from the login page on a failed attempt.
    await expect(page).toHaveURL(/\/login/);
  });

  test('successful login redirects to dashboard', async ({ page }) => {
    await page.route('**/api/admin/auth/login', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          // Middleware validates the cookie as a real JWT (aud/exp claims) —
          // an opaque placeholder string gets rejected and bounced back to /login.
          token: TEST_ADMIN_JWT,
          csrf_token: 'test-csrf',
          access_expires_at: '2100-01-01T00:00:00Z',
          user: { id: 'admin-1', email: 'admin@spinr.ca', role: 'admin' },
        }),
      });
    });
    // Let the real /api/auth/set-cookie route handler run (it just echoes the
    // token into a cookie) instead of the beforeEach catch-all's fake `{}` —
    // otherwise no admin_token cookie is ever set and middleware bounces the
    // post-login redirect straight back to /login.
    await page.route('**/api/auth/set-cookie', (route) => route.continue());
    await page.goto('/login');
    await expect(page.locator('#email')).toBeVisible({ timeout: 10000 });
    await page.fill('#email', 'admin@spinr.ca');
    await page.fill('#password', 'Test1234!');
    await page.click('button:has-text("Sign In"), button[type="submit"]');
    await page.waitForURL('**/dashboard**', { timeout: 10000 });
    await expect(page).toHaveURL(/\/dashboard/);
  });

  test('login page has no critical accessibility violations (axe-core)', async ({ page }) => {
    await page.goto('/login');
    await expect(page.locator('#email')).toBeVisible({ timeout: 10000 });
    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
      .analyze();
    if (results.violations.length > 0) {
      console.warn('Accessibility violations on /login:');
      results.violations.forEach(v => {
        console.warn(`  [${v.impact}] ${v.id}: ${v.description}`);
        v.nodes.forEach(n => console.warn(`    - ${n.html}`));
      });
    }
    const criticalViolations = results.violations.filter(v => v.impact === 'critical');
    expect(criticalViolations).toHaveLength(0);
  });
});
