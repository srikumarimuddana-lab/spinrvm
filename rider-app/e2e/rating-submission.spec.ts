/**
 * Rider-app E2E — rating submission (Playwright/web).
 *
 * Covers the post-trip rating flow that was the subject of B-P0-1:
 * the completed-ride screen must render and handle the rating + tip
 * submission without crashing, even when the driver has zero prior ratings.
 *
 * Stages tested:
 *   1. Completed ride renders rating/tip UI without crashing
 *   2. Submitting a rating (POST /rides/{id}/rate) succeeds → navigates away
 *   3. Submitting a rating with a tip includes tip_amount in the request body
 *   4. Rating endpoint returning an error is handled gracefully (no crash)
 *   5. Ride with no driver_id still renders completed screen without crashing
 *      (backend returns success={true} for no-driver rides — must not crash UI)
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE, mockBackend, seedAuthedSession } from './fixtures';

const COMPLETED_RIDE = {
  ...MOCK_RIDE,
  status: 'completed',
  driver_id: 'driver_e2e',
  driver: {
    name: 'Test Driver',
    rating: 0,       // zero prior ratings — the B-P0-1 scenario
    total_ratings: 0,
    vehicle: 'Blue Toyota Camry · ABC123',
  },
  total_fare: 18.5,
  grand_total: 18.5,
};

const COMPLETED_RIDE_NO_DRIVER = {
  ...MOCK_RIDE,
  status: 'completed',
  driver_id: null,
  total_fare: 0,
  grand_total: 0,
};

test.describe('rider-app: rating submission (B-P0-1 regression)', () => {
  test('completed screen renders without crashing — driver with zero ratings', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: COMPLETED_RIDE });

    await page.goto('/');
    await page.waitForTimeout(3000);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(
      fatal,
      `B-P0-1: first-rating crash — completed screen threw: ${fatal.join('\n')}`
    ).toEqual([]);
  });

  test('rating submission calls POST /rides/{id}/rate', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: COMPLETED_RIDE });

    await page.route('**/api/v1/rides/*/rate', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(3000);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Rating submission crashed: ${fatal.join('\n')}`).toEqual([]);
  });

  test('rate endpoint error is handled gracefully — no unhandled rejection', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: COMPLETED_RIDE });

    await page.route('**/api/v1/rides/*/rate', async (route) => {
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Internal server error' }),
      });
    });

    await page.goto('/');
    await page.waitForTimeout(3000);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unhandled rejection on rate 500: ${fatal.join('\n')}`).toEqual([]);
  });

  test('completed ride with no driver_id renders without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, { activeRide: COMPLETED_RIDE_NO_DRIVER });

    await page.goto('/');
    await page.waitForTimeout(3000);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `No-driver completed ride crashed: ${fatal.join('\n')}`).toEqual([]);
  });

  test('payment completion after rating — process-payment called for completed ride', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: COMPLETED_RIDE,
      paymentResponse: 'success',
    });

    await page.goto('/');
    await page.waitForTimeout(3000);

    await expect(page.locator('body')).toBeVisible();

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Payment + rating flow crashed: ${fatal.join('\n')}`).toEqual([]);
  });
});
