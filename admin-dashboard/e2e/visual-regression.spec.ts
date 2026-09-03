import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

/**
 * Full-page screenshot baselines for the shared chrome (sidebar, topbar,
 * card/table/badge/button primitives) plus a small, deliberately-tight set
 * of high-traffic pages — not all 41 dashboard routes. This is a starter
 * set (epic #2785 Phase 1 prerequisite); expand incrementally the same way
 * e2e/a11y-baseline.json's route coverage can grow over time.
 *
 * Baselines for 5 of these 6 pages were seeded 2026-09-02 — see
 * docs/change-log/2026-09-02-seed-visual-regression-baselines.md.
 * `dashboard-rides` is not yet seeded: capturing it first surfaced a
 * mock-fixture gap (admin-mocks.ts's generic /api/** fallback shape doesn't
 * match what getRides() expects), now fixed — it needs one more run of
 * update-visual-baselines.yml (see that workflow) to pick up a corrected
 * screenshot before it can be added.
 */

const PAGES = [
  { name: 'login', path: '/login', waitFor: '#email' },
  { name: 'dashboard-home', path: '/dashboard', waitFor: 'main' },
  { name: 'dashboard-rides', path: '/dashboard/rides', waitFor: 'h1' },
  { name: 'dashboard-drivers', path: '/dashboard/drivers', waitFor: 'h1' },
  { name: 'dashboard-monitoring', path: '/dashboard/monitoring', waitFor: 'main' },
  { name: 'dashboard-settings', path: '/dashboard/settings', waitFor: 'main' },
];

test.describe('Visual regression', () => {
  for (const { name, path, waitFor } of PAGES) {
    test(`${name} matches baseline`, async ({ page }) => {
      await setupAdminMocks(page);
      await page.goto(path);
      await page.locator(waitFor).first().waitFor({ state: 'visible', timeout: 20000 });
      // Let any mount-time transitions/skeleton states settle before capture.
      await page.waitForTimeout(500);

      await expect(page).toHaveScreenshot(`${name}.png`, {
        fullPage: true,
        animations: 'disabled',
      });
    });
  }
});
