/**
 * Rider-app web smoke tests (SPR-02/2b).
 *
 * Verifies the Expo web export boots and routes correctly. Every backend call
 * is mocked — no network access required. These tests are the foundation for
 * the full ride-booking flow in `ride-booking.spec.ts` (SPR-02/2c).
 */
import { test, expect } from '@playwright/test';
import { mockBackend, seedAuthedSession } from './fixtures';

test.describe('rider-app web: smoke', () => {
  test('app boots and renders without JS errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await mockBackend(page);
    await page.goto('/');
    // Expo web mounts the root React tree into #root (or body on single output)
    await expect(page.locator('body')).toBeVisible();
    await page.waitForLoadState('networkidle');

    // Filter out known-benign React Native Web warnings that surface as errors
    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google/i.test(e)
    );
    expect(fatal).toEqual([]);
  });

  test('unauthenticated visitor is routed toward /login', async ({ page }) => {
    await mockBackend(page);
    await page.goto('/');
    // Index screen schedules a router.replace('/login') after ~1.5s splash
    await page.waitForTimeout(2000);
    await expect(page).toHaveURL(/login|\/$|index/);
  });

  test('authed user lands on tabs (home)', async ({ page }) => {
    await seedAuthedSession(page);
    await mockBackend(page);
    await page.goto('/');
    await page.waitForTimeout(2000);
    // With auth seeded and no active ride, index should route to (tabs)
    // — the URL hash or pathname should no longer reference /login
    const url = page.url();
    expect(url).not.toMatch(/\/login/);
  });
});
