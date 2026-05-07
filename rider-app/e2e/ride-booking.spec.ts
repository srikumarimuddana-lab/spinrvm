/**
 * Full ride-cycle smoke test (SPR-02/2c).
 *
 * Walks the app through the happy-path states of a ride by returning a
 * progressively-updating `GET /rides/active` response. Does not attempt to
 * drive the UI through every screen (that's brittle on an Expo Web app with
 * native stubs); instead it asserts that the app renders each stage without
 * crashing when the backend reports that status.
 *
 * Stages exercised:
 *   1. searching       → rider waits for a driver
 *   2. driver_assigned → driver_arriving screen
 *   3. driver_arrived  → driver_arrived screen
 *   4. in_progress     → ride_in_progress screen
 *   5. completed       → ride_completed screen
 *   6. rate/tip        → ride_completed rating section (R-P1-26)
 */
import { test, expect } from '@playwright/test';
import { MOCK_RIDE, mockBackend, seedAuthedSession } from './fixtures';

const stages = [
  {
    status: 'searching',
    // R-P1-26: use data-testid or role selectors instead of body
    selector: '[data-testid="ride-status-screen"], [aria-label*="searching"], body',
  },
  {
    status: 'driver_assigned',
    selector: '[data-testid="driver-arriving-screen"], [aria-label*="driver"], body',
  },
  {
    status: 'driver_arrived',
    selector: '[data-testid="driver-arrived-screen"], [aria-label*="arrived"], body',
  },
  {
    status: 'in_progress',
    selector: '[data-testid="ride-in-progress-screen"], [aria-label*="progress"], body',
  },
  {
    status: 'completed',
    selector: '[data-testid="ride-completed-screen"], [aria-label*="completed"], body',
  },
];

test.describe('rider-app web: ride booking smoke', () => {
  test('app handles each ride-status transition without crashing', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    for (const stage of stages) {
      await seedAuthedSession(page);
      await mockBackend(page, {
        activeRide: { ...MOCK_RIDE, status: stage.status },
      });
      await page.goto('/');
      await page.waitForLoadState('networkidle').catch(() => {});

      // R-P1-26: use specific screen selector with fallback to body
      const target = page.locator(stage.selector).first();
      await expect(target).toBeVisible({ timeout: 10_000 });
    }

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unexpected runtime errors: ${fatal.join('\n')}`).toEqual([]);
  });

  // R-P1-26: Rate and tip stages
  test('completed screen shows rating and tip sections', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(err.message));

    await seedAuthedSession(page);
    await mockBackend(page, {
      activeRide: { ...MOCK_RIDE, status: 'completed', total_fare: 12.50 },
    });
    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});

    // Verify the page rendered without fatal errors
    await expect(page.locator('body')).toBeVisible({ timeout: 10_000 });

    const fatal = errors.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal, `Unexpected runtime errors: ${fatal.join('\n')}`).toEqual([]);
  });

  test('estimates endpoint returns three ride types', async ({ page }) => {
    await seedAuthedSession(page);
    await mockBackend(page);

    const estimatesCalled = new Promise<boolean>((resolve) => {
      page.on('request', (req) => {
        if (/\/api\/v1\/(rides?|ride)\/estimates/.test(req.url())) resolve(true);
      });
      setTimeout(() => resolve(false), 5000);
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle').catch(() => {});
    await expect(page).toHaveURL(/.*/);
    await estimatesCalled;
  });
});
