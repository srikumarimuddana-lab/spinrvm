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
 * `dashboard-monitoring`'s map panel fetches live vector tiles from
 * tiles.openfreemap.org (src/lib/map/maplibre-base.ts) — this used to make
 * its baseline depend on the CI runner's live network reachability at
 * capture/compare time (one run rendered a normal basemap, another a red
 * "Failed to load map style" error, same commit). Fixed 2026-09-04 by
 * stubbing that host below with a minimal, source-less MapLibre style: it
 * has no "sources"/"glyphs"/"sprite" keys, so maplibre-gl renders a plain
 * background and fires its "load" event without issuing any further
 * requests — no live network involved at all. See the `continue-on-error`
 * comment on `visual-regression-test` in ci.yml, which this fix should let
 * a follow-up PR flip off.
 */

const PAGES = [
  { name: 'login', path: '/login', waitFor: '#email' },
  { name: 'dashboard-home', path: '/dashboard', waitFor: 'main' },
  { name: 'dashboard-rides', path: '/dashboard/rides', waitFor: 'h1' },
  { name: 'dashboard-drivers', path: '/dashboard/drivers', waitFor: 'h1' },
  { name: 'dashboard-monitoring', path: '/dashboard/monitoring', waitFor: 'main' },
  { name: 'dashboard-settings', path: '/dashboard/settings', waitFor: 'main' },
];

// A minimal, self-contained MapLibre GL style: no "sources", "glyphs", or
// "sprite" keys, so the browser never needs to fetch anything beyond this
// JSON itself. Renders as a plain background layer -- visually flat, but
// deterministic, which is what a baseline needs. Applied to every page in
// this spec (harmless no-op for pages with no map) rather than only
// dashboard-monitoring, so it stays correct if another screenshotted page
// ever grows a map too.
const STUB_MAP_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e2e2e2' } }],
};

test.describe('Visual regression', () => {
  for (const { name, path, waitFor } of PAGES) {
    test(`${name} matches baseline`, async ({ page }) => {
      await setupAdminMocks(page);
      await page.route('**/tiles.openfreemap.org/**', (route) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(STUB_MAP_STYLE),
        })
      );
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
