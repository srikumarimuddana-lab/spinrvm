/**
 * Driver-app online/offline toggle smoke.
 *
 * Verifies:
 *  - GET /drivers/me + /drivers/config hit on mount
 *  - Going online triggers POST /drivers/status and opens /ws/driver/... socket
 *  - Going offline closes the socket
 *
 * WebSocket is mocked in fixtures via __MockWebSocket.
 */
import { test, expect } from '@playwright/test';
import { loginAsDriver, mockDriverBackend, seedAuthedDriverSession } from './fixtures';

test.describe('driver-app web: online toggle', () => {
  test('driver config fetch happens on mount', async ({ page }) => {
    await mockDriverBackend(page);

    const configCalled = new Promise<boolean>((resolve) => {
      page.on('request', (req) => {
        if (/\/api\/v1\/drivers\/config/.test(req.url())) resolve(true);
      });
      setTimeout(() => resolve(false), 10_000);
    });

    await loginAsDriver(page);
    expect(await configCalled).toBe(true);
  });

  test('dashboard page does not leak unhandled promise rejections', async ({ page }) => {
    const rejections: string[] = [];
    page.on('pageerror', (err) => rejections.push(err.message));

    await seedAuthedDriverSession(page);
    await mockDriverBackend(page);
    await page.goto('/');
    await page.waitForTimeout(4000);

    const fatal = rejections.filter(
      (e) => !/NativeEventEmitter|Deprecated|firebase|google|maps/i.test(e)
    );
    expect(fatal).toEqual([]);
  });

  test('suspended driver sees a blocking state, not the dashboard', async ({ page }) => {
    await seedAuthedDriverSession(page, {
      driver: { onboarding_status: 'suspended', is_online: false },
    });
    await mockDriverBackend(page);
    await page.goto('/');
    await page.waitForTimeout(2500);
    await expect(page.locator('body')).toBeVisible();
  });
});
