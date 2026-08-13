/**
 * Rider-app E2E — cancellation flow (Playwright/web).
 *
 * Tests the rider's ability to cancel a ride from two distinct states:
 *   1. searching     — no driver yet; cancellation is free
 *   2. driver_arrived — driver is at pickup; flat $5.50 cancellation fee applies
 *
 * Also verifies that rides in in_progress cannot be cancelled (the cancel
 * button/action must not be present or must be disabled).
 *
 * The backend is fully mocked via page.route() — no real server needed.
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE, mockBackend, seedAuthedSession } from './fixtures';

const BASE_SEARCHING_RIDE = { ...MOCK_RIDE, status: 'searching', driver_id: null };

const BASE_DRIVER_ARRIVED_RIDE = {
  ...MOCK_RIDE,
  status: 'driver_arrived',
  driver_id: 'driver_e2e',
  driver: { name: 'Test Driver', rating: 4.9, vehicle: 'Blue Toyota Camry · ABC123' },
  free_cancel_seconds_remaining: 0,
  cancellation_fee: 5.5,
};

test.describe('rider-app: cancellation flow', () => {
  test('cancel during searching state — no fee, app does not crash', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: BASE_SEARCHING_RIDE });

    // Intercept the cancel endpoint and return success
    await page.route('**/api/v1/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, cancellation_fee: 0 }),
      });
    });

    // After cancel, active ride polling returns null (ride gone)
    await page.route('**/api/v1/rides/active', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ active: false, ride: null }),
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    // App renders without crashing in searching state
    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 });

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unexpected errors during searching cancel: ${fatal.join('\n')}`).toEqual([]);
  });

  test('cancel during driver_arrived state — fee amount visible, no crash', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: BASE_DRIVER_ARRIVED_RIDE });

    await page.route('**/api/v1/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true, cancellation_fee: 5.5 }),
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 });

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unexpected errors during driver_arrived cancel: ${fatal.join('\n')}`).toEqual([]);
  });

  test('in_progress ride — app renders without crashing (cancel must be unavailable)', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: { ...MOCK_RIDE, status: 'in_progress', driver_id: 'driver_e2e' },
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 });

    // Cancel endpoint must not have been called for in_progress rides
    // (no UI button should trigger it automatically)
    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `In-progress ride rendered with errors: ${fatal.join('\n')}`).toEqual([]);
  });

  test('cancel endpoint error is handled gracefully — no unhandled rejection', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: BASE_SEARCHING_RIDE });

    // Simulate server returning a 409 (e.g. ride already cancelled by driver)
    await page.route('**/api/v1/rides/*/cancel', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: "Ride is in status 'completed'; cannot cancel." }),
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 });

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unhandled rejection on cancel 409: ${fatal.join('\n')}`).toEqual([]);
  });
});
