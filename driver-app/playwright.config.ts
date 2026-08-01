/**
 * Playwright config for driver-app E2E tests.
 *
 * Mirrors rider-app/playwright.config.ts. Runs against the Expo web export.
 *
 *   Local dev:  `yarn start --web` (dev server on :8081)
 *               PLAYWRIGHT_BASE_URL=http://localhost:8081 yarn test:e2e
 *
 *   CI:        `npx expo export --platform web` + `npx serve dist`
 *              Port 3003 avoids clashing with rider-app (:3002) and
 *              admin-dashboard (:3000).
 *
 * Backend API (`**\/api/v1/**`), WebSocket (`/ws/driver/*`), Google Maps,
 * and Firebase are all mocked in specs via `page.route()` + `page.addInitScript()`.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.PLAYWRIGHT_PORT || 3003);
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  // 'github' alone gives inline annotations in the Actions UI but never
  // writes playwright-report/ — the path ci.yml's "Upload Playwright
  // report" step expects, so every CI run had nothing to upload and a
  // failure would have no downloadable HTML report to debug from.
  // Keep the annotations, add 'html' so the artifact actually exists.
  // Same fix as admin-dashboard/playwright.config.ts (#3115).
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'html',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    navigationTimeout: 20_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: process.env.PLAYWRIGHT_START_SERVER
    ? {
        command: `npx serve dist -l ${PORT} --single`,
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      }
    : undefined,
});
