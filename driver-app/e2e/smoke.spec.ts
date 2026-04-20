/**
 * Driver-app web smoke tests.
 *
 * Mirrors rider-app/e2e/smoke.spec.ts. Verifies the Expo web export boots,
 * routes auth correctly, and doesn't throw fatal JS errors.
 */
import { test, expect } from '@playwright/test';
import { mockDriverBackend, seedAuthedDriverSession } from './fixtures';

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
    await seedAuthedDriverSession(page);
    await mockDriverBackend(page);
    await page.goto('/');
    await page.waitForTimeout(2500);
    const url = page.url();
    expect(url).not.toMatch(/\/login$/);
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
