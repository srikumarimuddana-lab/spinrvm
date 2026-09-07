/**
 * Driver-app web smoke tests.
 *
 * Mirrors rider-app/e2e/smoke.spec.ts. Verifies the Expo web export boots,
 * routes auth correctly, and doesn't throw fatal JS errors.
 */
import { test, expect } from '@playwright/test';
import { loginAsDriver, mockDriverBackend, seedAuthedDriverSession } from './fixtures';

test.describe('driver-app web: smoke', () => {
  test('app boots and renders without fatal JS errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await mockDriverBackend(page);
    await page.goto('/');
    await expect(page.locator('body')).toBeVisible();
    await page.waitForLoadState('networkidle');

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `Unexpected runtime errors: ${fatal.join('\n')}`).toEqual([]);
  });

  test('unauthenticated visitor is routed toward /login', async ({ page }) => {
    await mockDriverBackend(page);
    await page.goto('/');
    await page.waitForTimeout(2000);
    await expect(page).toHaveURL(/login|\/$|index/);
  });

  test('authed verified driver lands on /driver dashboard', async ({ page }) => {
    // Drives the real login rather than seeding storage: authStore keeps web
    // sessions memory-only (no client-readable token is ever persisted), so
    // seedAuthedDriverSession's localStorage writes are never read on web and
    // the app correctly falls through to /login. See fixtures.ts.
    //
    // This test used to seed and then wait a fixed 2.5s, which passed only
    // because the splash held <Stack> unmounted past that window — the URL was
    // still '/' when it was sampled, so the assertion never observed the real
    // routing outcome. mockDriverBackend must be registered first; Playwright
    // matches routes in reverse registration order.
    await mockDriverBackend(page);
    await loginAsDriver(page);
    expect(page.url()).not.toMatch(/\/login$/);
  });

  test('onboarding-incomplete driver is not routed to dashboard', async ({ page }) => {
    await seedAuthedDriverSession(page, {
      user: { profile_complete: false, first_name: '', last_name: '', email: '' },
      driver: { onboarding_status: 'profile_incomplete' },
    });
    await mockDriverBackend(page);
    await page.goto('/');
    await page.waitForTimeout(2500);
    expect(page.url()).toMatch(/login|profile-setup|\/$/);
  });
});
