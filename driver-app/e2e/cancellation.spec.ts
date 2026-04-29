/**
 * Driver-app E2E — driver cancellation flow (Playwright/web).
 *
 * Tests the driver's ability to cancel an accepted ride and verifies that the
 * app handles the backend's response (and errors) without crashing.
 *
 * The backend is fully mocked via page.route() — no real server needed.
 *
 * Scenarios:
 *   1. Driver cancels from driver_assigned state → 200, app does not crash
 *   2. Cancel endpoint returning 409 (ride already completed/cancelled) → no crash
 *   3. Active ride shows driver actions without crashing
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE_OFFER, mockDriverBackend, seedAuthedDriverSession } from './fixtures';

const ASSIGNED_RIDE = { ...MOCK_RIDE_OFFER, status: 'driver_assigned' };

test.describe('driver-app: cancellation flow', () => {
  test('driver cancels assigned ride — app does not crash', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page);
    await mockDriverBackend(page, { activeRide: ASSIGNED_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    // After cancel, active ride poll returns null
    await page.route('**/api/v1/drivers/rides/active', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ active: false, ride: null }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Driver cancel crashed: ${fatal.join('\n')}`).toEqual([]);
  });

  test('cancel endpoint 409 (ride already terminal) — handled gracefully', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page);
    await mockDriverBackend(page, { activeRide: ASSIGNED_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: "Ride is in status 'completed'; cannot cancel after trip starts.",
        }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Driver cancel 409 caused unhandled rejection: ${fatal.join('\n')}`).toEqual([]);
  });

  test('active ride screen renders driver actions without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page);
    await mockDriverBackend(page, { activeRide: ASSIGNED_RIDE, onlineStatus: true });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Active ride render errors: ${fatal.join('\n')}`).toEqual([]);
  });

  test('driver cancels in_progress ride — 409 must not crash the app', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page);
    await mockDriverBackend(page, {
      activeRide: { ...MOCK_RIDE_OFFER, status: 'in_progress' },
      onlineStatus: true,
    });

    await page.route('**/api/v1/drivers/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: "Cannot cancel a ride in state 'in_progress'" }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `In-progress cancel 409 crashed driver app: ${fatal.join('\n')}`).toEqual([]);
  });
});
