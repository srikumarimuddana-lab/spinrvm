import type { Page } from '@playwright/test';

/**
 * A test-only JWT with a far-future exp claim (year 2286).
 * The middleware validates JWT structure + exp but does NOT verify the
 * signature in Edge Runtime, so this token is safe to use in CI E2E tests.
 * It carries the aud: "spinr:admin" claim required by admin route guards.
 */
export const TEST_ADMIN_JWT =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJzdWIiOiJhZG1pbl90ZXN0XzEiLCJlbWFpbCI6ImFkbWluQHNwaW5yLmNhIiwicm9sZSI6ImFkbWluIiwiYXVkIjoic3BpbnI6YWRtaW4iLCJleHAiOjk5OTk5OTk5OTl9' +
  '.dGVzdC1zaWctbm90LXZlcmlmaWVk';

/**
 * Inject the admin_token HttpOnly-equivalent cookie so Next.js edge
 * middleware sees a valid session before the first page.goto() call.
 * Must be called before any navigation to a protected route.
 */
export async function setAdminAuthCookie(page: Page): Promise<void> {
  await page.context().addCookies([
    {
      name: 'admin_token',
      value: TEST_ADMIN_JWT,
      domain: 'localhost',
      path: '/',
      httpOnly: false, // Playwright injects at context level; no JS read needed
      secure: false,
      sameSite: 'Strict',
    },
  ]);
}
