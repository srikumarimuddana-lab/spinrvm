import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

/**
 * Full-page screenshot baselines for the shared chrome (sidebar, topbar,
 * card/table/badge/button primitives) plus a small, deliberately-tight set
 * of high-traffic pages — not all 41 dashboard routes. This is a starter
 * set (epic #2785 Phase 1 prerequisite); expand incrementally the same way
 * e2e/a11y-baseline.json's route coverage can grow over time.
 *
 * All 6 baselines seeded 2026-09-02/03 — see
 * docs/change-log/2026-09-02-seed-visual-regression-baselines.md.
 * `dashboard-rides` needed a follow-up: its first capture surfaced a
 * mock-fixture gap (admin-mocks.ts's generic /api/** fallback shape didn't
 * match what getRides() expects), fixed, then re-captured.
 *
 * `dashboard-monitoring` is a known flake risk, not yet mocked: its map
 * panel fetches live vector tiles from tiles.openfreemap.org
 * (src/lib/map/maplibre-base.ts) with no page.route() stub here, so its
 * comparison depends on the CI runner actually reaching that host at
 * capture/compare time — see the `continue-on-error` comment on
 * `visual-regression-test` in ci.yml for why that job isn't blocking yet.
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
