/**
 * Playwright config for rider-app E2E tests (SPR-02/2b).
 *
 * Tests run against the Expo web export. Two modes:
 *
 *   Local dev:  `yarn start --web` (dev server on :8081)
 *               PLAYWRIGHT_BASE_URL=http://localhost:8081 yarn test:e2e
 *
 *   CI / fresh build: `npx expo export --platform web` + `npx serve dist`
 *               Port 3002 is used to avoid clashing with admin-dashboard (:3000).
 *
 * All backend API calls (`**\/api/v1/**`) and third-party services (Google Maps,
 * Firebase, Stripe) are mocked inside individual specs via `page.route()`.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.PLAYWRIGHT_PORT || 3002);
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'html',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // Expo web bundles are large — allow extra time for initial load
    navigationTimeout: 20_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // webServer is opt-in via PLAYWRIGHT_START_SERVER=1; this lets CI start the
  // server with explicit control (export → serve) rather than blocking here.
  webServer: process.env.PLAYWRIGHT_START_SERVER
    ? {
        command: `npx serve dist -l ${PORT} --single`,
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      }
    : undefined,
});
