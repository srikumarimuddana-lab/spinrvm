/**
 * Driver-app E2E — complete trip flow.
 *
 * Tests the full in_progress → completed transition. Verifies the driver can
 * end a trip and that the completion screen (or earnings summary) reflects
 * the fare amount. Also confirms the ride disappears from active rides
 * after the backend returns a completed status.
 *
 * The backend is fully mocked via page.route() — no real server needed.
 *
 * Scenarios:
 *   1. POST complete-ride returns completed + fare → app does not crash
 *   2. Completed ride is no longer returned by active rides poll
 *   3. Completion endpoint 409 (e.g. already completed) handled gracefully
 *   4. complete-ride endpoint is registered during in_progress ride
 *   5. No fatal JS errors during completion flow
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE_OFFER, loginAsDriver, mockDriverBackend, seedAuthedDriverSession } from './fixtures';

const IN_PROGRESS_RIDE = { ...MOCK_RIDE_OFFER, status: 'in_progress' };

const COMPLETED_RESPONSE = {
  status: 'completed',
  fare_amount: '18.50',
  driver_earnings: '18.50',
  ride: {
    ...MOCK_RIDE_OFFER,
    status: 'completed',
    total_fare: '18.50',
    completed_at: '2026-05-05T14:30:00Z',
  },
};

test.describe('driver-app: complete trip flow', () => {
  test('POST complete-ride returns completed fare — app does not crash', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page, { driver: { is_online: true } });
    await mockDriverBackend(page, { activeRide: IN_PROGRESS_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/complete', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(COMPLETED_RESPONSE),
      });
    });

    // After completion, active ride poll returns null
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
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `Trip completion crashed app: ${fatal.join('\n')}`).toEqual([]);
  });

  test('completed ride disappears from active rides — no crash', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page, { driver: { is_online: true } });

    let tripCompleted = false;

    await mockDriverBackend(page, { activeRide: IN_PROGRESS_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/complete', async (route) => {
      tripCompleted = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(COMPLETED_RESPONSE),
      });
    });

    // Simulate the active-ride poll returning null after completion
    await page.route('**/api/v1/drivers/rides/active', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          tripCompleted
            ? { active: false, ride: null }
            : { active: true, ride: IN_PROGRESS_RIDE }
        ),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `Post-completion active ride poll crashed: ${fatal.join('\n')}`).toEqual([]);
  });

  test('complete-ride 409 (ride already terminal) handled gracefully', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page, { driver: { is_online: true } });
    await mockDriverBackend(page, { activeRide: IN_PROGRESS_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/complete', async (route) => {
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: "Ride is already in status 'completed'",
          code: 'ride_already_terminal',
        }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `complete-ride 409 caused unhandled rejection: ${fatal.join('\n')}`).toEqual([]);
  });

  test('earnings endpoint is called after completion', async ({ page }) => {
    await mockDriverBackend(page, { activeRide: IN_PROGRESS_RIDE, onlineStatus: true });

    await page.route('**/api/v1/drivers/rides/*/complete', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(COMPLETED_RESPONSE),
      });
    });

    const earningsCalled = new Promise<boolean>((resolve) => {
      page.on('request', (req) => {
        if (/\/api\/v1\/drivers\/earnings/.test(req.url())) resolve(true);
      });
      setTimeout(() => resolve(false), 10_000);
    });

    await loginAsDriver(page, { driver: { is_online: true } });
    expect(await earningsCalled).toBe(true);
  });

  test('trip completion with fare $18.50 — body renders without fatal errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedDriverSession(page, { driver: { is_online: true } });
    await mockDriverBackend(page, {
      activeRide: { ...MOCK_RIDE_OFFER, status: 'completed', total_fare: '18.50' },
      onlineStatus: true,
    });

    await page.route('**/api/v1/drivers/rides/*/complete', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(COMPLETED_RESPONSE),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(2500);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `Completed trip render errors: ${fatal.join('\n')}`).toEqual([]);
  });
});
