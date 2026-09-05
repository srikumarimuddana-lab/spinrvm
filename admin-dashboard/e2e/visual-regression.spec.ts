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
 * requests — no live network involved at all. `visual-regression-test` in
 * ci.yml is blocking as of this fix (ACTION_ITEMS.md B38 closed).
 */

const PAGES = [
  { name: 'login', path: '/login', waitFor: '#email', hasSidebar: false },
  { name: 'dashboard-home', path: '/dashboard', waitFor: 'main', hasSidebar: true },
  { name: 'dashboard-rides', path: '/dashboard/rides', waitFor: 'h1', hasSidebar: true },
  { name: 'dashboard-drivers', path: '/dashboard/drivers', waitFor: 'h1', hasSidebar: true },
  { name: 'dashboard-monitoring', path: '/dashboard/monitoring', waitFor: 'main', hasSidebar: true },
  { name: 'dashboard-settings', path: '/dashboard/settings', waitFor: 'main', hasSidebar: true },
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
  for (const { name, path, waitFor, hasSidebar } of PAGES) {
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
      if (hasSidebar) {
        // #4998: the sidebar's collapsible nav groups (PR #4985) compute
        // their open/closed state correctly on the very first render — the
        // localStorage read that can override it is async, but the initial
        // fallback (route-based default) is already right before that
        // effect ever fires. The bug this closes wasn't the app being
        // wrong; it was this test having no way to WAIT for a proven-
        // settled render before capturing, and instead capturing after a
        // fixed delay whose sufficiency depends on the CI runner's own
        // Chromium build — see sidebar.tsx's data-nav-hydrated attribute,
        // which flips only once that effect has actually run (success or
        // caught failure), for a wait this test can assert on directly
        // instead of inferring from a timeout.
        await page.locator('nav[data-nav-hydrated="true"]').waitFor({ state: 'attached', timeout: 5000 });
      }
      // Let any mount-time transitions/skeleton states settle before capture.
      await page.waitForTimeout(500);

      await expect(page).toHaveScreenshot(`${name}.png`, {
        fullPage: true,
        animations: 'disabled',
      });
    });
  }
});
