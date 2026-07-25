/**
 * Driver-app ride-offer / active-ride smoke.
 *
 * Walks the driver through each ride-state a backend `GET /drivers/rides/active`
 * response can return, and asserts the UI does not crash. Mirrors the strategy
 * in rider-app/e2e/ride-booking.spec.ts — we don't click through every button
 * (too brittle on Expo Web with native stubs); we just assert each status
 * renders cleanly.
 *
 * Driver-side ride states (see driver-app/store/driverStore.ts):
 *   idle                  → waiting for offers
 *   ride_offered          → incoming offer, countdown visible
 *   navigating_to_pickup  → offer accepted, driving to pickup
 *   arrived_at_pickup     → driver tapped "Arrived"
 *   trip_in_progress      → OTP verified, driving to dropoff
 *   trip_completed        → dropoff reached, fare recorded
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE_OFFER, loginAsDriver, mockDriverBackend, seedAuthedDriverSession } from './fixtures';

const stages = [
  { status: 'driver_assigned', label: 'offer received' },
  { status: 'driver_accepted', label: 'accepted, en route to pickup' },
  { status: 'driver_arrived', label: 'arrived at pickup' },
  { status: 'in_progress', label: 'trip in progress' },
  { status: 'completed', label: 'trip completed' },
];

test.describe('driver-app web: ride-offer lifecycle', () => {
  test('driver UI renders each ride state without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    for (const stage of stages) {
      await seedAuthedDriverSession(page, { driver: { is_online: true } });
      await mockDriverBackend(page, {
        onlineStatus: true,
        activeRide: { ...MOCK_RIDE_OFFER, status: stage.status },
      });
      await page.goto('/');
      await page.waitForTimeout(2500);

      await expect(page.locator('body')).toBeVisible();
    }

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps|MapView/i.test(e)
    );
    expect(fatal, `Unexpected runtime errors at stages ${stages.map(s => s.status).join(',')}: ${fatal.join('\n')}`).toEqual([]);
  });

  test('earnings endpoint is called on dashboard mount', async ({ page }) => {
    // useDriverDashboard.ts only calls fetchEarnings('today') once the
    // driver is online (see the `if (isOnline) fetchEarnings('today')`
    // effect) — an offline driver has no reason to see today's earnings yet.
    await mockDriverBackend(page, { onlineStatus: true });

    const earningsCalled = new Promise<boolean>((resolve) => {
      page.on('request', (req) => {
        if (/\/api\/v1\/drivers\/earnings/.test(req.url())) resolve(true);
      });
      setTimeout(() => resolve(false), 10_000);
    });

    await loginAsDriver(page);
    const called = await earningsCalled;
    expect(called).toBe(true);
  });

  test('active-ride poll is made on dashboard mount', async ({ page }) => {
    await mockDriverBackend(page);

    const activePolled = new Promise<boolean>((resolve) => {
      page.on('request', (req) => {
        if (/\/api\/v1\/drivers\/rides\/active/.test(req.url())) resolve(true);
      });
      setTimeout(() => resolve(false), 10_000);
    });

    await loginAsDriver(page);
    const called = await activePolled;
    expect(called).toBe(true);
  });
});
